#!/usr/bin/env python3
"""Materialise the pinned release-format-v2 release that active-corpus.json selects; fail closed.

The Docker build runs this before the model fetch and the audio hydration. A v1, disabled or
absent selection is a no-op, so the step is safe for every release. For a v2 selection, the
optional "source" block (server/release_v2.py, install_source) chooses the mode:

bundled (default)  The release directory ships in the image (COPY corpus-releases/). It is
                   verified in place: exactly release.json plus the pinned assets (no extras,
                   no symlinks), the manifest digest the selection pins, every asset's length
                   and SHA-256 (including the lazily read evidence.sqlite and examples.json),
                   the server's own loader and the full catalog/rights/evidence row validation.
                   Nothing is downloaded or moved.
object-store       The image carries only the selection. release.json and every asset are
                   fetched from one pinned HTTPS origin under content-addressed keys
                   <origin><pathPrefix><sha256>; release.json is the object keyed by the
                   selection's manifestSha256. Transfers run in a supervised worker process with
                   an absolute deadline and a byte budget that counts retries. Redirects are
                   refused; exact status, length and identity encoding are required; a partial
                   object resumes with a Range request. Objects land in a private stage inside
                   corpus-releases/ (a dot directory, which no selection can name). The parent
                   re-hashes every object, assembles the release in the stage, runs the same
                   verification as bundled mode and only then renames it into
                   corpus-releases/<name> in one step. A failed run leaves only the private
                   stage, and the next run resumes from it.

An existing release directory is verified, never overwritten or repaired. This script does not
start, configure or enable a server; the server re-verifies the release at every start.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ReleaseError, require  # noqa: E402
from release_v2 import (load_release_v2, open_confined, parse_manifest, selection_config_v2,  # noqa: E402
                        sha256_file, validate_rows)

MANIFEST_NAME = 'release.json'
MANIFEST_LIMIT = 262_144  # the loader's own cap for release.json
MAX_SECONDS = 3600
ATTEMPTS = 3
CHUNK = 1024 * 1024
STAGE_PREFIX = '.v2-install-'
USER_AGENT = 'music-atlas-release-install/1'
REFUSED = 'Object store refused the pinned object: HTTP '


class InstallStopped(ReleaseError):
    """A fail-closed transfer or installation error."""


class Transient(Exception):
    """A retryable transfer interruption; the partial object is kept and resumed."""


def read_selection(root):
    package = json.loads((Path(root) / 'package-manifest.json').read_bytes())
    return selection_config_v2(root, package)


def checked_directory(parsed, *, must_exist=True):
    """The selected directory, with no symlink in any component below the package root."""
    current = parsed.root
    parts = parsed.relative.split('/')
    for index, part in enumerate(parts):
        current = current / part
        if not must_exist and index == len(parts) - 1 and not current.exists() and not current.is_symlink():
            return current
        require(not current.is_symlink() and current.is_dir(), 'Corpus directory cannot contain symlinks')
    return current


def stage_directory(parsed):
    return checked_directory(parsed, must_exist=False).parent / (STAGE_PREFIX + Path(parsed.relative).name)


# Verification ------------------------------------------------------------------------------------
def read_manifest(directory, expected_sha256):
    with open_confined(directory, MANIFEST_NAME) as handle:
        size = os.fstat(handle.fileno()).st_size
        require(0 < size <= MANIFEST_LIMIT, 'v2 release manifest exceeds its budget')
        data = handle.read(size + 1)
    require(len(data) == size and hashlib.sha256(data).hexdigest() == expected_sha256,
            'Unreviewed v2 release manifest digest')
    return data


def verify_directory(directory, parsed):
    """Verify a complete release directory exactly as bundled mode does; return a summary."""
    started = time.monotonic()
    directory = Path(directory)
    require(not directory.is_symlink() and directory.is_dir(), 'Missing or symlinked v2 release directory')
    data = read_manifest(directory, parsed.manifest_sha256)
    manifest = parse_manifest(data, parsed.limits)
    assets = manifest['assets']
    expected = {MANIFEST_NAME} | {spec['path'] for spec in assets.values()}
    found = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            require(not entry.is_symlink() and entry.is_file(follow_symlinks=False),
                    'v2 release directory holds a non-file entry: ' + entry.name)
            found.add(entry.name)
    require(found == expected, 'v2 release directory inventory differs from its manifest')
    for spec in assets.values():
        with open_confined(directory, spec['path']) as handle:
            require(os.fstat(handle.fileno()).st_size == spec['bytes'], 'v2 asset size mismatch: ' + spec['path'])
            require(sha256_file(handle, spec['bytes']) == spec['sha256'], 'v2 asset integrity mismatch: ' + spec['path'])
    release = load_release_v2(directory, expected_manifest_sha256=parsed.manifest_sha256, limits=parsed.limits)
    rows = validate_rows(release)
    return {'releaseSha256': parsed.manifest_sha256, 'catalogId': release.catalog_id, 'graphId': release.graph_id,
            'count': release.count, 'assetBytes': sum(spec['bytes'] for spec in assets.values()),
            'assets': len(assets), 'rowValidation': rows, 'verifySeconds': round(time.monotonic() - started, 3)}


# Transfer ----------------------------------------------------------------------------------------
class Budget:
    """One byte allowance (every attempt reserves what it may still transfer) and one deadline."""

    def __init__(self, byte_limit, seconds):
        self.byte_limit, self.deadline = byte_limit, time.monotonic() + seconds
        self.reserved = self.attempts = 0
        self.lock = threading.Lock()

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise InstallStopped('Release install deadline expired')
        return left

    def reserve(self, length):
        with self.lock:
            self.remaining()
            if self.reserved + length > self.byte_limit:
                raise InstallStopped('Release transfer budget exceeded, including retries')
            self.reserved += length
            self.attempts += 1

    def extend(self, length):
        """Bytes beyond the reservation of the current attempt (a resume answered with the whole object)."""
        with self.lock:
            if self.reserved + length > self.byte_limit:
                raise InstallStopped('Release transfer budget exceeded, including retries')
            self.reserved += length


def object_url(source, digest):
    return source['origin'] + source['pathPrefix'] + digest


def transfer(session, url, size, have, part, budget):
    """One GET of the object's missing bytes into part; returns the bytes now held."""
    headers = {'Accept-Encoding': 'identity', 'User-Agent': USER_AGENT}
    if have:
        headers['Range'] = f'bytes={have}-{size - 1}'
    timeout = max(.1, min(30, budget.remaining()))
    with session.get(url, headers=headers, stream=True, allow_redirects=False, timeout=(timeout, timeout)) as response:
        status = response.status_code
        if status in (408, 429, 500, 502, 503, 504):
            raise Transient(f'HTTP {status}')
        if have and status == 206:
            require(response.headers.get('Content-Range') == f'bytes {have}-{size - 1}/{size}'
                    and response.headers.get('Content-Length') == str(size - have),
                    'Object store answered the resume with a different byte range')
            mode = 'ab'
        elif status == 200:
            # A server that ignores Range sends the whole object again; start over.
            require(response.headers.get('Content-Length') == str(size), 'Object length differs from its pin')
            budget.extend(have)
            have, mode = 0, 'wb'
        else:
            raise InstallStopped(REFUSED + str(status))
        require(response.headers.get('Content-Encoding', 'identity') == 'identity', 'Encoded object response')
        response.raw.decode_content = False
        try:
            with part.open(mode) as output:
                while True:
                    budget.remaining()
                    chunk = response.raw.read(min(CHUNK, size - have + 1))
                    if not chunk:
                        break
                    have += len(chunk)
                    if have > size:
                        raise InstallStopped('Object response exceeded its pinned length')
                    output.write(chunk)
        except InstallStopped:
            part.unlink(missing_ok=True)  # bytes from a response that broke its contract are not a resumable prefix
            raise
    if have != size:
        raise Transient('Incomplete object response')
    return have


def transient_errors():
    errors = (Transient, OSError)
    try:
        from urllib3.exceptions import ProtocolError, ReadTimeoutError
        return errors + (ProtocolError, ReadTimeoutError)
    except ImportError:  # pragma: no cover - urllib3 ships with requests
        return errors


def fetch_object(session, url, size, digest, objects, budget):
    """Fetch one pinned object into objects/<digest>, resuming objects/<digest>.part."""
    final, part = objects / digest, objects / (digest + '.part')
    if final.is_file() and not final.is_symlink():
        return 'present'
    retryable = transient_errors()
    for attempt in range(ATTEMPTS):
        have = part.stat().st_size if part.exists() else 0
        if have >= size:  # never a valid prefix; drop it
            part.unlink()
            have = 0
        budget.reserve(size - have)
        try:
            transfer(session, url, size, have, part, budget)
            break
        except InstallStopped:
            raise
        except retryable as error:
            if attempt == ATTEMPTS - 1:
                raise InstallStopped('Object transfer failed within its bounded retries: ' + digest) from error
            time.sleep(min(2, max(0, budget.deadline - time.monotonic())) * (attempt > 0))
    with part.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if part.stat().st_size != size or actual != digest:
        part.unlink()
        raise InstallStopped('Fetched object differs from its pinned SHA-256: ' + digest)
    os.replace(part, final)
    return 'fetched'


def fetch_manifest(session, parsed, objects, budget):
    """release.json is small and only its digest is pinned: one bounded GET, no resume."""
    digest, final = parsed.manifest_sha256, objects / parsed.manifest_sha256
    if final.is_file() and not final.is_symlink():
        return final.read_bytes()
    retryable = transient_errors()
    for attempt in range(ATTEMPTS):
        budget.reserve(MANIFEST_LIMIT)
        try:
            timeout = max(.1, min(30, budget.remaining()))
            with session.get(object_url(parsed.source, digest), headers={'Accept-Encoding': 'identity', 'User-Agent': USER_AGENT},
                             stream=True, allow_redirects=False, timeout=(timeout, timeout)) as response:
                if response.status_code in (408, 429, 500, 502, 503, 504):
                    raise Transient(f'HTTP {response.status_code}')
                if response.status_code != 200:
                    raise InstallStopped(REFUSED + str(response.status_code))
                length = response.headers.get('Content-Length')
                require(length is not None and length.isdigit() and 0 < int(length) <= MANIFEST_LIMIT
                        and response.headers.get('Content-Encoding', 'identity') == 'identity', 'Invalid release manifest response')
                response.raw.decode_content = False
                data = response.raw.read(int(length) + 1)
            if len(data) != int(length):
                raise Transient('Incomplete release manifest response')
            break
        except InstallStopped:
            raise
        except retryable as error:
            if attempt == ATTEMPTS - 1:
                raise InstallStopped('Release manifest transfer failed within its bounded retries') from error
    require(hashlib.sha256(data).hexdigest() == digest, 'Fetched release manifest differs from the reviewed digest')
    temporary = objects / (digest + '.part')
    temporary.write_bytes(data)
    os.replace(temporary, final)
    return data


def fetch_release(stage, parsed, session, *, seconds=MAX_SECONDS):
    """Worker body: fetch release.json and every pinned asset into stage/objects (resumable)."""
    objects = Path(stage) / 'objects'
    objects.mkdir(exist_ok=True)
    budget = Budget(ATTEMPTS * MANIFEST_LIMIT, seconds)
    manifest = parse_manifest(fetch_manifest(session, parsed, objects, budget), parsed.limits)
    assets = sorted(manifest['assets'].values(), key=lambda spec: (spec['bytes'], spec['path']))
    total = sum(spec['bytes'] for spec in assets)
    missing = sum(spec['bytes'] for spec in assets if not (objects / spec['sha256']).is_file())
    require(shutil.disk_usage(objects).free >= missing + 64_000_000, 'Insufficient disk for the v2 release')
    budget.byte_limit += 2 * total
    outcome = {}
    for spec in assets:
        outcome[spec['path']] = fetch_object(session, object_url(parsed.source, spec['sha256']), spec['bytes'],
                                             spec['sha256'], objects, budget)
    return {'assets': outcome, 'assetBytes': total, 'reservedBytesIncludingRetries': budget.reserved,
            'attempts': budget.attempts}


def run_bounded_worker(command, *, timeout):
    """An OS-process boundary enforces the absolute deadline even inside a stalled read."""
    try:
        return subprocess.run(command, check=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise InstallStopped('Absolute release install deadline exceeded; worker terminated, nothing installed') from error
    except subprocess.CalledProcessError as error:
        raise InstallStopped('Release install worker failed; nothing installed') from error


# Installation ------------------------------------------------------------------------------------
def assemble(stage, parsed):
    """Verify every fetched object again and lay the release out in stage/release."""
    objects, release = stage / 'objects', stage / 'release'
    path = objects / parsed.manifest_sha256
    require(path.is_file() and not path.is_symlink(), 'Missing fetched release manifest')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == parsed.manifest_sha256, 'Fetched release manifest changed')
    manifest = parse_manifest(data, parsed.limits)
    if release.exists() or release.is_symlink():
        require(release.is_dir() and not release.is_symlink(), 'Unexpected release stage entry')
        shutil.rmtree(release)  # a previous assembly that failed verification; its objects were re-fetched
    release.mkdir()
    for spec in manifest['assets'].values():
        source = objects / spec['sha256']
        require(source.is_file() and not source.is_symlink(), 'Missing fetched object: ' + spec['path'])
        with open_confined(objects, spec['sha256']) as handle:
            require(os.fstat(handle.fileno()).st_size == spec['bytes']
                    and sha256_file(handle, spec['bytes']) == spec['sha256'], 'Fetched object changed: ' + spec['path'])
        os.replace(source, release / spec['path'])
    (release / MANIFEST_NAME).write_bytes(data)
    return release


def install(root, parsed, *, session_factory=None, seconds=MAX_SECONDS, supervised=True):
    """Install an object-store release; an existing directory is verified, never replaced."""
    started = time.monotonic()
    deadline = started + seconds
    require(parsed.source['kind'] == 'object-store', 'Only an object-store release is fetched')
    final = checked_directory(parsed, must_exist=False)
    if final.exists() or final.is_symlink():
        return {'mode': 'object-store', 'action': 'verified-existing', **verify_directory(final, parsed)}
    stage = stage_directory(parsed)
    require(not stage.is_symlink() and (not stage.exists() or stage.is_dir()), 'Unexpected release stage entry')
    stage.mkdir(exist_ok=True)
    if supervised:
        run_bounded_worker([sys.executable, str(Path(__file__).resolve()), '--worker-stage', str(stage)],
                           timeout=max(.001, deadline - time.monotonic()))
        worker = json.loads((stage / 'worker-result.json').read_bytes())
    else:
        if session_factory is None:
            import requests
            session_factory = requests.Session
        session = session_factory()
        try:
            worker = fetch_release(stage, parsed, session, seconds=max(.001, deadline - time.monotonic()))
        finally:
            session.close()
    require(time.monotonic() < deadline, 'Release install deadline expired before verification')
    release = assemble(stage, parsed)
    summary = verify_directory(release, parsed)
    require(time.monotonic() < deadline, 'Release install deadline expired before publication')
    require(not final.exists() and not final.is_symlink(), 'Do not overwrite an existing release directory')
    os.rename(release, final)
    shutil.rmtree(stage)
    return {'mode': 'object-store', 'action': 'fetched', 'origin': parsed.source['origin'],
            'pathPrefix': parsed.source['pathPrefix'], 'transfer': worker, **summary,
            'elapsedSeconds': round(time.monotonic() - started, 3)}


def worker(root, parsed, stage):
    """Hidden worker entry: fetch into the private stage the parent created, nothing else."""
    expected = stage_directory(parsed)
    stage = Path(stage)
    require(parsed.source['kind'] == 'object-store' and not stage.is_symlink() and stage.is_dir()
            and stage.resolve() == expected.resolve(), 'Invalid private release stage')
    import requests
    session = requests.Session()
    session.trust_env = False  # no proxy or .netrc credentials from the build environment
    try:
        result = fetch_release(stage, parsed, session)
    finally:
        session.close()
    (stage / 'worker-result.json').write_text(json.dumps(result) + '\n')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verify-only', action='store_true', help='verify the installed release; never download')
    parser.add_argument('--worker-stage', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    parsed = read_selection(ROOT)
    if args.worker_stage is not None:
        require(parsed is not None and not args.verify_only, 'No v2 release selected for the worker')
        return worker(ROOT, parsed, args.worker_stage)
    if parsed is None:
        print(json.dumps({'installed': False, 'reason': 'The active selection is not release format v2; nothing to install'}))
        return 0
    if args.verify_only or parsed.source['kind'] == 'bundled':
        summary = verify_directory(checked_directory(parsed), parsed)
        print(json.dumps({'installed': True, 'mode': parsed.source['kind'], 'action': 'verified',
                          'directory': parsed.relative, **summary}))
        return 0
    print(json.dumps({'installed': True, 'directory': parsed.relative, **install(ROOT, parsed)}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
