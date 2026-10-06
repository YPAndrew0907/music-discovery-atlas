"""Build-time audio for a release-format-v2 selection, from the same pinned official FMA ranges.

hydrate_corpus_audio.py hands a schemaVersion 2 selection to run() here; its v1 code is
unchanged. The release itself is verified first by scripts/install_release_v2.py (an earlier
Docker step); this module opens it through the server's own loader.

The approved plan (audio-hydration.json, pinned by the package manifest) names the reviewed v1
fma2000 release. A v2 release qualifies only when it is a conversion of exactly that release:
release.json's source.manifestSha256 is that release; the plan's catalog, ids, rights and graph
bindings equal the v1 bytes rebuilt from the verified database, and those bytes hash to the
source digests release.json records; and every entry matches its catalog row, rights row and
evidence blob. Transport, budgets, retries, the worker deadline and decoding are the v1
functions, unchanged.

Outputs keep the v1 names, so the service's environment needs no change:
  audio-preview/NNNNNN.mp3      every approved MP3, hashed when extracted and again before publication
  audio-release-credits/        catalog.json, rights.json and evidence/ files, byte-identical to v1
  audio-delivery.verified.json  the v2 local delivery manifest (music-audio-delivery-v2); the server
                                checks inventory and sizes at start and hashes each file on its
                                first request
A v2 release without a matching plan hydrates nothing, and previews stay "Preview unavailable".
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import hydrate_corpus_audio as v1  # noqa: E402
from collection_v2 import DELIVERY_KIND, AudioDeliveryV2, local_route  # noqa: E402
from corpus_release import ReleaseError, integer, object_sha, require, sha256, strict_json, verify_spec  # noqa: E402
from make_audio_delivery_v2 import build as delivery_record  # noqa: E402
from release_v2 import reconstruct_sources, selected_release_v2  # noqa: E402

FINAL_NAMES = ('audio-preview', 'audio-release-credits', 'audio-delivery.verified.json')
RECORD_LIMIT = 8_000_000
SCOPE = ('Complete approved official ranges and unchanged MP3 hashes verified at image build; the server checks '
         'inventory and sizes at start and each file SHA-256 on first request; hosted playback is a separate check')


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def plan_applies(release):
    """Why a v2 release has no hydration plan, or None when the pinned plan names its source."""
    if release.manifest['source'].get('manifestSha256') != v1.RELEASE_SHA:
        return 'The selected v2 release is not a conversion of the reviewed fma2000 release; no audio is hydrated'
    if release.count != v1.RELEASE_COUNT:
        return f'The selected v2 release does not hold the reviewed {v1.RELEASE_COUNT:,} recordings; no audio is hydrated'
    return None


def source_bytes(release):
    """The v1 catalog, ids and rights bytes rebuilt from the verified database, each checked against
    the digest release.json records, plus the pinned graph manifest (byte-identical to v1)."""
    source = release.manifest['source']
    catalog, rights, _ = reconstruct_sources(release)
    data = {'catalog.json': encode(catalog), 'ids.json': encode(list(release.ordered_ids)), 'rights.json': encode(rights)}
    for name, key in (('catalog.json', 'catalogSha256'), ('ids.json', 'idsSha256'), ('rights.json', 'rightsSha256')):
        require(sha256(data[name]) == source.get(key), 'The v2 release does not rebuild its recorded v1 ' + name)
    spec = release.assets['graphManifest']
    data['manifest.json'] = verify_spec(release.directory, spec, spec['bytes'], spec['path'])
    require(sha256(data['manifest.json']) == source.get('graphManifestSha256'), 'The v2 graph manifest is not the recorded v1 one')
    return data


def validate_plan_v2(plan, release, sources):
    """validate_plan in hydrate_corpus_audio.py, bound to a v2 release and its rebuilt v1 sources."""
    require(isinstance(plan, dict) and integer(plan.get('schemaVersion'), 1, 1)
            and plan.get('kind') == 'fma-approved-audio-hydration'
            and integer(plan.get('count'), release.count, release.count)
            and plan.get('catalogId') == release.catalog_id
            and plan.get('orderedIdsSha256') == object_sha(list(release.ordered_ids)), 'Hydration corpus identity mismatch')
    require(object_sha(plan.get('rangePolicy')) == object_sha(v1.RANGE_POLICY), 'Hydration range policy changed')
    bindings = plan.get('bindings')
    require(isinstance(bindings, dict) and set(bindings) == set(sources) | {'release.json'}, 'Hydration binding inventory mismatch')
    for name, data in sources.items():
        require(object_sha(bindings[name]) == object_sha({'path': name, 'bytes': len(data), 'sha256': sha256(data)}),
                'Hydration asset binding mismatch')
    release_pin = bindings['release.json']
    # v1 re-reads the v1 release.json; a v2 release records that file's digest in its source block.
    require(isinstance(release_pin, dict) and release_pin.get('path') == 'release.json'
            and release_pin.get('sha256') == release.manifest['source'].get('manifestSha256') == v1.RELEASE_SHA,
            'Hydration release pin mismatch')
    archives = plan.get('archives')
    require(isinstance(archives, dict) and set(archives) == set(v1.ARCHIVES), 'Unexpected hydration source inventory')
    for key, expected in v1.ARCHIVES.items():
        spec = archives[key]
        require(isinstance(spec, dict) and isinstance(spec.get('etag'), str)
                and (spec.get('url'), spec.get('bytes'), spec.get('etag')) == expected
                and type(spec.get('bytes')) is int, 'Official archive identity changed')
    catalog = strict_json(sources['catalog.json'], 'catalog', 8_000_000)
    rights = strict_json(sources['rights.json'], 'rights', 8_000_000)['tracks']
    rows = plan.get('entries')
    require(isinstance(rows, list) and len(rows) == release.count
            and [row.get('id') for row in rows] == list(release.ordered_ids), 'Hydration ID count/order mismatch')
    entries = []
    for index, (row, track, right) in enumerate(zip(rows, catalog['tracks'], rights)):
        require(right.get('publicPlaybackDecision') == 'approved-with-attribution', 'Hydration requires approved public playback')
        require(row.get('audioPath') == track['audio'] and row.get('audioBytes') == track['audioBytes']
                and type(row.get('audioBytes')) is int and row.get('audioSha256') == track['audioSha256']
                and object_sha(row.get('evidence')) == object_sha(right['evidence']['asset']), 'Hydration recording/evidence mismatch')
        require(integer(row.get('method'), 12, 12) and integer(row.get('audioBytes'), 1, v1.MAX_AUDIO)
                and integer(row.get('compressedSize'), 1, v1.MAX_AUDIO)
                and integer(row.get('rangeBytes'), 513, v1.MAX_AUDIO + 512)
                and row['rangeBytes'] == row['compressedSize'] + 512
                and integer(row.get('crc32'), 0, 2**32 - 1), 'Invalid bounded bzip2 member')
        require(row.get('archive') in v1.ARCHIVES, 'Unapproved archive source')
        url, archive_bytes, etag = v1.ARCHIVES[row['archive']]
        require(integer(row.get('offset'), 0, archive_bytes - row['rangeBytes']), 'Hydration range is outside archive')
        number = track['id'].removeprefix('fma:')
        require(track['id'].startswith('fma:') and number.isdigit(), 'Unexpected recording identity')
        filename = f"{row['archive']}/{int(number)//1000:03}/{int(number):06}.mp3"
        require(row.get('filename') == filename and row['audioPath'] == f'audio/{int(number):06}.mp3', 'Hydration member path mismatch')
        # The evidence blob is checked against the pin in its verified rights row (== the plan's pin above).
        evidence = strict_json(release.evidence_for_row(index), 'rights/source evidence', 8_000_000)
        member = {'filename': filename, 'offset': row['offset'], 'compressedSize': row['compressedSize'],
                  'size': row['audioBytes'], 'crc32': row['crc32'], 'compression': 12}
        require(evidence.get('audioArchive') == url and object_sha(evidence.get('audioZipMember')) == object_sha(member),
                'Hydration member differs from approved source evidence')
        entries.append(v1.Entry(row['id'], row['audioPath'], row['audioBytes'], row['audioSha256'], url,
                                archive_bytes, etag, filename, row['offset'], row['compressedSize'], row['crc32'], row['rangeBytes']))
    require('fma:30702' not in release.ordered_ids and len({row.audio for row in entries}) == release.count
            and len({row.audio_sha for row in entries}) == release.count, 'Excluded or duplicate hydration audio')
    require(sum(row.range_bytes for row in entries) <= v1.MAX_TRANSFER, 'Hydration plan exceeds total transfer budget')
    return tuple(entries)


def load_plan_v2(root, package, release):
    """(entries, v1 source bytes) for a v2 release, or a ReleaseError; the plan keeps its package pin."""
    reason = plan_applies(release)
    require(reason is None, reason or '')
    pins = [row for row in package.get('files', []) if row.get('path') == v1.PLAN_NAME]
    require(len(pins) == 1 and pins[0].get('sha256') == v1.PLAN_SHA, 'Hydration plan lacks its reviewed package pin')
    plan = strict_json(verify_spec(root, pins[0], 1_500_000, v1.PLAN_NAME), 'hydration plan', 1_500_000)
    sources = source_bytes(release)
    return validate_plan_v2(plan, release, sources), sources


def delivery_rows(entries):
    rows = []
    for entry in entries:
        route = local_route(entry.id)
        require(route == '/audio/' + Path(entry.audio).name, 'Unexpected local audio route')
        rows.append({'id': entry.id, 'available': True, 'url': route, 'bytes': entry.audio_bytes, 'sha256': entry.audio_sha})
    return rows


def write_credits(directory, release, sources):
    """The v1 credit files: catalog.json, rights.json and every evidence file, each verified."""
    for name in ('catalog.json', 'rights.json'):
        (directory / name).write_bytes(sources[name])
    rights = json.loads(sources['rights.json'])['tracks']
    for index, row in enumerate(rights):
        pin = row['evidence']['asset']
        destination = directory / pin['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(release.evidence_for_row(index))


def hydrate_v2(root, release, entries, sources, *, source_cache=None, session_factory=None, progress=None):
    """hydrate() in hydrate_corpus_audio.py with v2 outputs. source_cache is an explicit local test and
    acceptance injection; the production CLI never accepts it."""
    root = Path(root)
    require(len(entries) == release.count, 'Hydration entry count mismatch')
    target, credits, delivery = (root / name for name in FINAL_NAMES)
    require(not any(path.exists() or path.is_symlink() for path in (target, credits, delivery)),
            'Do not overwrite an existing audio installation')
    evidence_bytes = release.manifest['source'].get('evidenceBytes', 0)
    required_bytes = sum(entry.audio_bytes for entry in entries) + evidence_bytes + 32_000_000 + 128_000_000
    require(shutil.disk_usage(root).free >= required_bytes, 'Insufficient disk for the complete hydrated corpus')
    started, budget = time.monotonic(), v1.Budget()
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
                audio = v1.fetch_member(entry, budget, local.session)
            else:
                audio = verify_spec(Path(source_cache), {'path': Path(entry.audio).name, 'bytes': entry.audio_bytes,
                                    'sha256': entry.audio_sha}, v1.MAX_AUDIO)
            budget.check()
            require(len(audio) == entry.audio_bytes and sha256(audio) == entry.audio_sha, 'Hydrated audio hash mismatch')
            with (audio_out / Path(entry.audio).name).open('xb') as output:
                output.write(audio)
            return entry.id
        completed = set()
        try:
            with ThreadPoolExecutor(max_workers=v1.WORKERS) as executor:
                futures = [executor.submit(acquire, entry) for entry in entries]
                try:
                    for future in as_completed(futures, timeout=v1.MAX_SECONDS):
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
        require(completed == set(release.ordered_ids) and len(list(audio_out.iterdir())) == len(entries),
                'The complete approved corpus is not ready')
        write_credits(credits_out, release, sources)
        record = {'schemaVersion': 2, 'kind': DELIVERY_KIND, 'catalogId': release.catalog_id,
                  'catalogSha256': release.catalog_sha256, 'releaseSha256': release.manifest_sha256,
                  'enabled': True, 'publicDeliveryVerified': True, 'mode': 'local', 'deliveryVerificationScope': SCOPE,
                  'tracks': delivery_rows(entries),
                  'hydration': {'planSha256': v1.PLAN_SHA, 'sourceReleaseSha256': v1.RELEASE_SHA,
                                'releaseSha256': release.manifest_sha256,
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
    return {'tracks': len(entries), 'audioBytes': sum(entry.audio_bytes for entry in entries),
            'elapsedSeconds': time.monotonic() - started, 'rangeBytesReservedIncludingRetries': budget.reserved,
            'networkAttempts': budget.attempts, 'localCacheTest': source_cache is not None,
            'runtimeEnabled': False, 'hostedDeliveryTested': False}


def verify_stage(stage, release, sources, *, deadline, local_cache_allowed=False):
    """Parent-side checks of a finished worker stage: result, inventory, every MP3 hash, credits, and
    the server's own acceptance of the delivery manifest. Returns the final delivery record."""
    stage = Path(stage)
    result = strict_json((stage / 'hydration-result.json').read_bytes(), 'hydration result', 16_384)
    record = strict_json((stage / FINAL_NAMES[2]).read_bytes(), 'hydrated delivery', RECORD_LIMIT)
    pins = release.audio_pins()
    require(result.get('tracks') == release.count and (local_cache_allowed or result.get('localCacheTest') is False)
            and integer(result.get('rangeBytesReservedIncludingRetries'), 0 if local_cache_allowed else 1, v1.MAX_TRANSFER)
            and integer(result.get('networkAttempts'), 0 if local_cache_allowed else 1, release.count * 2)
            and result.get('audioBytes') == sum(size for _, _, size, _ in pins)
            and record.get('schemaVersion') == 2 and record.get('kind') == DELIVERY_KIND
            and record.get('catalogId') == release.catalog_id and record.get('catalogSha256') == release.catalog_sha256
            and record.get('releaseSha256') == release.manifest_sha256 and record.get('mode') == 'local'
            and record.get('enabled') is True and record.get('publicDeliveryVerified') is True
            and [row.get('id') for row in record.get('tracks', [])] == list(release.ordered_ids),
            'Completed hydration worker identity mismatch')
    audio = stage / FINAL_NAMES[0]
    expected_names = {local_route(ident).rsplit('/', 1)[1] for _, ident, _, _ in pins}
    require({path.name for path in audio.iterdir()} == expected_names, 'Completed hydration worker audio inventory mismatch')
    require(time.monotonic() < deadline, 'Hydration deadline expired during final integrity checks')
    verified = delivery_record(release, mode='local', audio_dir=audio)  # hashes every file against the catalog pin
    require(verified['tracks'] == record['tracks'], 'Completed hydration delivery pin mismatch')
    credits = stage / FINAL_NAMES[1]
    for name in ('catalog.json', 'rights.json'):
        data = sources[name]
        verify_spec(credits, {'path': name, 'bytes': len(data), 'sha256': sha256(data)}, 8_000_000)
    for row in json.loads(sources['rights.json'])['tracks']:
        require(time.monotonic() < deadline, 'Hydration deadline expired during credit checks')
        verify_spec(credits, row['evidence']['asset'], 8_000_000)
    final = {**verified, 'deliveryVerificationScope': SCOPE, 'hydration': record.get('hydration')}
    (stage / FINAL_NAMES[2]).write_text(json.dumps(final, indent=2) + '\n')
    AudioDeliveryV2(stage / FINAL_NAMES[2], release, enabled=True, directory=audio)  # the server's own acceptance
    return final, result


def publish(stage, root, *, deadline):
    moved = []
    try:
        for name in FINAL_NAMES:
            require(time.monotonic() < deadline, 'Hydration deadline expired before publication')
            source, destination = Path(stage) / name, Path(root) / name
            source.rename(destination)
            moved.append((source, destination))
    except BaseException:
        for source, destination in reversed(moved):
            destination.rename(source)
        raise


def hydrate_supervised_v2(root, release, sources):
    root = Path(root)
    started = time.monotonic()
    deadline = started + v1.MAX_SECONDS
    require(not any((root / name).exists() or (root / name).is_symlink() for name in FINAL_NAMES),
            'Do not overwrite an existing audio installation')
    with tempfile.TemporaryDirectory(prefix='.hydration-build-', dir=root) as temporary:
        stage = Path(temporary)
        v1.run_bounded_worker([sys.executable, str(ROOT / 'scripts/hydrate_release_v2.py'), '--worker-dir', str(stage)],
                              timeout=max(.001, deadline - time.monotonic()))
        final, result = verify_stage(stage, release, sources, deadline=deadline)
        publish(stage, root, deadline=deadline)
    return {**result, 'releaseFormat': 2, 'deliveryManifest': FINAL_NAMES[2], 'available': len(final['tracks']),
            'elapsedSecondsIncludingSupervisor': time.monotonic() - started, 'absoluteDeadlineSupervised': True}


def run(package, *, verify_plan=False, worker_dir=None, legacy_url=''):
    """Entry from hydrate_corpus_audio.main() for a schemaVersion 2 selection."""
    selection = selected_release_v2(ROOT, package)
    require(selection is not None, 'No v2 release is selected')
    release = selection.release
    reason = plan_applies(release)
    if worker_dir is None and reason is not None:
        require(not verify_plan, reason)
        print(json.dumps({'tracks': 0, 'runtimeEnabled': False, 'releaseFormat': 2, 'reason': reason}))
        return 0
    entries, sources = load_plan_v2(ROOT, package, release)
    if worker_dir is not None:
        stage = Path(worker_dir)
        require(not verify_plan and stage.is_dir() and not stage.is_symlink()
                and stage.parent.resolve() == ROOT.resolve() and stage.name.startswith('.hydration-build-'),
                'Invalid private hydration worker directory')
        result = hydrate_v2(stage, release, entries, sources,
                            progress=lambda count, total: print(f'Approved hydration {count}/{total}', flush=True))
        (stage / 'hydration-result.json').write_text(json.dumps(result, indent=2) + '\n')
        return 0
    if verify_plan:
        print(json.dumps({'verified': True, 'releaseFormat': 2, 'releaseSha256': release.manifest_sha256,
                          'tracks': len(entries), 'rangeBytes': sum(entry.range_bytes for entry in entries),
                          'maximumTransferBytesIncludingRetries': v1.MAX_TRANSFER, 'maximumSeconds': v1.MAX_SECONDS,
                          'workers': v1.WORKERS}))
        return 0
    if legacy_url:
        print('The legacy 108-track audio archive does not apply to a v2 selection; ignored', flush=True)
    print(json.dumps(hydrate_supervised_v2(ROOT, release, sources)))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verify-plan', action='store_true')
    parser.add_argument('--worker-dir', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    package = json.loads((ROOT / 'package-manifest.json').read_bytes())
    return run(package, verify_plan=args.verify_plan, worker_dir=args.worker_dir)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
