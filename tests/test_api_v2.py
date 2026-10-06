"""Release format v2 through the real API, gateway and public boundary (fixture text encoder only)."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_fixtures import ROOT, V1_DIR, V1_SHA, FixtureEncoder, converted_fma2000, v2_web_root  # noqa: E402
from api import Settings, create_app, load_graph  # noqa: E402
from collection_v2 import AudioDeliveryV2  # noqa: E402
from corpus_release import ReleaseError, ReleaseLimits, object_sha, sha256, validate_release  # noqa: E402
from encoder import NativeEncoder  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from hosting import ReadinessGate, build_application  # noqa: E402
from hnsw_trace import HNSW  # noqa: E402
from public_boundary import PublicLimits  # noqa: E402
from release_v2 import load_release_v2  # noqa: E402
from web_gateway import SECURITY_HEADERS, WebGateway  # noqa: E402

GENERATION = 'parity-test-v1v2'
ORIGIN = 'https://preview.example'


class V1Encoder:
    """The v1 Engine's selected-release state for fma2000, without the ONNX model."""

    def __init__(self, vectors_by_text):
        verified = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=2000))
        manifest = json.loads(verified.assets['graphManifest'])
        profile = manifest['allowedQueryProfiles'][0]
        self.query_profile, self.engine = profile['identity'], profile['id']
        self.pair = json.loads((ROOT / 'model/model-space-q8.json').read_text())
        self.validated_release, self.release_directory = verified, V1_DIR
        self.catalog_version, self.catalog_sha = verified.catalog_id, sha256(verified.assets['catalog'])
        self.ids = list(verified.ordered_ids)
        self.vectors = np.frombuffer(verified.assets['vectors'], dtype='<f4').reshape((verified.count, 512))
        self.audio_receipt = {'vectorsSha256': sha256(verified.assets['vectors']),
                              'executionProfileSha256': object_sha(self.pair['identity']['audioExecutionProfile'])}
        self.vectors_by_text = vectors_by_text

    def manifest(self):
        return NativeEncoder.manifest(self)

    def encode(self, text, control):
        control.check()
        return self.vectors_by_text[text], {'originalTokens': 3, 'usedTokens': 3, 'truncated': False,
            'transform': 'test fixture only'}, {'tokenization': 0.0, 'inferenceAndNormalization': 0.0}


def search_body(encoder, text, n, **extra):
    return {'requestId': f'req-{n}', 'generation': n, 'query': text, 'engineId': encoder.engine,
            'catalogId': encoder.catalog_version, 'deploymentGeneration': GENERATION, **extra}


class ApiV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir, cls.sha = converted_fma2000()
        cls.release = load_release_v2(cls.dir, expected_manifest_sha256=cls.sha)
        examples = json.loads((V1_DIR / 'examples.json').read_bytes())['examples']
        rng = np.random.default_rng(2026)
        cls.texts = {item['text']: np.asarray(item['queryVector'], dtype=np.float32) for item in examples}
        for row in rng.choice(2000, size=14, replace=False).tolist():
            cls.texts[f'like row {row}'] = np.array(cls.release.vectors[row])
        cls.v1_encoder = V1Encoder(cls.texts)
        cls.v2_encoder = FixtureEncoder(cls.release, cls.dir, cls.texts)
        cls.temp = tempfile.TemporaryDirectory()
        cls.web, cls.web_manifest, cls.web_receipt = v2_web_root(cls.temp.name, cls.dir, cls.sha)
        cls.catalog = json.loads((V1_DIR / 'catalog.json').read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def client(self, encoder, *, gateway=False, audio=None):
        graph, binding = load_graph(encoder, str(encoder.release_directory))
        app = create_app(Settings(auth_token=None, deployment_generation=GENERATION), encoder=encoder,
                         graph=graph, graph_binding=binding)
        if gateway:
            app = WebGateway(ReadinessGate(app), web_root=self.web, web_manifest=self.web_manifest, catalog_path=None,
                             release_v2=self.release, **(audio or {}))
        client = TestClient(app, base_url=ORIGIN)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    # ---- /v1 parity ------------------------------------------------------------------------
    def test_search_responses_match_the_v1_server_for_twenty_queries(self):
        v1, v2 = self.client(self.v1_encoder), self.client(self.v2_encoder)
        same = ['schemaVersion', 'engineId', 'queryProfileId', 'pairId', 'catalogId', 'vectorsSha256', 'graphId',
                'indexSpaceId', 'graphManifestSha256', 'orderedIdsSha256', 'rankingAlgorithm', 'tokenization',
                'results', 'trace', 'annDiagnostic', 'interpretation']
        layout = np.asarray(json.loads((V1_DIR / 'layout.json').read_bytes())['positions'])
        checked = 0
        for n, text in enumerate(self.texts, 1):
            for extra in ({'k': 16, 'ef': 32, 'trace': True}, {'k': 8, 'ef': 64, 'trace': True, 'traceLimit': 2048}):
                a = v1.post('/v1/search', json=search_body(self.v1_encoder, text, n, **extra)).json()
                b = v2.post('/v1/search', json=search_body(self.v2_encoder, text, n, **extra)).json()
                for key in same:
                    self.assertEqual(a[key], b[key], key)
                scores = [abs(x['cosineSimilarity'] - y['cosineSimilarity']) for x, y in zip(a['results'], b['results'])]
                self.assertEqual(max(scores), 0.0)  # bit-identical, stricter than the 1e-5 requirement
                # The documented identity split: these name the files each server verified.
                self.assertEqual((a['catalogSha256'], a['indexSha256'], a['bindingStatus'], a['corpusReleaseSha256']),
                                 (sha256((V1_DIR / 'catalog.json').read_bytes()), sha256((V1_DIR / 'index.json').read_bytes()),
                                  'verified-corpus-release', V1_SHA))
                self.assertEqual((b['catalogSha256'], b['indexSha256'], b['bindingStatus'], b['corpusReleaseSha256'], b['releaseFormat']),
                                 (self.release.catalog_sha256, self.release.graph_sha256, 'verified-corpus-release-v2', self.sha, 2))
                self.assertNotIn('tracks', a)
                rows = [item['row'] for item in b['results']]
                self.assertEqual([t['row'] for t in b['tracks']][:len(rows)], rows)
                for track in b['tracks']:
                    source = self.catalog['tracks'][track['row']]
                    self.assertEqual((track['id'], track['title'], track['artist'], track['genre'], track['license']),
                                     (source['id'], source['title'], source['artist'], source.get('genre'), source['license']))
                xy = np.asarray(b['layout']['xy']).reshape(-1, 2)
                self.assertTrue(np.array_equal(xy, layout[b['layout']['rows']]))
                mentioned = {e['id'] for e in b['trace']['events'] if 'id' in e} | set(rows)
                self.assertLessEqual(mentioned, set(b['layout']['rows']))
                checked += 1
        self.assertEqual(checked, 40)

    def test_manifest_matches_v1_except_the_documented_split(self):
        a = self.client(self.v1_encoder).get('/v1/manifest').json()
        b = self.client(self.v2_encoder).get('/v1/manifest').json()
        differ = {'catalogSha256', 'indexSha256', 'bindingStatus', 'corpusReleaseSha256'}
        self.assertEqual(set(b) - set(a), {'releaseFormat'})
        self.assertEqual({k: v for k, v in a.items() if k not in differ}, {k: v for k, v in b.items() if k not in differ | {'releaseFormat'}})
        self.assertLess(len(json.dumps(b)), 32768)

    # ---- collection routes ---------------------------------------------------------------
    def test_browse_pages_cover_the_catalog_in_order(self):
        client = self.client(self.v2_encoder, gateway=True)
        seen = []
        for offset in range(0, 2000, 48):
            page = client.get(f'/collection/tracks?offset={offset}&limit=48').json()
            self.assertEqual((page['channel'], page['total'], page['baseTotal']), ('browse', 2000, 2000))
            seen.extend(t['id'] for t in page['rows'])
        self.assertEqual(seen, [t['id'] for t in self.catalog['tracks']])
        last = client.get('/collection/tracks?offset=1992&limit=12').json()
        self.assertEqual(len(last['rows']), 8)  # page 167 of 167
        rows = client.get('/collection/tracks?rows=1999,0,7,0').json()['rows']
        self.assertEqual([t['row'] for t in rows], [1999, 0, 7])
        for bad in ['limit=49', 'offset=-1', 'rows=2000', 'rows=1&offset=0', 'q=' + 'x' * 513, 'unknown=1', 'preview=yes',
                    'rows=' + ','.join(['1'] * 65)]:
            with self.subTest(bad=bad[:30]):
                self.assertEqual(client.get('/collection/tracks?' + bad).status_code, 400)
        self.assertEqual(client.get('/collection/other').status_code, 404)
        self.assertEqual(client.post('/collection/tracks').status_code, 405)

    def test_lookup_and_refinement_follow_the_page_rules_exactly(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is required for the cross-language check')
        client = self.client(self.v2_encoder, gateway=True)
        cases = [('love', '', ''), ('the', 'night', ''), ('Électro', '', ''), ('a', '', 'Electronic'), ('', 'piano', ''),
                 ('', '', 'Folk'), ('', 'ü', ''), ('lady love', '', ''), ('zzzz not here', '', '')]
        script = """
import {metadataSearch} from './web/listen-lab/src/retrieval.mjs';
import {refineCandidates, sourceGenres} from './web/search-studio/src/results-view.mjs';
import {readFileSync} from 'node:fs';
const tracks = JSON.parse(readFileSync('corpus-releases/fma2000/catalog.json')).tracks;
const out = JSON.parse(process.argv[1]).map(([q, text, genre]) => {
  const base = q.trim() ? metadataSearch(q, tracks, tracks.length).map(r => ({row: r.row})) : tracks.map((_, row) => ({row}));
  return {rows: refineCandidates(base, tracks, {text, genre}).map(r => r.row), genres: sourceGenres(base, tracks)};
});
console.log(JSON.stringify(out));"""
        expected = json.loads(subprocess.run([node, '--input-type=module', '-e', script, json.dumps(cases)], cwd=ROOT,
                                             capture_output=True, text=True, check=True).stdout)
        for (q, text, genre), want in zip(cases, expected):
            with self.subTest(q=q, text=text, genre=genre):
                rows, offset, total = [], 0, None
                while total is None or offset < total:
                    params = {'q': q, 'text': text, 'genre': genre, 'offset': offset, 'limit': 48, 'facets': 1}
                    page = client.get('/collection/tracks', params=params).json()
                    total, offset = page['total'], offset + 48
                    rows.extend(t['row'] for t in page['rows'])
                    genres = page['genres']
                self.assertEqual(rows, want['rows'])
                self.assertEqual(sorted((g['genre'], g['count']) for g in genres),
                                 sorted((g['genre'], g['count']) for g in want['genres']))

    def test_neighbors_match_the_v1_page_computation(self):
        client = self.client(self.v2_encoder, gateway=True)
        v1 = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=2000))
        reference = HNSW(json.loads(v1.assets['index']), v1.assets['vectors'])
        for row in (0, 5, 777, 1999):
            packet = client.get(f'/collection/neighbors?row={row}').json()
            query = np.array(self.release.vectors[row])
            exact = reference.exact_search(query, k=16, exclude_id=row)
            self.assertEqual([(r['row'], r['cosineSimilarity']) for r in packet['results']],
                             [(e['id'], 1 - e['distance']) for e in exact])
            self.assertEqual(packet['trace'], reference.search(query, k=17, ef=32, trace=True, trace_limit=2048)['trace'])
            self.assertEqual(packet['tracks'][0]['row'], row)
            self.assertIn(row, packet['layout']['rows'])

    def test_web_data_replaces_the_catalog_and_vectors_with_small_pinned_files(self):
        manifest = json.loads((self.web / 'search-studio/data/manifest.json').read_bytes())
        studio = (self.web / 'search-studio/src/studio-release.mjs').read_text()
        self.assertIn(sha256((self.web / 'search-studio/data/manifest.json').read_bytes()), studio)
        self.assertEqual((manifest['format'], manifest['releaseSha256'], manifest['count']), (2, self.sha, 2000))
        for name in ('catalog.json', 'vectors.f32', 'index.json', 'ids.json', 'artist-records.json'):
            self.assertFalse((self.web / 'search-studio/data' / name).exists(), name)
        layout = json.loads((self.web / 'search-studio/data/layout.json').read_bytes())
        self.assertEqual((layout['sampleCount'], layout['rows']), (2000, list(range(2000))))
        self.assertNotIn('weights', layout)
        index = json.loads((V1_DIR / 'index.json').read_bytes())
        pairs = {}
        for source, layers in enumerate(index['links']):  # indexConnections() from search-motion.mjs
            for level, neighbors in enumerate(layers):
                for target in neighbors:
                    key = (min(source, target), max(source, target))
                    pairs[key] = max(pairs.get(key, level), level)
        self.assertEqual(layout['edges'], [v for (a, b), level in pairs.items() for v in (a, b, level)])
        examples = json.loads((self.web / 'search-studio/data/examples.json').read_bytes())
        v1 = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=2000))
        reference = HNSW(json.loads(v1.assets['index']), v1.assets['vectors'])
        for item, source in zip(examples['examples'], json.loads((V1_DIR / 'examples.json').read_bytes())['examples']):
            query = np.asarray(source['queryVector'], dtype=np.float32)
            self.assertEqual([(r['row'], r['cosineSimilarity']) for r in item['results']],
                             [(e['id'], 1 - e['distance']) for e in reference.exact_search(query, k=16)])
            self.assertEqual(item['trace'], reference.search(query, k=16, ef=32, trace=True, trace_limit=2048)['trace'])
        sizes = self.web_receipt['webDataBytes']
        self.assertLess(sizes, 2_000_000)

    def test_gateway_refuses_a_web_package_built_for_another_release(self):
        broken = Path(self.temp.name) / 'broken'
        shutil.copytree(self.web, broken)
        (broken / 'search-studio/data/catalog.json').write_bytes(b'{}')
        from v2_fixtures import web_manifest_for
        manifest = broken.parent / 'broken-manifest.json'
        manifest.write_text(json.dumps(web_manifest_for(broken)))
        with self.assertRaisesRegex(ValueError, 'must not ship the whole catalog.json'):
            WebGateway(object(), web_root=broken, web_manifest=manifest, catalog_path=None, release_v2=self.release)
        with self.assertRaisesRegex(ValueError, 'does not match the active v2 release'):
            WebGateway(object(), web_root=ROOT / 'web', web_manifest=ROOT / 'web-manifest.json', catalog_path=None,
                       release_v2=self.release)

    def test_v1_gateway_keeps_its_routes_and_headers(self):
        gateway = WebGateway(object(), web_root=ROOT / 'web', web_manifest=ROOT / 'web-manifest.json',
                             catalog_path=ROOT / 'web/search-studio/data/catalog.json')
        self.assertIsNone(gateway.collection)
        self.assertIs(gateway.headers, SECURITY_HEADERS)
        client = TestClient(gateway, base_url=ORIGIN)
        self.assertEqual(client.get('/collection/tracks').status_code, 404)

    # ---- audio delivery ------------------------------------------------------------------
    def fake_pack(self, rows=(0, 1, 2)):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        pins, tracks = [], []
        for row in rows:
            ident = self.release.ordered_ids[row]
            data = hashlib.sha256(ident.encode()).digest() * 40
            name = '%06d.mp3' % int(ident[4:])
            (directory / 'pack').mkdir(exist_ok=True)
            (directory / 'pack' / name).write_bytes(data)
            pins.append((row, ident, len(data), hashlib.sha256(data).hexdigest()))
        class Release:
            catalog_id, catalog_sha256, manifest_sha256 = self.release.catalog_id, self.release.catalog_sha256, self.release.manifest_sha256
            def audio_pins(self_):
                return pins + [(r, self.release.ordered_ids[r], 1, '0' * 64) for r in range(3, 40)]
        return Release(), directory, pins

    def delivery(self, release, directory, pins, *, mode='local', **extra):
        rows = [{'id': ident, 'available': True, 'bytes': size, 'sha256': digest,
                 'url': ('/audio/%06d.mp3' % int(ident[4:])) if mode == 'local' else 'https://audio.example' + '/fma/' + digest + '.mp3'}
                for _, ident, size, digest in pins]
        manifest = {'schemaVersion': 2, 'kind': 'music-audio-delivery-v2', 'catalogId': release.catalog_id,
                    'catalogSha256': release.catalog_sha256, 'releaseSha256': release.manifest_sha256, 'enabled': True,
                    'publicDeliveryVerified': True, 'mode': mode, 'tracks': rows,
                    **({'origin': 'https://audio.example', 'pathPrefix': '/fma/'} if mode == 'remote' else {}), **extra}
        path = directory / f'delivery-{mode}.json'
        path.write_text(json.dumps(manifest))
        return path

    def test_local_audio_is_inventoried_at_startup_and_hashed_on_first_request(self):
        release, directory, pins = self.fake_pack()
        delivery = AudioDeliveryV2(self.delivery(release, directory, pins), release, enabled=True, directory=directory / 'pack')
        self.assertEqual(delivery.stats, {'firstRequestHashes': 0, 'identityRechecks': 0})
        summary = delivery.manifest
        self.assertEqual((summary['mode'], summary['available'], summary['total']), ('local', 3, 40))
        self.assertEqual(base64.b64decode(summary['availableRows'])[0], 0b111)
        route = '/audio/%06d.mp3' % int(pins[0][1][4:])
        self.assertEqual(delivery.resolve(route).name, route.rsplit('/', 1)[1])
        delivery.resolve(route)
        self.assertEqual(delivery.stats, {'firstRequestHashes': 1, 'identityRechecks': 0})
        path = directory / 'pack' / route.rsplit('/', 1)[1]
        data = bytearray(path.read_bytes())
        data[0] ^= 1
        path.write_bytes(bytes(data))  # same size, new identity (mtime), wrong content
        os.utime(path, ns=(1, 1))
        with self.assertRaisesRegex(ValueError, 'Audio content pin mismatch'):
            delivery.resolve(route)
        (directory / 'pack' / 'extra.mp3').write_bytes(b'x')
        with self.assertRaisesRegex(ReleaseError, 'missing or unapproved'):
            AudioDeliveryV2(self.delivery(release, directory, pins), release, enabled=True, directory=directory / 'pack')

    def test_remote_audio_requires_one_exact_origin_and_content_addressed_keys(self):
        release, directory, pins = self.fake_pack()
        delivery = AudioDeliveryV2(self.delivery(release, directory, pins, mode='remote'), release, enabled=True)
        self.assertEqual((delivery.paths, delivery.manifest['origin'], delivery.manifest['pathPrefix']), ({}, 'https://audio.example', '/fma/'))
        for change in ({'origin': 'http://audio.example'}, {'origin': 'https://audio.example/x'}, {'pathPrefix': '/../'}):
            with self.subTest(change=change):
                with self.assertRaises(ReleaseError):
                    AudioDeliveryV2(self.delivery(release, directory, pins, mode='remote', **change), release, enabled=True)
        path = self.delivery(release, directory, pins, mode='remote')
        record = json.loads(path.read_text())
        record['tracks'][0]['url'] = record['tracks'][0]['url'].replace('.mp3', '.mp3?x=1')
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ReleaseError, 'Unverified remote audio URL'):
            AudioDeliveryV2(path, release, enabled=True)

    def test_gateway_serves_lazily_verified_audio_and_a_compact_summary(self):
        client = self.client(self.v2_encoder, gateway=True)
        summary = client.get('/audio-delivery.json').json()
        self.assertEqual((summary['enabled'], summary['mode'], summary['total'], summary['releaseSha256']),
                         (False, 'disabled', 2000, self.sha))
        self.assertEqual(client.get('/audio/001382.mp3').status_code, 404)
        # Same gateway, with a lazily verified local pack (synthetic bytes, synthetic pins).
        release, directory, pins = self.fake_pack()
        gateway = client.app
        gateway.audio = AudioDeliveryV2(self.delivery(release, directory, pins), release, enabled=True,
                                        directory=directory / 'pack')
        route = '/audio/%06d.mp3' % int(pins[1][1][4:])
        whole = client.get(route)
        self.assertEqual((whole.status_code, len(whole.content), hashlib.sha256(whole.content).hexdigest()),
                         (200, pins[1][2], pins[1][3]))
        part = client.get(route, headers={'Range': 'bytes=10-19'})
        self.assertEqual((part.status_code, part.content), (206, whole.content[10:20]))
        self.assertEqual(gateway.audio.stats['firstRequestHashes'], 1)
        target = directory / 'pack' / route.rsplit('/', 1)[1]
        target.write_bytes(bytes(b ^ 1 for b in whole.content))
        os.utime(target, ns=(2, 2))
        refused = client.get(route)
        self.assertEqual((refused.status_code, refused.json()), (404, {'error': 'Preview unavailable'}))
        self.assertEqual(client.get('/audio-delivery.json').json()['available'], 3)

    # ---- anonymous boundary --------------------------------------------------------------
    def test_anonymous_preview_passes_v2_search_packets_and_collection_reads(self):
        graph, binding = load_graph(self.v2_encoder, str(self.dir))
        gate = build_application(mode='anonymous-preview', enable_anonymous=True, generation=GENERATION,
                                 public_origin=ORIGIN, encoder=self.v2_encoder, graph=graph, graph_binding=binding,
                                 limits=PublicLimits(), serve_web=False)
        gateway = WebGateway(gate, web_root=self.web, web_manifest=self.web_manifest, catalog_path=None,
                             mode='anonymous-preview', public_origin=ORIGIN, release_v2=self.release)
        with TestClient(gateway, base_url=ORIGIN) as client:
            headers = {'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-origin'}
            manifest = client.get('/v1/manifest', headers=headers).json()
            self.assertEqual((manifest['releaseFormat'], manifest['defaults']['traceLimit']), (2, 128))
            body = search_body(self.v2_encoder, next(iter(self.texts)), 1, k=16, ef=32, trace=True)
            body['requestId'] = '10000000-0000-4000-8000-000000000001'
            reply = client.post('/v1/search', json=body, headers=headers)
            self.assertEqual(reply.status_code, 200)
            packet = reply.json()
            self.assertEqual((len(packet['results']), packet['trace']['limit']), (16, 128))
            self.assertTrue(packet['tracks'] and packet['layout']['rows'])
            self.assertEqual(client.get('/collection/tracks?limit=12').status_code, 200)
            self.assertTrue(client.get('/deployment-config.json').json()['enabled'])


if __name__ == '__main__':
    unittest.main()
