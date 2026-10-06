#!/usr/bin/env python3
"""Reconstruct build_corpus_release.py inputs from a reviewed, committed release.

The historical acquisition and embedding pipelines are not in this repository; only their outputs
are. To rebuild a release (for example after a rights quarantine change) the builder's two input
directories are rebuilt from the reviewed release itself:

  <output>/ingestion/   inputs-ready.json, approved-catalog-tracks.json, approved-rights-rows.json,
                        approved-ids.json, evidence/ (byte copies), approved/audio -> --audio-dir
  <output>/embeddings/  ids.json, vectors.f32 (the release's exact bytes), embedding-provenance.json
                        (the release's per-recording receipts for the rows beyond its frozen prefix)

The source release is validated with its separately reviewed digest first, and inputs-ready.json
records it under "reconstructedFrom". The audio directory is only linked here; the builder hashes
every file against the catalog. Same source, same bytes: the output is deterministic.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ReleaseLimits, require, validate_release  # noqa: E402


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def reconstruct(release_dir, manifest_sha256, audio_dir, output, *, limits):
    release_dir, audio_dir, output = Path(release_dir), Path(audio_dir).resolve(), Path(output)
    require(not output.exists() and not output.is_symlink(), 'The output directory must not exist yet')
    release = validate_release(release_dir, expected_manifest_sha256=manifest_sha256, limits=limits)
    require(audio_dir.is_dir(), 'The audio directory does not exist')
    catalog = json.loads(release.assets['catalog'])
    rights = json.loads(release.assets['rights'])
    ids = list(release.ordered_ids)
    vectors = release.assets['vectors']
    provenance = json.loads((release_dir / 'embedding-provenance.json').read_bytes())
    rows = [row for row in provenance['tracks'] if 'audioSha256' in row]
    require(rows and all(row.get('preprocessorConfigSha256') == rows[0]['preprocessorConfigSha256'] for row in rows),
            'The release has no consistent per-recording embedding receipts')
    ingestion, embeddings = output / 'ingestion', output / 'embeddings'
    (ingestion / 'approved').mkdir(parents=True)
    embeddings.mkdir()
    os.symlink(audio_dir, ingestion / 'approved/audio', target_is_directory=True)
    for row in rights['tracks']:
        pin = row['evidence']['asset']
        target = ingestion / pin['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(release_dir / pin['path'], target)
        require(sha256(target.read_bytes()) == pin['sha256'], 'Evidence copy differs from its pin')
    hashes = {}
    for name, value in [('approved-catalog-tracks.json', catalog['tracks']), ('approved-rights-rows.json', rights['tracks']),
                        ('approved-ids.json', ids)]:
        data = encode(value)
        (ingestion / name).write_bytes(data)
        hashes[name] = sha256(data)
    (ingestion / 'inputs-ready.json').write_bytes(encode({
        'state': 'local-inputs-ready', 'tracks': len(ids), 'hashes': hashes,
        'reconstructedFrom': {'catalogId': release.catalog_id, 'manifestSha256': release.manifest_sha256,
                              'tool': 'scripts/reconstruct_release_inputs.py'}}))
    (embeddings / 'ids.json').write_bytes(encode(ids))
    (embeddings / 'vectors.f32').write_bytes(vectors)
    (embeddings / 'embedding-provenance.json').write_bytes(encode({
        'count': len(ids), 'newCount': len(rows), 'vectorsSha256': sha256(vectors),
        'audioModelSha256': provenance['sourceAudioModel'], 'preprocessorConfigSha256': rows[0]['preprocessorConfigSha256'],
        'modelRevision': provenance['modelRevision'], 'tracks': rows}))
    return {'tracks': len(ids), 'receiptRows': len(rows), 'evidenceFiles': len(rights['tracks']),
            'source': {'catalogId': release.catalog_id, 'manifestSha256': release.manifest_sha256},
            'inputHashes': hashes, 'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--release-dir', type=Path, required=True, help='the reviewed source release directory')
    parser.add_argument('--expected-manifest-sha256', required=True, help="the source release's reviewed digest")
    parser.add_argument('--audio-dir', type=Path, required=True, help='verified local MP3s named as in the catalog')
    parser.add_argument('--output-dir', type=Path, required=True, help='a new directory outside the repository')
    parser.add_argument('--max-tracks', type=int, default=2000)
    parser.add_argument('--core-byte-budget', type=int, default=16_000_000)
    parser.add_argument('--evidence-byte-budget', type=int, default=8_000_000)
    args = parser.parse_args()
    limits = ReleaseLimits(max_tracks=args.max_tracks, core_bytes=args.core_byte_budget, evidence_bytes=args.evidence_byte_budget)
    print(json.dumps(reconstruct(args.release_dir, args.expected_manifest_sha256, args.audio_dir, args.output_dir,
                                 limits=limits), indent=2))


if __name__ == '__main__':
    main()
