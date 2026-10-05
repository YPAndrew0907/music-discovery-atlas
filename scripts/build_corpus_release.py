#!/usr/bin/env python3
"""Build a separate real2000 local candidate without altering its frozen1000 parent."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ASSETS, PAIR_ID, ReleaseLimits, object_sha, sha256, require, validate_release


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def write_json(path, value):
    Path(path).write_bytes(encode(value))


def spec(path, name=None):
    data = Path(path).read_bytes()
    return {'path': name or Path(path).name, 'bytes': len(data), 'sha256': sha256(data)}


def build(ingestion, embeddings, output):
    count = 2000
    parent = ROOT / 'corpus-releases/fma1000'
    parent_ids = json.loads((parent / 'ids.json').read_bytes())
    parent_vectors = (parent / 'vectors.f32').read_bytes()
    parent_catalog = json.loads((parent / 'catalog.json').read_bytes())
    parent_rights = json.loads((parent / 'rights.json').read_bytes())
    ingestion, embeddings, output = map(Path, (ingestion, embeddings, output))
    require(output.resolve() == (ROOT / 'corpus-releases/fma2000').resolve(),
            'The2000 builder only writes its separate fma2000 release directory')
    require((ingestion / 'inputs-ready.json').is_file(), 'Final2000 input readiness receipt is required')
    ready = json.loads((ingestion / 'inputs-ready.json').read_bytes())
    require(ready.get('state') == 'local-inputs-ready' and ready.get('tracks') == count,
            'Input readiness count or state changed')
    for name in ['approved-catalog-tracks.json', 'approved-rights-rows.json', 'approved-ids.json']:
        require(sha256((ingestion / name).read_bytes()) == ready.get('hashes', {}).get(name),
                'Final input differs from the acquisition completion receipt')
    tracks = json.loads((ingestion / 'approved-catalog-tracks.json').read_bytes())
    rights_rows = json.loads((ingestion / 'approved-rights-rows.json').read_bytes())
    ids = [track['id'] for track in tracks]
    require(ids == json.loads((ingestion / 'approved-ids.json').read_bytes()), 'Selected ID order differs from acquisition receipt')
    require(len(ids) == count and len(set(ids)) == count and 'fma:30702' not in ids, 'Need exactly2000 approved distinct rows, excluding quarantined30702')
    require(len({track['audioSha256'] for track in tracks}) == count, 'Duplicate selected audio bytes')
    require([row['id'] for row in rights_rows] == ids and all(row['decision'] == 'approved'
            and row.get('publicPlaybackDecision') == 'approved-with-attribution' for row in rights_rows), 'Rights selection incomplete')
    require(ids[:1000] == parent_ids and tracks[:1000] == parent_catalog['tracks']
            and rights_rows[:1000] == parent_rights['tracks'], 'Frozen1000 catalog/rights prefix changed')
    source_ids = json.loads((embeddings / 'ids.json').read_bytes())
    source_vectors = (embeddings / 'vectors.f32').read_bytes()
    provenance = json.loads((embeddings / 'embedding-provenance.json').read_bytes())
    require(provenance['newCount'] >= 1000 and provenance['count'] == len(source_ids)
            and len(source_vectors) == len(source_ids) * 2048 and sha256(source_vectors) == provenance['vectorsSha256'],
            'Real embedding output is incomplete or inconsistent')
    legacy = ROOT / 'music-search-studio/data'
    legacy_ids = json.loads((legacy / 'ids.json').read_bytes())
    legacy_vectors = (legacy / 'vectors.f32').read_bytes()
    require(source_ids[:1000] == parent_ids and source_vectors[:len(parent_vectors)] == parent_vectors,
            'Frozen1000 embedding prefix was not preserved')
    pair = json.loads((ROOT / 'model/model-space-q8.json').read_bytes())['identity']
    require(provenance['audioModelSha256'] == pair['audioModelSha256']
            and provenance['preprocessorConfigSha256'] == pair['preprocessorConfigSha256']
            and provenance['modelRevision'] == pair['revision'], 'Embedding model pairing changed')
    lookup = {ident: row for row, ident in enumerate(source_ids)}
    require(len(lookup) == len(source_ids) and all(ident in lookup for ident in ids), 'Embedding ID coverage mismatch')
    vectors = b''.join(source_vectors[lookup[ident]*2048:(lookup[ident]+1)*2048] for ident in ids)
    source_rows = {row['id']: row for row in provenance['tracks']}
    require(all(ident in source_rows for ident in ids[1000:]), 'Every additional recording needs real embedding provenance')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'evidence').mkdir(exist_ok=True)
    artists = []
    for track, rights in zip(tracks, rights_rows):
        audio = ingestion / 'approved/audio' / Path(track['audio']).name
        with audio.open('rb') as stream:
            audio_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        require(audio.stat().st_size == track['audioBytes'] and audio_hash == track['audioSha256'],
                'Selected local audio differs from catalog')
        evidence = rights['evidence']['asset']
        evidence_path = ingestion / evidence['path']
        raw_evidence = evidence_path.read_bytes()
        require(len(raw_evidence) == evidence['bytes'] and sha256(raw_evidence) == evidence['sha256'], 'Evidence changed after review')
        target = output / evidence['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw_evidence)
        source = json.loads(raw_evidence)
        raw_track = source['rawTrack']
        notices = dict(track['suppliedNotices'])
        for key in ['track_copyright_c', 'track_copyright_p', 'track_composer', 'track_lyricist', 'track_publisher', 'track_information']:
            if raw_track.get(key):
                notices[key] = raw_track[key]
        if source.get('albumInformation'):
            notices['albumInformation'] = source['albumInformation']
        track['suppliedNotices'] = notices
        artist_id = str(raw_track.get('artist_id', '')).strip()
        require(artist_id.isdigit(), 'Missing source artist identity')
        artists.append({'trackId': track['id'], 'artistId': 'fma-artist:' + artist_id})
        if track['id'] in source_rows:
            row = source_rows[track['id']]
            vector = vectors[ids.index(track['id'])*2048:(ids.index(track['id'])+1)*2048]
            require(row['audioSha256'] == track['audioSha256'] and row['vectorSha256'] == sha256(vector), 'Per-track embedding provenance mismatch')
    source_identity = {'orderedIds': ids, 'vectorsSha256': sha256(vectors),
        'selectedInputSha256': sha256((ingestion / 'approved-catalog-tracks.json').read_bytes()),
        'legacyCatalogSha256': sha256((legacy / 'catalog.json').read_bytes()),
        'parent1000CatalogSha256': sha256((parent / 'catalog.json').read_bytes()),
        'parent1000VectorsSha256': sha256(parent_vectors),
        'parent1000ReleaseSha256': sha256((parent / 'release.json').read_bytes()),
        'embeddingProvenanceSha256': sha256((embeddings / 'embedding-provenance.json').read_bytes())}
    old_catalog = json.loads((legacy / 'catalog.json').read_bytes())
    catalog = {'schemaVersion': 1, 'id': 'fma2000:' + object_sha(source_identity), 'dimensions': 512,
        'sourceIdentity': source_identity, 'tracks': tracks,
        'coverage': '2000 screened FMA excerpts:the complete frozen1000 selection plus1000 additional recordings. Conflicted legacy row30702 remains excluded. This is a local candidate and a biased open-music sample, not mainstream coverage.',
        'metadataAttribution': old_catalog['metadataAttribution'], 'sourceAudioPreprocessing': old_catalog['sourceAudioPreprocessing']}
    write_json(output / 'catalog.json', catalog)
    write_json(output / 'ids.json', ids)
    (output / 'vectors.f32').write_bytes(vectors)
    manifest = json.loads((legacy / 'manifest.json').read_bytes())
    identity = manifest['graphIdentity']
    identity.update(count=count, vectorsSha256=sha256(vectors), orderedIdsSha256=object_sha(ids))
    graph_id = 'experimental-clap-audio-graph:' + object_sha(identity)
    manifest.update(graphId=graph_id, count=count, catalogId=catalog['id'], vectorsSha256=sha256(vectors),
                    orderedIdsSha256=object_sha(ids), orderedIds=ids, experiment='local-reviewed-fma2000-native-api',
                    scope='2000 screened CC BY/CC0 FMA metadata records and real audio-derived CLAP vectors. Playback is separately verified.')
    subprocess.run(['node', str(ROOT / 'scripts/build_corpus_graph.mjs'), str(output), graph_id,
                    str(ROOT / 'web/search-studio/data/examples.json'), str(count)], check=True)
    manifest['indexSha256'] = sha256((output / 'index.json').read_bytes())
    manifest['assets'] = {name: spec(output / name) for name in ['catalog.json', 'vectors.f32', 'ids.json', 'index.json']}
    write_json(output / 'manifest.json', manifest)
    rights = {'schemaVersion': 1, 'kind': 'music-corpus-rights', 'catalogId': catalog['id'],
              'catalogSha256': sha256((output / 'catalog.json').read_bytes()), 'scope': 'searchable-audio-embeddings',
              'tracks': rights_rows}
    write_json(output / 'rights.json', rights)
    release = {'schemaVersion': 1, 'kind': 'music-corpus-release', 'catalogId': catalog['id'], 'count': count,
               'dimensions': 512, 'pairId': PAIR_ID, 'assets': {key: spec(output / name) for key, name in ASSETS.items()}}
    write_json(output / 'release.json', release)
    manifest_sha = sha256((output / 'release.json').read_bytes())
    verified = validate_release(output, expected_manifest_sha256=manifest_sha, limits=ReleaseLimits(max_tracks=count))
    receipt = {'schemaVersion': 1, 'count': count, 'preservedParentCount': 1000, 'preservedLegacyCount': 107, 'newCount': 1000,
               'excludedLegacyIds': ['fma:30702'], 'inputEmbeddingProvenanceSha256': source_identity['embeddingProvenanceSha256'],
               'sourceAudioModel': provenance['audioModelSha256'], 'modelRevision': provenance['modelRevision'],
               'tracks': []}
    for i, ident in enumerate(ids):
        row = deepcopy(source_rows.get(ident, {'id': ident, 'source': 'unchanged frozen1000 vector',
            'parentVectorsSha256': sha256(parent_vectors)}))
        row.pop('audioPath', None)
        row['vectorSha256'] = sha256(vectors[i*2048:(i+1)*2048])
        receipt['tracks'].append(row)
    write_json(output / 'embedding-provenance.json', receipt)
    write_json(output / 'artist-records.json', {'schemaVersion': 1, 'catalogId': catalog['id'],
        'sourceSha256': source_identity['selectedInputSha256'], 'meaning': 'Stable artist IDs from official FMA raw metadata; not name-only inference',
        'metadataLicense': 'CC-BY-4.0', 'attribution': old_catalog['metadataAttribution'], 'rows': artists})
    summary = {**verified.summary(), 'licenses': dict(Counter(track['license'] for track in tracks)),
               'artists': len({row['artistId'] for row in artists}), 'genres': dict(Counter(track['genre'] for track in tracks)),
               'audioBytes': sum(track['audioBytes'] for track in tracks), 'releaseManifestSha256': manifest_sha}
    write_json(output / 'build-receipt.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ingestion-dir', type=Path, required=True)
    parser.add_argument('--embeddings-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.ingestion_dir, args.embeddings_dir, args.output_dir), indent=2))


if __name__ == '__main__':
    main()
