import test from 'node:test';
import assert from 'node:assert/strict';
import {ServerSearch,validateResponse,sameOriginApi} from '../web/search-studio/src/contracts.mjs';
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
