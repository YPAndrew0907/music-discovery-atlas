#!/usr/bin/env python3
"""Create one bounded local archive from approved IDs only; never upload it."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import require, sha256, validate_release
from build_corpus_release import encode
from install_corpus_audio_release import inspect_archive


def build(directory, expected_sha, approved_audio, credits, archive):
    release = validate_release(directory, expected_manifest_sha256=expected_sha)
    catalog = json.loads(release.assets['catalog'])
    rights = json.loads(release.assets['rights'])
    approved_audio, directory, archive = map(Path, (approved_audio, directory, archive))
    expected_names = {Path(track['audio']).name for track in catalog['tracks']}
    actual = list(approved_audio.iterdir())
    require(len(expected_names) == release.count and len(actual) == release.count
            and all(path.is_file() and not path.is_symlink() for path in actual)
            and {path.name for path in actual} == expected_names, 'Audio source must contain exactly the approved selection')
    require(not archive.exists(), 'Do not overwrite an existing release archive')
    members = {}
    for track in catalog['tracks']:
        path = approved_audio / Path(track['audio']).name
        with path.open('rb') as stream:
            actual_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        require(path.stat().st_size == track['audioBytes'] and actual_hash == track['audioSha256'], 'Approved audio changed')
        members[track['audio']] = (path, track['audioBytes'], actual_hash)
    for name, data in [('credits/catalog.json', release.assets['catalog']), ('credits/rights.json', release.assets['rights']),
                       ('credits/track-attribution.html', Path(credits).read_bytes())]:
        members[name] = (data, len(data), sha256(data))
    for row in rights['tracks']:
        evidence = row['evidence']['asset']
        members['credits/' + evidence['path']] = (directory / evidence['path'], evidence['bytes'], evidence['sha256'])
    pack = {'schemaVersion': 1, 'kind': 'music-corpus-audio-pack', 'catalogId': release.catalog_id,
            'catalogSha256': sha256(release.assets['catalog']), 'corpusReleaseSha256': release.manifest_sha256,
            'count': release.count, 'files': [{'path': name, 'bytes': length, 'sha256': digest}
                                            for name, (_, length, digest) in sorted(members.items())]}
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED, allowZip64=False) as output:
        for name, (source, _, _) in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 4, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            with output.open(info, 'w') as destination:
                if isinstance(source, bytes):
                    destination.write(source)
                else:
                    with source.open('rb') as input_file:
                        while chunk := input_file.read(1024 * 1024):
                            destination.write(chunk)
        info = zipfile.ZipInfo('audio-pack.json', date_time=(2026, 10, 4, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        output.writestr(info, encode(pack))
    require(archive.stat().st_size <= 650_000_000, 'Archive exceeded approved byte ceiling')
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    pin = {'bytes': archive.stat().st_size, 'sha256': checksum}
    # Local verification needs no guessed public asset URL.
    inspect_archive(archive, SimpleNamespace(release=release, audio_archive=pin))
    return {'archive': archive.name, 'count': release.count, 'audioBytes': sum(t['audioBytes'] for t in catalog['tracks']),
            **pin, 'corpusReleaseSha256': release.manifest_sha256, 'uploaded': False, 'deployed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--approved-audio-dir', type=Path, required=True)
    parser.add_argument('--credits', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.release_dir, args.expected_manifest_sha256, args.approved_audio_dir,
                           args.credits, args.archive), indent=2))


if __name__ == '__main__':
    main()
