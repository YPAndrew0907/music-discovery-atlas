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

The map's level of detail is read the same way: /collection/tiles serves the layout as a quadtree of
point tiles (every row of a tile when it holds at most TILE_CAP, otherwise a weighted stratified
sample), and /collection/links serves the stored index links of one recording, so the page draws
links only around the recording in focus.

Track credits are served the same way: /collection/credits renders the static credit page's
articles fifty at a time as plain HTML (no script needed), so a v2 package does not ship a
credits file that grows with the catalog (10 MB at 5,777 rows).
"""
import asyncio
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qsl

import numpy as np

from corpus_release import TRACK_ID, ReleaseError, require, strict_json, valid_sha
from release_v2 import exact_https_origin, page_query

LOCAL_ROUTE = re.compile(r'/audio/([0-9]{6})\.mp3\Z')
REMOTE_PATH = re.compile(r'(/(?:[A-Za-z0-9_-]{1,64}/){0,4})([a-f0-9]{64})\.mp3\Z')
FMA_ID = re.compile(r'fma:([0-9]{1,6})\Z')
DELIVERY_KIND = 'music-audio-delivery-v2'


def local_route(track_id):
    match = FMA_ID.fullmatch(track_id)
    return '/audio/%06d.mp3' % int(match.group(1)) if match else None


class AudioDeliveryV2:
    """Same interface as web_gateway.AudioDelivery: .manifest, .paths, .resolve(route)."""

    def __init__(self, manifest_path, release, *, enabled=False, directory=None, max_manifest_bytes=64_000_000,
                 serving=None):
        # serving: the request-time serving list. A held recording's file still counts in the inventory check,
        # but it gets no route (404) and its availability bit stays clear.
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
                if serving is not None and not serving.serves_row(row):
                    continue
                self.paths[route] = row
                self.specs[route] = {'filename': name, 'bytes': size, 'sha256': digest}
            else:
                require(entry.get('url') == self.origin + self.path_prefix + digest + '.mp3', 'Unverified remote audio URL')
                if serving is not None and not serving.serves_row(row):
                    continue
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


# ---- map tiles -------------------------------------------------------------------------------
TILE_BITS = 16       # positions are ordered along a Morton curve over a 65,536 x 65,536 grid
TILE_CAP = 1024      # points per tile: every row when the tile holds at most this many
TILE_MAX_LEVEL = 12  # 4,096 x 4,096 tiles at the deepest level


def tile_domain(bounds):
    """The square the tile pyramid covers: centred on the layout bounds [x0, y0, x1, y1], with the larger
    side plus a 1/512 margin. build_web_v2.py pins this in layout.json; every tile reply repeats it."""
    x0, y0, x1, y1 = (float(v) for v in bounds)
    side = max(x1 - x0, y1 - y0, 1e-6) * (1 + 1 / 512)
    return [(x0 + x1) / 2 - side / 2, (y0 + y1) / 2 - side / 2, side]


def spread_bits(values):
    """Interleave zeros between the low 16 bits of each value (one axis of a Morton code)."""
    v = np.asarray(values, dtype=np.uint64) & np.uint64(0xFFFF)
    for shift, mask in ((8, 0x00FF00FF), (4, 0x0F0F0F0F), (2, 0x33333333), (1, 0x55555555)):
        v = (v | (v << np.uint64(shift))) & np.uint64(mask)
    return v


def b64(array, dtype):
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode('ascii')


class TileIndex:
    """A quadtree of point tiles over the release layout, from one Morton ordering of the rows.

    Tile (z, x, y) covers the z-level cell (x, y) of tile_domain(); its rows are one contiguous run of the
    Morton order. A tile with at most `cap` rows is complete (every row). A fuller tile is a stratified
    sample, as build_web_v2.sample_layout draws the overview: the lowest row of each occupied cell at the
    finest sub-level whose occupied cells number at most `cap`, weighted by the rows that cell holds.
    Display only: tiles never decide a search result."""

    def __init__(self, layout, bounds, *, cap=TILE_CAP, max_level=TILE_MAX_LEVEL, cache=512, rows=None):
        # rows: the rows the tiles may show (the serving list's served rows); None shows every row.
        self.layout, self.cap, self.max_level = layout, cap, max_level
        self.domain = tile_domain(bounds)
        x0, y0, side = self.domain
        subset = None if rows is None else np.asarray(sorted(rows), dtype=np.int64)
        xy = np.asarray(layout, dtype=np.float64) if subset is None else np.asarray(layout, dtype=np.float64)[subset]
        scale = (1 << TILE_BITS) / side
        top = (1 << TILE_BITS) - 1
        qx = np.clip(np.floor((xy[:, 0] - x0) * scale), 0, top)
        qy = np.clip(np.floor((xy[:, 1] - y0) * scale), 0, top)
        codes = spread_bits(qx) | (spread_bits(qy) << np.uint64(1))
        order = np.argsort(codes, kind='stable')  # rows along the curve; equal codes keep catalog order
        self.order = order if subset is None else subset[order]
        self.codes = codes[order]
        self._cache, self._lock, self._size = OrderedDict(), threading.Lock(), cache

    def tile(self, z, x, y):
        require(0 <= z <= self.max_level and 0 <= x < (1 << z) and 0 <= y < (1 << z), 'Tile outside the map')
        key = (z, x, y)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        shift = np.uint64(2 * (TILE_BITS - z))
        prefix = int(spread_bits(x) | (spread_bits(y) << np.uint64(1)))
        lo = int(np.searchsorted(self.codes, np.uint64(prefix) << shift, 'left'))
        hi = int(np.searchsorted(self.codes, np.uint64(prefix + 1) << shift, 'left'))
        rows, codes, total = self.order[lo:hi], self.codes[lo:hi], hi - lo
        weights = None
        if total <= self.cap:
            chosen = np.sort(rows)
        else:
            starts = None
            for depth in range(1, TILE_BITS - z + 1):
                cells = codes >> np.uint64(2 * (TILE_BITS - z - depth))
                found = np.flatnonzero(np.r_[True, cells[1:] != cells[:-1]])
                if len(found) > self.cap:
                    break
                starts = found
            lowest, counts = np.minimum.reduceat(rows, starts), np.diff(np.r_[starts, total])
            ranked = np.argsort(lowest)
            chosen, weights = lowest[ranked], counts[ranked]
        payload = {'z': z, 'x': x, 'y': y, 'domain': self.domain, 'cap': self.cap, 'total': total,
                   'complete': weights is None, 'count': len(chosen),
                   'rows': b64(chosen, '<u4'), 'xy': b64(np.asarray(self.layout)[chosen], '<f4'),
                   'weights': None if weights is None else b64(weights, '<u4')}
        with self._lock:
            self._cache[key] = payload
            if len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return payload


# ---- track credits ---------------------------------------------------------------------------
CREDITS_PAGE_SIZE = 50
CREDITS_STYLE = ('body{max-width:75ch;margin:2rem auto;padding:0 1rem;font:16px/1.6 system-ui}'
                 'article{border-top:1px solid #bbb;padding:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}')
PAGED_CREDITS_STYLE = (CREDITS_STYLE + 'nav{margin:1rem 0;padding:.75rem 0;border-top:1px solid #bbb}'
                       'nav p{margin:0 0 .5rem}nav ul{list-style:none;margin:0 0 .5rem;padding:0;display:flex;flex-wrap:wrap;gap:.25rem 1.25rem}'
                       'nav .unavailable{color:#666}nav form{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}'
                       'nav input{width:7ch;font:inherit;padding:.15rem .3rem}nav button{font:inherit;padding:.15rem .6rem}'
                       '.skip{position:absolute;left:-999px}.skip:focus{position:static}'
                       # Source URLs run to 220 characters (median 96): without this a credit page scrolls sideways
                       # at 390 px and even at 1440 px (tests/browser_credits_v2.mjs measures it).
                       'article{overflow-wrap:anywhere}')


# The last sentence of the credits introduction. Since the rights quarantine of 2026-10-06 every release is built
# with corpus-releases/quarantine.json applied, and scripts/build_corpus_credits.py writes the static page with this
# same sentence (its QUARANTINE_EXCLUSION; tests/test_api_v2.py keeps the two equal). The server never reads the list.
CREDITS_EXCLUSIONS = ('Conflicted legacy recording 30702 is excluded, and so are the recordings on the rights '
                      'quarantine list (removed or held after a rights review).')


def credits_head(count, title_suffix='', style=CREDITS_STYLE, note=None):
    """The static credit page's head, heading and introduction (scripts/build_corpus_credits.py, generalised from
    prepare-local2000.py --phase credits), for a catalog of `count` recordings. note: one more sentence for the
    introduction (the serving list's, when it holds recordings back)."""
    total = f'{count:,}'
    return ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
            f'<title>Music Discovery Atlas: {total} track credits{title_suffix}</title><style>{style}</style>',
            f'<h1>Track credits and licenses</h1><p>{total} screened FMA recordings. Source metadata and license notices are '
            f'reproduced for attribution. This collection does not imply endorsement. {CREDITS_EXCLUSIONS}'
            + (f' {note}' if note else '') + '</p>']


def credit_article(track):
    """One recording's credit, byte for byte as the static credit page writes it."""
    escape = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    parts = ['<article id="' + escape(track['id'].replace(':', '-')) + '"><h2>' + escape(track['title']) + '</h2><p>'
             + escape(track['artist']) + '</p>']
    for label, value in (('Source', track['sourceUrl']), ('License', track['licenseUrl'])):
        require(isinstance(value, str) and value.startswith(('https://', 'http://')), 'Unexpected credit URL scheme')
        parts.append('<p>' + label + ': <a href="' + escape(value) + '">' + escape(value) + '</a></p>')
    for label, value in (('Attribution', track['attribution']), ('Modifications', track['modifications']),
                         ('Supplied notices', track['suppliedNotices'])):
        rendered = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)
        parts.append('<h3>' + label + '</h3><pre>' + escape(rendered) + '</pre>')
    parts.append('</article>')
    return '\n'.join(parts)


def credits_pages(count, size=CREDITS_PAGE_SIZE):
    return max(1, -(-count // size))


def credits_page(release, page, *, size=CREDITS_PAGE_SIZE, served=None, note=None):
    """Page `page` (1-based, clamped) of the track credits: the static page's heading and introduction, the
    credit articles of `size` consecutive catalog rows, and plain-link navigation with a page-jump form.
    served: the sorted rows the serving list serves; the pages then hold those rows only."""
    count = release.count if served is None else len(served)
    pages = credits_pages(count, size)
    page = min(max(1, page), pages)
    start, stop = (page - 1) * size, min(page * size, count)
    wanted = list(range(start, stop)) if served is None else list(served[start:stop])
    records = (release.connection().execute('SELECT row, track_json FROM records WHERE row >= ? AND row < ? ORDER BY row',
                                            (start, stop)).fetchall() if served is None else
               release.connection().execute('SELECT row, track_json FROM records WHERE row IN ({}) ORDER BY row'.format(
                   ','.join('?' * len(wanted))), wanted).fetchall() if wanted else [])
    require([row for row, _ in records] == wanted, 'Credit rows are incomplete')
    articles = [credit_article(json.loads(track)) for _, track in records]
    position = f'Page {page:,} of {pages:,} · recordings {start + 1:,}–{stop:,} of {count:,}'

    def nav(label, suffix):
        links = []
        for text, target, rel in (('First page', 1, ''), ('Previous page', page - 1, ' rel="prev"'),
                                  ('Next page', page + 1, ' rel="next"'), ('Last page', pages, '')):
            usable = 1 <= target <= pages and target != page
            links.append(f'<li><a href="?page={target}"{rel}>{text}</a></li>' if usable else
                         f'<li><span class="unavailable">{text}</span></li>')
        return (f'<nav aria-label="{label}"><p>{position}</p><ul>{"".join(links)}</ul>'
                f'<form method="get" action="/collection/credits"><label for="credit-page-{suffix}">Go to page</label>'
                f'<input id="credit-page-{suffix}" name="page" type="number" min="1" max="{pages}" value="{page}" inputmode="numeric" required>'
                '<button type="submit">Go</button></form></nav>')
    head = credits_head(count, f', page {page:,} of {pages:,}', PAGED_CREDITS_STYLE, note)
    body = [head[0], head[1], '<a class="skip" href="#credits">Skip to the credits</a>', head[2],
            '<p><a href="/search-studio/">Back to the music map</a></p>', nav('Credit pages', 'top'),
            f'<main id="credits" tabindex="-1" aria-label="Credits, {position}">', *articles, '</main>',
            nav('Credit pages, end of list', 'end')]
    return ('\n'.join(body) + '\n').encode(), page


def credits_index_page(count, note=None):
    """The small static page a v2 web package carries at /notices/track-attribution.html instead of every
    credit: the same heading and introduction, a link to the paged credits, and a forward for old
    #fma-N links to the page that holds that recording."""
    lines = credits_head(count, note=note)
    lines.append('<p>The credits for every recording are listed fifty at a time: '
                 '<a href="/collection/credits">read the track credits and licenses</a>.</p>')
    lines.append('<script>const m=/^#fma-([0-9]{1,6})$/.exec(location.hash);'
                 "if(m)location.replace('/collection/credits?id=fma%3A'+m[1]+'#fma-'+m[1]);</script>")
    return ('\n'.join(lines) + '\n').encode()


def credits_error(message):
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Track credits</title><style>{CREDITS_STYLE}</style><h1>Track credits</h1><p>{html.escape(message)}</p>'
            '<p><a href="/collection/credits">First page of the track credits</a> · <a href="/search-studio/">Back to the music map</a></p>\n')


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


class NotServed(Exception):
    """A request names a recording the serving list holds: 404, as for a recording that does not exist."""


class CollectionRoutes:
    """GET /collection/tracks and /collection/neighbors over a verified ReleaseV2.

    serving: the request-time serving list (server/serving.py). Held recordings are absent from every page,
    lookup, neighbour list, tile, link list and credit page, and a request that names one answers 404."""

    def __init__(self, release, graph, *, audio, headers, tracks_per_minute=300, rows_per_minute=600,
                 neighbors_per_minute=60, credits_per_minute=300, tiles_per_minute=2400, links_per_minute=600, pending=8,
                 trace_limit=2048, page_limit=48, rows_limit=64, serving=None):
        # trace_limit 2048 is the v1 page's local default; ef 32 traces hold about 50 events.
        self.release, self.graph, self.audio, self.headers = release, graph, audio, headers
        self.serving = serving
        self.allowed = serving.served_rows if serving is not None else None
        self.served_rows = serving.rows_in_order() if serving is not None else None
        self.note = None
        if serving is not None and serving.count < serving.total:
            from serving import HELD_SENTENCE
            self.note = HELD_SENTENCE
        self._bits = base64.b64decode(audio.manifest['availableRows']) if audio.manifest.get('enabled') else b''
        # Every collection read spends process CPU, which the anonymous search budget (public_boundary.PreviewBudget)
        # also counts, so each route has a per-minute cap. A browse or lookup session uses about 20 pages.
        self.tracks_budget = Budget(tracks_per_minute)
        # Explicit rows (/collection/tracks?rows=, at most rows_limit primary-key lookups each) are the map's hover and
        # click reads. They have their own cap, so using the map never spends the pages' budget (scale UI review F1). The
        # page reads a hovered row only after a 250 ms rest, so one visitor's hovering sends at most 240 a minute.
        self.rows_budget = Budget(rows_per_minute)
        self.neighbors_budget = Budget(neighbors_per_minute)
        self.credits_budget = Budget(credits_per_minute)
        self.tiles_budget, self.links_budget = Budget(tiles_per_minute), Budget(links_per_minute)
        self._tiles, self._tiles_lock = None, threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='music-collection')
        self.pending, self.max_pending, self.lock = 0, pending, threading.Lock()
        self.trace_limit, self.page_limit, self.rows_limit = trace_limit, page_limit, rows_limit

    def available(self, row):
        return bool(self._bits) and bool(self._bits[row >> 3] >> (row & 7) & 1)

    def served(self, row):
        return self.allowed is None or row in self.allowed

    def require_served(self, row):
        if not self.served(row):
            raise NotServed()

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
            for row in rows:
                self.require_served(row)
            return {'rows': self.release.display_rows(rows)}
        offset = self.number(values, 'offset', 0, 0, 10_000_000)
        limit = self.number(values, 'limit', 12, 1, self.page_limit)
        flags = {key: values.get(key, '0') for key in ('preview', 'facets')}
        require(all(flag in ('0', '1') for flag in flags.values()), 'Invalid flag')
        text = {key: values.get(key, '') for key in ('q', 'text', 'genre')}
        require(all(len(value) <= 512 for value in text.values()), 'Collection filter exceeds 512 characters')
        return page_query(self.release, offset=offset, limit=limit, query=text['q'], text=text['text'],
                          genre=text['genre'], rows_filter=self.available if flags['preview'] == '1' else None,
                          facets=flags['facets'] == '1', served=self.allowed)

    def neighbors(self, values):
        require(set(values) == {'row'}, 'Expected row')
        row = self.number(values, 'row', None, 0, self.release.count - 1)
        self.require_served(row)
        from search_v2 import label_rows, trace_rows
        started = time.perf_counter()
        query = self.release.vectors[row]
        exact = (self.graph.exact_search(query, k=16, exclude_id=row) if self.allowed is None else
                 self.graph.exact_search(query, k=16, exclude_id=row, allowed=self.allowed))
        exact_at = time.perf_counter()
        traced = self.graph.search(query, k=17, ef=32, trace=True, trace_limit=self.trace_limit)
        if self.allowed is not None:  # the traversal crosses held rows; none is reported as a result
            kept = [item for item in traced['results'] if item['id'] in self.allowed]
            traced = {**traced, 'results': kept, 'trace': {**traced['trace'], 'finalResults': kept, 'events': [
                {**event, 'items': [item for item in event['items'] if item['id'] in self.allowed]}
                if event.get('type') == 'results' else event for event in traced['trace']['events']]}}
        finished = time.perf_counter()
        ranked = [item['id'] for item in exact]
        labels = [r for r in label_rows(traced['trace']) if r not in ranked and r != row and self.served(r)]
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

    def tile_index(self):
        with self._tiles_lock:  # built on first use: one sort of the layout (about 20 ms at 200K rows)
            if self._tiles is None:
                self._tiles = TileIndex(self.release.layout, self.release.manifest['layout']['bounds'], rows=self.allowed)
            return self._tiles

    def tiles(self, values):
        require(set(values) == {'z', 'x', 'y'}, 'Expected z, x and y')
        z = self.number(values, 'z', None, 0, TILE_MAX_LEVEL)
        return self.tile_index().tile(z, self.number(values, 'x', None, 0, (1 << z) - 1),
                                      self.number(values, 'y', None, 0, (1 << z) - 1))

    def links(self, values):
        """The stored index links of one recording, level by level in stored order, with the positions of
        the recording and every linked row. Display only: the page draws them around the focus."""
        require(set(values) == {'row'}, 'Expected row')
        release = self.release
        row = self.number(values, 'row', None, 0, release.count - 1)
        self.require_served(row)
        first, last = int(release.node_layers[row]), int(release.node_layers[row + 1])
        levels = [[target for target in
                   release.neighbors[int(release.layer_offsets[layer]):int(release.layer_offsets[layer + 1])].tolist()
                   if self.served(target)] for layer in range(first, last)]
        return {'schemaVersion': 1, 'kind': 'stored-index-links', 'row': row, 'graphId': release.graph_id,
                'indexSha256': release.graph_sha256, 'levels': levels,
                'layout': release.positions({row, *(target for level in levels for target in level)})}

    def credits(self, values):
        """An HTML page of track credits, or a redirect from a recording ID to the page that holds it."""
        from starlette.responses import HTMLResponse, RedirectResponse
        require(len(values) <= 1, 'Use either page or id')
        headers = {**self.headers, 'Cache-Control': 'no-cache'}
        if 'id' in values:
            ident = values['id']
            require(TRACK_ID.fullmatch(ident) is not None, 'Invalid recording ID')
            row = self.release.row_of.get(ident)
            if row is None or not self.served(row):
                return HTMLResponse(credits_error('No recording with this ID is in the collection.'), status_code=404,
                                    headers=headers)
            index = row if self.served_rows is None else self.served_rows.index(row)
            page = index // CREDITS_PAGE_SIZE + 1
            return RedirectResponse(f'/collection/credits?page={page}#' + ident.replace(':', '-'), status_code=303,
                                    headers=headers)
        body, _ = credits_page(self.release, self.number(values, 'page', 1, 0, 10_000_000),
                               served=self.served_rows, note=self.note)
        return HTMLResponse(body, headers=headers)

    async def __call__(self, scope, receive, send):
        from starlette.responses import HTMLResponse, JSONResponse, Response
        path = scope['path']
        routes = {'/collection/tracks': (self.tracks, {'rows', 'offset', 'limit', 'q', 'text', 'genre', 'preview', 'facets'},
                                         self.tracks_budget, 'Collection page'),
                  '/collection/neighbors': (self.neighbors, {'row'}, self.neighbors_budget, 'Neighbor exploration'),
                  '/collection/tiles': (self.tiles, {'z', 'x', 'y'}, self.tiles_budget, 'Map tile'),
                  '/collection/links': (self.links, {'row'}, self.links_budget, 'Map link'),
                  '/collection/credits': (self.credits, {'page', 'id'}, self.credits_budget, 'Credit page')}
        headers = {**self.headers, 'Cache-Control': 'no-store'}

        def failure(message, status, extra=None):
            if path == '/collection/credits':  # a page people read: its errors are pages too
                return HTMLResponse(credits_error(message), status_code=status, headers={**headers, **(extra or {})})
            return JSONResponse({'error': message}, status_code=status, headers={**headers, **(extra or {})})
        if path not in routes:
            await failure('Not found', 404)(scope, receive, send)
            return
        handler, allowed, budget, name = routes[path]
        try:
            values = self.params(scope.get('query_string', b''), allowed)
        except ReleaseError as error:
            await failure(str(error), 400)(scope, receive, send)
            return
        if path == '/collection/tracks' and 'rows' in values:
            budget, name = self.rows_budget, 'Collection row'
        if budget is not None:
            retry = budget.admit()
            if retry:
                await failure(name + ' budget exhausted', 429, {'Retry-After': str(retry)})(scope, receive, send)
                return
        with self.lock:
            if self.pending >= self.max_pending:
                full = True
            else:
                full, self.pending = False, self.pending + 1
        if full:
            await failure('Collection queue full', 429, {'Retry-After': '1'})(scope, receive, send)
            return
        try:
            body = await asyncio.get_running_loop().run_in_executor(self.executor, handler, values)
            response = body if isinstance(body, Response) else JSONResponse(body, status_code=200, headers=headers)
        except ReleaseError as error:
            response = failure(str(error), 400)
        except NotServed:
            response = failure('No recording with this ID is in the collection.' if path == '/collection/credits'
                               else 'Recording not found', 404)
        except Exception:
            response = failure('Collection read failed', 500)
        finally:
            with self.lock:
                self.pending -= 1
        await response(scope, receive, send)

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
