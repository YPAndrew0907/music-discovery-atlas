#!/usr/bin/env python3
"""Build the page's small, pinned web data for a release-format-v2 deployment.

Instead of catalog.json, vectors.f32, index.json and ids.json, web/search-studio/data/ holds:
* manifest.json: release identities (what the server will report), counts, genres, the audio
  policy and pins for the two files below. Its SHA-256 is MANIFEST_SHA in studio-release.mjs.
* layout.json: the density-cloud sample. At or below --sample-cap rows it is every position
  (so the map draws exactly what v1 drew); above it, one representative row per occupied cell
  of a stratified grid, weighted by the rows it stands for. It also carries the stored index
  connections between sampled rows, in the order the v1 page derived them.
* examples.json: the six recorded queries as complete search packets (exact ranking, trace,
  display rows and positions) computed by the v2 server code, so no vectors are needed.

The rest of the catalog is read page by page from /collection/ on the same server, and so are the
track credits: notices/track-attribution.html becomes a small page that links to /collection/credits
(and forwards old #fma-N links there) instead of every credit in one file.
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from collection_v2 import credits_index_page  # noqa: E402
from corpus_release import require, sha256  # noqa: E402
from release_v2 import load_release_v2, LimitsV2  # noqa: E402
from search_v2 import GraphV2, label_rows, trace_rows  # noqa: E402

DATA_FILES = ('manifest.json', 'layout.json', 'examples.json')
V1_DATA_FILES = ('catalog.json', 'vectors.f32', 'index.json', 'ids.json', 'artist-records.json')


def dump(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def connections(release, rows):
    """indexConnections() from search-motion.mjs, restricted to sampled rows: pairs keyed by
    (min, max), first-seen order over rows, levels and stored neighbor order, max level kept."""
    keep = np.zeros(release.count, dtype=bool)
    keep[rows] = True
    nodes, offsets, neighbors = release.node_layers, release.layer_offsets, release.neighbors
    pairs = {}
    for source in rows:
        for level, layer in enumerate(range(int(nodes[source]), int(nodes[source + 1]))):
            for target in neighbors[int(offsets[layer]):int(offsets[layer + 1])].tolist():
                if not keep[target]:
                    continue
                key = (min(source, target), max(source, target))
                pairs[key] = max(pairs.get(key, level), level)
    flat = []
    for (a, b), level in pairs.items():
        flat.extend((a, b, level))
    return flat


def sample_layout(release, cap):
    xy = np.asarray(release.layout, dtype=np.float64)
    count = release.count
    if count <= cap:
        return list(range(count)), None, 'every row'
    x0, y0 = xy.min(axis=0)
    x1, y1 = xy.max(axis=0)
    grid = int(np.sqrt(cap))
    while True:
        cx = np.minimum(((xy[:, 0] - x0) / max(x1 - x0, 1e-9) * grid).astype(np.int64), grid - 1)
        cy = np.minimum(((xy[:, 1] - y0) / max(y1 - y0, 1e-9) * grid).astype(np.int64), grid - 1)
        cells = cx * grid + cy
        unique, first, counts = np.unique(cells, return_index=True, return_counts=True)
        if len(unique) <= cap or grid <= 2:
            break
        grid -= max(1, grid // 16)
    order = np.argsort(first)
    rows = first[order].tolist()  # the lowest row in each occupied cell, in catalog order
    return rows, counts[order].tolist(), f'lowest row per occupied cell of a {grid}x{grid} grid'


def packet(release, graph, item):
    query = np.asarray(item['queryVector'], dtype=np.float32)
    require(sha256(query.tobytes()) == item['queryVectorSha256'], 'Recorded query vector changed')
    exact = graph.exact_search(query, k=16)
    trace = graph.search(query, k=16, ef=32, trace=True, trace_limit=2048)['trace']
    ranked = [entry['id'] for entry in exact]
    labels = [row for row in label_rows(trace) if row not in ranked]
    return {'id': item['id'], 'label': item['label'], 'text': item['text'], 'mode': item['mode'],
            'queryProfileId': item['queryProfileId'], 'queryVectorSha256': item['queryVectorSha256'],
            'results': [{'rank': rank, 'row': entry['id'], 'id': release.ordered_ids[entry['id']],
                         'cosineSimilarity': 1 - entry['distance']} for rank, entry in enumerate(exact, 1)],
            'trace': trace, 'tracks': release.display_rows(ranked + labels),
            'layout': release.positions(trace_rows(trace, exact))}


def build(release_dir, manifest_sha, web_root, *, sample_cap=8192, audio_mode='local', audio_origin=None,
          audio_prefix=None, limits=None):
    release = load_release_v2(release_dir, expected_manifest_sha256=manifest_sha, limits=limits or LimitsV2())
    graph = GraphV2(release)
    data = Path(web_root) / 'search-studio/data'
    data.mkdir(parents=True, exist_ok=True)
    rows, weights, method = sample_layout(release, sample_cap)
    bounds = release.manifest['layout']['bounds']
    layout = {'schemaVersion': 2, 'kind': 'music-layout-sample-v2', 'releaseSha256': release.manifest_sha256,
              'graphId': release.graph_id, 'count': release.count, 'sampleCount': len(rows), 'sampleMethod': method,
              'bounds': bounds, 'rows': rows,
              'xy': [float(v) for v in np.asarray(release.layout)[rows].reshape(-1)],
              **({'weights': weights} if weights is not None else {}),
              'edges': connections(release, rows),
              'description': release.manifest['layout'].get('description') or
              'Fixed t-SNE layout of the same normalized 512-dimensional audio vectors; positions never determine results.'}
    source_examples = json.loads(release.directory.joinpath(release.assets['examples']['path']).read_bytes())
    require(sha256(release.directory.joinpath(release.assets['examples']['path']).read_bytes())
            == release.assets['examples']['sha256'], 'Recorded examples changed after verification')
    examples = {'schemaVersion': 2, 'kind': 'music-recorded-examples-v2', 'releaseSha256': release.manifest_sha256,
                'graphId': release.graph_id, 'indexSha256': release.graph_sha256,
                'queryProfileId': source_examples['queryProfileId'], 'defaultExampleId': source_examples['defaultExampleId'],
                'count': len(source_examples['examples']), 'description': source_examples['description'],
                'examples': [packet(release, graph, item) for item in source_examples['examples']]}
    files = {}
    for name, value in (('layout', layout), ('examples', examples)):
        payload = dump(value)
        (data / f'{name}.json').write_bytes(payload)
        files[name] = {'path': f'{name}.json', 'bytes': len(payload), 'sha256': sha256(payload)}
    graph_manifest = release.graph_manifest
    licenses = {}
    for (license_id, count) in release.connection().execute('SELECT license, count(*) FROM tracks GROUP BY license'):
        licenses[license_id] = count
    audio_policy = {'mode': audio_mode, 'origin': audio_origin, 'pathPrefix': audio_prefix}
    manifest = {'schemaVersion': 2, 'format': 2, 'kind': 'music-collection-web-v2',
                'releaseSha256': release.manifest_sha256, 'catalogId': release.catalog_id, 'graphId': release.graph_id,
                'indexSha256': release.graph_sha256, 'catalogSha256': release.catalog_sha256,
                'vectorsSha256': release.vectors_sha256, 'orderedIdsSha256': release.manifest['orderedIdsSha256'],
                'count': release.count, 'dimensions': release.dimensions,
                'allowedQueryProfiles': graph_manifest['allowedQueryProfiles'], 'collectionApi': '/collection/',
                'searchDefaults': {'k': 16, 'ef': 32}, 'audioPolicy': audio_policy,
                'summary': {'artists': release.artist_count, 'licenses': licenses,
                            'genres': [{'genre': g, 'count': c} for g, c in release.genres]},
                'layout': {'count': release.count, 'sampleCount': len(rows), 'sampleMethod': method, 'bounds': bounds,
                           'description': layout['description']},
                'examples': {'count': examples['count'], 'defaultExampleId': examples['defaultExampleId']},
                'files': files, 'source': release.manifest['source'],
                'scope': 'Release format v2: the catalog, vectors and index stay on the server. The page reads pages, '
                         'search packets and this pinned layout sample from the same origin.'}
    payload = dump(manifest)
    (data / 'manifest.json').write_bytes(payload)
    manifest_sha = sha256(payload)
    studio = Path(web_root) / 'search-studio/src/studio-release.mjs'
    # Artist IDs now live in the verified catalog database, so its digest pins them.
    studio.write_text(f"export const MANIFEST_SHA='{manifest_sha}';\nexport const ARTIST_METADATA_SHA='{release.catalog_sha256}';\n")
    removed = []
    for name in V1_DATA_FILES:
        if (data / name).exists():
            (data / name).unlink()
            removed.append(name)
    credits = credits_index_page(release.count)
    (Path(web_root) / 'notices').mkdir(parents=True, exist_ok=True)
    (Path(web_root) / 'notices/track-attribution.html').write_bytes(credits)
    return {'manifestSha256': manifest_sha, 'files': {**files, 'manifest': {'path': 'manifest.json', 'bytes': len(payload),
            'sha256': manifest_sha}}, 'sampleCount': len(rows), 'edges': len(layout['edges']) // 3,
            'removedV1DataFiles': removed, 'webDataBytes': sum(f['bytes'] for f in files.values()) + len(payload),
            'credits': {'path': 'notices/track-attribution.html', 'bytes': len(credits), 'pagedAt': '/collection/credits'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--web-root', type=Path, required=True, help='the web/ directory of the package being built')
    parser.add_argument('--sample-cap', type=int, default=8192)
    parser.add_argument('--audio-mode', choices=('local', 'remote', 'disabled'), default='local')
    parser.add_argument('--audio-origin')
    parser.add_argument('--audio-prefix')
    parser.add_argument('--limits', type=json.loads, default=None, help='JSON object of v2 limits, as in the selection')
    args = parser.parse_args()
    print(json.dumps(build(args.release_dir, args.expected_manifest_sha256, args.web_root, sample_cap=args.sample_cap,
                           audio_mode=args.audio_mode, audio_origin=args.audio_origin, audio_prefix=args.audio_prefix,
                           limits=LimitsV2.from_config(args.limits)),
                     indent=2))


if __name__ == '__main__':
    main()
