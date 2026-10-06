import test from 'node:test';
import assert from 'node:assert/strict';
import {ServerSearch,validateResponse,sameOriginApi,publicCharacterLimit,READINESS_TIMEOUT_MS} from '../web/search-studio/src/contracts.mjs';
const origin='https://music.example';
const ids=new Set(['fma:1']);
const expected={catalogId:'c',graphId:'g',indexSha256:'i',catalogSha256:'s',vectorsSha256:'v',allowedEngineIds:['e']};
const manifest={...expected,engineId:'e',deploymentGeneration:'d'};
const identity={...expected,requestId:'r',generation:1,engineId:'e',deploymentGeneration:'d',trackIds:ids};
const response={...identity,results:[{id:'fma:1',rank:1,cosineSimilarity:.5}]};
const client=(fetcher,overrides={})=>new ServerSearch({endpoint:'/v1/',pageOrigin:origin,expected,fetcher,...overrides});

test('native default fetch keeps its global receiver for connect, search and cancellation',async()=>{
  const original=globalThis.fetch,calls=[];let resolveSearch;
  // Node's built-in fetch tolerates the wrong receiver; Window.fetch does not.
  globalThis.fetch=function(url,options){
    assert.equal(this,globalThis,'Browser fetch would throw Illegal invocation');
    calls.push(url.pathname);
    if(url.pathname==='/v1/manifest')return Promise.resolve({ok:true,json:async()=>manifest});
    if(url.pathname==='/v1/cancel')return Promise.resolve({ok:true});
    const body=JSON.parse(options.body);
    return new Promise(resolve=>{resolveSearch=()=>resolve({ok:true,json:async()=>({...manifest,...body,results:[]})});});
  };
  try {
    const c=new ServerSearch({endpoint:'/v1/',pageOrigin:origin,expected});
    await c.connect();
    const result=c.search('A public instrumental example',ids);c.cancel();resolveSearch();
    await assert.rejects(result,{name:'AbortError'});
    assert.deepEqual(calls,['/v1/manifest','/v1/search','/v1/cancel']);
  } finally {globalThis.fetch=original;}
});

test('explicitly injected fetcher is not replaced or rebound',async()=>{
  let observed;
  const injected=function(){observed=this;return Promise.resolve({ok:true,json:async()=>manifest});};
  const c=client(injected);assert.equal(c.fetcher,injected);await c.connect();assert.equal(observed,c);
});

test('response validates query generation, catalog, graph, encoder and deployment identities',()=>{
  assert.equal(validateResponse(response,identity),response);
  for(const field of ['requestId','generation','catalogId','graphId','indexSha256','vectorsSha256','catalogSha256','engineId','deploymentGeneration']) {
    assert.throws(()=>validateResponse({...response,[field]:'wrong'},identity),/identity/);
  }
  for(const results of [[{id:'other',cosineSimilarity:.4}],[{id:'fma:1',cosineSimilarity:2}],
    [{id:'fma:1',cosineSimilarity:.4},{id:'fma:1',cosineSimilarity:.4}]]) {
    assert.throws(()=>validateResponse({...response,results},identity),/Invalid/);
  }
  // Bounded by the requested k and ordered as a ranking; both hold for the shipped k:16 contract.
  const many=new Set(Array.from({length:20},(_,i)=>'fma:'+(i+1)));
  const ranking=n=>Array.from({length:n},(_,i)=>({id:'fma:'+(i+1),cosineSimilarity:.9-i*.01}));
  assert.equal(validateResponse({...response,results:ranking(16)},{...identity,trackIds:many,k:16}).results.length,16);
  assert.throws(()=>validateResponse({...response,results:ranking(17)},{...identity,trackIds:many,k:16}),/Invalid/);
  assert.equal(validateResponse({...response,results:ranking(17)},{...identity,trackIds:many}).results.length,17,'no k, no length bound');
  const unsorted=ranking(3);unsorted[2].cosineSimilarity=.95;
  assert.throws(()=>validateResponse({...response,results:unsorted},{...identity,trackIds:many,k:16}),/Invalid/);
  const ties=[{id:'fma:1',cosineSimilarity:.5},{id:'fma:2',cosineSimilarity:.5}];
  assert.equal(validateResponse({...response,results:ties},{...identity,trackIds:many,k:16}).results.length,2);
});

test('all routes require exact same-origin HTTPS /v1/ before transmitting anything',async()=>{
  let calls=0;const fetcher=async()=>{calls++;return {ok:true,json:async()=>manifest};};
  for(const endpoint of [null,'http://music.example/v1/','https://other.example/v1/','//other.example/v1/',
    '/v1/?token=secret','/v1/#other','https://user:pass@music.example/v1/','/api/v1/']) {
    await assert.rejects(client(fetcher,{endpoint}).connect());
  }
  await assert.rejects(client(fetcher,{expected:null}).connect(),/Pinned/);
  assert.equal(calls,0);
  assert.equal(sameOriginApi('/v1/',origin).href,origin+'/v1/');
});

test('manifest mismatch blocks query submission and raw submitted text is preserved on the correct route',async()=>{
  const calls=[];
  const c=client(async(url,options)=>{
    calls.push({url:String(url),options});
    if(!options.body)return {ok:true,json:async()=>manifest};
    const body=JSON.parse(options.body);
    return {ok:true,json:async()=>({...manifest,...body,results:[{id:'fma:1',cosineSimilarity:.2}]})};
  });
  await c.connect();assert.equal(calls.length,1);
  await c.search('  Less SAD, no vocals?  ',ids);
  assert.deepEqual(calls.map(c=>c.url),[origin+'/v1/manifest',origin+'/v1/search']);
  const body=JSON.parse(calls[1].options.body);
  assert.equal(body.query,'  Less SAD, no vocals?  ');assert.equal(body.trace,true);assert.equal('vector' in body,false);
  assert.equal(calls[1].options.redirect,'error');assert.equal(calls[1].options.mode,'same-origin');
  assert.deepEqual(Object.keys(calls[1].options.headers),['Content-Type']);
  const mismatch=client(async()=>({ok:true,json:async()=>({...manifest,indexSha256:'wrong'})}));
  await assert.rejects(mismatch.connect(),/identity/);
  await assert.rejects(mismatch.search('not sent',ids),/not connected/);
});

test('cancellation sends only request identity and rejects a late response when fetch ignores abort',async()=>{
  let resolve;const cancelled=[];
  const c=client(async(url,options)=>{
    if(!options.body)return {ok:true,json:async()=>manifest};
    const body=JSON.parse(options.body);
    if(url.pathname==='/v1/cancel'){cancelled.push({url:String(url),body,options});return {ok:true};}
    return new Promise(done=>{resolve=()=>done({ok:true,json:async()=>({...manifest,...body,results:[]})});});
  });
  await c.connect();const result=c.search('public example',ids);c.cancel();resolve();
  await assert.rejects(result,{name:'AbortError'});
  assert.equal(cancelled.length,1);assert.equal(cancelled[0].url,origin+'/v1/cancel');
  assert.deepEqual(Object.keys(cancelled[0].body).sort(),['generation','requestId']);
  assert.equal(cancelled[0].options.redirect,'error');
});

test('newer search wins and route substitution cannot transmit query or cancellation data',async()=>{
  const pending=[];let calls=0;
  const c=client(async(url,options)=>{
    calls++;
    if(!options.body)return {ok:true,json:async()=>manifest};
    if(url.pathname==='/v1/cancel')return {ok:true};
    const body=JSON.parse(options.body);
    return new Promise(resolve=>pending.push(()=>resolve({ok:true,json:async()=>({...manifest,...body,results:[]})})));
  });
  await c.connect();const first=c.search('first',ids);const second=c.search('second',ids);
  pending[1]();assert.equal((await second).query,'second');pending[0]();await assert.rejects(first,{name:'AbortError'});
  const before=calls;c.endpoint='https://external.example/v1/';await assert.rejects(c.search('never sent',ids),/same-origin/);
  c.current={requestId:'private-id',generation:1};c.cancel();assert.equal(calls,before);
});

test('refusals carry the status, the server’s message and Retry-After; non-JSON refusals keep a generic message',async()=>{
  const cases=[[429,{error:'Public preview budget exhausted'},'10'],[400,{error:'Invalid or oversized preview request'},null],[409,{error:'Encoder, catalog or deployment identity mismatch; reload manifest'},null],[503,'not json',null]];
  for(const [status,body,retryAfter] of cases){
    const c=client(async(url,options)=>{
      if(!options.body)return {ok:true,json:async()=>manifest};
      return {ok:false,status,headers:{get:name=>name==='retry-after'?retryAfter:null},json:async()=>{if(typeof body!=='object')throw new SyntaxError('not json');return body;}};
    });
    await c.connect();
    await assert.rejects(c.search('public example',ids),e=>{
      assert.equal(e.status,status);assert.equal(e.retryAfter,retryAfter?Number(retryAfter):null);
      assert.equal(e.message,typeof body==='object'?body.error:'Server search failed');return true;
    });
  }
  // Fake responses without headers (as in these tests) must not turn into a TypeError.
  const bare=client(async(url,options)=>options.body?{ok:false,status:500,json:async()=>({})}:{ok:true,json:async()=>manifest});
  await bare.connect();await assert.rejects(bare.search('public example',ids),e=>e.status===500&&e.message==='Server search failed'&&e.retryAfter===null);
});

test('the published public character limit is enforced before transmission and absent limits fall back to the byte bound',async()=>{
  const sent=[];
  const limited=client(async(url,options)=>{
    if(!options.body)return {ok:true,json:async()=>({...manifest,maxQueryUtf8Bytes:2048,publicPreview:{maxQueryCharacters:512}})};
    sent.push(JSON.parse(options.body).query);return {ok:true,json:async()=>({...manifest,...JSON.parse(options.body),results:[]})};
  });
  await limited.connect();assert.equal(publicCharacterLimit(limited.manifest),512);
  await assert.rejects(limited.search('x'.repeat(513),ids),/server limit of 512 characters/);
  assert.deepEqual(sent,[]);
  await limited.search('x'.repeat(512),ids);assert.deepEqual(sent,['x'.repeat(512)]);
  const unlimited=client(async(url,options)=>options.body?{ok:true,json:async()=>({...manifest,...JSON.parse(options.body),results:[]})}:{ok:true,json:async()=>({...manifest,maxQueryUtf8Bytes:2048})});
  await unlimited.connect();assert.equal(publicCharacterLimit(unlimited.manifest),null);
  await unlimited.search('x'.repeat(600),ids);
  await assert.rejects(unlimited.search('é'.repeat(1100),ids),/server input limit/);
});

test('readiness checks are bounded by a timeout signal and still carry no payload, redirect or cache allowance',async()=>{
  let options;const c=client(async(url,init)=>{options=init;return {ok:true,json:async()=>manifest};});
  await c.connect();
  const {signal,...rest}=options;
  assert.deepEqual(rest,{credentials:'same-origin',mode:'same-origin',cache:'no-store',redirect:'error'});
  assert.ok(signal instanceof AbortSignal&&!signal.aborted);assert.equal(READINESS_TIMEOUT_MS,10_000);
});
