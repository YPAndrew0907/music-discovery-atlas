"""Bounded build-time hydration of the exact approved 2,000 MP3s from official ranges.

No full-archive fallback, audio transformation, model work, or runtime downloads.
The complete verified delivery marker is committed last; failed builds retain
the hosting platform's previous deployment.
"""
import argparse
import bz2
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from active_corpus import selected_corpus
from corpus_release import ReleaseError, integer, object_sha, require, sha256, strict_json, verify_spec

PLAN_NAME = 'audio-hydration.json'
PLAN_SHA = 'cc00b6d1447eb290fe2fb4883580d8ed9b06e451723a5ec79738ca189948a79b'
RELEASE_SHA = 'af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283'
MAX_TRANSFER = 2_400_000_000
MAX_SECONDS = 3600
WORKERS = 6
MAX_AUDIO = 20_000_000
ARCHIVES = {
    'fma_large': ('https://os.unil.cloud.switch.ch/fma/fma_large.zip', 100306112191, '"150d434dec77ecfcfa78aea547235a0f-9566"'),
    'fma_small': ('https://os.unil.cloud.switch.ch/fma/fma_small.zip', 7679594875, '"0c99b6eda4ec8d7f62f635c4f65d7c9f-1465"'),
}
RANGE_POLICY = {'contentRangeExact': True, 'fullArchiveFallbackAllowed': False, 'ifMatchRequired': True,
                'localHeaderAllowanceBytes': 512, 'redirectsAllowed': False, 'requiredStatus': 206,
                'responseBytesExact': True, 'unchangedAudioOnly': True}


class HydrationStopped(ReleaseError):
    """A fail-closed error that must not be retried or routed elsewhere."""


@dataclass(frozen=True)
class Entry:
    id: str
    audio: str
    audio_bytes: int
    audio_sha: str
    url: str
    archive_bytes: int
    etag: str
    filename: str
    offset: int
    compressed_bytes: int
    crc32: int
    range_bytes: int


def validate_plan(plan, selected):
    release = selected.release
    require(isinstance(plan, dict) and integer(plan.get('schemaVersion'), 1, 1)
            and plan.get('kind') == 'fma-approved-audio-hydration'
            and integer(plan.get('count'), release.count, release.count)
            and plan.get('catalogId') == release.catalog_id
            and plan.get('orderedIdsSha256') == object_sha(list(release.ordered_ids)), 'Hydration corpus identity mismatch')
    require(object_sha(plan.get('rangePolicy')) == object_sha(RANGE_POLICY), 'Hydration range policy changed')
    assets = {'catalog.json': release.assets['catalog'], 'ids.json': release.assets['ids'],
              'manifest.json': release.assets['graphManifest'], 'rights.json': release.assets['rights']}
    bindings = plan.get('bindings')
    require(isinstance(bindings, dict) and set(bindings) == set(assets) | {'release.json'}, 'Hydration binding inventory mismatch')
    for name, data in assets.items():
        require(object_sha(bindings[name]) == object_sha({'path': name, 'bytes': len(data), 'sha256': sha256(data)}),
                'Hydration asset binding mismatch')
    release_pin = bindings['release.json']
    require(release_pin.get('path') == 'release.json' and release_pin.get('sha256') == release.manifest_sha256,
            'Hydration release pin mismatch')
    verify_spec(selected.directory, release_pin, 32_768, 'release.json')
    archives = plan.get('archives')
    require(isinstance(archives, dict) and set(archives) == set(ARCHIVES), 'Unexpected hydration source inventory')
    for key, expected in ARCHIVES.items():
        spec = archives[key]
        require(isinstance(spec, dict) and isinstance(spec.get('etag'), str)
                and (spec.get('url'), spec.get('bytes'), spec.get('etag')) == expected
                and type(spec.get('bytes')) is int, 'Official archive identity changed')
    catalog = strict_json(release.assets['catalog'], 'catalog', 8_000_000)
    rights = strict_json(release.assets['rights'], 'rights', 8_000_000)['tracks']
    rows = plan.get('entries')
    require(isinstance(rows, list) and len(rows) == release.count
            and [row.get('id') for row in rows] == list(release.ordered_ids), 'Hydration ID count/order mismatch')
    entries = []
    for row, track, right in zip(rows, catalog['tracks'], rights):
        require(right.get('publicPlaybackDecision') == 'approved-with-attribution', 'Hydration requires approved public playback')
        require(row.get('audioPath') == track['audio'] and row.get('audioBytes') == track['audioBytes']
                and type(row.get('audioBytes')) is int and row.get('audioSha256') == track['audioSha256']
                and object_sha(row.get('evidence')) == object_sha(right['evidence']['asset']), 'Hydration recording/evidence mismatch')
        require(integer(row.get('method'), 12, 12) and integer(row.get('audioBytes'), 1, MAX_AUDIO)
                and integer(row.get('compressedSize'), 1, MAX_AUDIO)
                and integer(row.get('rangeBytes'), 513, MAX_AUDIO + 512)
                and row['rangeBytes'] == row['compressedSize'] + 512
                and integer(row.get('crc32'), 0, 2**32 - 1), 'Invalid bounded bzip2 member')
        require(row.get('archive') in ARCHIVES, 'Unapproved archive source')
        url, archive_bytes, etag = ARCHIVES[row['archive']]
        require(integer(row.get('offset'), 0, archive_bytes - row['rangeBytes']), 'Hydration range is outside archive')
        number = track['id'].removeprefix('fma:')
        require(track['id'].startswith('fma:') and number.isdigit(), 'Unexpected recording identity')
        filename = f"{row['archive']}/{int(number)//1000:03}/{int(number):06}.mp3"
        require(row.get('filename') == filename and row['audioPath'] == f'audio/{int(number):06}.mp3', 'Hydration member path mismatch')
        evidence_data = verify_spec(selected.directory, row['evidence'], 8_000_000)
        evidence = strict_json(evidence_data, 'rights/source evidence', 8_000_000)
        member = {'filename': filename, 'offset': row['offset'], 'compressedSize': row['compressedSize'],
                  'size': row['audioBytes'], 'crc32': row['crc32'], 'compression': 12}
        require(evidence.get('audioArchive') == url and object_sha(evidence.get('audioZipMember')) == object_sha(member),
                'Hydration member differs from approved source evidence')
        entries.append(Entry(row['id'], row['audioPath'], row['audioBytes'], row['audioSha256'], url,
                             archive_bytes, etag, filename, row['offset'], row['compressedSize'], row['crc32'], row['rangeBytes']))
    require('fma:30702' not in release.ordered_ids and len({row.audio for row in entries}) == release.count
            and len({row.audio_sha for row in entries}) == release.count, 'Excluded or duplicate hydration audio')
    require(sum(row.range_bytes for row in entries) <= MAX_TRANSFER, 'Hydration plan exceeds total transfer budget')
    return tuple(entries)


def load_plan(root, package, selected):
    require(selected is not None and selected.release.count == 2000
            and selected.release.manifest_sha256 == RELEASE_SHA, 'Hydration is scoped to the reviewed 2000 release')
    pins = [row for row in package.get('files', []) if row.get('path') == PLAN_NAME]
    require(len(pins) == 1 and pins[0].get('sha256') == PLAN_SHA, 'Hydration plan lacks its reviewed package pin')
    data = verify_spec(root, pins[0], 1_500_000, PLAN_NAME)
    return validate_plan(strict_json(data, 'hydration plan', 1_500_000), selected)


class Budget:
    def __init__(self, *, byte_limit=MAX_TRANSFER, seconds=MAX_SECONDS):
        self.byte_limit, self.deadline = byte_limit, time.monotonic() + seconds
        self.reserved, self.attempts = 0, 0
        self.lock, self.stopped = threading.Lock(), threading.Event()

    def check(self):
        if self.stopped.is_set() or time.monotonic() >= self.deadline:
            raise HydrationStopped('Hydration stopped or its global deadline expired')

    def reserve(self, length):
        with self.lock:
            self.check()
            if self.reserved + length > self.byte_limit:
                self.stopped.set()
                raise HydrationStopped('Hydration transfer budget exceeded, including retries')
            self.reserved += length
            self.attempts += 1


def decode_member(data, entry):
    require(len(data) == entry.range_bytes and data[:4] == b'PK\x03\x04', 'Invalid local ZIP member range')
    _, _, flags, method, _, _, crc, compressed, size, filename_bytes, extra_bytes = struct.unpack_from('<4s5H3I2H', data)
    offset = 30 + filename_bytes + extra_bytes
    require(not flags & ~0x0808 and method == 12 and offset <= 512
            and offset + entry.compressed_bytes <= len(data)
            and data[30:30 + filename_bytes] == entry.filename.encode('utf-8'), 'ZIP member/header identity mismatch')
    if not flags & 8:
        require((crc, compressed, size) == (entry.crc32, entry.compressed_bytes, entry.audio_bytes), 'ZIP local size/CRC mismatch')
    decoder = bz2.BZ2Decompressor()
    audio = decoder.decompress(data[offset:offset + entry.compressed_bytes], max_length=entry.audio_bytes + 1)
    require(decoder.eof and not decoder.unused_data and len(audio) == entry.audio_bytes
            and zlib.crc32(audio) == entry.crc32 and sha256(audio) == entry.audio_sha,
            'Extracted official MP3 differs from its approved byte/hash identity')
    return audio


def fetch_member(entry, budget, session):
    import requests
    from urllib3.exceptions import ProtocolError, ReadTimeoutError
    end = entry.offset + entry.range_bytes - 1
    for attempt in range(2):
        budget.reserve(entry.range_bytes)
        try:
            timeout = max(.1, min(15, budget.deadline - time.monotonic()))
            with session.get(entry.url, headers={'Range': f'bytes={entry.offset}-{end}', 'If-Match': entry.etag,
                             'Accept-Encoding': 'identity', 'User-Agent': 'music-atlas-approved-hydration/1'},
                             stream=True, allow_redirects=False, timeout=(timeout, timeout)) as response:
                if response.status_code in (401, 403, 404, 412, 416, 451):
                    raise HydrationStopped(f'Official source refused the pinned range: HTTP {response.status_code}')
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt == 0:
                        budget.check()
                        time.sleep(min(2, max(0, budget.deadline - time.monotonic())))
                        continue
                    raise HydrationStopped(f'Official source unavailable: HTTP {response.status_code}')
                if (response.status_code != 206
                        or response.headers.get('Content-Range') != f'bytes {entry.offset}-{end}/{entry.archive_bytes}'
                        or response.headers.get('Content-Length') != str(entry.range_bytes)
                        or response.headers.get('ETag') != entry.etag
                        or response.headers.get('Content-Encoding', 'identity') != 'identity'):
                    raise HydrationStopped('Official server ignored or changed the exact approved byte range')
                response.raw.decode_content = False
                chunks, total = [], 0
                while True:
                    budget.check()
                    chunk = response.raw.read(min(65_536, entry.range_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > entry.range_bytes:
                        raise HydrationStopped('Official range response exceeded its approved byte length')
                    chunks.append(chunk)
                if total != entry.range_bytes:
                    raise HydrationStopped('Incomplete official range response')
            return decode_member(b''.join(chunks), entry)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, ProtocolError, ReadTimeoutError) as error:
            if attempt or budget.stopped.is_set():
                raise HydrationStopped('Official range transfer failed within its bounded retries') from error
            budget.check()
    raise HydrationStopped('Official range transfer did not complete')


def hydrate(root, selected, entries, *, source_cache=None, session_factory=None, progress=None):
    """source_cache is an explicit local test injection; the production CLI never accepts it."""
    root = Path(root)
    require(len(entries) == selected.release.count, 'Hydration entry count mismatch')
    target, credits, delivery = root / 'audio-preview', root / 'audio-release-credits', root / 'audio-delivery.verified.json'
    require(not any(path.exists() or path.is_symlink() for path in (target, credits, delivery)), 'Do not overwrite an existing audio installation')
    required_bytes = sum(entry.audio_bytes for entry in entries) + selected.release.evidence_bytes + 32_000_000 + 128_000_000
    require(shutil.disk_usage(root).free >= required_bytes, 'Insufficient disk for the complete hydrated corpus')
    started, budget = time.monotonic(), Budget()
    local, sessions, sessions_lock = threading.local(), [], threading.Lock()
    if session_factory is None and source_cache is None:
        import requests
        session_factory = requests.Session
    with tempfile.TemporaryDirectory(prefix='.approved-hydration-', dir=root) as temporary:
        temporary = Path(temporary)
        audio_out, credits_out = temporary / 'audio', temporary / 'credits'
        audio_out.mkdir()
        credits_out.mkdir()
        def acquire(entry):
            budget.check()
            if source_cache is None:
                if not hasattr(local, 'session'):
                    local.session = session_factory()
                    with sessions_lock:
                        sessions.append(local.session)
                audio = fetch_member(entry, budget, local.session)
            else:
                audio = verify_spec(Path(source_cache), {'path': Path(entry.audio).name, 'bytes': entry.audio_bytes,
                                    'sha256': entry.audio_sha}, MAX_AUDIO)
            budget.check()
            require(len(audio) == entry.audio_bytes and sha256(audio) == entry.audio_sha, 'Hydrated audio hash mismatch')
            with (audio_out / Path(entry.audio).name).open('xb') as output:
                output.write(audio)
            return entry.id
        completed = set()
        try:
            with ThreadPoolExecutor(max_workers=WORKERS) as executor:
                futures = [executor.submit(acquire, entry) for entry in entries]
                try:
                    for future in as_completed(futures, timeout=MAX_SECONDS):
                        ident = future.result()
                        require(ident not in completed, 'Duplicate hydrated recording')
                        completed.add(ident)
                        if progress and len(completed) % 100 == 0:
                            progress(len(completed), len(entries))
                except BaseException:
                    budget.stopped.set()
                    for future in futures:
                        future.cancel()
                    raise
        finally:
            for session in sessions:
                session.close()
        budget.check()
        require(completed == set(selected.release.ordered_ids) and len(list(audio_out.iterdir())) == len(entries),
                'The complete approved corpus is not ready')
        rights = strict_json(selected.release.assets['rights'], 'rights', 8_000_000)
        for name, data in [('catalog.json', selected.release.assets['catalog']), ('rights.json', selected.release.assets['rights'])]:
            (credits_out / name).write_bytes(data)
        for row in rights['tracks']:
            budget.check()
            pin = row['evidence']['asset']
            data = verify_spec(selected.directory, pin, 8_000_000)
            destination = credits_out / pin['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        rows = [{'id': entry.id, 'available': True, 'url': '/audio/' + Path(entry.audio).name,
                 'bytes': entry.audio_bytes, 'sha256': entry.audio_sha} for entry in entries]
        record = {'schemaVersion': 1, 'catalogId': selected.release.catalog_id,
                  'catalogSha256': sha256(selected.release.assets['catalog']), 'enabled': True,
                  'publicDeliveryVerified': True, 'tracks': rows,
                  'deliveryVerificationScope': 'Complete approved official ranges and unchanged MP3 hashes verified at image build; hosted playback is a separate check',
                  'hydration': {'planSha256': PLAN_SHA, 'releaseSha256': selected.release.manifest_sha256,
                                'transferredRangeBudgetBytes': budget.reserved, 'networkAttempts': budget.attempts}}
        (temporary / 'delivery.json').write_text(json.dumps(record, indent=2) + '\n')
        budget.check()
        published = []
        try:
            audio_out.rename(target)
            published.append((target, audio_out))
            credits_out.rename(credits)
            published.append((credits, credits_out))
            (temporary / 'delivery.json').rename(delivery)
        except BaseException:
            for destination, original in reversed(published):
                destination.rename(original)
            raise
    return {'tracks': len(rows), 'audioBytes': sum(row['bytes'] for row in rows), 'elapsedSeconds': time.monotonic() - started,
            'rangeBytesReservedIncludingRetries': budget.reserved, 'networkAttempts': budget.attempts,
            'localCacheTest': source_cache is not None, 'runtimeEnabled': False, 'hostedDeliveryTested': False}


def run_bounded_worker(command, *, timeout):
    """An OS-process boundary enforces an absolute deadline even during slow HTTP headers/body reads."""
    try:
        return subprocess.run(command, check=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        # subprocess.run kills and waits for the process, including all its threads,
        # before raising. The parent then removes its private staging directory.
        raise HydrationStopped('Absolute hydration deadline exceeded; worker terminated without activation') from error


def hydrate_supervised(root, selected):
    root = Path(root)
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    final_names = ['audio-preview', 'audio-release-credits', 'audio-delivery.verified.json']
    require(not any((root / name).exists() or (root / name).is_symlink() for name in final_names),
            'Do not overwrite an existing audio installation')
    with tempfile.TemporaryDirectory(prefix='.hydration-build-', dir=root) as temporary:
        stage = Path(temporary)
        run_bounded_worker([sys.executable, str(ROOT / 'scripts/hydrate_corpus_audio.py'), '--worker-dir', str(stage)],
                           timeout=max(.001, deadline - time.monotonic()))
        result = strict_json((stage / 'hydration-result.json').read_bytes(), 'hydration result', 16_384)
        record = strict_json((stage / 'audio-delivery.verified.json').read_bytes(), 'hydrated delivery', 1_500_000)
        tracks = strict_json(selected.release.assets['catalog'], 'catalog', 8_000_000)['tracks']
        require(result.get('tracks') == selected.release.count and result.get('localCacheTest') is False
                and integer(result.get('rangeBytesReservedIncludingRetries'), 1, MAX_TRANSFER)
                and integer(result.get('networkAttempts'), 1, selected.release.count * 2)
                and result.get('audioBytes') == sum(track['audioBytes'] for track in tracks)
                and record.get('catalogId') == selected.release.catalog_id
                and record.get('catalogSha256') == sha256(selected.release.assets['catalog'])
                and record.get('enabled') is True and record.get('publicDeliveryVerified') is True
                and [row.get('id') for row in record.get('tracks', [])] == list(selected.release.ordered_ids),
                'Completed hydration worker identity mismatch')
        expected_names = {Path(track['audio']).name for track in tracks}
        audio_files = list((stage / 'audio-preview').iterdir())
        require(len(audio_files) == selected.release.count and {path.name for path in audio_files} == expected_names,
                'Completed hydration worker audio inventory mismatch')
        for track, delivered in zip(tracks, record['tracks']):
            require(time.monotonic() < deadline, 'Hydration deadline expired during final integrity checks')
            expected = {'id': track['id'], 'available': True, 'url': '/audio/' + Path(track['audio']).name,
                        'bytes': track['audioBytes'], 'sha256': track['audioSha256']}
            require(object_sha(delivered) == object_sha(expected), 'Completed hydration delivery pin mismatch')
            verify_spec(stage / 'audio-preview', {'path': Path(track['audio']).name,
                        'bytes': track['audioBytes'], 'sha256': track['audioSha256']}, MAX_AUDIO)
        for name, data in [('catalog.json', selected.release.assets['catalog']), ('rights.json', selected.release.assets['rights'])]:
            verify_spec(stage / 'audio-release-credits', {'path': name, 'bytes': len(data), 'sha256': sha256(data)}, 8_000_000)
        rights = strict_json(selected.release.assets['rights'], 'rights', 8_000_000)
        for row in rights['tracks']:
            require(time.monotonic() < deadline, 'Hydration deadline expired during credit checks')
            verify_spec(stage / 'audio-release-credits', row['evidence']['asset'], 8_000_000)
        require(time.monotonic() < deadline, 'Hydration deadline expired before publication')
        moved = []
        try:
            for name in final_names:
                require(time.monotonic() < deadline, 'Hydration deadline expired before publication')
                source, destination = stage / name, root / name
                source.rename(destination)
                moved.append((source, destination))
        except BaseException:
            for source, destination in reversed(moved):
                destination.rename(source)
            raise
    return {**result, 'elapsedSecondsIncludingSupervisor': time.monotonic() - started,
            'absoluteDeadlineSupervised': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-url', default='')
    parser.add_argument('--verify-plan', action='store_true')
    parser.add_argument('--worker-dir', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    package = json.loads((ROOT / 'package-manifest.json').read_bytes())
    selected = selected_corpus(ROOT, package)
    if selected is None or selected.audio_archive is not None:
        require(not args.verify_plan and args.worker_dir is None, 'No hydration plan is active')
        return subprocess.run([sys.executable, str(ROOT / 'scripts/install_corpus_audio_release.py'), '--legacy-url', args.legacy_url], check=True).returncode
    entries = load_plan(ROOT, package, selected)
    if args.worker_dir is not None:
        stage = args.worker_dir
        require(not args.verify_plan and stage.is_dir() and not stage.is_symlink()
                and stage.parent.resolve() == ROOT.resolve() and stage.name.startswith('.hydration-build-'),
                'Invalid private hydration worker directory')
        result = hydrate(stage, selected, entries,
                         progress=lambda count, total: print(f'Approved hydration {count}/{total}', flush=True))
        (stage / 'hydration-result.json').write_text(json.dumps(result, indent=2) + '\n')
        return 0
    if args.verify_plan:
        print(json.dumps({'verified': True, 'tracks': len(entries), 'rangeBytes': sum(entry.range_bytes for entry in entries),
                          'maximumTransferBytesIncludingRetries': MAX_TRANSFER, 'maximumSeconds': MAX_SECONDS, 'workers': WORKERS}))
        return 0
    print(json.dumps(hydrate_supervised(ROOT, selected)))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
