"""Allowlisted same-origin web assets and optional verified local audio only."""
import hashlib
import json
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from starlette.responses import FileResponse, JSONResponse, RedirectResponse

SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
    'Cache-Control': 'no-cache',
}
STATIC_SUFFIXES = {'.html', '.css', '.mjs', '.js', '.json', '.f32', '.wasm', '.txt', '.md'}
# Default static budgets; a reviewed web-manifest.json may set its own total within STATIC_TOTAL_MAXIMUM.
STATIC_FILE_LIMIT, STATIC_TOTAL_LIMIT, STATIC_TOTAL_MAXIMUM = 20_000_000, 30_000_000, 64_000_000


def static_limits(manifest):
    limits = manifest.get('limits')
    if limits is None:
        return STATIC_FILE_LIMIT, STATIC_TOTAL_LIMIT
    if (not isinstance(limits, dict) or set(limits) != {'maxFileBytes', 'maxTotalBytes'}
            or type(limits['maxFileBytes']) is not int or type(limits['maxTotalBytes']) is not int
            or not 1 <= limits['maxFileBytes'] <= STATIC_FILE_LIMIT
            or not 1 <= limits['maxTotalBytes'] <= STATIC_TOTAL_MAXIMUM):
        raise ValueError('Invalid public web budget')
    return limits['maxFileBytes'], limits['maxTotalBytes']


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def confined_file(root, relative):
    """Reject dot segments and symlinks before resolving to an existing root file."""
    relative = str(relative)
    parts = relative.split('/')
    if (not relative or relative.startswith('/') or '\\' in relative or '\x00' in relative
            or any(not p or p in ('.', '..') or p.startswith('.') for p in parts)):
        raise ValueError('Invalid asset path')
    root = Path(root).resolve(strict=True)
    current = root
    for component in parts:
        current = current / component
        if current.is_symlink():
            raise ValueError('Symlinked assets are not served')
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError('Asset is outside the static root')
    return resolved


class AudioDelivery:
    def __init__(self, manifest_path, catalog_path, *, enabled=False, directory=None, catalog_bytes=None):
        catalog_path = Path(catalog_path)
        catalog_bytes = bytes(catalog_bytes) if catalog_bytes is not None else catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        self.disabled = {'schemaVersion': 1, 'catalogId': catalog['id'],
            'catalogSha256': hashlib.sha256(catalog_bytes).hexdigest(), 'enabled': False,
            'publicDeliveryVerified': False, 'tracks': [],
            'unlistedTrackBehavior': 'Preview unavailable'}
        self.manifest, self.paths = self.disabled, {}
        self.directory, self.specs = None, {}
        if not enabled:
            return
        if not directory:
            raise ValueError('An explicitly configured local audio directory is required')
        directory = Path(directory)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Invalid local audio directory')
        source = json.loads(Path(manifest_path).read_text())
        if (not isinstance(source, dict) or source.get('schemaVersion') != 1 or source.get('enabled') is not True
                or source.get('publicDeliveryVerified') is not True
                or source.get('catalogId') != self.disabled['catalogId']
                or source.get('catalogSha256') != self.disabled['catalogSha256']):
            raise ValueError('Audio delivery approval/pin mismatch')
        catalog_tracks = {t['id']: t for t in catalog['tracks']}
        entries, seen, expected_files = [], set(), set()
        if not isinstance(source.get('tracks'), list):
            raise ValueError('Invalid audio delivery rows')
        for row in source.get('tracks', []):
            if not isinstance(row, dict):
                raise ValueError('Invalid audio delivery row')
            ident = row.get('id')
            if ident in seen or ident not in catalog_tracks:
                raise ValueError('Unknown or duplicate audio ID')
            seen.add(ident)
            track = catalog_tracks[ident]
            if row.get('available') is False and row.get('url') is None:
                entries.append({'id': ident, 'available': False, 'url': None})
                continue
            filename = PurePosixPath(track['audio']).name
            route = '/audio/' + filename
            if (row.get('available') is not True or row.get('url') != route
                    or row.get('bytes') != track['audioBytes']
                    or row.get('sha256') != track['audioSha256'] or not filename.endswith('.mp3')):
                raise ValueError('Unverified audio delivery row')
            path = confined_file(directory, filename)
            if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
                raise ValueError('Audio content pin mismatch')
            expected_files.add(filename)
            self.paths[route] = path
            self.specs[route] = {'filename': filename, 'bytes': row['bytes'], 'sha256': row['sha256']}
            entries.append({k: row[k] for k in ['id', 'available', 'url', 'bytes', 'sha256']})
        actual = list(directory.iterdir())
        if not self.paths or any(p.is_symlink() or not p.is_file() for p in actual) or {p.name for p in actual} != expected_files:
            raise ValueError('Audio directory contains missing or unapproved files')
        self.manifest = {**self.disabled, 'enabled': True, 'publicDeliveryVerified': True, 'tracks': entries,
            'deliveryVerificationScope': 'Verified local file integrity and explicit routes; hosted browser playback is a separate acceptance check'}
        self.directory = directory.resolve(strict=True)

    def resolve(self, route):
        spec = self.specs[route]
        path = confined_file(self.directory, spec['filename'])
        if path.stat().st_size != spec['bytes'] or digest(path) != spec['sha256']:
            raise ValueError('Audio content changed after verification')
        return path


class WebGateway:
    def __init__(self, api, *, web_root, web_manifest, catalog_path,
                 mode='authenticated', public_origin='', audio_manifest=None,
                 enable_audio=False, audio_directory=None, catalog_bytes=None, release_v2=None):
        self.api, self.mode, self.public_origin = api, mode, public_origin
        self.headers, self.collection = SECURITY_HEADERS, None
        self.web_root = Path(web_root)
        manifest = json.loads(Path(web_manifest).read_text())
        if manifest.get('schemaVersion') != 1 or manifest.get('kind') != 'music-public-web-assets':
            raise ValueError('Unknown public web manifest')
        self.assets = {}
        total = 0
        file_limit, total_limit = static_limits(manifest)
        for row in manifest['files']:
            relative = row['path']
            if relative in self.assets or PurePosixPath(relative).suffix.lower() not in STATIC_SUFFIXES:
                raise ValueError('Invalid or duplicate static asset')
            path = confined_file(self.web_root, relative)
            size = path.stat().st_size
            total += size
            if size != row['bytes'] or size > file_limit or digest(path) != row['sha256']:
                raise ValueError('Public web asset integrity failure')
            self.assets[relative] = path
        if total > total_limit or 'search-studio/index.html' not in self.assets:
            raise ValueError('Unexpected public web package')
        if release_v2 is not None:
            self.configure_v2(release_v2, audio_manifest, enable_audio, audio_directory)
        else:
            self.audio = AudioDelivery(audio_manifest, catalog_path,
                                       enabled=enable_audio, directory=audio_directory, catalog_bytes=catalog_bytes)
        if hasattr(getattr(self.api, 'app', None), 'audio_enabled'):
            self.api.app.audio_enabled = self.audio.manifest['enabled']

    def configure_v2(self, release, audio_manifest, enable_audio, audio_directory):
        """Release format v2: paged collection reads and lazily verified (or remote) previews."""
        from collection_v2 import AudioDeliveryV2, CollectionRoutes, check_web_release
        from corpus_release import ReleaseError
        from search_v2 import GraphV2
        try:
            check_web_release(self.web_root, self.assets, release)
            self.audio = AudioDeliveryV2(audio_manifest, release, enabled=enable_audio, directory=audio_directory)
        except ReleaseError as error:
            raise ValueError(str(error)) from None
        if self.audio.origin:
            # Hardening only: previews may load from the one pinned object-store origin.
            self.headers = {**SECURITY_HEADERS, 'Content-Security-Policy':
                            SECURITY_HEADERS['Content-Security-Policy'] + f"; media-src 'self' {self.audio.origin}"}
        self.collection = CollectionRoutes(release, GraphV2(release), audio=self.audio, headers=self.headers)

    def deployment_config(self, scope):
        hosts = [v.decode('latin-1').lower() for k, v in scope.get('headers', []) if k.lower() == b'host']
        exact_host = urlsplit(self.public_origin).netloc.lower() if self.public_origin else None
        enabled = (self.mode == 'anonymous-preview' and self.api.ready
                   and hosts == [exact_host])
        return {'schemaVersion': 1, 'enabled': enabled, 'mode': self.mode,
            'apiBase': '/v1/', 'origin': self.public_origin or None,
            'recipient': 'This site’s server',
            'privacySummary': ('Your description is sent to this site’s server for inference. '
                'Application query/access logging is disabled; host infrastructure metadata may be retained.')}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.api(scope, receive, send)
            return
        path, method = scope['path'], scope['method']
        if path.startswith('/v1/') or path in ('/health', '/healthz'):
            await self.api(scope, receive, send)
            return
        if method not in ('GET', 'HEAD'):
            await JSONResponse({'error': 'Method not allowed'}, status_code=405,
                headers={**self.headers, 'Cache-Control': 'no-store'})(scope, receive, send)
            return
        if path == '/deployment-config.json':
            await JSONResponse(self.deployment_config(scope), headers={
                **self.headers, 'Cache-Control': 'no-store'})(scope, receive, send)
            return
        if path == '/audio-delivery.json':
            await JSONResponse(self.audio.manifest, headers={
                **self.headers, 'Cache-Control': 'no-store'})(scope, receive, send)
            return
        if path == '/':
            await RedirectResponse('/search-studio/', status_code=307,
                headers=self.headers)(scope, receive, send)
            return
        if self.collection is not None and path.startswith('/collection/'):
            await self.collection(scope, receive, send)
            return
        if path in self.audio.paths:
            try:
                if self.collection is not None:
                    # v2: hash on first request (off the event loop), then identity checks only.
                    import asyncio
                    audio_path = await asyncio.to_thread(self.audio.resolve, path)
                else:
                    audio_path = self.audio.resolve(path)
            except (OSError, ValueError):
                await JSONResponse({'error': 'Preview unavailable'}, status_code=404,
                    headers={**self.headers, 'Cache-Control': 'no-store'})(scope, receive, send)
                return
            await FileResponse(audio_path, media_type='audio/mpeg', headers=self.headers)(scope, receive, send)
            return
        relative = path[1:] if path.startswith('/') else ''
        if relative.endswith('/'):
            relative += 'index.html'
        path_on_disk = self.assets.get(relative)
        if path_on_disk is None:
            await JSONResponse({'error': 'Not found'}, status_code=404,
                headers={**self.headers, 'Cache-Control': 'no-store'})(scope, receive, send)
            return
        # Recheck confinement at each read; the image remains read-only at runtime.
        try:
            path_on_disk = confined_file(self.web_root, relative)
        except (OSError, ValueError):
            await JSONResponse({'error': 'Not found'}, status_code=404,
                headers={**self.headers, 'Cache-Control': 'no-store'})(scope, receive, send)
            return
        mime = {'.mjs': 'text/javascript', '.js': 'text/javascript', '.wasm': 'application/wasm',
                '.f32': 'application/octet-stream'}.get(path_on_disk.suffix)
        await FileResponse(path_on_disk, media_type=mime, headers=self.headers)(scope, receive, send)
