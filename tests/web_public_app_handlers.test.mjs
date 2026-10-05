import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {reviewQueryLimits} from '../web/search-studio/src/query-limits.mjs';
import {indexConnections} from '../web/search-studio/src/search-motion.mjs';
import {MANIFEST_SHA,ARTIST_METADATA_SHA} from '../web/search-studio/src/studio-release.mjs';
import {rankCandidates} from '../web/search-studio/src/rerank.mjs';
import {ServerSearch} from '../web/search-studio/src/contracts.mjs';
import {SERVER_CONFIG,loadDeploymentConfig} from '../web/search-studio/src/server-config.mjs';
import {loadAudioDelivery,previewForTrack,UNAVAILABLE_PREVIEW} from '../web/search-studio/src/audio-delivery.mjs';
import {HNSW,exactSearch} from '../web/search-studio/src/hnsw.mjs';
import {RELEASE} from '../web/listen-lab/src/release.mjs';
import {metadataSearch} from '../web/listen-lab/src/retrieval.mjs';

// Execute the checked-in app, including init, form/click handlers and page lifecycle.
// Only DOM/canvas and encoder hardware are fixtures; the real pack, retrieval,
// configuration validator and ServerSearch request/response boundary run offline.
const root=new URL('../web/',import.meta.url), origin='https://music.example';
const bytes=path=>readFile(new URL(path,root));
const manifest=JSON.parse(await bytes('search-studio/data/manifest.json'));
const catalog=JSON.parse(await bytes('search-studio/data/catalog.json'));
const examples=JSON.parse(await bytes('search-studio/data/examples.json'));
const vectorBytes=await bytes('search-studio/data/vectors.f32');
const vectors=new Float32Array(vectorBytes.buffer,vectorBytes.byteOffset,vectorBytes.byteLength/4);
const graph=HNSW.load(JSON.parse(await bytes('search-studio/data/index.json')),vectors);
const serverManifest={catalogId:manifest.catalogId,graphId:manifest.graphId,indexSha256:manifest.indexSha256,
  catalogSha256:manifest.files.catalog.sha256,vectorsSha256:manifest.vectorsSha256,
  engineId:manifest.allowedQueryProfiles.find(p=>p.kind==='server-live').id,deploymentGeneration:'fixture-generation',maxQueryUtf8Bytes:2048};
const config={schemaVersion:1,enabled:true,mode:'anonymous-preview',origin,apiBase:'/v1/',recipient:'This site’s server',
  privacySummary:'Application query/access logging is disabled; host infrastructure metadata may be retained.'};
const source=(await bytes('search-studio/src/app.mjs')).toString()
  .replace(/^import .*;\n/gm,'').replaceAll('import.meta.url',JSON.stringify(origin+'/search-studio/src/app.mjs'))
  .replace('await init();','');
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return{promise,resolve,reject};};
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate);};
async function until(condition){const deadline=performance.now()+1500;while(performance.now()<deadline){if(condition())return;await new Promise(resolve=>setTimeout(resolve,1));}assert.fail('Fixture did not reach the expected state');}
function responseFor(body,example=0){
  const q=new Float32Array(examples.examples[example].queryVector);
  return {...serverManifest,...body,results:exactSearch(vectors,q,16).map(r=>({id:catalog.tracks[r.id].id,row:r.id,cosineSimilarity:1-r.distance})),
    trace:graph.search(q,{k:16,ef:32,trace:true,spaceId:manifest.graphId}).trace,
    timingMs:{serverCompute:1},tokenization:{truncated:false}};
}
async function harness({deferManifest=false,enabled=true,badManifest=false,failConfig=false,deferSearch=false}={}){
  const elements=new Map(),events={},clicks={},calls=[],searches=[],manifests=[],encodes=[],audioRequests=[];
  const api={enabled,badManifest,failConfig,deferManifest,deferSearch,failSearch:false};
  function el(key){
    if(!elements.has(key))elements.set(key,{dataset:{},style:{},handlers:{},attributes:{},value:key==='#query-kind'?'description':'',src:'',paused:true,hidden:false,
      textContent:'',innerHTML:'',addEventListener(name,fn){this.handlers[name]=fn;},setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){this[name]='';},
      focus(){},scrollIntoView(){},closest(){return null;},showModal(){this.open=true;},close(){this.open=false;},pause(){this.paused=true;},load(){this.paused=true;},async play(){this.paused=false;}});
    return elements.get(key);
  }
  const fetcher=async(url,options={})=>{
    url=new URL(url);const body=options.body?JSON.parse(options.body):null;
    calls.push({url:url.href,path:url.pathname,options,body});
    assert.equal(url.origin,origin,'No off-origin requests are allowed in this fixture');
    if(url.pathname==='/deployment-config.json'){
      if(api.failConfig)throw new Error('Fixture configuration unavailable');
      return new Response(JSON.stringify({...config,enabled:api.enabled}));
    }
    if(url.pathname==='/audio-delivery.json'){
      const d=deferred();audioRequests.push(d);if(api.deferAudio)await d.promise;
      const track=catalog.tracks[0];
      return new Response(JSON.stringify(api.enableAudio?{schemaVersion:1,enabled:true,publicDeliveryVerified:true,
        catalogId:catalog.id,catalogSha256:manifest.files.catalog.sha256,
        tracks:[{id:track.id,available:true,url:'/audio/'+track.id.split(':')[1].padStart(6,'0')+'.mp3',bytes:track.audioBytes,sha256:track.audioSha256}]}:{enabled:false}));
    }
    if(url.pathname==='/v1/manifest'){
      const d=deferred();manifests.push(d);
      const reply=()=>new Response(JSON.stringify({...serverManifest,...(api.badManifest?{graphId:'wrong-graph'}:{})}));
      if(api.deferManifest){await d.promise;return reply();}
      return reply();
    }
    if(url.pathname==='/v1/cancel')return new Response('{}');
    if(url.pathname==='/v1/search'){
      const d=deferred(),record={body,options,...d};searches.push(record);
      if(api.deferSearch)return d.promise;
      return api.failSearch?new Response('{}',{status:503}):new Response(JSON.stringify(responseFor(body)));
    }
    assert.ok(url.pathname.startsWith('/search-studio/data/'),'Unexpected fetch: '+url.pathname);
    return new Response(await bytes(url.pathname.slice(1)));
  };
  let encoder,map;
  class Encoder{
    constructor({status}){encoder=this;this.status=status;this.state='unloaded';this.prepares=0;}
    set(state){this.state=state;this.status({state});}
    async prepare(){this.prepares++;this.set('loading');await Promise.resolve();this.set('ready');}
    encode(text){this.set('encoding');const d=deferred();encodes.push({text,...d});return d.promise.finally(()=>{if(this.state==='encoding')this.set('ready');});}
    cancel(){this.set('unloaded');}
  }
  class TestAudioMap{
    constructor(canvas,options){map=this;this.options=options;this.searches=[];this.playing=false;this.replays=0;this.pauses=0;}
    select(){}fit(){}draw(){}destroy(){}finish(){}zoom(){}
    replay(){this.replays++;this.playing=true;}pause(){this.pauses++;this.playing=false;}
    setSearch(trace,rows,options){this.searches.push({trace,rows,options});this.playing=options.animate;}
  }
  const context=vm.createContext({BrowserEncoder:Encoder,AudioMap:TestAudioMap,
    ServerSearch:class extends ServerSearch{constructor(options){super({...options,fetcher});}},
    loadDeploymentConfig:options=>loadDeploymentConfig({...options,fetcher}),
    loadAudioDelivery:options=>loadAudioDelivery({...options,fetcher}),previewForTrack,UNAVAILABLE_PREVIEW,SERVER_CONFIG,
    reviewQueryLimits,indexConnections,MANIFEST_SHA,ARTIST_METADATA_SHA,rankCandidates,HNSW,exactSearch,RELEASE,metadataSearch,
    fetch:fetcher,crypto:webcrypto,TextDecoder,TextEncoder,Float32Array,Uint8Array,URL,Blob,DOMException,performance,
    getComputedStyle:()=>({getPropertyValue:()=> '#000'}),
    document:{body:{dataset:{}},activeElement:null,querySelector:el,querySelectorAll:selector=>selector==='[data-needs-catalog]'?[el('#search'),el('#enable-local')]:[],addEventListener(name,fn){clicks[name]=fn;}},
    location:{href:origin+'/search-studio/',origin},window:{addEventListener(name,fn){events[name]=fn;}}});
  vm.runInContext(source,context);
  const ready=vm.runInContext('init()',context);
  if(deferManifest)await until(()=>manifests.length===1);else await ready;
  assert.equal(context.document.body.dataset.ready,'true',el('#status').textContent);
  const submit=async text=>{el('#query').value=text;el('#query-form').handlers.submit({preventDefault(){}});await flush();};
  const clickExample=async id=>{const button={dataset:{example:id}};clicks.click({target:{closest:()=>button}});await flush();};
  return{el,context,events,calls,searches,manifests,encodes,audioRequests,encoder,map,api,ready,submit,clickExample,
    resolveSearch(i,example=0){searches[i].resolve(new Response(JSON.stringify(responseFor(searches[i].body,example))));}};
}

test('actual bootstrap uses the active release graph, keeps recorded results labeled and sends no input/model work',async()=>{
  const h=await harness();
  assert.equal(h.map.options.tracks.length,manifest.count);assert.equal(h.map.searches.length,1);
  assert.equal(h.map.searches[0].options.animate,false);assert.ok(h.map.searches[0].trace.events.length);
  assert.equal(h.el('#engine-label').textContent,'Recorded example');assert.match(h.el('#results-source').textContent,/Recorded example/);
  assert.equal(h.el('#open-engine').textContent,'Server ready');assert.match(h.el('#search-processing').textContent,/Search is processed on this server/);
  assert.equal(h.searches.length,0);assert.equal(h.encoder.prepares,0);assert.equal(h.encodes.length,0);
  assert.ok(h.calls.every(c=>!c.body));
  h.el('#query').value='  Unsubmitted private text  ';h.el('#query').handlers.input?.();await flush();
  assert.equal(h.searches.length,0);assert.ok(h.calls.every(c=>!c.url.includes('Unsubmitted')));
});

test('actual form submits exact raw text with server search as default, including an exact recorded-example text',async()=>{
  const h=await harness();const raw='  Less SAD, no vocals?  ';
  await h.submit(raw);assert.equal(h.searches.length,1);assert.equal(h.searches[0].body.query,raw);
  assert.equal(h.el('#query').value,raw);assert.equal(h.el('#engine-label').textContent,'Live · server');
  assert.equal(h.el('#engine-dialog').open,undefined);assert.equal(h.encoder.prepares,0);
  assert.equal(h.map.searches.at(-1).options.animate,true);
  await h.submit(examples.examples[0].text);assert.equal(h.searches.length,2);
  assert.equal(h.el('#engine-label').textContent,'Live · server');
});

test('submit while readiness is pending fails honestly, queues no text and requires a fresh deliberate submit',async()=>{
  const h=await harness({deferManifest:true});
  assert.equal(h.el('#search').disabled,true);assert.equal(h.el('#search').textContent,'Checking server…');
  await h.submit('first pending');await h.submit('  second pending  ');
  assert.equal(h.searches.length,0);assert.match(h.el('#status').textContent,/Nothing was sent.*Submit again/);
  h.el('#query').value='  latest edited, not submitted  ';
  h.manifests[0].resolve();await h.ready;await flush();
  assert.equal(h.searches.length,0);assert.equal(h.el('#query').value,'  latest edited, not submitted  ');
  assert.equal(h.el('#search').disabled,false);assert.equal(h.el('#search').textContent,'Find music');
  assert.equal(h.el('#engine-label').textContent,'Recorded example');
  await h.submit('  latest deliberate SUBMIT  ');
  assert.deepEqual(h.searches.map(s=>s.body.query),['  latest deliberate SUBMIT  ']);
});

test('configuration and manifest failure never submit or replay an exact example through the ordinary form',async()=>{
  for(const options of [{enabled:false},{badManifest:true},{failConfig:true}]){
    const h=await harness(options);const count=h.map.searches.length;
    await h.submit(examples.examples[1].text);
    assert.equal(h.searches.length,0);assert.equal(h.map.searches.length,count);
    assert.match(h.el('#status').textContent,/unavailable.*Nothing was sent/);
    assert.equal(h.el('#engine-label').textContent,'Recorded example');
    h.api.enabled=true;h.api.badManifest=false;h.api.failConfig=false;
    await h.submit('retry, not queued');assert.equal(h.searches.length,0);
    assert.match(h.el('#status').textContent,/Server ready.*Submit.*again/);
    await h.submit('  now ready  ');assert.equal(h.searches.at(-1).body.query,'  now ready  ');
  }
});

test('server failure keeps honest result provenance and never falls back to recorded or on-device search',async()=>{
  const h=await harness();h.api.failSearch=true;
  await h.submit(examples.examples[1].text);
  assert.equal(h.searches.length,1);assert.equal(h.map.searches.length,1);
  assert.equal(h.el('#engine-label').textContent,'Recorded example');assert.match(h.el('#status').textContent,/Server search failed.*no fallback/);
  assert.equal(h.encoder.prepares,0);assert.equal(h.encodes.length,0);
});

test('latest server response wins; actual example click cancels and locally replays its saved query',async()=>{
  const h=await harness({deferSearch:true});
  await h.submit('old submitted');await h.submit('  new submitted  ');
  assert.equal(h.searches.length,2);assert.equal(h.searches[0].options.signal.aborted,true);
  h.resolveSearch(1,1);await flush();h.resolveSearch(0);await flush();
  assert.equal(h.el('#query').value,'  new submitted  ');assert.equal(h.map.searches.length,2);
  await h.submit('replaced by example');await h.clickExample('dev-03');h.resolveSearch(2);await flush();
  assert.equal(h.el('#engine-label').textContent,'Recorded example');assert.equal(h.el('#query').value,examples.examples[2].text);
  assert.equal(h.map.searches.length,3);assert.equal(h.map.searches.at(-1).options.animate,true);
  const cancellations=h.calls.filter(c=>c.path==='/v1/cancel');assert.ok(cancellations.length>=2);
  for(const c of cancellations)assert.deepEqual(Object.keys(c.body).sort(),['generation','requestId']);
});

test('explicit local choice during readiness stays selected and the actual form uses only the local encoder',async()=>{
  const h=await harness({deferManifest:true});await h.el('#enable-local').onclick();
  h.manifests[0].resolve();await h.ready;
  assert.equal(h.el('#open-engine').textContent,'On-device ready');assert.match(h.el('#search-processing').textContent,/on this device/);
  await h.submit('  local exact raw query  ');assert.equal(h.searches.length,0);assert.equal(h.encodes.length,1);
  assert.equal(h.encodes[0].text,'  local exact raw query  ');
  h.encodes[0].resolve({encoded:{spaceId:RELEASE.encoderSpaceId,modelSha256:RELEASE.textAssets[0].sha256,tokenizerSha256:RELEASE.textAssets[1].sha256,vector:examples.examples[0].queryVector}});
  await until(()=>h.el('#engine-label').textContent==='Live · on device');
});

test('switching engine never submits input, and paused ordinary submits cannot silently replay an example',async()=>{
  const h=await harness();h.el('#query').value='unsent';await h.el('#use-server').onclick();
  assert.equal(h.el('#open-engine').textContent,'Live search paused');
  await h.submit(examples.examples[1].text);assert.equal(h.searches.length,0);assert.equal(h.map.searches.length,1);
  assert.match(h.el('#status').textContent,/paused/);
  await h.el('#use-server').onclick();assert.equal(h.searches.length,0);
  assert.equal(h.el('#engine-label').textContent,'Recorded example');
});

test('BFCache preserves explicit local or paused preference without downloading or transmitting on restore',async()=>{
  for(const preference of ['local','paused']){
    const h=await harness();
    if(preference==='local')await h.el('#enable-local').onclick();else await h.el('#use-server').onclick();
    const prepares=h.encoder.prepares;h.events.pagehide({persisted:true});await h.events.pageshow({persisted:true});
    assert.equal(h.searches.length,0);assert.equal(h.encoder.prepares,prepares);
    assert.equal(h.el('#open-engine').textContent,preference==='local'?'On-device off':'Live search paused');
    await h.submit('  do not switch to server  ');assert.equal(h.searches.length,0);assert.equal(h.encodes.length,0);
    assert.equal(h.el('#engine-label').textContent,'Recorded example');
  }
});

test('pagehide suppresses pending readiness and search; default server restore only checks availability',async()=>{
  const h=await harness({deferManifest:true});h.events.pagehide({persisted:true});h.manifests[0].resolve();await h.ready;
  assert.equal(vm.runInContext('server',h.context),null);assert.equal(h.searches.length,0);
  h.api.deferManifest=false;await h.events.pageshow({persisted:true});assert.equal(h.el('#open-engine').textContent,'Server ready');
  h.api.deferSearch=true;await h.submit('closed pending');h.events.pagehide({persisted:true});h.resolveSearch(0);await flush();
  assert.equal(h.map.searches.length,1);assert.equal(h.searches[0].options.signal.aborted,true);
  await h.events.pageshow({persisted:true});assert.equal(h.searches.length,1);assert.equal(h.el('#engine-label').textContent,'Recorded example');
});

test('title lookup remains local and cancels an outstanding server query on the actual selector event',async()=>{
  const h=await harness({deferSearch:true});await h.submit('pending server');
  h.el('#query-kind').value='lookup';h.el('#query-kind').handlers.change();h.resolveSearch(0);await flush();
  const raw='  '+catalog.tracks[0].title+'  ';await h.submit(raw);
  assert.equal(h.searches.length,1);assert.equal(h.el('#engine-label').textContent,'Title / artist');
  assert.match(h.el('#search-processing').textContent,/lookup stays in this browser/);assert.equal(h.el('#query').value,raw);
  assert.equal(h.map.searches.at(-1).trace.events.length,0);assert.equal(h.map.searches.at(-1).rows[0].score,null);
});

test('replay, skip, neighbors and reduced-motion callback retain the existing graph behavior without server queries',async()=>{
  const h=await harness();h.el('#trace-play').onclick();assert.equal(h.map.replays,1);
  h.map.options.onTrace({total:1,completed:true,progress:1,reducedMotion:true});
  assert.equal(h.el('#trace-note').textContent,'Motion reduced');assert.equal(h.el('#trace-skip').hidden,true);
  h.el('#trace-skip').onclick();vm.runInContext('nearby(0)',h.context);
  assert.equal(h.searches.length,0);assert.equal(h.el('#engine-label').textContent,'Audio neighbors');
  assert.ok(h.map.searches.at(-1).rows.every(r=>r.row!==0));assert.ok(h.map.searches.at(-1).trace.events.length);
});

test('visible disclosure and privacy page describe the actual service policy without claiming zero host retention',async()=>{
  const html=(await bytes('search-studio/index.html')).toString();const privacy=(await bytes('notices/search-privacy.html')).toString();
  assert.match(html,/aria-describedby="search-privacy"/);assert.match(html,/id="search-processing">Search is processed on this server/);
  assert.match(html,/href="\/notices\/search-privacy.html">Privacy/);
  assert.match(privacy,/Application query and access logging is disabled/);assert.match(privacy,/provider may retain infrastructure and connection metadata/);
  assert.doesNotMatch(privacy,/never (?:store|log)|zero logs|no data is retained/i);
});


test('BFCache audio restoration completes during a new query without replacing search status, and a second pagehide rejects it',async()=>{
  const h=await harness({deferSearch:true});h.events.pagehide({persisted:true});h.api.deferAudio=true;h.api.enableAudio=true;
  const restore=h.events.pageshow({persisted:true});await until(()=>h.audioRequests.length===2);
  await h.submit('new query during audio restoration');const searching=h.el('#status').textContent;
  h.audioRequests[1].resolve();await restore;
  assert.match(h.el('#audio-availability').textContent,new RegExp('1 of '+catalog.tracks.length));assert.equal(h.el('#status').textContent,searching);
  h.resolveSearch(0);await flush();assert.equal(h.el('#engine-label').textContent,'Live · server');
  h.events.pagehide({persisted:true});const obsolete=h.events.pageshow({persisted:true});await until(()=>h.audioRequests.length===3);
  h.events.pagehide({persisted:true});h.audioRequests[2].resolve();await obsolete;
  assert.equal(vm.runInContext('audioDelivery.size',h.context),0);assert.equal(h.el('#player').hidden,true);
});
