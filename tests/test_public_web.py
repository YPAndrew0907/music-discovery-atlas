"""Real app/static/config routes plus confined synthetic audio fixtures; no model/GUI/network."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from fastapi.testclient import TestClient
from api import load_graph
from hosting import build_application
from web_gateway import AudioDelivery, WebGateway, confined_file
from test_public_api import FixtureEncoder, ORIGIN, IDS


class PublicWebTests(unittest.TestCase):
    def setUp(self):
        self.encoder = FixtureEncoder()
        self.graph, self.binding = load_graph(self.encoder, str(ROOT / 'music-search-studio/data'))
        self.app = build_application(mode='anonymous-preview', enable_anonymous=True,
            generation='test-web-v1', public_origin=ORIGIN, encoder=self.encoder,
            graph=self.graph, graph_binding=self.binding)
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_real_web_and_same_origin_api(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.url.path, '/search-studio/')
        self.assertIn('text/html', response.headers['content-type'])
        self.assertNotIn('DS4300', response.text)
        for path in ['/search-studio/src/app.mjs', '/search-studio/style.css',
                     '/listen-lab/src/encoder.mjs', '/listen-lab/runtime/ort.wasm.min.mjs',
                     '/notices/track-attribution.html', '/notices/search-privacy.html']:
            result = self.client.get(path)
            self.assertEqual(result.status_code, 200, path)
            self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        manifest = self.client.get('/v1/manifest').json()
        response = self.client.post('/v1/search', headers={'Origin': ORIGIN}, json={
            'requestId': IDS[0], 'generation': 1, 'query': 'calm instrumental music',
            'engineId': manifest['engineId'], 'catalogId': manifest['catalogId'],
            'deploymentGeneration': manifest['deploymentGeneration']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['results'][0]['id'], self.encoder.ids[0])

    def test_explicit_activation_and_disabled_audio_config(self):
        config = self.client.get('/deployment-config.json')
        self.assertEqual(config.headers['cache-control'], 'no-store')
        self.assertTrue(config.json()['enabled'])
        self.assertEqual(config.json()['apiBase'], '/v1/')
        self.assertEqual(config.json()['origin'], ORIGIN)
        self.assertFalse(self.client.get('/deployment-config.json', headers={'Host': 'other.example'}).json()['enabled'])
        audio = self.client.get('/audio-delivery.json').json()
        self.assertFalse(audio['enabled'])
        self.assertFalse(audio['publicDeliveryVerified'])
        if audio.get('schemaVersion') == 2:  # release format v2 serves a compact summary instead of rows
            self.assertEqual((audio['mode'], audio['available'], audio['availableRows']), ('disabled', 0, ''))
        else:
            self.assertEqual(audio['tracks'], [])
        self.assertEqual(self.client.get('/audio/001382.mp3').status_code, 404)
        self.assertEqual(self.client.get('/listen-lab/audio/001382.mp3').status_code, 404)

    def test_authenticated_mode_never_exposes_secret_or_enables_public_api(self):
        token = 'not-a-real-credential-00000000000000'
        app = build_application(token=token, generation='test-private-v1', encoder=self.encoder,
            graph=self.graph, graph_binding=self.binding)
        with TestClient(app, base_url=ORIGIN) as client:
            config = client.get('/deployment-config.json')
            self.assertFalse(config.json()['enabled'])
            self.assertNotIn(token, config.text)
            self.assertEqual(client.get('/v1/manifest').status_code, 401)
            self.assertEqual(client.get('/v1/manifest', headers={'Authorization': 'Bearer ' + token}).status_code, 200)

    def test_secret_and_traversal_routes_are_not_served(self):
        for path in ['/.env', '/.git/config', '/server/hosting.py', '/model/tokenizer.json',
                     '/requirements.lock', '/render.yaml', '/package-manifest.json', '/web-manifest.json',
                     '/search-studio/../../server/hosting.py', '/search-studio/%2e%2e/%2e%2e/server/hosting.py',
                     '/%2fserver/hosting.py', '/search-studio/src/%252e%252e/server/hosting.py']:
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.post('/search-studio/style.css', content='ignored').status_code, 405)

    def test_static_integrity_manifest_rejects_extra_and_symlink_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            # confined_file() returns resolved paths; resolve the fixture root too so the
            # comparison holds where the temp directory is itself behind a symlink (macOS /var).
            root = Path(temp).resolve()
            (root / 'ok.txt').write_text('fixture')
            (root / 'link.txt').symlink_to(root / 'ok.txt')
            self.assertEqual(confined_file(root, 'ok.txt'), root / 'ok.txt')
            for bad in ['../ok.txt', '/ok.txt', '.env', 'a//b', 'link.txt', 'a\\b']:
                with self.assertRaises((OSError, ValueError)):
                    confined_file(root, bad)


class OptionalAudioTests(unittest.TestCase):
    def test_future_pack_requires_explicit_config_and_matching_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            directory = root / 'audio'
            directory.mkdir()
            content = b'ID3 synthetic test fixture, not playable music'
            track = {'id': 'fixture:1', 'audio': 'audio/000001.mp3', 'audioBytes': len(content),
                'audioSha256': hashlib.sha256(content).hexdigest()}
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'id': 'catalog:test', 'tracks': [track]}))
            path = directory / '000001.mp3'
            path.write_bytes(content)
            manifest = root / 'delivery.json'
            record = {'schemaVersion': 1, 'catalogId': 'catalog:test',
                'catalogSha256': hashlib.sha256(catalog.read_bytes()).hexdigest(),
                'enabled': True, 'publicDeliveryVerified': True, 'tracks': [{
                    'id': 'fixture:1', 'available': True, 'url': '/audio/000001.mp3',
                    'bytes': len(content), 'sha256': track['audioSha256']}]}
            manifest.write_text(json.dumps(record))
            disabled = AudioDelivery(manifest, catalog, enabled=False, directory=directory)
            self.assertFalse(disabled.manifest['enabled'])
            self.assertFalse(disabled.paths)
            enabled = AudioDelivery(manifest, catalog, enabled=True, directory=directory)
            self.assertEqual(enabled.paths['/audio/000001.mp3'], path)
            self.assertIn('local file integrity', enabled.manifest['deliveryVerificationScope'])
            self.assertNotIn('sourceUrl', enabled.manifest)
            web = root / 'web'
            (web / 'search-studio').mkdir(parents=True)
            index = web / 'search-studio/index.html'
            index.write_text('<!doctype html><title>Test fixture</title>')
            web_manifest = root / 'web-manifest.json'
            web_manifest.write_text(json.dumps({'schemaVersion': 1, 'kind': 'music-public-web-assets',
                'files': [{'path': 'search-studio/index.html', 'bytes': index.stat().st_size,
                           'sha256': hashlib.sha256(index.read_bytes()).hexdigest()}]}))
            class ReadyAPI:
                ready = True
                async def __call__(self, scope, receive, send):
                    if scope['type'] == 'lifespan':
                        while True:
                            message = await receive()
                            if message['type'] == 'lifespan.startup':
                                await send({'type': 'lifespan.startup.complete'})
                            elif message['type'] == 'lifespan.shutdown':
                                await send({'type': 'lifespan.shutdown.complete'})
                                return
            gateway = WebGateway(ReadyAPI(), web_root=web, web_manifest=web_manifest,
                catalog_path=catalog, audio_manifest=manifest, enable_audio=True, audio_directory=directory)
            with TestClient(gateway, base_url=ORIGIN) as client:
                response = client.get('/audio/000001.mp3')
                self.assertEqual(response.content, content)
                self.assertEqual(response.headers['content-type'], 'audio/mpeg')
                ranged = client.get('/audio/000001.mp3', headers={'Range': 'bytes=0-2'})
                self.assertEqual(ranged.status_code, 206)
                self.assertEqual(ranged.content, b'ID3')
                self.assertEqual(client.get('/audio/not-approved.mp3').status_code, 404)
                path.write_bytes(b'wrong')
                self.assertEqual(client.get('/audio/000001.mp3').status_code, 404)
                path.write_bytes(content)
            record['tracks'][0]['url'] = 'https://unapproved.example/audio.mp3'
            manifest.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                AudioDelivery(manifest, catalog, enabled=True, directory=directory)
            record['tracks'][0]['url'] = '/audio/000001.mp3'
            manifest.write_text(json.dumps(record))
            path.write_bytes(b'wrong')
            with self.assertRaises(ValueError):
                AudioDelivery(manifest, catalog, enabled=True, directory=directory)


if __name__ == '__main__':
    unittest.main()
