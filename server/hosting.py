"""Single-process hosting adapter with preserved authentication boundaries."""
import json
import logging
import os
from pathlib import Path


class SafeLogFilter(logging.Filter):
    """Do not forward URLs, tokens, request data or exception detail to host logs."""

    def filter(self, record):
        record.msg = 'music_service_event'
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class ReadinessGate:
    def __init__(self, app):
        self.app = app
        self.ready = False

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            async def lifespan_send(message):
                if message['type'] == 'lifespan.startup.complete':
                    self.ready = True
                elif message['type'] in ('lifespan.startup.failed', 'lifespan.shutdown.complete', 'lifespan.shutdown.failed'):
                    self.ready = False
                await send(message)
            await self.app(scope, receive, lifespan_send)
            return
        if scope['type'] == 'http' and scope['path'] == '/healthz':
            status = 200 if self.ready else 503
            if scope.get('method') != 'GET':
                status = 405
            body = json.dumps({'ok': status == 200}, separators=(',', ':')).encode()
            await send({'type': 'http.response.start', 'status': status, 'headers': [
                (b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                (b'x-content-type-options', b'nosniff'),
                (b'content-length', str(len(body)).encode())]})
            await send({'type': 'http.response.body', 'body': body})
            return
        await self.app(scope, receive, send)


def selected_releases(root, package):
    """(v2 selection or None, v1 selection or None) of the repository root, each validated once."""
    from active_corpus import selected_corpus
    from release_v2 import selected_release_v2
    v2 = selected_release_v2(root, package)
    return v2, (None if v2 is not None else selected_corpus(root, package))


def selected_serving_list(root, v2, selected):
    """The serving list bound to the selected release. A missing selection, list or binding refuses the
    start: the public site serves only what a reviewed list allows."""
    from corpus_release import ReleaseError
    from serving import load_serving_list
    release = v2.release if v2 is not None else getattr(selected, 'release', None)
    try:
        if release is None:
            raise ReleaseError('No corpus release is selected, so no serving list can apply')
        return load_serving_list(root, catalog_id=release.catalog_id, ordered_ids=release.ordered_ids)
    except ReleaseError as error:
        raise ValueError(str(error)) from None


def build_application(*, mode='authenticated', token='', generation='',
                      enable_anonymous=False, public_origin='', encoder=None,
                      graph=None, graph_binding=None, limits=None, budget=None,
                      serve_web=True, enable_audio=False, audio_directory=None,
                      audio_manifest_path=None, serving='selected', rights_contact=None):
    """serving: 'selected' (production) binds corpus-releases/serving.json to the selected release; tests may
    pass a ServingList, or None to serve every row of a fixture catalog. rights_contact: the takedown page's
    address (MUSIC_RIGHTS_CONTACT); without one the page answers 404."""
    if not generation or generation == 'local-development-v1':
        raise ValueError('Configure a unique deployment generation before starting')
    if mode == 'authenticated':
        if len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError('Configure an approved service credential before starting')
        pending, timeout = 2, 30.0
    elif mode == 'anonymous-preview':
        if not enable_anonymous or token:
            raise ValueError('Anonymous preview requires explicit opt-in and no shared service credential')
        from public_boundary import exact_origin, PublicLimits
        exact_origin(public_origin)
        limits = limits or PublicLimits()
        pending, timeout = limits.pending, limits.request_seconds
    else:
        raise ValueError('Unknown service mode')
    root = Path(__file__).resolve().parent.parent
    package = v2 = selected = None
    if serving == 'selected' or serve_web:
        package = json.loads((root / 'package-manifest.json').read_text())
        v2, selected = selected_releases(root, package)
    if serving == 'selected':
        serving = selected_serving_list(root, v2, selected)
    from api import create_app, Settings
    app = create_app(Settings(auth_token=token or None,
        deployment_generation=generation, max_pending=pending,
        request_timeout_seconds=timeout), encoder=encoder, graph=graph,
        graph_binding=graph_binding, serving=serving)
    if mode == 'anonymous-preview':
        from public_boundary import PublicBoundary
        app = PublicBoundary(app, public_origin, limits, budget)
    gate = ReadinessGate(app)
    if not serve_web:
        return gate
    from web_gateway import WebGateway
    if v2 is not None:
        # Release format v2: the catalog stays on the server; the gateway serves bounded pages.
        return WebGateway(gate, web_root=root / 'web', web_manifest=root / 'web-manifest.json',
            catalog_path=None, mode=mode, public_origin=public_origin,
            audio_manifest=audio_manifest_path or root / 'audio-delivery.json',
            enable_audio=enable_audio, audio_directory=audio_directory, release_v2=v2.release,
            serving=serving, rights_contact=rights_contact)
    catalog_path=(selected.directory if selected else root / 'music-search-studio/data') / 'catalog.json'
    return WebGateway(gate, web_root=root / 'web', web_manifest=root / 'web-manifest.json',
        catalog_path=catalog_path, catalog_bytes=selected.release.assets['catalog'] if selected else None, mode=mode,
        public_origin=public_origin, audio_manifest=audio_manifest_path or root / 'audio-delivery.json',
        enable_audio=enable_audio, audio_directory=audio_directory, serving=serving, rights_contact=rights_contact)


def main():
    from serving import rights_contact
    port = int(os.environ.get('PORT', '10000'))
    if not 1 <= port <= 65535:
        raise SystemExit('Invalid PORT')
    try:
        app = build_application(mode=os.environ.get('MUSIC_SERVICE_MODE', 'authenticated'),
            token=os.environ.get('MUSIC_API_AUTH_TOKEN', ''),
            generation=os.environ.get('MUSIC_DEPLOYMENT_GENERATION', ''),
            enable_anonymous=os.environ.get('MUSIC_ENABLE_ANONYMOUS_PREVIEW') == '1',
            public_origin=os.environ.get('MUSIC_PUBLIC_ORIGIN', ''),
            enable_audio=os.environ.get('MUSIC_ENABLE_AUDIO_PREVIEWS') == '1',
            audio_directory=os.environ.get('MUSIC_AUDIO_PACK_DIR'),
            audio_manifest_path=os.environ.get('MUSIC_AUDIO_MANIFEST_PATH'),
            rights_contact=rights_contact(os.environ.get('MUSIC_RIGHTS_CONTACT')))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    import uvicorn
    log_config = {
        'version': 1, 'disable_existing_loggers': False,
        'filters': {'safe': {'()': SafeLogFilter}},
        'formatters': {'safe': {'format': '%(levelname)s %(message)s'}},
        'handlers': {'safe': {'class': 'logging.StreamHandler', 'formatter': 'safe', 'filters': ['safe']}},
        'root': {'handlers': ['safe'], 'level': 'WARNING'},
        'loggers': {'uvicorn': {'handlers': ['safe'], 'propagate': False},
                    'uvicorn.error': {'handlers': ['safe'], 'propagate': False},
                    'uvicorn.access': {'handlers': [], 'propagate': False}}
    }
    uvicorn.run(app, host='0.0.0.0', port=port, workers=1, access_log=False,
                log_config=log_config, log_level='warning', limit_concurrency=64,  # 16 refused module requests: a page load fires up to 19 at once (2026-10-07)
                timeout_keep_alive=5, timeout_graceful_shutdown=15)


if __name__ == '__main__':
    main()
