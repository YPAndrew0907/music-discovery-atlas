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
from v2_fixtures import ROOT, V1_COUNT, V1_DIR, V1_SHA, FixtureEncoder, converted_fma2000, v2_web_root  # noqa: E402
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
        verified = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=V1_COUNT))
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
        for row in rng.choice(V1_COUNT, size=14, replace=False).tolist():
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
        for offset in range(0, V1_COUNT, 48):
            page = client.get(f'/collection/tracks?offset={offset}&limit=48').json()
            self.assertEqual((page['channel'], page['total'], page['baseTotal']), ('browse', V1_COUNT, V1_COUNT))
            seen.extend(t['id'] for t in page['rows'])
        self.assertEqual(seen, [t['id'] for t in self.catalog['tracks']])
        last_page = (V1_COUNT - 1) // 12 * 12
        last = client.get(f'/collection/tracks?offset={last_page}&limit=12').json()
        self.assertEqual(len(last['rows']), V1_COUNT - last_page)  # the last page: at 1,992 rows, 12 rows on page 166 of 166
        rows = client.get(f'/collection/tracks?rows={V1_COUNT - 1},0,7,0').json()['rows']
        self.assertEqual([t['row'] for t in rows], [V1_COUNT - 1, 0, 7])
        for bad in ['limit=49', 'offset=-1', f'rows={V1_COUNT}', 'rows=1&offset=0', 'q=' + 'x' * 513, 'unknown=1', 'preview=yes',
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
        v1 = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=V1_COUNT))
        reference = HNSW(json.loads(v1.assets['index']), v1.assets['vectors'])
        for row in (0, 5, 777, V1_COUNT - 1):
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
        self.assertEqual((manifest['format'], manifest['releaseSha256'], manifest['count']), (2, self.sha, V1_COUNT))
        for name in ('catalog.json', 'vectors.f32', 'index.json', 'ids.json', 'artist-records.json'):
            self.assertFalse((self.web / 'search-studio/data' / name).exists(), name)
        layout = json.loads((self.web / 'search-studio/data/layout.json').read_bytes())
        self.assertEqual((layout['schemaVersion'], layout['kind'], layout['links']), (3, 'music-layout-lod-v2', '/collection/links'))
        self.assertEqual((layout['sampleCount'], layout['rows']), (V1_COUNT, list(range(V1_COUNT))))
        self.assertNotIn('weights', layout)
        self.assertNotIn('edges', layout)  # stored links are read per recording from /collection/links
        self.assertIsNone(layout['tiles'])  # every position is pinned at this size
        # Region labels: honest majorities of source genres, two zoom levels, every recording in one area per level.
        genres = [t.get('genre') for t in self.catalog['tracks']]
        self.assertEqual([(lv['clusters'], lv['fromDetail'], lv['toDetail']) for lv in layout['regions']['levels']], [(12, 0, 2), (48, 2, 10)])
        for level in layout['regions']['levels']:
            self.assertEqual(sum(item['count'] for item in level['items']), V1_COUNT)
            for item in level['items']:
                top, top_count = item['genres'][0] if item['genres'] else (None, 0)
                self.assertAlmostEqual(item['share'], round(top_count / item['count'], 3))
                if item['label'] is not None:
                    self.assertEqual(item['label'], top)
                    self.assertGreaterEqual(top_count / item['count'], 0.4)
                    self.assertNotEqual(item['label'], 'Unknown')
                    self.assertIn(item['label'], genres)
                else:
                    self.assertTrue(top_count / item['count'] < 0.4 or top == 'Unknown')
        self.assertGreater(sum(item['label'] is not None for item in layout['regions']['levels'][0]['items']), 3)
        examples = json.loads((self.web / 'search-studio/data/examples.json').read_bytes())
        v1 = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=V1_COUNT))
        reference = HNSW(json.loads(v1.assets['index']), v1.assets['vectors'])
        for item, source in zip(examples['examples'], json.loads((V1_DIR / 'examples.json').read_bytes())['examples']):
            query = np.asarray(source['queryVector'], dtype=np.float32)
            self.assertEqual([(r['row'], r['cosineSimilarity']) for r in item['results']],
                             [(e['id'], 1 - e['distance']) for e in reference.exact_search(query, k=16)])
            self.assertEqual(item['trace'], reference.search(query, k=16, ef=32, trace=True, trace_limit=2048)['trace'])
        sizes = self.web_receipt['webDataBytes']
        self.assertLess(sizes, 2_000_000)

    def test_credits_are_read_page_by_page_with_the_static_page_content(self):
        import re
        client = self.client(self.v2_encoder, gateway=True)
        # The checked-in static page. notices/ keeps it in every tree; web/notices/ holds the small v2 page
        # once a v2 release is activated (scripts/activate_release_v2.py).
        static = (ROOT / 'notices/track-attribution.html').read_text()
        expected = re.findall(r'<article id=.*?</article>', static, flags=re.S)
        intro = re.search(r'<h1>.*?</p>', static, flags=re.S).group(0)
        # The paged credits and the static generator state the same exclusions (the rights quarantine list).
        from build_corpus_credits import QUARANTINE_EXCLUSION
        from collection_v2 import CREDITS_EXCLUSIONS
        self.assertEqual(CREDITS_EXCLUSIONS, QUARANTINE_EXCLUSION)
        self.assertIn(QUARANTINE_EXCLUSION, intro)
        self.assertEqual(len(expected), V1_COUNT)
        pages = -(-V1_COUNT // 50)
        self.assertEqual(pages, 40)
        articles = []
        for page in range(1, pages + 1):
            reply = client.get(f'/collection/credits?page={page}')
            self.assertEqual(reply.status_code, 200)
            self.assertTrue(reply.headers['content-type'].startswith('text/html'))
            self.assertIn(intro, reply.text)
            self.assertIn(f'Page {page} of {pages} · recordings {(page - 1) * 50 + 1:,}–{min(page * 50, V1_COUNT):,} of {V1_COUNT:,}',
                          reply.text)
            articles += re.findall(r'<article id=.*?</article>', reply.text, flags=re.S)
        self.assertEqual(articles, expected)  # every credit, in catalog order, byte for byte
        first = client.get('/collection/credits').text
        for part in ('<a href="?page=2" rel="next">Next page</a>', '<span class="unavailable">Previous page</span>',
                     '<nav aria-label="Credit pages">', '<label for="credit-page-top">Go to page</label>', '<main id="credits"',
                     '<a class="skip" href="#credits">', '<html lang="en">', '<a href="/search-studio/">',
                     'article{overflow-wrap:anywhere}'):  # long source URLs wrap instead of scrolling the page sideways
            self.assertIn(part, first)
        self.assertIn('Page 40 of 40', client.get('/collection/credits?page=999').text)  # a page jump clamps
        self.assertIn('Page 1 of 40', client.get('/collection/credits?page=0').text)
        first_id = self.catalog['tracks'][0]['id']
        moved = client.get('/collection/credits', params={'id': first_id}, follow_redirects=False)
        self.assertEqual((moved.status_code, moved.headers['location']),
                         (303, '/collection/credits?page=1#' + first_id.replace(':', '-')))
        # Recordings on the rights quarantine list are not in the release, so they have no credit page.
        quarantined = [entry['id'] for entry in json.loads((ROOT / 'corpus-releases/quarantine.json').read_bytes())['entries']]
        self.assertEqual(len(quarantined), 8)
        for ident in quarantined:
            self.assertEqual(client.get('/collection/credits', params={'id': ident}, follow_redirects=False).status_code, 404, ident)
        row = 1234
        ident = self.catalog['tracks'][row]['id']
        moved = client.get('/collection/credits', params={'id': ident}, follow_redirects=False)
        self.assertEqual(moved.headers['location'], f'/collection/credits?page={row // 50 + 1}#' + ident.replace(':', '-'))
        self.assertIn('<article id="' + ident.replace(':', '-') + '">', client.get(moved.headers['location']).text)
        for bad, status in (('id=fma:999999999', 404), ('id=../x', 400), ('page=x', 400), ('page=1&id=fma:1382', 400),
                            ('other=1', 400)):
            with self.subTest(bad=bad):
                reply = client.get('/collection/credits?' + bad)
                self.assertEqual(reply.status_code, status)
                self.assertTrue(reply.headers['content-type'].startswith('text/html'))
        # The v2 package carries a small page that links to the paged credits instead of every credit.
        small = (self.web / 'notices/track-attribution.html').read_bytes()
        self.assertLess(len(small), 4096)
        self.assertIn(b'href="/collection/credits"', small)
        self.assertIn(intro.encode(), small)
        self.assertEqual(self.web_receipt['credits']['pagedAt'], '/collection/credits')

    def test_a_sampled_overview_pins_the_tile_pyramid_and_builds_deterministically(self):
        from build_web_v2 import build
        from collection_v2 import TILE_CAP, TILE_MAX_LEVEL, tile_domain
        outputs = []
        for name in ('one', 'two'):
            root = Path(self.temp.name) / ('sampled-' + name)
            (root / 'search-studio/src').mkdir(parents=True)
            receipt = build(self.dir, self.sha, root, sample_cap=500)
            outputs.append((root / 'search-studio/data/layout.json').read_bytes())
        layout = json.loads(outputs[0])
        self.assertEqual(outputs[0], outputs[1])
        self.assertLessEqual(layout['sampleCount'], 500)
        self.assertEqual(sum(layout['weights']), V1_COUNT)
        self.assertEqual(layout['tiles'], {'api': '/collection/tiles', 'domain': tile_domain(self.release.manifest['layout']['bounds']),
                                           'cap': TILE_CAP, 'maxLevel': TILE_MAX_LEVEL})
        self.assertTrue(receipt['tiles'])

    def tile(self, client, z, x, y):
        reply = client.get(f'/collection/tiles?z={z}&x={x}&y={y}')
        self.assertEqual(reply.status_code, 200)
        body = reply.json()
        rows = np.frombuffer(base64.b64decode(body['rows']), dtype='<u4')
        xy = np.frombuffer(base64.b64decode(body['xy']), dtype='<f4').reshape(-1, 2)
        weights = None if body['weights'] is None else np.frombuffer(base64.b64decode(body['weights']), dtype='<u4')
        return body, rows, xy, weights

    def test_map_tiles_partition_the_layout_and_sample_fuller_tiles(self):
        from collection_v2 import tile_domain
        client = self.client(self.v2_encoder, gateway=True)
        layout = np.asarray(self.release.layout)
        leaves, stack, payloads = [], [(0, 0, 0)], {}
        while stack:
            z, x, y = stack.pop()
            body, rows, xy, weights = self.tile(client, z, x, y)
            payloads[f'{z}/{x}/{y}'] = body
            self.assertEqual(body['domain'], tile_domain(self.release.manifest['layout']['bounds']))
            self.assertEqual((len(rows), body['count']), (len(xy), len(rows)))
            self.assertTrue(np.array_equal(xy, layout[rows]), 'tile positions are the release layout')
            self.assertTrue(np.all(np.diff(rows.astype(np.int64)) > 0), 'rows in catalog order')
            if body['complete']:
                self.assertEqual(body['total'], len(rows))
                leaves.append(rows)
            else:
                self.assertLessEqual(len(rows), body['cap'])
                self.assertEqual(int(weights.sum()), body['total'])
                stack.extend((z + 1, 2 * x + i, 2 * y + j) for i in (0, 1) for j in (0, 1))
        every = np.sort(np.concatenate(leaves))
        self.assertTrue(np.array_equal(every, np.arange(V1_COUNT)), 'complete tiles partition the catalog')
        for bad in ('z=13&x=0&y=0', 'z=1&x=2&y=0', 'z=1&x=0', 'z=0&x=0&y=0&row=1', 'z=-1&x=0&y=0'):
            with self.subTest(bad=bad):
                self.assertEqual(client.get('/collection/tiles?' + bad).status_code, 400)
        # The page's emulation (tests/v2_web_fixture.mjs) cuts every tile exactly as the server does; the page's
        # TileField children of complete tiles are checked against that emulation in web_map_lod.test.mjs.
        node = shutil.which('node')
        if node:
            keys = json.dumps(sorted(payloads))
            script = ("import {tiles} from './tests/v2_web_fixture.mjs';"
                      "console.log(JSON.stringify(Object.fromEntries(JSON.parse(process.argv[1]).map(k=>[k,tiles.tile(...k.split('/').map(Number))]))));")
            emulated = json.loads(subprocess.run([node, '--input-type=module', '-e', script, keys], cwd=ROOT, capture_output=True,
                                                 text=True, check=True).stdout)
            self.assertEqual(emulated, payloads)

    def test_stored_links_are_the_graph_links_of_one_recording(self):
        client = self.client(self.v2_encoder, gateway=True)
        index = json.loads((V1_DIR / 'index.json').read_bytes())
        for row in (0, 5, 777, V1_COUNT - 1):
            body = client.get(f'/collection/links?row={row}').json()
            self.assertEqual((body['row'], body['levels']), (row, index['links'][row]))
            self.assertEqual((body['graphId'], body['indexSha256']), (self.release.graph_id, self.release.graph_sha256))
            linked = sorted({row, *(n for level in index['links'][row] for n in level)})
            self.assertEqual(body['layout']['rows'], linked)
            self.assertEqual(body['layout']['xy'], [float(v) for v in np.asarray(self.release.layout)[linked].reshape(-1)])
        for bad in (f'row={V1_COUNT}', 'row=x', 'row=1&z=0', ''):
            with self.subTest(bad=bad):
                self.assertEqual(client.get('/collection/links?' + bad).status_code, 400)

    def test_collection_pages_map_tiles_links_and_credits_have_their_own_budgets(self):
        from collection_v2 import CollectionRoutes
        from web_gateway import SECURITY_HEADERS
        routes = CollectionRoutes(self.release, None, audio=AudioDeliveryV2(None, self.release), headers=SECURITY_HEADERS,
                                  tracks_per_minute=2, rows_per_minute=2, tiles_per_minute=2, links_per_minute=1,
                                  credits_per_minute=1)
        self.addCleanup(routes.close)
        client = TestClient(routes, base_url=ORIGIN)
        # Collection pages and lookups spend the CPU that the anonymous search budget counts, so they are capped too.
        self.assertEqual([client.get('/collection/tracks?limit=12').status_code for _ in range(2)], [200, 200])
        refused = client.get('/collection/tracks?q=a&preview=1')
        self.assertEqual((refused.status_code, refused.json()['error']), (429, 'Collection page budget exhausted'))
        self.assertTrue(1 <= int(refused.headers['retry-after']) <= 61)
        # Explicit rows (the map's hover and click reads) have a cap of their own, so they still answer (F1).
        self.assertEqual([client.get('/collection/tracks?rows=1').status_code for _ in range(3)], [200, 200, 429])
        refused = client.get('/collection/tracks?rows=2,3')
        self.assertEqual((refused.status_code, refused.json()['error']), (429, 'Collection row budget exhausted'))
        self.assertTrue(1 <= int(refused.headers['retry-after']) <= 61)
        self.assertEqual((routes.tracks_budget.per_minute, routes.rows_budget.per_minute), (2, 2))
        defaults = CollectionRoutes.__init__.__kwdefaults__
        self.assertEqual((defaults['tracks_per_minute'], defaults['rows_per_minute']), (300, 600))
        self.assertEqual([client.get('/collection/tiles?z=0&x=0&y=0').status_code for _ in range(3)], [200, 200, 429])
        refused = client.get('/collection/tiles?z=1&x=0&y=0')
        self.assertEqual((refused.status_code, refused.json()['error']), (429, 'Map tile budget exhausted'))
        self.assertEqual([client.get('/collection/links?row=1').status_code for _ in range(2)], [200, 429])
        self.assertEqual([client.get('/collection/credits?page=1').status_code for _ in range(2)], [200, 429])
        self.assertTrue(client.get('/collection/credits?page=1').headers['content-type'].startswith('text/html'))

    def test_explicit_row_reads_never_spend_the_page_budget(self):
        """Scale UI review F1: hovering and clicking the v2 map read rows with /collection/tracks?rows=. With the default
        caps, a burst of row reads up to theirs (600 a minute) leaves every page read answering 200, and a spent page
        budget leaves row reads answering."""
        from collection_v2 import CollectionRoutes
        routes = CollectionRoutes(self.release, None, audio=AudioDeliveryV2(None, self.release), headers=SECURITY_HEADERS)
        self.addCleanup(routes.close)
        client = TestClient(routes, base_url=ORIGIN)
        cap = routes.rows_budget.per_minute
        self.assertEqual((cap, routes.tracks_budget.per_minute), (600, 300))
        # Single rows (a hover) and full reads of 64 distinct rows (the most one request may ask for), alternately.
        wide = lambda n: ','.join(str((n * 64 + k) % V1_COUNT) for k in range(64))  # noqa: E731
        statuses = [client.get('/collection/tracks?rows=' + (str(n % V1_COUNT) if n % 2 else wide(n))).status_code
                    for n in range(cap)]
        self.assertEqual(statuses, [200] * cap)
        refused = client.get('/collection/tracks?rows=5')
        self.assertEqual((refused.status_code, refused.json()['error']), (429, 'Collection row budget exhausted'))
        self.assertTrue(1 <= int(refused.headers['retry-after']) <= 61)
        pages = ['limit=12&facets=1', 'offset=12&limit=12', 'offset=1980&limit=12', 'q=love&facets=1', 'q=love&offset=12',
                 'text=piano', 'genre=Folk', 'q=the&text=night&preview=1']
        for query in pages:
            with self.subTest(query=query):
                reply = client.get('/collection/tracks?' + query)
                self.assertEqual(reply.status_code, 200, reply.text)
                self.assertIn(reply.json()['channel'], ('browse', 'lookup'))
        self.assertEqual(len(routes.tracks_budget.events), len(pages))
        # The other way round: pages spent, a hover or a click still reads its row.
        spent = CollectionRoutes(self.release, None, audio=AudioDeliveryV2(None, self.release), headers=SECURITY_HEADERS,
                                 tracks_per_minute=1)
        self.addCleanup(spent.close)
        other = TestClient(spent, base_url=ORIGIN)
        self.assertEqual([other.get('/collection/tracks?limit=12').status_code for _ in range(2)], [200, 429])
        reply = other.get('/collection/tracks?rows=7')
        self.assertEqual((reply.status_code, [row['row'] for row in reply.json()['rows']]), (200, [7]))

    def test_gateway_refuses_a_web_package_built_for_another_release(self):
        broken = Path(self.temp.name) / 'broken'
        shutil.copytree(self.web, broken)
        (broken / 'search-studio/data/catalog.json').write_bytes(b'{}')
        from v2_fixtures import web_manifest_for
        manifest = broken.parent / 'broken-manifest.json'
        manifest.write_text(json.dumps(web_manifest_for(broken)))
        with self.assertRaisesRegex(ValueError, 'must not ship the whole catalog.json'):
            WebGateway(object(), web_root=broken, web_manifest=manifest, catalog_path=None, release_v2=self.release)
        other = Path(self.temp.name) / 'other-release'
        shutil.copytree(self.web, other)
        page = json.loads((other / 'search-studio/data/manifest.json').read_bytes())
        page['releaseSha256'] = '0' * 64  # built for a different release
        (other / 'search-studio/data/manifest.json').write_text(json.dumps(page))
        manifest = other.parent / 'other-manifest.json'
        manifest.write_text(json.dumps(web_manifest_for(other)))
        with self.assertRaisesRegex(ValueError, 'does not match the active v2 release'):
            WebGateway(object(), web_root=other, web_manifest=manifest, catalog_path=None, release_v2=self.release)
        if json.loads((ROOT / 'web/search-studio/data/manifest.json').read_bytes()).get('format') != 2:
            with self.assertRaisesRegex(ValueError, 'does not match the active v2 release'):  # a v1 page package
                WebGateway(object(), web_root=ROOT / 'web', web_manifest=ROOT / 'web-manifest.json', catalog_path=None,
                           release_v2=self.release)

    def test_v1_gateway_keeps_its_routes_and_headers(self):
        # The v1 release catalog is byte-identical to the v1 page's, and stays in the tree after v2 activation.
        gateway = WebGateway(object(), web_root=ROOT / 'web', web_manifest=ROOT / 'web-manifest.json',
                             catalog_path=V1_DIR / 'catalog.json')
        self.assertIsNone(gateway.collection)
        self.assertIs(gateway.headers, SECURITY_HEADERS)
        client = TestClient(gateway, base_url=ORIGIN)
        self.assertEqual(client.get('/collection/tracks').status_code, 404)

    def test_collection_reads_are_budgeted_and_refused_rather_than_queued(self):
        from collection_v2 import Budget
        clock = [0.0]
        budget = Budget(2, monotonic=lambda: clock[0])
        self.assertEqual([budget.admit(), budget.admit()], [0, 0])
        self.assertGreater(budget.admit(), 0)
        clock[0] = 61.0
        self.assertEqual(budget.admit(), 0)
        client = self.client(self.v2_encoder, gateway=True)
        routes = client.app.collection
        routes.neighbors_budget = Budget(1)
        self.assertEqual(client.get('/collection/neighbors?row=3').status_code, 200)
        refused = client.get('/collection/neighbors?row=4')
        self.assertEqual((refused.status_code, refused.json()['error']), (429, 'Neighbor exploration budget exhausted'))
        self.assertGreater(int(refused.headers['retry-after']), 0)
        routes.pending = routes.max_pending  # simulate a full queue: excess reads are refused, never queued
        full = client.get('/collection/tracks?limit=12')
        self.assertEqual((full.status_code, full.json()['error'], full.headers['retry-after']), (429, 'Collection queue full', '1'))
        routes.pending = 0
        self.assertEqual(client.get('/collection/tracks?limit=12').status_code, 200)

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
                         (False, 'disabled', V1_COUNT, self.sha))
        self.assertEqual(client.get('/audio/' + Path(self.catalog['tracks'][0]['audio']).name).status_code, 404)
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
