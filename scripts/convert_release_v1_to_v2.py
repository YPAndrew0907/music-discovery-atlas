#!/usr/bin/env python3
"""Convert a reviewed v1 corpus release into release format v2 (docs/PLATFORM_V2.md).

The source is validated first with the unchanged v1 validator at explicit budgets. The output
directory receives catalog.sqlite, evidence.sqlite, vectors.f32 (identical bytes), graph.bin
(CSR int32), layout.f32, graph-manifest.json and examples.json (identical bytes), then
release.json, whose SHA-256 is the v2 release identity. Before publishing, the converter
proves the conversion: the database rebuilds the v1 catalog, rights and artist objects
exactly, every evidence blob is byte-identical, the CSR decodes to the v1 index links, the
layout is unchanged, the server loader accepts the result, and a sampled oracle finds the
exact ranking and the full search trace identical to the v1 reference implementation.

Nothing is downloaded and the source directory is never modified.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import PAIR_ID, ReleaseError, ReleaseLimits, object_sha, require, sha256, strict_json, validate_release  # noqa: E402
from hnsw_trace import HNSW  # noqa: E402
from release_v2 import (ASSET_PATHS, CATALOG_KIND, CATALOG_SCHEMA, EVIDENCE_KIND, EVIDENCE_SCHEMA, FORMAT,  # noqa: E402
                        GRAPH_ENCODING, GRAPH_HEADER, GRAPH_MAGIC, LimitsV2, fold, load_release_v2,
                        reconstruct_sources, validate_rows)
from search_v2 import GraphV2  # noqa: E402

COMPACT = {'ensure_ascii': False, 'separators': (',', ':')}


def compact(value):
    return json.dumps(value, **COMPACT)


def header_with_placeholder(value, key):
    require(isinstance(value, dict) and key in value, 'Source object lacks ' + key)
    return compact({name: (None if name == key else item) for name, item in value.items()})


def read_bound(path, label, max_bytes, expected=None):
    data = Path(path).read_bytes()
    require(len(data) <= max_bytes, label + ' exceeds its budget')
    if expected is not None:
        require(sha256(data) == expected, label + ' differs from its pin')
    return data


def build_catalog_db(path, *, catalog, catalog_sha, rights, artists, ids, source_digests):
    tracks, rights_rows, artist_rows = catalog['tracks'], rights['tracks'], artists['rows']
    require([row['trackId'] for row in artist_rows] == ids, 'Artist records do not follow catalog order')
    connection = sqlite3.connect(path)
    try:
        connection.execute('PRAGMA page_size = 4096')
        connection.execute('PRAGMA journal_mode = OFF')
        connection.execute('PRAGMA synchronous = OFF')
        connection.executescript(CATALOG_SCHEMA)
        meta = {'schemaVersion': '2', 'kind': CATALOG_KIND, 'catalogId': catalog['id'], 'count': str(len(ids)),
                'orderedIdsSha256': object_sha(ids), 'catalogHeader': header_with_placeholder(catalog, 'tracks'),
                'rightsHeader': header_with_placeholder(rights, 'tracks'),
                'artistHeader': header_with_placeholder(artists, 'rows'), 'sourceCatalogSha256': catalog_sha,
                **{'source' + key[0].upper() + key[1:]: value for key, value in sorted(source_digests.items())}}
        connection.executemany('INSERT INTO meta (key, value) VALUES (?, ?)', sorted(meta.items()))
        for row, (track, right, artist) in enumerate(zip(tracks, rights_rows, artist_rows)):
            album = track.get('album')
            connection.execute(
                'INSERT INTO tracks (row, id, title, artist, album, genre, license, artist_id, audio_bytes, audio_sha256, '
                'fold_title, fold_title_artist, fold_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (row, track['id'], track['title'], track['artist'], album, track.get('genre'), track['license'],
                 artist['artistId'], track['audioBytes'], track['audioSha256'], fold(track['title']),
                 fold(f"{track['title']} {track['artist']}"),
                 fold(f"{track['title']} {track['artist']} {'' if album is None else album}")))
            connection.execute('INSERT INTO records (row, track_json, rights_json) VALUES (?, ?, ?)',
                               (row, compact(track), compact(right)))
        connection.commit()
        connection.execute('VACUUM')
    finally:
        connection.close()


def build_evidence_db(path, *, source, rights):
    pins = {}
    for row in rights['tracks']:
        spec = row['evidence']['asset']
        require(pins.setdefault(spec['path'], spec) == spec, 'Conflicting evidence pins')
    connection = sqlite3.connect(path)
    total = 0
    try:
        connection.execute('PRAGMA page_size = 4096')
        connection.execute('PRAGMA journal_mode = OFF')
        connection.execute('PRAGMA synchronous = OFF')
        connection.executescript(EVIDENCE_SCHEMA)
        for relative in sorted(pins):
            spec = pins[relative]
            data = read_bound(Path(source) / relative, relative, spec['bytes'], spec['sha256'])
            require(len(data) == spec['bytes'], 'Evidence length differs from its pin: ' + relative)
            connection.execute('INSERT INTO evidence (path, bytes, sha256, data) VALUES (?, ?, ?, ?)',
                               (relative, len(data), spec['sha256'], data))
            total += len(data)
        connection.executemany('INSERT INTO meta (key, value) VALUES (?, ?)', sorted({
            'schemaVersion': '2', 'kind': EVIDENCE_KIND, 'files': str(len(pins)), 'bytes': str(total)}.items()))
        connection.commit()
        connection.execute('VACUUM')
    finally:
        connection.close()
    return len(pins), total


def encode_csr(index):
    node_layers, layer_offsets, neighbors = [0], [0], []
    for layers in index['links']:
        for level in layers:
            neighbors.extend(level)
            layer_offsets.append(len(neighbors))
        node_layers.append(len(layer_offsets) - 1)
    header = GRAPH_HEADER.pack(GRAPH_MAGIC, 1, index['count'], index['dimensions'], index['M'], index['efConstruction'],
                               index['seed'], index['entry'], index['maxLevel'], 0, len(node_layers), len(layer_offsets),
                               len(neighbors))
    data = (header + np.asarray(node_layers, dtype='<i4').tobytes() + np.asarray(layer_offsets, dtype='<i4').tobytes()
            + np.asarray(neighbors, dtype='<i4').tobytes())
    description = {'algorithm': index['algorithm'], 'encoding': GRAPH_ENCODING, 'M': index['M'],
                   'efConstruction': index['efConstruction'], 'seed': index['seed'], 'entry': index['entry'],
                   'maxLevel': index['maxLevel'], 'layers': len(layer_offsets) - 1, 'links': len(neighbors)}
    return data, description


def decode_links(release):
    nodes, offsets, neighbors = (release.node_layers.tolist(), release.layer_offsets.tolist(), release.neighbors.tolist())
    return [[neighbors[offsets[layer]:offsets[layer + 1]] for layer in range(nodes[row], nodes[row + 1])]
            for row in range(release.count)]


def layout_block(layout, positions):
    xs, ys = positions[:, 0], positions[:, 1]
    keep = ('method', 'settings', 'fitInputs', 'displayTransform', 'metrics', 'klDivergence', 'iterations',
            'environment', 'limitations', 'description')
    return {**{key: layout[key] for key in keep if key in layout}, 'count': len(positions),
            'bounds': [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]}


def oracle(v1, v2, examples, *, samples, seed):
    """Exact ranking and full traces, v1 reference versus v2, on recorded and sampled queries."""
    rng = np.random.default_rng(seed)
    count = v2.size
    queries = [('example:' + item['id'], np.asarray(item['queryVector'], dtype=np.float32), None)
               for item in examples.get('examples', [])]
    for row in sorted(rng.choice(count, size=min(samples, count), replace=False).tolist()):
        queries.append((f'row:{row}', np.array(v2.matrix[row], dtype=np.float32), row))
    for n in range(max(1, samples // 4)):
        vector = rng.standard_normal(512)
        queries.append((f'random:{n}', (vector / np.linalg.norm(vector)).astype(np.float32), None))
    mismatches, checked = [], 0
    for name, query, exclude in queries:
        k = 16
        exact_v1 = v1.exact_search(query, k=k, exclude_id=exclude)
        exact_v2 = v2.exact_search(query, k=k, exclude_id=exclude)
        traced_v1 = v1.search(query, k=k + (exclude is not None), ef=32, trace=True, trace_limit=2048)
        traced_v2 = v2.search(query, k=k + (exclude is not None), ef=32, trace=True, trace_limit=2048)
        checked += 1
        if exact_v1 != exact_v2:
            mismatches.append({'query': name, 'kind': 'exact'})
        if traced_v1 != traced_v2:
            mismatches.append({'query': name, 'kind': 'trace'})
    return {'queries': checked, 'recordedExamples': len(examples.get('examples', [])), 'sampledRows': min(samples, count),
            'randomUnitVectors': max(1, samples // 4), 'k': 16, 'ef': 32, 'traceLimit': 2048, 'seed': seed,
            'exactMismatches': sum(m['kind'] == 'exact' for m in mismatches),
            'traceMismatches': sum(m['kind'] == 'trace' for m in mismatches), 'mismatches': mismatches[:20],
            'v2ExactPath': dict(v2.stats)}


def convert(source, expected_manifest_sha256, output, *, v1_limits, v2_limits=None, samples=48, seed=20261006):
    started = time.perf_counter()
    source, output = Path(source).resolve(), Path(output)
    require(not output.exists() and not output.is_symlink(), 'Refusing to overwrite an existing output directory')
    v2_limits = v2_limits or LimitsV2()
    verified = validate_release(source, expected_manifest_sha256=expected_manifest_sha256, limits=v1_limits)
    raw = verified.assets
    catalog = strict_json(raw['catalog'], 'catalog', v1_limits.json_bytes)
    rights = strict_json(raw['rights'], 'rights', v1_limits.json_bytes)
    index = strict_json(raw['index'], 'index', v1_limits.json_bytes)
    ids = list(verified.ordered_ids)
    artists_bytes = read_bound(source / 'artist-records.json', 'artist records', 64_000_000)
    artists = strict_json(artists_bytes, 'artist records', 64_000_000)
    require(isinstance(artists, dict) and artists.get('catalogId') == verified.catalog_id
            and isinstance(artists.get('rows'), list), 'Artist records do not bind to this catalog')
    layout_bytes = read_bound(source / 'layout.json', 'layout', 64_000_000)
    layout = strict_json(layout_bytes, 'layout', 64_000_000)
    require(layout.get('graphId') == verified.graph_id and layout.get('count') == verified.count
            and layout.get('vectorsSha256') == sha256(raw['vectors']) and layout.get('orderedIdsSha256') == object_sha(ids),
            'Layout does not bind to this release')
    positions = np.asarray(layout['positions'], dtype=np.float64)
    require(positions.shape == (verified.count, 2) and bool(np.isfinite(positions).all())
            and bool(np.all(positions.astype(np.float32).astype(np.float64) == positions)),
            'Layout positions are not exact float32 values')
    examples_bytes = read_bound(source / 'examples.json', 'examples', 64_000_000)
    examples = strict_json(examples_bytes, 'examples', 64_000_000)
    require(examples.get('graphId') == verified.graph_id, 'Recorded examples do not bind to this graph')
    csr, graph = encode_csr(index)
    source_digests = {'releaseSha256': expected_manifest_sha256, 'rightsSha256': sha256(raw['rights']),
                      'idsSha256': sha256(raw['ids']), 'indexSha256': sha256(raw['index']),
                      'graphManifestSha256': sha256(raw['graphManifest']), 'layoutSha256': sha256(layout_bytes),
                      'artistRecordsSha256': sha256(artists_bytes), 'examplesSha256': sha256(examples_bytes)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.v2-convert-', dir=output.parent) as temporary:
        stage = Path(temporary) / output.name
        stage.mkdir()
        build_catalog_db(stage / ASSET_PATHS['catalog'], catalog=catalog, catalog_sha=sha256(raw['catalog']), rights=rights,
                         artists=artists, ids=ids, source_digests=source_digests)
        evidence_files, evidence_bytes = build_evidence_db(stage / ASSET_PATHS['evidence'], source=source, rights=rights)
        (stage / ASSET_PATHS['vectors']).write_bytes(raw['vectors'])
        (stage / ASSET_PATHS['graph']).write_bytes(csr)
        (stage / ASSET_PATHS['layout']).write_bytes(positions.astype('<f4').tobytes())
        (stage / ASSET_PATHS['graphManifest']).write_bytes(raw['graphManifest'])
        (stage / ASSET_PATHS['examples']).write_bytes(examples_bytes)
        assets = {}
        for name, relative in ASSET_PATHS.items():
            data = (stage / relative).read_bytes()
            assets[name] = {'path': relative, 'bytes': len(data), 'sha256': sha256(data)}
        manifest = {
            'schemaVersion': 2, 'kind': FORMAT, 'catalogId': verified.catalog_id, 'graphId': verified.graph_id,
            'count': verified.count, 'dimensions': verified.dimensions, 'pairId': PAIR_ID, 'orderedIdsSha256': object_sha(ids), 'vectorsSha256': sha256(raw['vectors']),
            'assets': assets, 'graph': graph, 'layout': layout_block(layout, positions),
            'source': {'kind': 'music-corpus-release', 'manifestSha256': expected_manifest_sha256,
                       'catalogSha256': sha256(raw['catalog']), **{k: v for k, v in source_digests.items() if k != 'releaseSha256'},
                       'evidenceFiles': evidence_files, 'evidenceBytes': evidence_bytes},
            'build': {'tool': 'scripts/convert_release_v1_to_v2.py', 'python': platform.python_version(),
                      'sqlite': sqlite3.sqlite_version, 'numpy': np.__version__,
                      'v1ValidationLimits': {'maxTracks': v1_limits.max_tracks, 'coreBytes': v1_limits.core_bytes,
                                             'evidenceBytes': v1_limits.evidence_bytes, 'jsonBytes': v1_limits.json_bytes}}}
        manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()
        (stage / 'release.json').write_bytes(manifest_bytes)
        manifest_sha = sha256(manifest_bytes)
        # ---- proofs, against the staged files, before anything is published ----
        release = load_release_v2(stage, expected_manifest_sha256=manifest_sha, limits=v2_limits)
        try:
            rebuilt_catalog, rebuilt_rights, rebuilt_artists = reconstruct_sources(release)
            require(rebuilt_catalog == catalog and rebuilt_rights == rights and rebuilt_artists == artists,
                    'Database does not reproduce the v1 catalog/rights/artist objects')
            catalog_bytes_equal = (json.dumps(rebuilt_catalog, indent=2, ensure_ascii=False) + '\n').encode() == raw['catalog']
            require(decode_links(release) == index['links'], 'CSR graph does not decode to the v1 index links')
            require(bool(np.array_equal(np.asarray(release.layout, dtype=np.float64), positions)), 'Layout changed')
            rows = validate_rows(release)
            require(rows['evidenceFiles'] == evidence_files and rows['evidenceBytes'] == evidence_bytes
                    == verified.evidence_bytes, 'Evidence inventory changed during conversion')
            v1 = HNSW(index, raw['vectors'])
            parity = oracle(v1, GraphV2(release), examples, samples=samples, seed=seed)
            require(parity['exactMismatches'] == 0 and parity['traceMismatches'] == 0,
                    'v1/v2 parity oracle failed: ' + json.dumps(parity['mismatches'][:3]))
        finally:
            release.close()
        os.replace(stage, output)
    receipt = {'schemaVersion': 1, 'kind': 'music-corpus-release-v2-conversion', 'output': str(output),
               'releaseSha256': manifest_sha, 'catalogId': verified.catalog_id, 'graphId': verified.graph_id,
               'count': verified.count, 'source': manifest['source'],
               'assets': {name: spec for name, spec in assets.items()},
               'proofs': {'catalogRightsArtistObjectsEqual': True, 'catalogJsonBytesReconstructed': catalog_bytes_equal,
                          'evidenceBlobsByteIdentical': evidence_files, 'csrDecodesToIndexLinks': True,
                          'layoutUnchanged': True, 'vectorsByteIdentical': True, 'rowValidation': rows,
                          'parityOracle': parity},
               'elapsedSeconds': round(time.perf_counter() - started, 3), 'build': manifest['build']}
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source-dir', type=Path, required=True, help='validated v1 release directory')
    parser.add_argument('--expected-manifest-sha256', required=True, help='reviewed v1 release.json digest')
    parser.add_argument('--output-dir', type=Path, required=True, help='new v2 release directory (must not exist)')
    parser.add_argument('--max-tracks', type=int, default=2000)
    parser.add_argument('--core-byte-budget', type=int, default=16_000_000)
    parser.add_argument('--evidence-byte-budget', type=int, default=8_000_000)
    parser.add_argument('--json-byte-budget', type=int, default=8_000_000)
    parser.add_argument('--oracle-samples', type=int, default=48)
    parser.add_argument('--receipt', type=Path, help='write the conversion receipt JSON here as well')
    args = parser.parse_args()
    try:
        receipt = convert(args.source_dir, args.expected_manifest_sha256, args.output_dir,
                          v1_limits=ReleaseLimits(max_tracks=args.max_tracks, core_bytes=args.core_byte_budget,
                                                  evidence_bytes=args.evidence_byte_budget, json_bytes=args.json_byte_budget),
                          samples=args.oracle_samples)
    except ReleaseError as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        return 1
    text = json.dumps(receipt, indent=2, ensure_ascii=False) + '\n'
    if args.receipt:
        args.receipt.write_text(text)
    print(text, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
