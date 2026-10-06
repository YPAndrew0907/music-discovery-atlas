"""Bounded API source for later authenticated hosting. Default listener is loopback.

No credentials are created, no model is downloaded, and no audio is served here.
"""
import os
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TORCH='0',USE_TF='0',
                  OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',TOKENIZERS_PARALLELISM='false')
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from encoder import NativeEncoder, RequestControl, SearchCancelled
from loopback_service import WORK, object_sha, digest
from corpus_release import ValidatedRelease


@dataclass
class Settings:
    precision: str = 'q8'
    graph_dir: str | None = str(WORK/'music-search-studio/data')
    auth_token: str | None = None
    deployment_generation: str = 'local-development-v1'
    max_pending: int = 8
    request_timeout_seconds: float = 30.0


class InvalidRequest(Exception):
    def __init__(self,message,status=400):
        self.message,self.status=message,status


def valid_id(value):
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',value) is not None


def load_graph_v2(encoder, release, directory):
    """Bind a release-format-v2 graph: memory-mapped vectors, CSR links, unchanged search code."""
    from search_v2 import GraphV2
    if directory is None or Path(directory).resolve()!=Path(encoder.release_directory).resolve():
        raise RuntimeError('Active corpus graph directory mismatch')
    if (encoder.catalog_version!=release.catalog_id or encoder.ids!=list(release.ordered_ids)
        or encoder.catalog_sha!=release.catalog_sha256
        or encoder.audio_receipt['vectorsSha256']!=release.vectors_sha256
        or encoder.vectors is not release.vectors):
        raise RuntimeError('Active corpus encoder snapshot mismatch')
    profile=release.graph_manifest['allowedQueryProfiles'][0]
    if encoder.engine!=profile['id'] or object_sha(encoder.query_profile)!=object_sha(profile['identity']):
        raise RuntimeError('Active corpus query profile mismatch')
    binding={'indexSpaceId':release.graph_id,'graphId':release.graph_id,
             'indexSha256':release.graph_sha256,
             'graphManifestSha256':release.graph_manifest_sha256,
             'orderedIdsSha256':object_sha(encoder.ids),'bindingStatus':'verified-corpus-release-v2',
             'corpusReleaseSha256':release.manifest_sha256,'releaseFormat':2}
    return GraphV2(release),binding


def load_graph(encoder, directory):
    from hnsw_trace import HNSW
    if getattr(encoder,'release_v2',None) is not None:
        return load_graph_v2(encoder,encoder.release_v2,directory)
    release=getattr(encoder,'validated_release',None)
    if isinstance(release,ValidatedRelease):
        # Consume the approved immutable bytes, never reopen mutable candidate files.
        if directory is None or Path(directory).resolve()!=Path(encoder.release_directory).resolve():
            raise RuntimeError('Active corpus graph directory mismatch')
        raw=release.assets
        manifest=json.loads(raw['graphManifest']);graph=json.loads(raw['index'])
        if (encoder.catalog_version!=release.catalog_id or encoder.ids!=list(release.ordered_ids)
            or encoder.catalog_sha!=hashlib.sha256(raw['catalog']).hexdigest()
            or encoder.audio_receipt['vectorsSha256']!=hashlib.sha256(raw['vectors']).hexdigest()
            or encoder.vectors.shape!=(release.count,release.dimensions)
            or encoder.vectors.tobytes()!=raw['vectors']):
            raise RuntimeError('Active corpus encoder snapshot mismatch')
        profile=manifest['allowedQueryProfiles'][0]
        if encoder.engine!=profile['id'] or object_sha(encoder.query_profile)!=object_sha(profile['identity']):
            raise RuntimeError('Active corpus query profile mismatch')
        binding={'indexSpaceId':release.graph_id,'graphId':release.graph_id,
                 'indexSha256':hashlib.sha256(raw['index']).hexdigest(),
                 'graphManifestSha256':hashlib.sha256(raw['graphManifest']).hexdigest(),
                 'orderedIdsSha256':object_sha(encoder.ids),'bindingStatus':'verified-corpus-release',
                 'corpusReleaseSha256':release.manifest_sha256}
        return HNSW(graph,raw['vectors']),binding
    if directory is None:
        # Temporary local fallback has its own audio-only identity. A release must
        # use the graph owner’s bound manifest via MUSIC_GRAPH_DIR.
        path=encoder.pack/'results/index.json'
        graph=json.loads(path.read_text())
        graph['spaceId']='audio-index:'+object_sha({
            'audioModelSha256':encoder.pair['identity']['audioModelSha256'],
            'audioPreprocessing':encoder.pair['identity']['audioPreprocessing'],
            'vectorsSha256':encoder.audio_receipt['vectorsSha256'],'orderedIds':encoder.ids})
        binding={'indexSpaceId':graph['spaceId'],'indexSha256':digest(path),
                 'bindingStatus':'local-assessment-fallback; use released graph directory before deployment'}
    else:
        path=Path(directory)/'index.json'
        graph=json.loads(path.read_text())
        manifest=json.loads((Path(directory)/'manifest.json').read_text())
        identity=manifest.get('graphIdentity',{})
        graph_id='experimental-clap-audio-graph:'+object_sha(identity)
        ordered_sha=object_sha(encoder.ids)
        audio=identity.get('audio',{})
        pair=encoder.pair['identity']
        expected_audio={
            'family':pair['family'],'repo':pair['repo'],'revision':pair['revision'],
            'audioModelSha256':pair['audioModelSha256'],'audioQuantization':pair['audioQuantization'],
            'preprocessorConfigSha256':pair['preprocessorConfigSha256'],
            'preprocessing':pair['audioPreprocessing'],
            'preprocessingSha256':object_sha(pair['audioPreprocessing']),
            'executionProfile':pair['audioExecutionProfile'],
            'executionProfileSha256':encoder.audio_receipt['executionProfileSha256']}
        if (manifest.get('graphId')!=graph_id or graph.get('spaceId')!=graph_id
            or manifest.get('catalogId')!=encoder.catalog_version
            or manifest.get('vectorsSha256')!=encoder.audio_receipt['vectorsSha256']
            or identity.get('vectorsSha256')!=encoder.audio_receipt['vectorsSha256']
            or manifest.get('orderedIds')!=encoder.ids
            or manifest.get('orderedIdsSha256')!=ordered_sha
            or identity.get('orderedIdsSha256')!=ordered_sha
            or identity.get('count')!=108 or identity.get('dimensions')!=512
            or identity.get('algorithm')!='hnsw-static-cosine-v1'
            or identity.get('metric')!='cosine' or audio!=expected_audio
            or identity.get('construction')!={k:graph[k] for k in ['M','efConstruction','seed']}
            or manifest.get('indexSha256')!=digest(path)):
            raise RuntimeError('Graph audio-space binding mismatch')
        for name in ['index.json','catalog.json','vectors.f32','ids.json']:
            artifact=Path(directory)/name
            spec=manifest.get('assets',{}).get(name,{})
            if spec.get('sha256')!=digest(artifact) or spec.get('bytes')!=artifact.stat().st_size:
                raise RuntimeError('Graph artifact integrity mismatch')
        if (digest(Path(directory)/'catalog.json')!=encoder.catalog_sha
            or digest(Path(directory)/'vectors.f32')!=encoder.audio_receipt['vectorsSha256']
            or json.loads((Path(directory)/'ids.json').read_text())!=encoder.ids):
            raise RuntimeError('Graph catalog identity mismatch')
        compatible=[p for p in manifest.get('allowedQueryProfiles',[]) if p.get('id')==encoder.engine]
        if (len(compatible)!=1 or compatible[0].get('identity')!=encoder.query_profile
            or compatible[0].get('source',{}).get('queryProfileSha256')!=object_sha(encoder.query_profile)):
            raise RuntimeError('Native query profile is not allowed by graph manifest')
        binding={'indexSpaceId':graph_id,'graphId':graph_id,'indexSha256':digest(path),
                 'graphManifestSha256':digest(Path(directory)/'manifest.json'),
                 'orderedIdsSha256':ordered_sha,'bindingStatus':'verified-release-graph'}
    return HNSW(graph,encoder.vectors),binding


def create_app(settings=None, encoder=None, graph=None, graph_binding=None):
    settings=settings or Settings(
        precision=os.environ.get('MUSIC_TEXT_PRECISION','q8'),
        graph_dir=os.environ.get('MUSIC_GRAPH_DIR',str(WORK/'music-search-studio/data')),
        auth_token=os.environ.get('MUSIC_API_AUTH_TOKEN'),
        deployment_generation=os.environ.get('MUSIC_DEPLOYMENT_GENERATION','local-development-v1'))
    if settings.precision not in ('q8','fp32'):
        raise ValueError('Choose q8 or fp32')
    @asynccontextmanager
    async def lifespan(app):
        app.state.encoder=encoder or NativeEncoder(settings.precision)
        if graph is None:
            app.state.graph,app.state.graph_binding=load_graph(app.state.encoder,getattr(app.state.encoder,'release_directory',settings.graph_dir))
        else:
            app.state.graph,app.state.graph_binding=graph,graph_binding
        app.state.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='music-inference')
        app.state.active={}
        app.state.cancelled={}
        yield
        for state in app.state.active.values():
            state['control'].cancel()
        app.state.executor.shutdown(wait=True,cancel_futures=True)
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware('http')
    async def headers_and_auth(request,call_next):
        if settings.auth_token:
            supplied=request.headers.get('authorization','')
            if not hmac.compare_digest(supplied,'Bearer '+settings.auth_token):
                return JSONResponse({'error':'Unauthorized'},status_code=401)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.exception_handler(InvalidRequest)
    async def invalid_handler(request,exc):
        return JSONResponse({'error':exc.message},status_code=exc.status)

    async def body(request):
        parts=[]
        total=0
        async for part in request.stream():
            total+=len(part)
            if total>16384:
                raise InvalidRequest('Request body exceeds 16384 bytes',413)
            parts.append(part)
        try:
            value=json.loads(b''.join(parts))
        except (ValueError,UnicodeError):
            raise InvalidRequest('Expected JSON object')
        if not isinstance(value,dict):
            raise InvalidRequest('Expected JSON object')
        return value

    def request_identity(value):
        if not valid_id(value.get('requestId')):
            raise InvalidRequest('Invalid requestId')
        if type(value.get('generation')) is not int or not 0<=value['generation']<=2**53-1:
            raise InvalidRequest('Invalid generation')
        return value['requestId'],value['generation']

    @app.get('/health')
    async def health():
        return {'ok':True,'deploymentGeneration':settings.deployment_generation}

    @app.get('/v1/manifest')
    async def manifest():
        enc=app.state.encoder
        return {**enc.manifest(),**({'quality':'Experimental reviewed-release catalog; no listener relevance judgments'} if isinstance(getattr(enc,'validated_release',None),ValidatedRelease) or getattr(enc,'release_v2',None) is not None else {}),**app.state.graph_binding,
                'deploymentGeneration':settings.deployment_generation,
                'rankingAlgorithm':'exact-cosine-js-order-v1',
                'traceAlgorithm':'hnsw-static-cosine-v1',
                'defaults':{'k':8,'ef':32,'trace':False,'traceLimit':256},
                'cancellation':'POST /v1/cancel; per-call ORT termination plus queue/traversal checks; client must discard older generations',
                'maxQueryUtf8Bytes':8000,'maxPendingRequests':settings.max_pending}

    @app.post('/v1/cancel')
    async def cancel(request:Request):
        value=await body(request)
        request_id,generation=request_identity(value)
        state=app.state.active.get(request_id)
        found=state is not None and state['generation']==generation
        if found:
            state['control'].cancel()
        now=time.monotonic()
        app.state.cancelled={k:v for k,v in app.state.cancelled.items() if v>now}
        if len(app.state.cancelled)>=256:
            app.state.cancelled.pop(next(iter(app.state.cancelled)))
        app.state.cancelled[(request_id,generation)]=now+60
        return {'requestId':request_id,'generation':generation,'cancelled':found,
                'preCancelledForSeconds':60,'deploymentGeneration':settings.deployment_generation}

    def compute(value,control,queued_at):
        enc=app.state.encoder
        control.check()
        started=time.perf_counter()
        vector,tokens,timing=enc.encode(value['query'],control)
        encoded=time.perf_counter()
        exact=app.state.graph.exact_search(vector,k=value['k'])
        control.check()
        exact_at=time.perf_counter()
        trace_result=None
        if value['trace']:
            trace_result=app.state.graph.search(vector,k=value['k'],ef=value['ef'],trace=True,
                trace_limit=value['traceLimit'],cancelled=control.event.is_set)
        control.check()
        finished=time.perf_counter()
        results=[{'rank':rank,'row':item['id'],'id':enc.ids[item['id']],
                  'cosineSimilarity':1-item['distance'],'similarityIsProbability':False}
                 for rank,item in enumerate(exact,1)]
        trace=None
        ann=None
        if trace_result:
            trace=trace_result['trace']
            ann_ids=[item['id'] for item in trace_result['results']]
            exact_ids=[item['id'] for item in exact]
            ann={**trace_result['stats'],'recallAgainstExactAtK':len(set(ann_ids)&set(exact_ids))/value['k'],
                 'resultRows':ann_ids,'exactRows':exact_ids,
                 'meaning':'Index agreement on this query; not music relevance or a speedup claim'}
        response={'schemaVersion':1,'requestId':value['requestId'],'generation':value['generation'],
                'deploymentGeneration':settings.deployment_generation,'engineId':enc.engine,
                'queryProfileId':enc.engine,'pairId':enc.pair['id'],
                'catalogId':enc.catalog_version,'catalogSha256':enc.catalog_sha,
                'vectorsSha256':enc.audio_receipt['vectorsSha256'],**app.state.graph_binding,
                'rankingAlgorithm':'exact-cosine-js-order-v1','tokenization':tokens,
                'results':results,'trace':trace,'annDiagnostic':ann,
                'interpretation':'Descriptive similarity; no verified vocals/language or hard constraints',
                'timingMs':{'queue':(started-queued_at)*1000,**timing,
                    'exactSearch':(exact_at-encoded)*1000,'graphTrace':(finished-exact_at)*1000,
                    'serverCompute':(finished-started)*1000}}
        release=getattr(app.state.graph,'release',None)
        if release is not None:
            # Release format v2: the page holds no catalog or layout, so each reply carries the
            # display rows of its results and labelled trace nodes, and 2-D positions for every
            # row the trace or results mention. Rankings, scores and trace are unchanged.
            from search_v2 import label_rows, trace_rows
            rows=[item['id'] for item in exact]
            labels=[row for row in label_rows(trace) if row not in rows]
            response['tracks']=release.display_rows(rows+labels)
            response['layout']=release.positions(trace_rows(trace,exact))
            response['timingMs']['collectionMetadata']=(time.perf_counter()-finished)*1000
        return response

    @app.post('/v1/search')
    async def search(request:Request):
        value=await body(request)
        request_id,generation=request_identity(value)
        enc=app.state.encoder
        if any(value.get(k)!=v for k,v in (('engineId',enc.engine),('catalogId',enc.catalog_version),
                  ('deploymentGeneration',settings.deployment_generation))):
            raise InvalidRequest('Encoder, catalog or deployment identity mismatch; reload manifest',409)
        query=value.get('query')
        if not isinstance(query,str) or not query.strip() or len(query.encode())>8000:
            raise InvalidRequest('Expected nonempty query up to 8000 UTF-8 bytes')
        for name,default,minimum,maximum in [('k',8,1,20),('ef',32,1,108),('traceLimit',256,0,2048)]:
            value.setdefault(name,default)
            if type(value[name]) is not int or not minimum<=value[name]<=maximum:
                raise InvalidRequest('Invalid '+name)
        if value['ef']<value['k']:
            raise InvalidRequest('ef must be at least k')
        value.setdefault('trace',False)
        if type(value['trace']) is not bool:
            raise InvalidRequest('trace must be boolean')
        if app.state.cancelled.get((request_id,generation),0)>time.monotonic():
            raise InvalidRequest('Search cancelled',499)
        if request_id in app.state.active:
            raise InvalidRequest('Duplicate active requestId',409)
        if len(app.state.active)>=settings.max_pending:
            raise InvalidRequest('Search queue full',429)
        control=RequestControl()
        app.state.active[request_id]={'generation':generation,'control':control}
        queued_at=time.perf_counter()
        future=asyncio.get_running_loop().run_in_executor(app.state.executor,compute,value,control,queued_at)
        async def watch_disconnect():
            while not future.done():
                if await request.is_disconnected():
                    control.cancel()
                    return
                await asyncio.sleep(0.01)
        watcher=asyncio.create_task(watch_disconnect())
        try:
            result=await asyncio.wait_for(asyncio.shield(future),settings.request_timeout_seconds)
            control.check()
            return result
        except asyncio.TimeoutError:
            control.cancel()
            raise InvalidRequest('Search timed out',504)
        except SearchCancelled:
            raise InvalidRequest('Search cancelled',499)
        except Exception as exc:
            if control.event.is_set():
                raise InvalidRequest('Search cancelled',499)
            # Never reflect internal exception messages, query text or paths.
            raise InvalidRequest('Search execution failed',500) from None
        finally:
            watcher.cancel()
            control.cancel()
            # Retain admission accounting until a timed-out native job really exits.
            if future.done():
                app.state.active.pop(request_id,None)
            else:
                def completed(done):
                    app.state.active.pop(request_id,None)
                    if not done.cancelled():
                        done.exception()
                future.add_done_callback(completed)
    return app


def main():
    import argparse
    import uvicorn
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=int(os.environ.get('PORT','8080')))
    args=parser.parse_args()
    if args.host not in ('127.0.0.1','::1','localhost') and not os.environ.get('MUSIC_API_AUTH_TOKEN'):
        raise SystemExit('Non-loopback listener requires configured service authentication')
    uvicorn.run(create_app(),host=args.host,port=args.port,access_log=False,log_level='warning')


if __name__=='__main__':
    main()
