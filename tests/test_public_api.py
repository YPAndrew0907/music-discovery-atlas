"""Actual API/graph wiring with a deterministic encoder fixture; no ONNX model load."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import numpy as np
from fastapi.testclient import TestClient
from api import load_graph
from encoder import NativeEncoder
from hosting import build_application
from public_boundary import PublicLimits, PreviewBudget

ORIGIN = 'https://preview.example'
IDS = ['10000000-0000-4000-8000-00000000000' + str(n) for n in range(8)]
DATA = ROOT / 'music-search-studio/data'


class FixtureEncoder:
    def __init__(self):
        manifest = json.loads((DATA / 'manifest.json').read_text())
        self.query_profile = manifest['allowedQueryProfiles'][0]['identity']
        self.engine = manifest['allowedQueryProfiles'][0]['id']
        self.pair = json.loads((ROOT / 'model/model-space-q8.json').read_text())
        self.catalog_version = manifest['catalogId']
        self.catalog_sha = hashlib.sha256((DATA / 'catalog.json').read_bytes()).hexdigest()
        self.ids = json.loads((DATA / 'ids.json').read_text())
        self.audio_receipt = {'vectorsSha256': manifest['vectorsSha256'],
            'executionProfileSha256': manifest['graphIdentity']['audio']['executionProfileSha256']}
        self.vectors = np.frombuffer((DATA / 'vectors.f32').read_bytes(), dtype='<f4').reshape((108, 512))
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_first = False
        self.ignore_cancel_until_release = False

    def manifest(self):
        return NativeEncoder.manifest(self)

    def encode(self, text, control):
        self.calls += 1
        if self.block_first and self.calls == 1:
            self.entered.set()
            # A bounded wait exercises real API admission/cancellation, not model speed.
            until = time.monotonic() + 2
            while not self.release.wait(0.001):
                if not self.ignore_cancel_until_release:
                    control.check()
                if time.monotonic() >= until:
                    raise RuntimeError('Test fixture timed out')
        control.check()
        return self.vectors[0], {'originalTokens': 3, 'usedTokens': 3, 'truncated': False,
            'transform': 'test fixture only'}, {'tokenization': 0.0, 'inferenceAndNormalization': 0.0}


class PublicAPITests(unittest.TestCase):
    def setup_client(self, limits=None, budget=None):
        self.encoder = FixtureEncoder()
        self.graph, self.binding = load_graph(self.encoder, str(DATA))
        self.hosted = build_application(mode='anonymous-preview', enable_anonymous=True,
            generation='test-fixture-v1', public_origin=ORIGIN, encoder=self.encoder,
            graph=self.graph, graph_binding=self.binding, limits=limits, budget=budget, serve_web=False,
            serving=None)  # the legacy fixture catalog has no serving list; tests/test_serving.py covers it
        self.client = TestClient(self.hosted, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(self.encoder.release.set)

    def payload(self, ident=0, **changes):
        return {'requestId': IDS[ident], 'generation': 1, 'query': 'calm instrumental music',
            'engineId': self.encoder.engine, 'catalogId': self.encoder.catalog_version,
            'deploymentGeneration': 'test-fixture-v1', **changes}

    def post(self, path, payload=None, **kwargs):
        headers = {'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-origin', **kwargs.pop('headers', {})}
        return self.client.post(path, json=payload, headers=headers, **kwargs)

    def test_default_and_explicit_opt_in(self):
        with self.assertRaises(ValueError):
            build_application(generation='test-fixture-v1')
        for changes in [{}, {'enable_anonymous': True, 'public_origin': 'http://preview.example'},
                        {'enable_anonymous': True, 'public_origin': 'https://*.example'},
                        {'enable_anonymous': True, 'public_origin': ORIGIN, 'token': 'not-a-real-credential'}]:
            with self.assertRaises(ValueError):
                build_application(mode='anonymous-preview', generation='test-fixture-v1', **changes)

    def test_actual_manifest_search_trace_and_health(self):
        self.setup_client()
        self.assertEqual(self.client.get('/healthz').json(), {'ok': True})
        m = self.client.get('/v1/manifest').json()
        self.assertEqual(m['maxQueryUtf8Bytes'], 2048)
        self.assertEqual(m['publicPreview']['maxPendingRequests'], 2)
        self.assertFalse(m['publicPreview']['audioEnabled'])
        response = self.post('/v1/search', self.payload(trace=True))
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['results'][0]['id'], self.encoder.ids[0])
        self.assertLessEqual(len(data['trace']['events']), 128)
        self.assertNotIn('query', data)
        self.assertNotIn('vector', data)
        self.assertEqual(data['graphId'], self.binding['graphId'])
        self.assertEqual(self.post('/v1/search', self.payload(engineId='stale')).status_code, 409)

    def test_auth_mode_is_preserved(self):
        self.setup_client()
        token = 'not-a-real-credential-00000000000000'
        app = build_application(token=token, generation='test-auth-v1', encoder=self.encoder,
            graph=self.graph, graph_binding=self.binding, serve_web=False, serving=None)
        with TestClient(app) as client:
            self.assertEqual(client.get('/healthz').status_code, 200)
            self.assertEqual(client.get('/v1/manifest').status_code, 401)
            self.assertEqual(client.get('/v1/manifest', headers={'Authorization': 'Bearer ' + token}).status_code, 200)

    def test_fixed_origin_and_host(self):
        self.setup_client()
        self.assertEqual(self.client.post('/v1/search', json=self.payload()).status_code, 403)
        for headers in [{'Origin': 'https://other.example'}, {'Host': 'other.example'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'null'}]:
            response = self.post('/v1/search', self.payload(), headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('access-control-allow-origin', response.headers)
        self.assertEqual(self.client.options('/v1/search', headers={'Origin': ORIGIN}).status_code, 405)
        self.assertEqual(self.encoder.calls, 0)

    def test_json_text_and_body_bounds(self):
        self.setup_client(limits=replace(PublicLimits(), searches_per_minute=60, searches_per_hour=100))
        headers = {'Origin': ORIGIN, 'Content-Type': 'application/json'}
        for body, expected in [(b'x' * 4097, 413), (b'{"query":NaN}', 400),
                               (b'{"requestId":"one","requestId":"two"}', 400), (b'[]', 400)]:
            self.assertEqual(self.client.post('/v1/search', content=body, headers=headers).status_code, expected)
        self.assertEqual(self.post('/v1/search', self.payload(), headers={'Content-Type': 'text/plain'}).status_code, 415)
        self.assertEqual(self.post('/v1/search', self.payload(), headers={'Content-Encoding': 'gzip'}).status_code, 415)
        for change in [{'query': 'x' * 513}, {'traceLimit': 129}, {'k': 17}, {'ef': 33},
                       {'requestId': 'guessable'}, {'generation': True}]:
            self.assertEqual(self.post('/v1/search', self.payload(**change)).status_code, 400)
        raw = json.dumps(self.payload(query='\ud800')).encode()
        self.assertEqual(self.client.post('/v1/search', content=raw, headers=headers).status_code, 400)
        self.assertEqual(self.encoder.calls, 0)

    def test_slow_body_deadline(self):
        self.setup_client(limits=replace(PublicLimits(), body_seconds=0.01))
        async def invoke():
            messages = []
            async def receive():
                await asyncio.sleep(0.1)
                return {'type': 'http.request', 'body': b'{}'}
            async def send(message): messages.append(message)
            await self.hosted({'type': 'http', 'path': '/v1/search', 'method': 'POST',
                'headers': [(b'host', b'preview.example'), (b'origin', ORIGIN.encode()),
                            (b'content-type', b'application/json')]}, receive, send)
            return messages[0]['status']
        self.assertEqual(asyncio.run(invoke()), 408)
        self.assertEqual(self.encoder.calls, 0)

    def test_search_budget_keeps_cancel_available(self):
        self.setup_client(limits=replace(PublicLimits(), searches_per_minute=1))
        self.assertEqual(self.post('/v1/search', self.payload()).status_code, 200)
        response = self.post('/v1/search', self.payload(1))
        self.assertEqual(response.status_code, 429)
        self.assertIn('retry-after', response.headers)
        self.assertEqual(self.post('/v1/cancel', {'requestId': IDS[0], 'generation': 1}).status_code, 200)

    def test_precancel_is_generation_scoped(self):
        self.setup_client()
        self.assertEqual(self.post('/v1/cancel', {'requestId': IDS[0], 'generation': 1}).status_code, 200)
        self.assertEqual(self.post('/v1/search', self.payload()).status_code, 499)
        self.assertEqual(self.post('/v1/search', self.payload(generation=2)).status_code, 200)

    def test_one_running_one_pending_and_real_cancel(self):
        self.setup_client()
        self.encoder.block_first = True
        outcomes = {}
        def run(n): outcomes[n] = self.post('/v1/search', self.payload(n))
        first = threading.Thread(target=run, args=(0,))
        second = threading.Thread(target=run, args=(1,))
        first.start()
        self.assertTrue(self.encoder.entered.wait(1))
        second.start()
        until = time.monotonic() + 1
        api = self.hosted.app.app
        while len(api.state.active) != 2 and time.monotonic() < until:
            time.sleep(0.001)
        self.assertEqual(len(api.state.active), 2)
        self.assertEqual(self.encoder.calls, 1)
        self.assertEqual(self.post('/v1/search', self.payload(2)).status_code, 429)
        ack = self.post('/v1/cancel', {'requestId': IDS[0], 'generation': 1})
        self.assertTrue(ack.json()['cancelled'])
        first.join(1)
        second.join(1)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(outcomes[0].status_code, 499)
        self.assertEqual(outcomes[1].status_code, 200)

    def test_cpu_cutoff_cancels_work_and_refuses_new_search(self):
        cpu = [0.0]
        limits = PublicLimits()
        budget = PreviewBudget(limits, cpu=lambda: cpu[0])
        self.setup_client(limits=limits, budget=budget)
        self.encoder.block_first = True
        outcome = {}
        thread = threading.Thread(target=lambda: outcome.update(response=self.post('/v1/search', self.payload())))
        thread.start()
        self.assertTrue(self.encoder.entered.wait(1))
        cpu[0] = 31.0  # Simulated process-CPU reading; no CPU burn or model run.
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome['response'].status_code, 499)
        self.assertEqual(self.post('/v1/search', self.payload(1)).status_code, 429)
        self.assertEqual(self.post('/v1/cancel', {'requestId': IDS[0], 'generation': 1}).status_code, 200)

    def test_timeout_keeps_slot_until_fixture_really_exits(self):
        self.setup_client(limits=replace(PublicLimits(), request_seconds=0.02))
        self.encoder.block_first = True
        self.encoder.ignore_cancel_until_release = True
        first = self.post('/v1/search', self.payload())
        self.assertEqual(first.status_code, 504)
        api = self.hosted.app.app
        self.assertEqual(len(api.state.active), 1)
        second = self.post('/v1/search', self.payload(1))
        self.assertEqual(second.status_code, 504)
        self.assertEqual(len(api.state.active), 2)
        self.assertEqual(self.post('/v1/search', self.payload(2)).status_code, 429)
        self.encoder.release.set()
        until = time.monotonic() + 1
        while api.state.active and time.monotonic() < until:
            time.sleep(0.001)
        self.assertFalse(api.state.active)
        self.assertEqual(self.post('/v1/search', self.payload(3)).status_code, 200)

    def test_hourly_allowance_and_control_rate(self):
        limits = replace(PublicLimits(), searches_per_hour=1, controls_per_minute=1)
        now = [100.0]
        budget = PreviewBudget(limits, monotonic=lambda: now[0], cpu=lambda: 0.0)
        self.setup_client(limits=limits, budget=budget)
        self.assertEqual(self.post('/v1/search', self.payload()).status_code, 200)
        now[0] += 61
        self.assertEqual(self.post('/v1/search', self.payload(1)).status_code, 429)
        self.assertEqual(self.post('/v1/cancel', {'requestId': IDS[0], 'generation': 1}).status_code, 200)
        self.assertEqual(self.post('/v1/cancel', {'requestId': IDS[1], 'generation': 1}).status_code, 429)
        now[0] += 3600
        self.assertEqual(self.post('/v1/search', self.payload(1)).status_code, 200)


if __name__ == '__main__':
    unittest.main()
