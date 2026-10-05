#!/usr/bin/env python3
"""Generate local2000 candidate web assets from a validated release; no deployment."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ReleaseLimits, object_sha, require, sha256, validate_release
from build_corpus_release import encode, spec, write_json


def build(release_dir, expected_sha, credits):
    import numpy as np
    import sklearn
    from sklearn.manifold import TSNE, trustworthiness
    from sklearn.metrics import pairwise_distances
    from threadpoolctl import threadpool_limits
    credits = Path(credits)
    require(credits.is_file(), 'The local2000 credit document must exist before a web rebuild')
    release_dir = Path(release_dir)
    require(release_dir.resolve() == (ROOT / 'corpus-releases/fma2000').resolve(),
            'Only the separate local2000 release may update this candidate web tree')
    release = validate_release(release_dir, expected_manifest_sha256=expected_sha, limits=ReleaseLimits(max_tracks=2000))
    require(release.count == 2000, 'This web build is scoped to the separate2000 candidate')
    vectors = np.frombuffer(release.assets['vectors'], dtype='<f4').reshape((2000, 512))
    settings = {'n_components': 2, 'perplexity': 30, 'early_exaggeration': 12,
                'learning_rate': 100, 'max_iter': 1500, 'metric': 'euclidean', 'init': 'random',
                'random_state': 20261004, 'method': 'barnes_hut', 'angle': .5, 'n_jobs': 1}
    with threadpool_limits(limits=1):
        reducer = TSNE(**settings)
        coordinates = reducer.fit_transform(vectors)
        high = pairwise_distances(vectors, metric='euclidean')
        low = pairwise_distances(coordinates, metric='euclidean')
        np.fill_diagonal(high, np.inf)
        np.fill_diagonal(low, np.inf)
        neighbors_high = np.argsort(high, axis=1, kind='stable')[:, :8]
        neighbors_low = np.argsort(low, axis=1, kind='stable')[:, :8]
        recalls = [len(set(a) & set(b)) / 8 for a, b in zip(neighbors_high, neighbors_low)]
        quality = {'neighborRecall': float(np.mean(recalls)), 'minimumNeighborRecall': float(min(recalls)),
                   'trustworthiness': float(trustworthiness(vectors, coordinates, n_neighbors=8))}
    center = (coordinates.max(axis=0) + coordinates.min(axis=0)) / 2
    scale = float(np.max(coordinates.max(axis=0) - coordinates.min(axis=0)) / 2)
    positions = (coordinates - center) / scale
    layout = {'schemaVersion': 1, 'graphId': release.graph_id, 'count': 2000, 'inputDimensions': 512,
              'outputDimensions': 2, 'method': 't-SNE', 'settings': settings,
              'fitInputs': 'All512 components of each stored L2-normalized real audio vector; no metadata, genre or query labels',
              'vectorsSha256': sha256(release.assets['vectors']), 'orderedIdsSha256': object_sha(list(release.ordered_ids)),
              'positions': positions.tolist(), 'orderedIds': list(release.ordered_ids),
              'displayTransform': {'center': center.tolist(), 'uniformScale': scale},
              'metrics': {'8': quality}, 'klDivergence': float(reducer.kl_divergence_), 'iterations': int(reducer.n_iter_),
              'environment': {'scikit-learn': sklearn.__version__, 'numpy': np.__version__, 'threads': 1},
              'limitations': ['Projection loses512-dimensional information; positions never determine search ranking.',
                              'Neighborhood preservation is a geometry diagnostic, not listener relevance.']}
    write_json(release_dir / 'layout.json', layout)
    web = ROOT / 'web/search-studio/data'
    previous = json.loads((web / 'manifest.json').read_bytes())
    profiles = previous['allowedQueryProfiles']
    manifest = deepcopy(previous)
    manifest.update(json.loads(release.assets['graphManifest']))
    manifest.update(experiment='local-reviewed-fma2000-discovery-graph', allowedQueryProfiles=profiles,
        scope='2000 screened FMA recordings with real paired CLAP audio vectors. Six public recorded queries are examples; live descriptions use the disclosed selected engine. Playback requires the separately verified approved archive.',
        layout={'method': 't-SNE', 'settings': settings, 'metrics': {'8': quality}, 'inputDimensions': 512,
                'note': 'New unsupervised projection of all2000 actual audio vectors; no metadata or query labels.'},
        limitations=['This2000-track open-music sample is biased and does not represent mainstream music.',
                     'Cosine similarity and ANN agreement do not establish listener relevance.',
                     'The map loses512-dimensional information and never supplies search distances.',
                     'Browser and recorded/native query profiles retain distinct numerical identities.'])
    for name in ['catalog.json', 'vectors.f32', 'ids.json', 'index.json', 'layout.json', 'examples.json', 'artist-records.json']:
        shutil.copyfile(release_dir / name, web / name)
    names = {'catalog': 'catalog.json', 'vectors': 'vectors.f32', 'ids': 'ids.json',
             'index': 'index.json', 'layout': 'layout.json', 'examples': 'examples.json'}
    manifest['files'] = {key: spec(web / name) for key, name in names.items()}
    manifest['assets'] = {name: spec(web / name) for name in names.values()}
    write_json(web / 'manifest.json', manifest)
    (ROOT / 'web/search-studio/src/studio-release.mjs').write_text(
        "export const MANIFEST_SHA='" + sha256((web / 'manifest.json').read_bytes()) + "';\n"
        "export const ARTIST_METADATA_SHA='" + sha256((web / 'artist-records.json').read_bytes()) + "';\n")
    path = ROOT / 'web/listen-lab/src/release.mjs'
    source = path.read_text()
    browser = json.loads(source.removeprefix('export const RELEASE = ').strip().removesuffix(';'))
    browser.update(experiment='local-opt-in-q8-fma2000', catalogId=release.catalog_id,
                   scope='Optional browser-only inference over the same local reviewed2000-track corpus. Model opt-in and query-transmission boundaries are unchanged.')
    for key, name in [('catalog', 'catalog.json'), ('vectors', 'vectors.f32')]:
        browser[key] = {'url': '../../search-studio/data/' + name, 'bytes': len((web / name).read_bytes()),
                        'sha256': sha256((web / name).read_bytes())}
    path.write_text('export const RELEASE = ' + json.dumps(browser, ensure_ascii=False) + ';\n')
    shutil.copyfile(credits, ROOT / 'web/notices/track-attribution.html')
    shutil.copyfile(credits, ROOT / 'notices/track-attribution.html')
    return {'count': 2000, 'layout': quality, 'iterations': int(reducer.n_iter_),
            'publicManifestSha256': sha256((web / 'manifest.json').read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--credits', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.release_dir, args.expected_manifest_sha256, args.credits), indent=2))


if __name__ == '__main__':
    main()
