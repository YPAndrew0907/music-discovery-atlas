"""Deliberately small anonymous preview boundary; no model, audio or sessions."""
import asyncio
from collections import deque
from dataclasses import dataclass
import json
import math
import re
import time
from urllib.parse import urlsplit

UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')


@dataclass(frozen=True)
class PublicLimits:
    body_bytes: int = 4096
    body_seconds: float = 2.0
    text_characters: int = 512
    text_bytes: int = 2048
    searches_per_minute: int = 6
    searches_per_hour: int = 30
    controls_per_minute: int = 60
    process_cpu_seconds_per_hour: float = 30.0
    request_seconds: float = 8.0
    pending: int = 2  # One executing inference and at most one waiting.


def exact_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or value != f'https://{parsed.netloc}'
            or any(c.isspace() for c in value) or '*' in value):
        raise ValueError('An exact HTTPS public origin without a path is required')
    # Accessing port also rejects malformed/out-of-range ports.
    parsed.port
    return value, parsed.netloc.lower()


class PreviewBudget:
    """One-process, volatile bounds. No IP, user, token or query is retained."""
    def __init__(self, limits, monotonic=time.monotonic, cpu=time.process_time):
        self.limits, self.monotonic, self.cpu = limits, monotonic, cpu
        self.hour_started, self.cpu_started = monotonic(), cpu()
        self.search_count = 0
        self.searches, self.controls = deque(), deque()

    def refresh(self):
        now = self.monotonic()
        if now - self.hour_started >= 3600:
            self.hour_started, self.cpu_started, self.search_count = now, self.cpu(), 0
        for entries in (self.searches, self.controls):
            while entries and now - entries[0] >= 60:
                entries.popleft()
        return now

    def cpu_exhausted(self):
        self.refresh()
        return self.cpu() - self.cpu_started >= self.limits.process_cpu_seconds_per_hour

    def admit(self, search):
        now = self.refresh()
        entries = self.searches if search else self.controls
        limit = self.limits.searches_per_minute if search else self.limits.controls_per_minute
        if len(entries) >= limit:
            return max(1, math.ceil(60 - (now - entries[0])))
        if search and (self.search_count >= self.limits.searches_per_hour or self.cpu_exhausted()):
            return max(1, math.ceil(3600 - (now - self.hour_started)))
        entries.append(now)
        if search:
            self.search_count += 1
        return 0


class PublicBoundary:
    def __init__(self, app, origin, limits=None, budget=None):
        self.app = app
        self.origin, self.host = exact_origin(origin)
        self.limits = limits or PublicLimits()
        self.budget = budget or PreviewBudget(self.limits)
        self.audio_enabled = False

    def cancel_if_over_budget(self):
        if self.budget.cpu_exhausted():
            for state in tuple(getattr(self.app.state, 'active', {}).values()):
                state['control'].cancel()

    async def watch_budget(self):
        while True:
            self.cancel_if_over_budget()
            await asyncio.sleep(0.25)

    async def error(self, send, status, message, retry=0):
        body = json.dumps({'error': message}, separators=(',', ':')).encode()
        headers = [(b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                   (b'x-content-type-options', b'nosniff'), (b'content-length', str(len(body)).encode())]
        if retry:
            headers.append((b'retry-after', str(retry).encode()))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': body})

    async def manifest(self, scope, receive, send):
        start, chunks = None, []
        async def capture(message):
            nonlocal start
            if message['type'] == 'http.response.start':
                start = message
            elif message['type'] == 'http.response.body':
                chunks.append(message.get('body', b''))
        await self.app(scope, receive, capture)
        raw = b''.join(chunks)
        if start is None or start['status'] != 200 or len(raw) > 32768:
            await self.error(send, 503, 'Preview manifest unavailable')
            return
        data = json.loads(raw)
        data['maxQueryUtf8Bytes'] = self.limits.text_bytes
        data['defaults']['traceLimit'] = 128
        data['publicPreview'] = {'anonymous': True, 'origin': self.origin,
            'maxQueryCharacters': self.limits.text_characters,
            'maxBodyBytes': self.limits.body_bytes, 'maxPendingRequests': self.limits.pending,
            'searchesPerMinute': self.limits.searches_per_minute,
            'searchesPerProcessHour': self.limits.searches_per_hour,
            'processCpuSecondsPerHour': self.limits.process_cpu_seconds_per_hour,
            'requestTimeoutSeconds': self.limits.request_seconds,
            'limitsResetOnProcessRestart': True, 'audioEnabled': self.audio_enabled}
        data['privacy'] = ('Descriptions are sent to this service for inference. '
            'Application query/access logging is disabled; host infrastructure metadata may be retained.')
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        start = {**start, 'headers': [(k, v) for k, v in start['headers'] if k.lower() != b'content-length']
                 + [(b'content-length', str(len(raw)).encode())]}
        await send(start)
        await send({'type': 'http.response.body', 'body': raw})

    def validate(self, value, search):
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        fields = {'requestId', 'generation'}
        if search:
            fields |= {'query', 'engineId', 'catalogId', 'deploymentGeneration', 'k', 'ef', 'trace', 'traceLimit'}
        if set(value) - fields:
            raise ValueError('Unknown request field')
        if not isinstance(value.get('requestId'), str) or not UUID4.fullmatch(value['requestId']):
            raise ValueError('A lowercase UUIDv4 requestId is required')
        if type(value.get('generation')) is not int or not 0 <= value['generation'] <= 2**53 - 1:
            raise ValueError('Invalid generation')
        if search:
            text = value.get('query')
            if (not isinstance(text, str) or not text.strip() or len(text) > self.limits.text_characters
                    or len(text.encode()) > self.limits.text_bytes):
                raise ValueError('Description exceeds the public preview limit')
            for name, default, lower, upper in [('k', 8, 1, 16), ('ef', 32, 1, 32), ('traceLimit', 128, 0, 128)]:
                value.setdefault(name, default)
                if type(value[name]) is not int or not lower <= value[name] <= upper:
                    raise ValueError('Invalid ' + name)
            if value['ef'] < value['k']:
                raise ValueError('ef must be at least k')
            value.setdefault('trace', False)
            if type(value['trace']) is not bool:
                raise ValueError('Invalid trace')
        return value

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            watchdog = None
            async def lifespan_send(message):
                nonlocal watchdog
                if message['type'] == 'lifespan.startup.complete':
                    watchdog = asyncio.create_task(self.watch_budget())
                await send(message)
            try:
                await self.app(scope, receive, lifespan_send)
            finally:
                if watchdog:
                    watchdog.cancel()
                    await asyncio.gather(watchdog, return_exceptions=True)
            return
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            return
        path, method = scope['path'], scope['method']
        expected = {'/v1/manifest': 'GET', '/v1/search': 'POST', '/v1/cancel': 'POST'}
        if path not in expected:
            await self.error(send, 404, 'Unknown route')
            return
        if method != expected[path]:
            await self.error(send, 405, 'Method not allowed')
            return
        multi = {}
        for key, val in scope.get('headers', []):
            multi.setdefault(key.lower(), []).append(val)
        critical = (b'host', b'origin', b'content-type', b'content-length', b'content-encoding', b'sec-fetch-site')
        if any(len(multi.get(key, [])) > 1 for key in critical):
            await self.error(send, 400, 'Duplicate request header')
            return
        headers = {k: v[0].decode('latin-1') for k, v in multi.items()}
        origin = headers.get(b'origin')
        fetch_site = headers.get(b'sec-fetch-site')
        if (headers.get(b'host', '').lower() != self.host or (origin is not None and origin != self.origin)
                or (method == 'POST' and origin != self.origin)
                or (fetch_site is not None and fetch_site != 'same-origin')):
            await self.error(send, 403, 'Same-origin preview requests only')
            return
        search = path == '/v1/search'
        # Controls have a separate allowance so search exhaustion cannot block cancellation.
        retry = self.budget.admit(search)
        if retry:
            self.cancel_if_over_budget()
            await self.error(send, 429, 'Public preview budget exhausted', retry)
            return
        if method == 'GET':
            await self.manifest(scope, receive, send)
            return
        if (headers.get(b'content-type', '').lower().split(';')[0].strip() != 'application/json'
                or headers.get(b'content-encoding', 'identity').lower() != 'identity'):
            await self.error(send, 415, 'Uncompressed application/json is required')
            return
        declared = headers.get(b'content-length')
        if declared is not None and (not declared.isascii() or not declared.isdigit()):
            await self.error(send, 400, 'Invalid content length')
            return
        if declared is not None and (len(declared) > 10 or int(declared) > self.limits.body_bytes):
            await self.error(send, 413, 'Request body limit')
            return
        parts, total = [], 0
        try:
            async with asyncio.timeout(self.limits.body_seconds):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        await self.error(send, 499, 'Request disconnected')
                        return
                    if message['type'] != 'http.request':
                        raise ValueError('Unexpected request message')
                    part = message.get('body', b'')
                    total += len(part)
                    if total > self.limits.body_bytes:
                        await self.error(send, 413, 'Request body limit')
                        return
                    parts.append(part)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            await self.error(send, 408, 'Request body deadline exceeded')
            return
        except ValueError:
            await self.error(send, 400, 'Invalid request body')
            return
        if declared is not None and int(declared) != total:
            await self.error(send, 400, 'Content length mismatch')
            return
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON field')
                result[key] = value
            return result
        def reject_constant(value):
            raise ValueError('Non-finite JSON number')
        try:
            value = json.loads(b''.join(parts), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
            value = self.validate(value, search)
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            await self.error(send, 400, 'Invalid or oversized preview request')
            return
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': raw, 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)
