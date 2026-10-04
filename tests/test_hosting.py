"""No dependencies, network, model load, or inference required."""
import asyncio
import importlib.util
import logging
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('hosting', Path(__file__).resolve().parents[1] / 'server/hosting.py')
hosting = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosting)


class HostingTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_does_not_bypass_api_authentication(self):
        calls = []
        async def protected_app(scope, receive, send):
            calls.append(scope['path'])
            await send({'type': 'http.response.start', 'status': 401, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'Unauthorized'})
        gate = hosting.ReadinessGate(protected_app)
        async def invoke(path, method='GET'):
            messages = []
            async def send(message): messages.append(message)
            async def receive(): return {'type': 'http.request', 'body': b''}
            await gate({'type': 'http', 'path': path, 'method': method}, receive, send)
            return messages[0]['status']
        self.assertEqual(await invoke('/healthz'), 503)
        gate.ready = True
        self.assertEqual(await invoke('/healthz'), 200)
        self.assertEqual(await invoke('/healthz', 'POST'), 405)
        for path in ['/health', '/v1/manifest', '/v1/search', '/v1/cancel', '/healthz/']:
            self.assertEqual(await invoke(path), 401)
        self.assertEqual(len(calls), 5)

    async def test_ready_tracks_successful_lifespan(self):
        async def app(scope, receive, send):
            await send({'type': 'lifespan.startup.complete'})
            self.assertTrue(gate.ready)
            await send({'type': 'lifespan.shutdown.complete'})
        gate = hosting.ReadinessGate(app)
        async def send(message): pass
        async def receive(): return {}
        await gate({'type': 'lifespan'}, receive, send)
        self.assertFalse(gate.ready)

    def test_logs_redact_exception_and_message(self):
        record = logging.LogRecord('uvicorn', 40, 'host.py', 1, 'query %s', ('private-sample',), None)
        record.exc_text = 'sensitive traceback'
        self.assertTrue(hosting.SafeLogFilter().filter(record))
        self.assertEqual(record.getMessage(), 'music_service_event')
        self.assertIsNone(record.exc_text)


if __name__ == '__main__':
    unittest.main()
