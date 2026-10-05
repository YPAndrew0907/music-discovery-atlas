"""Bounded build-time installer for an explicitly selected corpus audio pack.

No runtime acquisition. Absent/disabled selection delegates to the unchanged
legacy108 installer only when its existing build argument is supplied.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from active_corpus import selected_corpus
from corpus_release import ReleaseError, integer, require, sha256, strict_json, valid_sha
from install_audio_release import ReleaseRedirects, download_deadline, digest, release_url


def download_selected(spec, target):
    opener = urllib.request.build_opener(ReleaseRedirects())
    request = urllib.request.Request(release_url(spec['url']), headers={'User-Agent': 'music-atlas-reviewed-build/2'})
    size = 0
    with download_deadline(600), opener.open(request, timeout=30) as response, Path(target).open('xb') as output:
        require(response.status == 200, 'Unexpected corpus audio response')
        length = response.headers.get('Content-Length')
        require(length is None or length == str(spec['bytes']), 'Corpus audio response length mismatch')
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            require(size <= spec['bytes'], 'Corpus audio archive exceeds approved byte limit')
            output.write(chunk)
    require(size == spec['bytes'] and digest(target) == spec['sha256'], 'Corpus audio archive digest mismatch')


def inspect_archive(archive, selected):
    spec, release = selected.audio_archive, selected.release
    require(spec is not None, 'No corpus audio archive is approved')
    archive = Path(archive)
    require(not archive.is_symlink() and archive.is_file() and archive.stat().st_size == spec['bytes']
            and digest(archive) == spec['sha256'], 'Unreviewed corpus audio archive')
    catalog = strict_json(release.assets['catalog'], 'catalog', 8_000_000)
    expected_audio = {}
    for track in catalog['tracks']:
        name = track.get('audio')
        require(isinstance(name, str) and name.startswith('audio/') and name.count('/') == 1
                and name.endswith('.mp3') and not Path(name).name.startswith('.') and '\\' not in name,
                'Invalid approved audio member name')
        require(name not in expected_audio, 'Duplicate corpus audio filename')
        expected_audio[name] = track
    expected_credits = {'credits/catalog.json': release.assets['catalog'], 'credits/rights.json': release.assets['rights']}
    rights = strict_json(release.assets['rights'], 'rights', 8_000_000)
    require(all(row.get('publicPlaybackDecision') == 'approved-with-attribution' for row in rights['tracks']),
            'Public playback requires an explicit per-track rights decision')
    evidence_specs = {}
    for row in rights['tracks']:
        evidence = row['evidence']['asset']
        evidence_specs['credits/' + evidence['path']] = evidence
    fixed = {'audio-pack.json', 'credits/track-attribution.html'} | set(expected_credits) | set(evidence_specs)
    expected = set(expected_audio) | fixed
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        require(len(entries) == len(expected) and {row.filename for row in entries} == expected,
                'Unexpected corpus archive inventory')
        for info in entries:
            mode = info.external_attr >> 16
            require(not info.is_dir() and not stat.S_ISLNK(mode) and not info.flag_bits & 1
                    and info.compress_type == zipfile.ZIP_STORED, 'Unexpected corpus archive entry type')
            require(integer(info.file_size, 1, 20_000_000), 'Corpus archive member size exceeds limit')
        require(source.getinfo('audio-pack.json').file_size <= 1_000_000, 'Audio pack manifest too large')
        pack = strict_json(source.read('audio-pack.json'), 'audio pack manifest', 1_000_000)
        require(isinstance(pack, dict) and integer(pack.get('schemaVersion'), 1, 1)
                and pack.get('kind') == 'music-corpus-audio-pack'
                and pack.get('catalogId') == release.catalog_id
                and pack.get('catalogSha256') == sha256(release.assets['catalog'])
                and pack.get('corpusReleaseSha256') == release.manifest_sha256
                and integer(pack.get('count'), release.count, release.count), 'Corpus audio identity mismatch')
        require(isinstance(pack.get('files'), list), 'Missing corpus audio inventory')
        pins = {}
        for pin in pack['files']:
            require(isinstance(pin, dict) and set(pin) == {'path', 'bytes', 'sha256'}
                    and isinstance(pin['path'], str) and pin['path'] not in pins
                    and integer(pin['bytes'], 1, 20_000_000) and valid_sha(pin['sha256']), 'Invalid corpus audio member pin')
            pins[pin['path']] = pin
        require(set(pins) == expected - {'audio-pack.json'}, 'Corpus audio pin inventory mismatch')
        for name, pin in pins.items():
            info = source.getinfo(name)
            require(info.file_size == pin['bytes'], 'Corpus audio member length mismatch')
            if name in expected_audio:
                track = expected_audio[name]
                require(pin['bytes'] == track['audioBytes'] and pin['sha256'] == track['audioSha256'],
                        'Corpus audio pin differs from catalog')
            if name in evidence_specs:
                evidence = evidence_specs[name]
                require(pin['bytes'] == evidence['bytes'] and pin['sha256'] == evidence['sha256'], 'Rights evidence pin mismatch')
            with source.open(name) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(actual == pin['sha256'], 'Corpus audio member content mismatch')
        for name, expected_bytes in expected_credits.items():
            require(source.read(name) == expected_bytes, 'Corpus audio credit document mismatch')
        pins['audio-pack.json'] = {'path': 'audio-pack.json', 'bytes': source.getinfo('audio-pack.json').file_size,
                                   'sha256': sha256(source.read('audio-pack.json'))}
    return catalog, pins


def install(archive, root, selected):
    root = Path(root)
    catalog, pins = inspect_archive(archive, selected)
    target, credits, delivery = root / 'audio-preview', root / 'audio-release-credits', root / 'audio-delivery.verified.json'
    require(not any(path.exists() or path.is_symlink() for path in (target, credits, delivery)),
            'Existing corpus audio installation; do not overwrite')
    with tempfile.TemporaryDirectory(prefix='.corpus-audio-', dir=root) as temporary:
        temporary = Path(temporary)
        audio_out, credits_out = temporary / 'audio', temporary / 'credits'
        audio_out.mkdir()
        credits_out.mkdir()
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            require(len(entries) == len(pins) and {entry.filename for entry in entries} == set(pins),
                    'Corpus archive changed after verification')
            for name in sorted(pins):
                pin = pins[name]
                info = source.getinfo(name)
                require(not info.is_dir() and not stat.S_ISLNK(info.external_attr >> 16)
                        and not info.flag_bits & 1 and info.compress_type == zipfile.ZIP_STORED
                        and info.file_size == pin['bytes'], 'Corpus member changed after verification')
                if name.startswith('audio/'):
                    destination = audio_out / Path(name).name
                elif name.startswith('credits/'):
                    destination = credits_out / name.removeprefix('credits/')
                else:
                    destination = credits_out / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                copied, checksum = 0, hashlib.sha256()
                with source.open(name) as input_file, destination.open('xb') as output:
                    while chunk := input_file.read(min(1024 * 1024, pin['bytes'] - copied + 1)):
                        copied += len(chunk)
                        require(copied <= pin['bytes'], 'Extracted corpus member exceeded its approved length')
                        checksum.update(chunk)
                        output.write(chunk)
                require(copied == pin['bytes'] and checksum.hexdigest() == pin['sha256'],
                        'Extracted corpus member integrity mismatch')
        rows = []
        for track in catalog['tracks']:
            path = audio_out / Path(track['audio']).name
            require(path.stat().st_size == track['audioBytes'] and digest(path) == track['audioSha256'],
                    'Extracted corpus audio mismatch')
            rows.append({'id': track['id'], 'available': True, 'url': '/audio/' + path.name,
                         'bytes': track['audioBytes'], 'sha256': track['audioSha256']})
        record = {'schemaVersion': 1, 'catalogId': catalog['id'], 'catalogSha256': sha256(selected.release.assets['catalog']),
                  'enabled': True, 'publicDeliveryVerified': True, 'tracks': rows,
                  'deliveryVerificationScope': 'Pinned local image files; hosted browser delivery remains a separate acceptance check',
                  'releaseArchive': dict(selected.audio_archive)}
        (temporary / 'delivery.json').write_text(json.dumps(record, indent=2) + '\n')
        audio_out.rename(target)
        credits_out.rename(credits)
        (temporary / 'delivery.json').rename(delivery)
    return {'tracks': len(rows), 'audioBytes': sum(row['bytes'] for row in rows),
            'runtimeEnabled': False, 'hostedDeliveryTested': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-url', default='')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    package = json.loads((ROOT / 'package-manifest.json').read_text())
    selected = selected_corpus(ROOT, package)
    if selected is None:
        require(args.archive is None and not args.verify_only, 'Local corpus archive requires explicit selected corpus')
        if args.legacy_url:
            import install_audio_release as legacy
            with tempfile.TemporaryDirectory(prefix='legacy-audio-') as temp:
                archive = Path(temp) / 'audio.zip'
                legacy.download(args.legacy_url, archive)
                print(json.dumps(legacy.install(archive, ROOT, legacy.release_url(args.legacy_url))))
        return 0
    if selected.audio_archive is None:
        require(args.archive is None and not args.verify_only, 'No audio archive approved for selected corpus')
        print(json.dumps({'tracks': 0, 'runtimeEnabled': False, 'reason': 'No approved audio archive'}))
        return 0
    if args.archive:
        if args.verify_only:
            inspect_archive(args.archive, selected)
            print(json.dumps({'verified': True, 'tracks': selected.release.count, 'runtimeEnabled': False}))
        else:
            print(json.dumps(install(args.archive, ROOT, selected)))
        return 0
    require(not args.verify_only, 'Verify-only requires a local archive')
    with tempfile.TemporaryDirectory(prefix='corpus-audio-release-') as temporary:
        archive = Path(temporary) / 'audio.zip'
        download_selected(selected.audio_archive, archive)
        print(json.dumps(install(archive, ROOT, selected)))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReleaseError, ValueError, OSError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error)) from None
