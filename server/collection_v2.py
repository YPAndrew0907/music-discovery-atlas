"""Release-format-v2 public collection reads and audio delivery, served by WebGateway.

The page no longer downloads the catalog, vectors or index. It reads bounded pages and
search packets from these GET routes instead; the data is the same public catalog metadata
the v1 page fetched as static files, so no bearer credential applies. Every route validates
its parameters, bounds its output, runs SQLite/numpy work off the event loop and refuses
excess work with 429 instead of queueing it.

Audio previews keep the v1 delivery contract (an explicit, catalog-bound manifest; unlisted
rows are "Preview unavailable") with two v2 modes:
* local: files in one configured directory. Startup checks the inventory and sizes only;
  each file's SHA-256 is verified on its first request and re-verified whenever its
  (device, inode, size, mtime) identity changes. Nothing re-hashes the pack at startup.
* remote: content-addressed objects on one pinned HTTPS origin, verified at publish time by
  the operator's verifier. The server never fetches or serves them.
"""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qsl, urlsplit

from corpus_release import ReleaseError, require, strict_json, valid_sha
from release_v2 import page_query

LOCAL_ROUTE = re.compile(r'/audio/([0-9]{6})\.mp3\Z')
REMOTE_PATH = re.compile(r'(/(?:[A-Za-z0-9_-]{1,64}/){0,4})([a-f0-9]{64})\.mp3\Z')
FMA_ID = re.compile(r'fma:([0-9]{1,6})\Z')
DELIVERY_KIND = 'music-audio-delivery-v2'


def local_route(track_id):
    match = FMA_ID.fullmatch(track_id)
    return '/audio/%06d.mp3' % int(match.group(1)) if match else None


def exact_https_origin(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    require(parsed is not None and parsed.scheme == 'https' and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
            and value == f'https://{parsed.netloc}' and '*' not in value, 'Audio origin must be an exact HTTPS origin')
    parsed.port  # rejects malformed ports
    return value


class AudioDeliveryV2:
    """Same interface as web_gateway.AudioDelivery: .manifest, .paths, .resolve(route)."""

    def __init__(self, manifest_path, release, *, enabled=False, directory=None, max_manifest_bytes=64_000_000):
        self.release = release
        pins = release.audio_pins()
        self.total = len(pins)
        base = {'schemaVersion': 2, 'kind': DELIVERY_KIND, 'catalogId': release.catalog_id,
                'catalogSha256': release.catalog_sha256, 'releaseSha256': release.manifest_sha256}
        self.disabled = {**base, 'enabled': False, 'publicDeliveryVerified': False, 'mode': 'disabled',
                         'available': 0, 'total': self.total, 'availableRows': '',
                         'unlistedTrackBehavior': 'Preview unavailable'}
        self.manifest, self.paths, self.specs, self.directory = self.disabled, {}, {}, None
        self.origin = self.path_prefix = None
        self._verified, self._lock = {}, threading.Lock()
        self.stats = {'firstRequestHashes': 0, 'identityRechecks': 0}
        if not enabled:
            return
        path = Path(manifest_path)
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= max_manifest_bytes,
                'Audio delivery manifest is missing or oversized')
        source = strict_json(path.read_bytes(), 'audio delivery manifest', max_manifest_bytes)
        require(isinstance(source, dict) and source.get('schemaVersion') == 2 and source.get('kind') == DELIVERY_KIND
                and source.get('enabled') is True and source.get('publicDeliveryVerified') is True
                and all(source.get(key) == value for key, value in base.items())
                and source.get('mode') in ('local', 'remote') and isinstance(source.get('tracks'), list),
                'Audio delivery approval/pin mismatch')
        mode = source['mode']
        if mode == 'remote':
            self.origin = exact_https_origin(source.get('origin'))
            prefix = source.get('pathPrefix')
            require(isinstance(prefix, str) and REMOTE_PATH.fullmatch(prefix + '0' * 64 + '.mp3'),
                    'Invalid remote audio path prefix')
            self.path_prefix = prefix
            require(directory is None, 'Remote audio delivery does not use a local directory')
        else:
            require(directory, 'An explicitly configured local audio directory is required')
            directory = Path(directory)
            require(not directory.is_symlink() and directory.is_dir(), 'Invalid local audio directory')
            self.directory = directory.resolve(strict=True)
        by_id = {ident: (row, size, digest) for row, ident, size, digest in pins}
        available, seen = bytearray((self.total + 7) // 8), set()
        expected_files = {}
        for entry in source['tracks']:
            require(isinstance(entry, dict), 'Invalid audio delivery row')
            ident = entry.get('id')
            require(isinstance(ident, str) and ident in by_id and ident not in seen, 'Unknown or duplicate audio ID')
            seen.add(ident)
            if entry.get('available') is False and entry.get('url') is None:
                continue
            row, size, digest = by_id[ident]
            require(entry.get('available') is True and entry.get('bytes') == size and entry.get('sha256') == digest
                    and type(entry.get('bytes')) is int, 'Unverified audio delivery row')
            if mode == 'local':
                route = local_route(ident)
                require(route is not None and entry.get('url') == route, 'Unverified audio delivery row')
                name = route.rsplit('/', 1)[1]
                expected_files[name] = size
                self.paths[route] = row
                self.specs[route] = {'filename': name, 'bytes': size, 'sha256': digest}
            else:
                require(entry.get('url') == self.origin + self.path_prefix + digest + '.mp3', 'Unverified remote audio URL')
            available[row >> 3] |= 1 << (row & 7)
        count = sum(bin(b).count('1') for b in available)
        require(count > 0, 'Audio delivery lists no available preview')
        if mode == 'local':
            # Inventory and sizes only. Content hashes are verified on first request.
            actual = {}
            with os.scandir(self.directory) as entries:
                for item in entries:
                    require(not item.is_symlink() and item.is_file(follow_symlinks=False),
                            'Audio directory contains missing or unapproved files')
                    actual[item.name] = item.stat(follow_symlinks=False).st_size
            require(actual == expected_files, 'Audio directory contains missing or unapproved files')
        self.manifest = {**base, 'enabled': True, 'publicDeliveryVerified': True, 'mode': mode,
                         **({'origin': self.origin, 'pathPrefix': self.path_prefix} if mode == 'remote' else {}),
                         'available': count, 'total': self.total,
                         'availableRows': base64.b64encode(bytes(available)).decode('ascii'),
                         'unlistedTrackBehavior': 'Preview unavailable',
                         'deliveryVerificationScope': ('Local inventory and sizes checked at startup; each file SHA-256 verified on '
                             'first request and whenever its file identity changes' if mode == 'local' else
                             'Content-addressed objects verified by the operator at publication; served by the pinned origin'),
                         **({'verification': source['verification']} if isinstance(source.get('verification'), dict) else {})}

    def resolve(self, route):
        """Return the verified local path for a route; hash on first use or after any change."""
        spec = self.specs[route]
        path = self.directory / spec['filename']
        info = os.stat(path, follow_symlinks=False)
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if self._verified.get(route) == identity:
            return path
        with self._lock:
            if self._verified.get(route) == identity:
                return path
            self.stats['identityRechecks' if route in self._verified else 'firstRequestHashes'] += 1
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as stream:
                held = os.fstat(stream.fileno())
                require((held.st_dev, held.st_ino, held.st_size, held.st_mtime_ns) == identity
                        and held.st_size == spec['bytes'], 'Audio content changed during verification')
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != spec['sha256']:
                self._verified.pop(route, None)
                raise ValueError('Audio content pin mismatch')
            self._verified[route] = identity
        return path


class Budget:
    """A per-process sliding-minute allowance for CPU-bound collection reads."""

    def __init__(self, per_minute, monotonic=time.monotonic):
        self.per_minute, self.monotonic, self.events, self.lock = per_minute, monotonic, [], threading.Lock()

    def admit(self):
        with self.lock:
            now = self.monotonic()
            self.events = [t for t in self.events if now - t < 60]
            if len(self.events) >= self.per_minute:
                return max(1, int(60 - (now - self.events[0])) + 1)
            self.events.append(now)
            return 0


class CollectionRoutes:
    """GET /collection/tracks and /collection/neighbors over a verified ReleaseV2."""

    def __init__(self, release, graph, *, audio, headers, neighbors_per_minute=60, pending=8,
                 trace_limit=2048, page_limit=48, rows_limit=64):
        # trace_limit 2048 is the v1 page's local default; ef 32 traces hold about 50 events.
        self.release, self.graph, self.audio, self.headers = release, graph, audio, headers
        self._bits = base64.b64decode(audio.manifest['availableRows']) if audio.manifest.get('enabled') else b''
        self.neighbors_budget = Budget(neighbors_per_minute)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='music-collection')
        self.pending, self.max_pending, self.lock = 0, pending, threading.Lock()
        self.trace_limit, self.page_limit, self.rows_limit = trace_limit, page_limit, rows_limit

    def available(self, row):
        return bool(self._bits) and bool(self._bits[row >> 3] >> (row & 7) & 1)

    @staticmethod
    def params(query_string, allowed):
        try:
            pairs = parse_qsl(query_string.decode('ascii'), keep_blank_values=True, strict_parsing=False,
                              max_num_fields=16)
        except (UnicodeDecodeError, ValueError) as error:
            raise ReleaseError('Invalid query string') from error
        values = {}
        for key, value in pairs:
            require(key in allowed and key not in values and len(value.encode()) <= 2048, 'Invalid collection parameter')
            values[key] = value
        return values

    @staticmethod
    def number(values, key, default, low, high):
        value = values.get(key)
        if value is None:
            return default
        require(re.fullmatch(r'[0-9]{1,8}', value) is not None and low <= int(value) <= high, 'Invalid ' + key)
        return int(value)

    def tracks(self, values):
        if 'rows' in values:
            require(set(values) == {'rows'}, 'rows cannot be combined with paging')
            parts = values['rows'].split(',')
            require(1 <= len(parts) <= self.rows_limit and all(re.fullmatch(r'[0-9]{1,7}', p) for p in parts),
                    'Invalid rows')
            rows = list(dict.fromkeys(int(p) for p in parts))
            require(all(row < self.release.count for row in rows), 'Row outside the catalog')
            return {'rows': self.release.display_rows(rows)}
        offset = self.number(values, 'offset', 0, 0, 10_000_000)
        limit = self.number(values, 'limit', 12, 1, self.page_limit)
        flags = {key: values.get(key, '0') for key in ('preview', 'facets')}
        require(all(flag in ('0', '1') for flag in flags.values()), 'Invalid flag')
        text = {key: values.get(key, '') for key in ('q', 'text', 'genre')}
        require(all(len(value) <= 512 for value in text.values()), 'Collection filter exceeds 512 characters')
        return page_query(self.release, offset=offset, limit=limit, query=text['q'], text=text['text'],
                          genre=text['genre'], rows_filter=self.available if flags['preview'] == '1' else None,
                          facets=flags['facets'] == '1')

    def neighbors(self, values):
        require(set(values) == {'row'}, 'Expected row')
        row = self.number(values, 'row', None, 0, self.release.count - 1)
        from search_v2 import label_rows, trace_rows
        started = time.perf_counter()
        query = self.release.vectors[row]
        exact = self.graph.exact_search(query, k=16, exclude_id=row)
        exact_at = time.perf_counter()
        traced = self.graph.search(query, k=17, ef=32, trace=True, trace_limit=self.trace_limit)
        finished = time.perf_counter()
        ranked = [item['id'] for item in exact]
        labels = [r for r in label_rows(traced['trace']) if r not in ranked and r != row]
        return {'schemaVersion': 1, 'kind': 'audio-neighbors', 'catalogId': self.release.catalog_id,
                'graphId': self.release.graph_id, 'indexSha256': self.release.graph_sha256,
                'releaseSha256': self.release.manifest_sha256, 'row': row, 'id': self.release.ordered_ids[row],
                'rankingAlgorithm': 'exact-cosine-js-order-v1',
                'results': [{'rank': rank, 'row': item['id'], 'id': self.release.ordered_ids[item['id']],
                             'cosineSimilarity': 1 - item['distance'], 'similarityIsProbability': False}
                            for rank, item in enumerate(exact, 1)],
                'trace': traced['trace'], 'tracks': self.release.display_rows([row] + ranked + labels),
                'layout': self.release.positions(trace_rows(traced['trace'], exact) | {row}),
                'timingMs': {'exactSearch': (exact_at - started) * 1000, 'graphTrace': (finished - exact_at) * 1000}}

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse
        path = scope['path']
        routes = {'/collection/tracks': (self.tracks, {'rows', 'offset', 'limit', 'q', 'text', 'genre', 'preview', 'facets'}),
                  '/collection/neighbors': (self.neighbors, {'row'})}
        headers = {**self.headers, 'Cache-Control': 'no-store'}
        if path not in routes:
            await JSONResponse({'error': 'Not found'}, status_code=404, headers=headers)(scope, receive, send)
            return
        handler, allowed = routes[path]
        try:
            values = self.params(scope.get('query_string', b''), allowed)
        except ReleaseError as error:
            await JSONResponse({'error': str(error)}, status_code=400, headers=headers)(scope, receive, send)
            return
        if handler == self.neighbors:
            retry = self.neighbors_budget.admit()
            if retry:
                await JSONResponse({'error': 'Neighbor exploration budget exhausted'}, status_code=429,
                                   headers={**headers, 'Retry-After': str(retry)})(scope, receive, send)
                return
        with self.lock:
            if self.pending >= self.max_pending:
                full = True
            else:
                full, self.pending = False, self.pending + 1
        if full:
            await JSONResponse({'error': 'Collection queue full'}, status_code=429,
                               headers={**headers, 'Retry-After': '1'})(scope, receive, send)
            return
        try:
            body = await asyncio.get_running_loop().run_in_executor(self.executor, handler, values)
            status = 200
        except ReleaseError as error:
            body, status = {'error': str(error)}, 400
        except Exception:
            body, status = {'error': 'Collection read failed'}, 500
        finally:
            with self.lock:
                self.pending -= 1
        await JSONResponse(body, status_code=status, headers=headers)(scope, receive, send)

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)


def check_web_release(web_root, assets, release):
    """A v2 deployment must serve the web data built for this exact release."""
    path = assets.get('search-studio/data/manifest.json')
    require(path is not None, 'The v2 web package lacks its collection manifest')
    manifest = json.loads(Path(path).read_bytes())
    require(isinstance(manifest, dict) and manifest.get('format') == 2
            and manifest.get('releaseSha256') == release.manifest_sha256
            and manifest.get('catalogId') == release.catalog_id and manifest.get('graphId') == release.graph_id
            and manifest.get('indexSha256') == release.graph_sha256
            and manifest.get('catalogSha256') == release.catalog_sha256
            and manifest.get('vectorsSha256') == release.vectors_sha256, 'Web package does not match the active v2 release')
    for name in ('catalog.json', 'vectors.f32', 'index.json', 'ids.json'):
        require('search-studio/data/' + name not in assets, 'A v2 web package must not ship the whole ' + name)
    return manifest
