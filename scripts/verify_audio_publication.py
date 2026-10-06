#!/usr/bin/env python3
"""Verify a published remote audio pack before a release is marked publishable (PLATFORM_V2.md 8).

In remote mode every preview is served from one pinned HTTPS origin under a content-addressed key,
<origin><pathPrefix><sha256>.mp3; the server never fetches or serves those objects. This verifier is
the operator's gate between uploading the objects and committing the delivery manifest that turns
previews on. It never uploads, needs no credential, ignores proxy and .netrc settings and sends no
cookies. `make_audio_delivery_v2.py --mode remote` writes a manifest without fetching anything; the
manifest this script writes is the one to commit.

For every listed recording it requires, before writing anything:
  * its rights record: the catalog and rights rows exist, the licence is an admitted CC class with
    its exact URL in both rows, the rights row's audio pins equal the catalog's, the review decision
    is "approved" and the public playback decision is "approved-with-attribution";
  * its object: an anonymous GET of exactly the content-addressed URL answers 200 with no redirect,
    Content-Type audio/mpeg, identity encoding and a Content-Length equal to the catalog's
    audioBytes, and the body has exactly that length and the catalog's SHA-256;
  * for a deterministic sample (first, last and every --range-every-th row): a Range request
    answers 206 with the exact Content-Range and the same bytes as the full body.
Only when every row passes does it write the v2 remote delivery manifest (publicDeliveryVerified
true, with a verification block) to --output, after the server's own AudioDeliveryV2 accepts it.
Otherwise nothing is written there, the report lists the failures, and the exit status is 1.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
from collection_v2 import AudioDeliveryV2  # noqa: E402
from corpus_release import LICENSES, ReleaseError, require, strict_json  # noqa: E402
from make_audio_delivery_v2 import build as delivery_record  # noqa: E402
from release_v2 import SOURCE_PREFIX, LimitsV2, exact_https_origin, load_release_v2  # noqa: E402

USER_AGENT = 'music-atlas-publication-verifier/1'
MAX_AUDIO = 20_000_000
RANGE_BYTES = 65_536


def object_url(origin, prefix, digest):
    return origin + prefix + digest + '.mp3'


def rights_problems(release, row, ident, size, digest):
    """Why a row may not be published, from its verified catalog and rights records ([] when it may)."""
    try:
        track, rights = release.track_record(row)
    except (ReleaseError, ValueError):
        return ['no catalog/rights record for the row']
    problems = []
    license_id = track.get('license')
    if track.get('id') != ident or rights.get('id') != ident:
        problems.append('record identity differs from the catalog row')
    if license_id not in LICENSES or track.get('licenseUrl') != LICENSES[license_id]:
        problems.append('catalog licence is not an admitted CC class with its exact URL')
    elif rights.get('license') != license_id or rights.get('licenseUrl') != LICENSES[license_id]:
        problems.append('rights licence differs from the catalog')
    if (track.get('audioBytes'), track.get('audioSha256')) != (size, digest) or \
            (rights.get('audioBytes'), rights.get('audioSha256')) != (size, digest):
        problems.append('audio pins differ between the catalog and rights rows')
    if rights.get('decision') != 'approved':
        problems.append('the rights review is not approved')
    if rights.get('publicPlaybackDecision') != 'approved-with-attribution':
        problems.append('public playback is not approved with attribution')
    return problems


def range_window(size, row):
    start = (row * 7919) % max(1, size - RANGE_BYTES) if size > RANGE_BYTES else 0
    return start, min(size, start + RANGE_BYTES) - 1


def anonymous_session():
    """A requests session that carries nothing ambient: no proxy, .netrc or other environment settings, and a
    cookie policy that stores no cookie, so a Set-Cookie from the origin is never sent back."""
    from http.cookiejar import DefaultCookiePolicy
    import requests
    session = requests.Session()
    session.trust_env = False
    session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return session


def object_problems(session, url, size, digest, *, row, sample, deadline):
    """Fetch one object anonymously and compare it with its pins ([] when it matches)."""
    headers = {'Accept-Encoding': 'identity', 'User-Agent': USER_AGENT}
    timeout = max(.1, min(60, deadline - time.monotonic()))
    if timeout <= .1:
        return ['verification deadline expired']
    body = bytearray() if sample else None
    with session.get(url, headers=headers, stream=True, allow_redirects=False, timeout=(timeout, timeout)) as response:
        status = response.status_code
        if status != 200:
            return [f'HTTP {status}' + (' (redirects are not allowed)' if 300 <= status < 400 else '')]
        problems = []
        content_type = response.headers.get('Content-Type', '')
        if content_type.split(';')[0].strip().lower() != 'audio/mpeg':
            problems.append('Content-Type is ' + (content_type or 'missing') + ', not audio/mpeg')
        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            problems.append('encoded response')
        if response.headers.get('Content-Length') != str(size):
            problems.append(f"Content-Length {response.headers.get('Content-Length')} differs from the pinned {size}")
        response.raw.decode_content = False
        hasher, total = hashlib.sha256(), 0
        while True:
            chunk = response.raw.read(min(1024 * 1024, size - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > size:
                problems.append('body is longer than the pinned length')
                break
            hasher.update(chunk)
            if body is not None:
                body.extend(chunk)
        if total < size:
            problems.append(f'body has {total} bytes, the pin {size}')
        elif total == size and hasher.hexdigest() != digest:
            problems.append('body SHA-256 differs from the pin')
    if sample and not problems:
        start, end = range_window(size, row)
        with session.get(url, headers={**headers, 'Range': f'bytes={start}-{end}'}, stream=True, allow_redirects=False,
                         timeout=(timeout, timeout)) as response:
            part = response.raw.read(end - start + 2) if response.status_code == 206 else b''
            if response.status_code != 206:
                problems.append(f'Range request answered HTTP {response.status_code}, not 206')
            elif response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
                problems.append('Range response has the wrong Content-Range')
            elif bytes(part) != bytes(body[start:end + 1]):
                problems.append('Range response bytes differ from the object')
    return problems


def verify(release, *, origin, prefix, session_factory, subset=None, range_every=50, workers=4,
           seconds=7200, max_failures=20):
    """Check every listed row; returns (record or None, report)."""
    origin = exact_https_origin(origin)
    require(isinstance(prefix, str) and SOURCE_PREFIX.fullmatch(prefix) is not None, 'Invalid remote audio path prefix')
    require(1 <= workers <= 16 and range_every >= 1, 'Invalid verifier settings')
    pins = release.audio_pins()
    if subset is not None:
        wanted = set(subset)
        require(len(wanted) == len(subset) and wanted <= {ident for _, ident, _, _ in pins}, 'Unknown or duplicate subset ID')
        pins = [pin for pin in pins if pin[1] in wanted]
    require(pins, 'Nothing to verify')
    for _, ident, size, _ in pins:
        require(isinstance(size, int) and 1 <= size <= MAX_AUDIO, 'Audio pin outside the preview bound: ' + ident)
    deadline = time.monotonic() + seconds
    sampled = {pins[0][0], pins[-1][0]} | {row for index, (row, _, _, _) in enumerate(pins) if index % range_every == 0}
    failures, lock, local, sessions = [], threading.Lock(), threading.local(), []
    stats = {'fetched': 0, 'bytes': 0, 'rangeChecks': 0}

    def session():
        if not hasattr(local, 'session'):
            local.session = session_factory()
            with lock:
                sessions.append(local.session)
        return local.session

    def check(pin):
        row, ident, size, digest = pin
        with lock:
            if len(failures) >= max_failures:
                return
        problems = rights_problems(release, row, ident, size, digest)
        url = object_url(origin, prefix, digest)
        if not problems:
            try:
                problems = object_problems(session(), url, size, digest, row=row, sample=row in sampled, deadline=deadline)
            except Exception as error:  # transport failures are failures; nothing is retried silently
                problems = ['transfer failed: ' + type(error).__name__]
        with lock:
            if problems:
                failures.append({'row': row, 'id': ident, 'url': url, 'problems': problems})
            else:
                stats['fetched'] += 1
                stats['bytes'] += size
                stats['rangeChecks'] += row in sampled
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(check, pins))
    finally:
        for item in sessions:
            item.close()
    checked = stats['fetched'] + len(failures)
    report = {'releaseSha256': release.manifest_sha256, 'catalogId': release.catalog_id, 'origin': origin, 'pathPrefix': prefix,
              'listed': len(pins), 'checked': checked, 'passed': stats['fetched'], 'failed': len(failures),
              'stoppedEarly': checked < len(pins), 'rangeChecks': stats['rangeChecks'], 'bytes': stats['bytes'],
              'failures': failures}
    if failures or checked != len(pins):
        return None, report
    record = delivery_record(release, mode='remote', origin=origin, prefix=prefix, subset=subset)
    record['verification'] = {
        'method': 'anonymous GET of every listed object without redirects: 200, audio/mpeg, identity encoding, exact '
                  'Content-Length, length and SHA-256; sampled Range requests 206 with exact bytes; rights rows approved '
                  'for public playback with attribution under an admitted CC licence',
        'tool': 'scripts/verify_audio_publication.py', 'origin': origin, 'pathPrefix': prefix,
        'filesFetched': stats['fetched'], 'bytes': stats['bytes'], 'rangeChecks': stats['rangeChecks'],
        'verifiedAt': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    return record, report


def publish(record, release, output, *, replace=False):
    """Write the manifest only after the server's own parser accepts it; never clobber silently."""
    output = Path(output)
    require(replace or not output.exists(), 'The delivery manifest exists; pass --replace to write a new verified one')
    temporary = output.with_name('.' + output.name + '.verifying')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    try:
        AudioDeliveryV2(temporary, release, enabled=True)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--limits', type=json.loads, default=None)
    parser.add_argument('--origin', required=True, help='exact https:// origin of the object store (custom domain)')
    parser.add_argument('--prefix', default='/', help='key prefix, e.g. /fma2000/')
    parser.add_argument('--output', type=Path, required=True, help='where the verified remote delivery manifest goes')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--report', type=Path, help='JSON report (written on success and failure)')
    parser.add_argument('--subset-ids', type=Path, help='JSON array of catalog IDs; the others stay "Preview unavailable"')
    parser.add_argument('--range-every', type=int, default=50)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--deadline-seconds', type=int, default=7200)
    parser.add_argument('--max-failures', type=int, default=20)
    args = parser.parse_args()
    release = load_release_v2(args.release_dir, expected_manifest_sha256=args.expected_manifest_sha256,
                              limits=LimitsV2.from_config(args.limits))
    subset = strict_json(args.subset_ids.read_bytes(), 'subset IDs', 16_000_000) if args.subset_ids else None
    require(subset is None or (isinstance(subset, list) and all(isinstance(i, str) for i in subset)), 'Subset must be a JSON array of IDs')
    record, report = verify(release, origin=args.origin, prefix=args.prefix, session_factory=anonymous_session,
                            subset=subset, range_every=args.range_every, workers=args.workers,
                            seconds=args.deadline_seconds, max_failures=args.max_failures)
    if record is not None:
        report['output'] = str(publish(record, release, args.output, replace=args.replace))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'failures'} | {'failures': report['failures'][:5]}))
    return 0 if record is not None else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
