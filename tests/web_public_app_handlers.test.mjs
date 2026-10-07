import {sourceGenres,refineCandidates,resultPage,resultScope,compactResultScope} from '../web/search-studio/src/results-view.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {reviewQueryLimits} from '../web/search-studio/src/query-limits.mjs';
import {indexConnections} from '../web/search-studio/src/search-motion.mjs';
import {v1Data,V1_MANIFEST_SHA as MANIFEST_SHA,V1_ARTIST_METADATA_SHA as ARTIST_METADATA_SHA} from './v1_page_data.mjs';
import {rankCandidates} from '../web/search-studio/src/rerank.mjs';
import {ServerSearch,publicCharacterLimit} from '../web/search-studio/src/contracts.mjs';
import {SERVER_CONFIG,loadDeploymentConfig} from '../web/search-studio/src/server-config.mjs';
import {loadAudioDelivery,previewForTrack,UNAVAILABLE_PREVIEW} from '../web/search-studio/src/audio-delivery.mjs';
import {HNSW,exactSearch} from '../web/search-studio/src/hnsw.mjs';
import {RELEASE} from '../web/listen-lab/src/release.mjs';
import {metadataSearch} from '../web/listen-lab/src/retrieval.mjs';
import {loadServing,bindServing} from '../web/search-studio/src/serving.mjs';

// Execute the checked-in app, including init, form/click handlers and page lifecycle.
// Only DOM/canvas and encoder hardware are fixtures; the real pack, retrieval,
// configuration validator and ServerSearch request/response boundary run offline.
const root=new URL('../web/',import.meta.url), origin='https://music.example';
const bytes=path=>readFile(new URL(path,root));
const manifest=JSON.parse(await v1Data('manifest.json'));
const catalog=JSON.parse(await v1Data('catalog.json'));
const examples=JSON.parse(await v1Data('examples.json'));
const vectorBytes=await v1Data('vectors.f32');
const vectors=new Float32Array(vectorBytes.buffer,vectorBytes.byteOffset,vectorBytes.byteLength/4);
const graph=HNSW.load(JSON.parse(await v1Data('index.json')),vectors);
const serverManifest={catalogId:manifest.catalogId,graphId:manifest.graphId,indexSha256:manifest.indexSha256,
  catalogSha256:manifest.files.catalog.sha256,vectorsSha256:manifest.vectorsSha256,
  engineId:manifest.allowedQueryProfiles.find(p=>p.kind==='server-live').id,deploymentGeneration:'fixture-generation',maxQueryUtf8Bytes:2048,
  publicPreview:{anonymous:true,maxQueryCharacters:512,searchesPerMinute:6,searchesPerProcessHour:30,audioEnabled:false}};
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
async function harness({deferManifest=false,enabled=true,badManifest=false,failConfig=false,deferSearch=false,direction=null,phone=false}={}){
  const elements=new Map(),events={},clicks={},calls=[],searches=[],manifests=[],encodes=[],audioRequests=[];
  const api={enabled,badManifest,failConfig,deferManifest,deferSearch,failSearch:false,searchStatus:503,searchError:'fixture failure',searchRetryAfter:null,searchTransportError:false};
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
    if(url.pathname==='/serving.json')return new Response('{"error":"Not found"}',{status:404});// no serving list: every row is served
    if(url.pathname==='/v1/search'){
      const d=deferred(),record={body,options,...d};searches.push(record);
      if(api.deferSearch)return d.promise;
      if(api.searchTransportError)throw new TypeError('Failed to fetch');
      if(api.oversizedReply){const reply=responseFor(body);reply.results=exactSearch(vectors,new Float32Array(examples.examples[0].queryVector),api.oversizedReply).map(r=>({id:catalog.tracks[r.id].id,row:r.id,cosineSimilarity:1-r.distance}));return new Response(JSON.stringify(reply));}
      return api.failSearch?new Response(JSON.stringify({error:api.searchError}),{status:api.searchStatus,headers:api.searchRetryAfter?{'retry-after':String(api.searchRetryAfter)}:{}}):new Response(JSON.stringify(responseFor(body)));
    }
    assert.ok(url.pathname.startsWith('/search-studio/data/'),'Unexpected fetch: '+url.pathname);
    return new Response(await v1Data(url.pathname.slice('/search-studio/data/'.length)));
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
    loadServing:options=>loadServing({...options,fetcher}),bindServing,
    sourceGenres,refineCandidates,resultPage,resultScope,compactResultScope,reviewQueryLimits,indexConnections,MANIFEST_SHA,ARTIST_METADATA_SHA,rankCandidates,HNSW,exactSearch,RELEASE,metadataSearch,publicCharacterLimit,
    fetch:fetcher,crypto:webcrypto,TextDecoder,TextEncoder,Float32Array,Uint8Array,URL,Blob,DOMException,performance,
    getComputedStyle:()=>({getPropertyValue:()=> '#000'}),
    document:{body:{dataset:{}},activeElement:null,querySelector:el,querySelectorAll:selector=>selector==='[data-needs-catalog]'?[el('#search'),el('#enable-local')]:[],addEventListener(name,fn){clicks[name]=fn;}},
    ...(phone?{matchMedia:query=>({matches:query==='(max-width: 760px)',addEventListener(){}})}:{}),
    location:{href:origin+'/search-studio/'+(direction?'?direction='+direction:''),origin},window:{addEventListener(name,fn){events[name]=fn;}}});
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
  // Under reduced motion replay() finishes immediately, so "Watch again" is not offered as a live control.
  assert.equal(h.el('#trace-note').textContent,'Animation off (reduced motion)');assert.equal(h.el('#trace-skip').hidden,true);assert.equal(h.el('#trace-play').disabled,true);
  h.map.options.onTrace({total:1,completed:true,progress:1,reducedMotion:false});assert.equal(h.el('#trace-play').disabled,false);assert.equal(h.el('#trace-note').textContent,'Ready');
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
  assert.equal(h.el('#audio-availability').textContent,`1 of ${catalog.tracks.length.toLocaleString()} recordings have verified previews.`);assert.equal(h.el('#status').textContent,searching);
  h.resolveSearch(0);await flush();assert.equal(h.el('#engine-label').textContent,'Live · server');
  h.events.pagehide({persisted:true});const obsolete=h.events.pageshow({persisted:true});await until(()=>h.audioRequests.length===3);
  h.events.pagehide({persisted:true});h.audioRequests[2].resolve();await obsolete;
  assert.equal(vm.runInContext('audioDelivery.size',h.context),0);assert.equal(h.el('#player').hidden,true);
});

test('sound candidates page 12 then 4 without fetching deeper results or changing source ranks',async()=>{
  const h=await harness();
  const first=Array.from(vm.runInContext('rows.map(r=>r.row)',h.context));
  assert.equal(first.length,12);assert.match(h.el('#result-scope-detail').textContent,/12 of 16 from 16 retrieved sound candidates/);
  assert.equal(h.el('#result-scope').textContent,'1–12 of 16 · searched 1,992');
  h.el('#next-page').onclick();
  const next=Array.from(vm.runInContext('rows.map(r=>r.row)',h.context));
  assert.equal(next.length,4);assert.equal(new Set([...first,...next]).size,16);
  assert.equal(h.el('#next-page').disabled,true);assert.equal(h.searches.length,0);
  assert.deepEqual(Array.from(vm.runInContext('rows.map(r=>r.sourceRank)',h.context)),[13,14,15,16]);
  assert.equal(h.map.searches.at(-1).options.animate,false);
});

test('collection browse and source-genre/name refinements cover the full real catalog locally',async()=>{
  const h=await harness();h.el('#query').value='typed but not yet submitted';h.el('#browse-collection').onclick();
  // Browse is a view of the catalog, not a mode switch: the visible selector and unsent text stay as they were.
  assert.equal(h.el('#query-kind').value,'description');assert.equal(h.el('#query').value,'typed but not yet submitted');
  assert.match(h.el('#query-label').textContent,/Describe the music/);assert.equal(h.el('#engine-label').textContent,'Catalog browse');
  assert.equal(h.el('#results-heading').textContent,'Collection');assert.match(h.el('#results-source').textContent,/^Collection browse · local metadata$/);
  assert.equal(vm.runInContext('candidateRows.length',h.context),catalog.tracks.length);
  assert.match(h.el('#result-scope-detail').textContent,/^Showing 1–12 of 1,992 recordings · 1,992 recordings in the collection/);
  assert.equal(h.el('#result-scope').textContent,'1–12 of 1,992 recordings');
  assert.equal(h.el('#page-indicator').textContent,'1–12 / 1,992');assert.equal(h.el('#page-position').textContent,'Page 1 of 166');
  const genre=catalog.tracks[1200].genre;h.el('#genre-filter').value=genre;h.el('#genre-filter').handlers.change();
  const expected=catalog.tracks.filter(t=>t.genre===genre).length;
  assert.equal(vm.runInContext('viewPage.total',h.context),expected);assert.ok(vm.runInContext('rows.length',h.context)<=12);
  h.el('#refine-text').value=catalog.tracks[1200].artist;h.el('#refine-text').handlers.input();
  assert.ok(vm.runInContext('viewPage.total',h.context)>0);assert.equal(h.searches.length,0);
  let focused=null;h.el('#refine-text').focus=()=>{focused='refine-text';};
  h.el('#clear-refinements').onclick();assert.equal(vm.runInContext('viewPage.total',h.context),catalog.tracks.length);
  assert.equal(focused,'refine-text','Clear refinements keeps keyboard focus in the panel');
  await h.submit('a sound description after browsing');assert.equal(h.searches.length,1);assert.equal(h.el('#engine-label').textContent,'Live · server');
});

test('empty refined sound results can be cleared without issuing a new query or misreporting scope',async()=>{
  const h=await harness();h.el('#refine-text').value='No such recorded name 000000';h.el('#refine-text').handlers.input();
  assert.equal(h.el('#results-empty').hidden,false);assert.equal(h.el('#focus-track').disabled,true);
  assert.match(h.el('#result-scope-detail').textContent,/Showing 0 from 16/);assert.equal(h.el('#result-scope').textContent,'0 of 16 · searched 1,992');
  assert.match(h.el('#empty-detail').textContent,/retrieved sound candidates/);
  h.el('#empty-clear').onclick();assert.equal(h.el('#results-empty').hidden,true);assert.equal(h.searches.length,0);
  h.el('#refine-text').value='No such recorded name 000000';h.el('#refine-text').handlers.input();
  let focused=false;h.el('#results-heading').focus=()=>{focused=true;};h.el('#empty-browse').onclick();
  assert.equal(h.el('#results-empty').hidden,true);assert.equal(h.el('#refine-text').value,'');assert.equal(focused,true);
  assert.equal(vm.runInContext('viewPage.total',h.context),catalog.tracks.length);assert.equal(h.searches.length,0);
});

test('cancel returns to the previous result state and rejects a late successful response',async()=>{
  const h=await harness({deferSearch:true});const previous=h.el('#results-source').textContent;
  await h.submit('new cancellable query');assert.equal(h.el('#results-region').attributes['aria-busy'],'true');
  assert.equal(h.el('#cancel-search').hidden,false);h.el('#cancel-search').onclick();h.resolveSearch(0,1);await flush();
  assert.equal(h.el('#results-region').attributes['aria-busy'],'false');assert.equal(h.el('#cancel-search').hidden,true);
  assert.equal(h.el('#results-source').textContent,previous);assert.match(h.el('#status').textContent,/cancelled.*Previous results/);
  assert.equal(h.searches[0].options.signal.aborted,true);
});

test('current playback survives result paging and stays marked when its row returns',async()=>{
  const h=await harness();h.api.enableAudio=true;await h.events.pageshow({persisted:true});
  h.el('#browse-collection').onclick();await vm.runInContext('play(0)',h.context);h.el('#audio').handlers.play();
  assert.match(h.el('#results').innerHTML,/is-playing/);assert.match(h.el('#results').innerHTML,/>Playing</);
  const url=h.el('#audio').src;h.el('#next-page').onclick();
  assert.equal(h.el('#audio').src,url);assert.equal(h.el('#player').hidden,false);
  h.el('#previous-page').onclick();assert.match(h.el('#results').innerHTML,/>Playing</);
  h.el('#audio').pause();h.el('#audio').handlers.pause();assert.match(h.el('#results').innerHTML,/>Paused</);
  assert.equal(h.searches.length,0);
});

test('paged list, map and kept-track export agree on absolute display ranks',async()=>{
  const h=await harness();h.el('#next-page').onclick();
  assert.deepEqual(Array.from(h.map.searches.at(-1).rows.map(r=>r.displayRank)),[13,14,15,16]);
  vm.runInContext('keep(rows[0].row)',h.context);h.el('#export').onclick();
  const exported=JSON.parse(h.el('#export-content').value);
  assert.equal(exported.view.page,2);assert.equal(exported.view.candidateCount,16);
  assert.deepEqual(exported.rankingContext.displayed.map(r=>r.displayRank),[13,14,15,16]);
});

test('cancelling on-device work unloads the worker and does not silently change engine',async()=>{
  const h=await harness();await h.el('#enable-local').onclick();await h.submit('local cancellable');
  assert.equal(h.encoder.state,'encoding');h.el('#cancel-search').onclick();
  assert.equal(h.encoder.state,'unloaded');assert.equal(vm.runInContext('inflight',h.context),null);
  assert.match(h.el('#status').textContent,/cancelled and model unloaded/);assert.equal(h.el('#open-engine').textContent,'On-device off');
  h.encodes[0].reject(new DOMException('Stopped fixture worker','AbortError'));await flush();
  assert.equal(h.el('#results-region').attributes['aria-busy'],'false');assert.equal(h.searches.length,0);
});

test('server refusals are explained in the server’s own terms and keep the honest no-fallback statement',async()=>{
  const h=await harness();h.api.failSearch=true;
  assert.match(h.el('#server-detail').textContent,/allow 6 searches per minute and 30 per hour, up to 512 characters each/);
  assert.equal(h.el('#query').maxLength,512);assert.match(h.el('#search-processing').textContent,/Up to 512 characters/);
  h.api.searchStatus=429;h.api.searchError='Public preview budget exhausted';h.api.searchRetryAfter=10;
  await h.submit('seventh search this minute');
  assert.match(h.el('#status').textContent,/^Server search failed: search limit reached \(anonymous previews allow 6 searches per minute and 30 per hour\)\. Previous results remain; no fallback search was run\. Try again in 10 s\.$/);
  assert.equal(h.el('#open-engine').textContent,'Server ready');assert.equal(h.el('#engine-label').textContent,'Recorded example');
  h.api.searchStatus=400;h.api.searchError='Invalid or oversized preview request';h.api.searchRetryAfter=null;
  await h.submit('refused by the server');
  assert.match(h.el('#status').textContent,/refused this description \(Invalid or oversized preview request\); it accepts at most 512 characters.*no fallback.*Shorten it/);
  assert.equal(h.el('#open-engine').textContent,'Server ready');
  h.api.searchStatus=409;h.api.searchError='Encoder, catalog or deployment identity mismatch; reload manifest';
  await h.submit('stale identity');
  assert.match(h.el('#status').textContent,/no longer matches the server release \(Encoder, catalog or deployment identity mismatch; reload manifest\).*Reload the page/);
  assert.equal(h.el('#open-engine').textContent,'Server unavailable');assert.equal(h.searches.length,3);assert.equal(h.encodes.length,0);
});

test('a server error or transport failure marks the server unavailable; the next submit re-checks instead of retrying blindly',async()=>{
  for(const failure of [{searchStatus:503,searchError:'Service unavailable',expected:/^Server search failed: server error \(503: Service unavailable\)\. Previous results remain; no fallback search was run\. Try again later\.$/},
                        {searchTransportError:true,expected:/^Server search failed: server unreachable \(Failed to fetch\)\. Previous results remain; no fallback search was run\. Check the connection and submit again; availability is re-checked first\.$/}]){
    const h=await harness();Object.assign(h.api,{failSearch:true,...failure});
    const source=h.el('#results-source').textContent;
    await h.submit('fails at the server');
    assert.equal(h.searches.length,1);assert.equal(h.calls.filter(c=>c.path==='/v1/search').length,1);
    assert.match(h.el('#status').textContent,failure.expected);
    assert.equal(h.el('#open-engine').textContent,'Server unavailable');assert.equal(h.el('#use-server').textContent,'Check server availability');
    assert.equal(h.el('#server-state').textContent,'Unavailable');assert.match(h.el('#server-detail').textContent,/did not complete the last search\. Nothing else was sent/);
    assert.equal(h.el('#results-source').textContent,source);assert.equal(h.el('#results-region').attributes['aria-busy'],'false');
    assert.equal(vm.runInContext('server',h.context),null);
    Object.assign(h.api,{failSearch:false,searchTransportError:false});
    const manifests=h.calls.filter(c=>c.path==='/v1/manifest').length;
    await h.submit('after recovery');
    assert.equal(h.calls.filter(c=>c.path==='/v1/manifest').length,manifests+1);
    assert.equal(h.calls.filter(c=>c.path==='/v1/search').length,1,'the re-check sends no description');
    assert.match(h.el('#status').textContent,/Server ready\. Submit your description again/);assert.equal(h.el('#open-engine').textContent,'Server ready');
    await h.submit('deliberately submitted again');assert.equal(h.calls.filter(c=>c.path==='/v1/search').length,2);
    assert.equal(h.el('#engine-label').textContent,'Live · server');
  }
});

test('descriptions over the published public character limit are refused before transmission, and the cap does not apply to local lookup',async()=>{
  const h=await harness();const long='x'.repeat(513);
  await h.submit(long);
  assert.equal(h.searches.length,0);assert.equal(h.calls.filter(c=>c.path==='/v1/search').length,0);
  assert.match(h.el('#status').textContent,/^This description is 513 characters; this server accepts at most 512\. Nothing was sent\. Shorten it and submit again\.$/);
  assert.equal(h.el('#results-region').attributes['aria-busy'],'false');
  await h.submit('x'.repeat(512));assert.equal(h.searches.length,1);assert.equal(h.searches[0].body.query.length,512);
  h.el('#query-kind').value='lookup';h.el('#query-kind').handlers.change();
  assert.equal(h.el('#query').maxLength,4096);
  await h.submit(long);assert.equal(h.searches.length,1);
  h.el('#query-kind').value='description';h.el('#query-kind').handlers.change();assert.equal(h.el('#query').maxLength,512);
  await h.el('#use-server').onclick();assert.equal(h.el('#query').maxLength,4096);
});

test('a retained refinement that hides new results is stated on the status line, not only in the scope line',async()=>{
  const h=await harness();h.el('#refine-text').value='no such recorded name 000000';h.el('#refine-text').handlers.input();
  await h.submit('new sound search under a stale refinement');
  assert.equal(h.searches.length,1);assert.equal(vm.runInContext('rows.length',h.context),0);assert.equal(h.el('#results-empty').hidden,false);
  assert.match(h.el('#result-scope-detail').textContent,/^Showing 0 from 16 retrieved sound candidates/);assert.equal(h.el('#result-scope').textContent,'0 of 16 · searched 1,992');
  assert.match(h.el('#status').textContent,/Results ready\..*Refinements hide 16 of 16; clear them to see every result\.$/);
  h.el('#refine-text').value='';h.el('#refine-text').handlers.input();
  await h.submit('a second search with no refinement');
  assert.doesNotMatch(h.el('#status').textContent,/Refinements hide/);assert.equal(vm.runInContext('rows.length',h.context),12);
  h.el('#genre-filter').value=catalog.tracks[vm.runInContext('rows[0].row',h.context)].genre;h.el('#genre-filter').handlers.change();
  const shown=vm.runInContext('viewPage.total',h.context);await h.clickExample('dev-02');
  const after=vm.runInContext('viewPage.total',h.context);
  if(after<16)assert.match(h.el('#status').textContent,new RegExp(`Refinements hide ${16-after} of 16`));else assert.doesNotMatch(h.el('#status').textContent,/Refinements hide/);
  assert.ok(shown>=1);
});

test('the map draws sound matches numbered, name matches unnumbered and browse pages not at all; refinements keep its view',async()=>{
  const h=await harness();const count=h.map.searches.length;
  assert.equal(h.el('#fit').disabled,false);assert.deepEqual(h.map.searches.at(-1).rows.map(r=>r.displayRank),[1,2,3,4,5,6,7,8,9,10,11,12]);
  assert.notEqual(h.map.searches.at(-1).options.preserveView,true);
  for(const text of ['p','pi','pia']){h.el('#refine-text').value=text;h.el('#refine-text').handlers.input();}
  assert.equal(h.map.searches.length,count+3);assert.ok(h.map.searches.slice(-3).every(s=>s.options.preserveView===true&&s.options.animate===false));
  h.el('#refine-text').value='';h.el('#refine-text').handlers.input();h.el('#next-page').onclick();
  assert.equal(h.map.searches.at(-1).options.preserveView,true);assert.deepEqual(h.map.searches.at(-1).rows.map(r=>r.displayRank),[13,14,15,16]);
  h.el('#browse-collection').onclick();
  assert.equal(h.map.searches.at(-1).rows.length,0);assert.equal(h.map.searches.at(-1).options.preserveView,false);assert.equal(h.el('#fit').disabled,true);
  assert.equal(vm.runInContext('rows.length',h.context),12);
  h.el('#query-kind').value='lookup';h.el('#query-kind').handlers.change();await h.submit(catalog.tracks[0].artist);
  assert.ok(h.map.searches.at(-1).rows.length>0);assert.ok(h.map.searches.at(-1).rows.every(r=>r.displayRank===null));assert.equal(h.el('#fit').disabled,false);
  h.el('#query-kind').value='description';h.el('#query-kind').handlers.change();await h.clickExample('dev-02');
  assert.deepEqual(h.map.searches.at(-1).rows.map(r=>r.displayRank).slice(0,3),[1,2,3]);assert.equal(h.map.searches.at(-1).options.animate,true);
});

test('an over-long server reply is rejected before any label or row changes, and later refinements still work',async()=>{
  const h=await harness();h.api.oversizedReply=21;
  const before={source:h.el('#results-source').textContent,heading:h.el('#results-heading').textContent,label:h.el('#engine-label').textContent,rows:Array.from(vm.runInContext('rows.map(r=>r.row)',h.context)),candidates:vm.runInContext('candidateRows.length',h.context)};
  await h.submit('a query answered with 21 rows');
  assert.equal(h.searches.length,1);assert.match(h.el('#status').textContent,/^Server search failed: Invalid search results\. Previous results remain; no fallback search was run\./);
  assert.equal(h.el('#results-source').textContent,before.source);assert.equal(h.el('#results-heading').textContent,before.heading);assert.equal(h.el('#engine-label').textContent,before.label);
  assert.deepEqual(Array.from(vm.runInContext('rows.map(r=>r.row)',h.context)),before.rows);assert.equal(vm.runInContext('candidateRows.length',h.context),before.candidates);
  assert.equal(h.el('#open-engine').textContent,'Server ready');
  h.el('#refine-text').value='';h.el('#refine-text').handlers.input();h.el('#next-page').onclick();
  assert.equal(vm.runInContext('rows.length',h.context),4);assert.equal(h.el('#results-region').attributes['aria-busy'],'false');
  // The same guard protects showResult itself: a packet the display policy rejects changes nothing.
  assert.throws(()=>vm.runInContext(`showResult({text:'bad',ranked:Array.from({length:21},(_,i)=>({row:i,score:.5})),trace:{events:[],finalResults:[]},label:'Live search · server Q8',timing:'x'})`,h.context),/Unsupported display policy/);
  assert.equal(h.el('#results-source').textContent,before.source);assert.equal(vm.runInContext('candidateRows.length',h.context),before.candidates);
});

test('pagination scales: First/Last, a page jump and compact top controls reach any of the 166 browse pages',async()=>{
  const h=await harness();h.el('#browse-collection').onclick();
  const pages=Math.ceil(catalog.tracks.length/12);
  assert.equal(h.el('#page-position').textContent,`Page 1 of ${pages}`);assert.equal(h.el('#page-count').textContent,String(pages));assert.equal(h.el('#page-number').value,'1');assert.equal(h.el('#page-number').max,pages);
  assert.equal(h.el('#first-page').disabled,true);assert.equal(h.el('#previous-page-top').disabled,true);assert.equal(h.el('#last-page').disabled,false);assert.equal(h.el('#next-page-top').disabled,false);assert.equal(h.el('#page-go').disabled,false);
  let focused=0;h.el('#results-heading').focus=()=>{focused++;};
  h.el('#page-number').value='50';h.el('#page-jump').handlers.submit({preventDefault(){}});
  assert.equal(h.el('#page-position').textContent,`Page 50 of ${pages}`);assert.equal(vm.runInContext('rows[0].displayRank',h.context),589);assert.equal(focused,1);
  h.el('#last-page').onclick();assert.equal(h.el('#page-position').textContent,`Page ${pages} of ${pages}`);assert.equal(vm.runInContext('rows.length',h.context),catalog.tracks.length-(pages-1)*12);
  assert.equal(h.el('#last-page').disabled,true);assert.equal(h.el('#next-page-top').disabled,true);assert.equal(h.el('#next-page').disabled,true);
  h.el('#previous-page-top').onclick();assert.equal(h.el('#page-position').textContent,`Page ${pages-1} of ${pages}`);
  h.el('#first-page').onclick();assert.equal(h.el('#page-position').textContent,`Page 1 of ${pages}`);assert.equal(vm.runInContext('rows[0].displayRank',h.context),1);
  h.el('#next-page-top').onclick();assert.equal(h.el('#page-position').textContent,`Page 2 of ${pages}`);
  for(const [input,expected] of [['999',pages],['0',1],['abc',1],['2.6',3]]){h.el('#page-number').value=input;h.el('#page-jump').handlers.submit({preventDefault(){}});assert.equal(h.el('#page-position').textContent,`Page ${expected} of ${pages}`);}
  assert.equal(h.searches.length,0);
  await h.clickExample('dev-01');assert.equal(h.el('#page-count').textContent,'2');assert.equal(h.el('#page-go').disabled,false);
  h.el('#genre-filter').value=catalog.tracks[vm.runInContext('rows[0].row',h.context)].genre;h.el('#genre-filter').handlers.change();
  if(vm.runInContext('viewPage.pages',h.context)<=1){assert.equal(h.el('#page-number').disabled,true);assert.equal(h.el('#page-go').disabled,true);}
});

test('startup defaults per composition: the Atlas map is open on every viewport (phones animate searches) with refinements collapsed; the List keeps its phone rule',async()=>{
  for(const [direction,phone,map,refinement] of [[null,false,true,false],[null,true,true,false],['atlas',true,true,false],['field',true,true,false],['list',false,true,true],['list',true,false,false]]){
    const h=await harness({direction,phone}),name=`${direction??'default'}${phone?' phone':''}`;
    assert.equal(h.context.document.body.dataset.direction,direction??'atlas',name);
    assert.equal(h.el('#map-panel').open,map,name+': map panel');assert.equal(h.el('.refinement').open,refinement,name+': refinement panel');
    assert.equal(h.map.searches[0].options.animate,false,name+': bootstrap never animates');
    await h.clickExample('dev-02');assert.equal(h.map.searches.at(-1).options.animate,map,name+': a recorded example animates exactly when the map is open');
  }
});

test('the List composition keeps the full scope sentence and the original heading on screen',async()=>{
  const h=await harness({direction:'list'});
  assert.match(h.el('#result-scope').textContent,/^Showing 1–12 of 16 from 16 retrieved sound candidates · searched 1,992 recordings\. Refinements apply to these candidates, not the full collection\.$/);
  assert.equal(h.el('#result-scope').textContent,h.el('#result-scope-detail').textContent);assert.equal(h.el('#results-heading').textContent,'Sound matches');
  h.el('#browse-collection').onclick();assert.match(h.el('#result-scope').textContent,/^Showing 1–12 of 1,992 recordings · 1,992 recordings in the collection/);
  const atlas=await harness();assert.equal(atlas.el('#results-heading').textContent,'Matches');assert.equal(atlas.el('#result-scope').textContent,'1–12 of 16 · searched 1,992');
});

test('the refinement summary counts active refinements and the preview filter appears only when it can act',async()=>{
  const h=await harness();
  assert.equal(h.el('.refinement').open,false,'the Atlas starts with refinements collapsed');assert.equal(h.el('#map-panel').open,true);
  assert.equal(h.el('#refinement-active').textContent,'');assert.equal(h.el('#preview-only-label').hidden,true,'no previews: the filter cannot change anything');
  h.el('#genre-filter').value=catalog.tracks[vm.runInContext('rows[0].row',h.context)].genre;h.el('#genre-filter').handlers.change();
  assert.equal(h.el('#refinement-active').textContent,' · 1 active');
  h.el('#refine-text').value='a';h.el('#refine-text').handlers.input();assert.equal(h.el('#refinement-active').textContent,' · 2 active');
  h.el('#clear-refinements').onclick();assert.equal(h.el('#refinement-active').textContent,'');
  h.api.enableAudio=true;await h.events.pageshow({persisted:true});
  assert.equal(h.el('#preview-only-label').hidden,false,'1 of 1,992 verified previews: the filter is offered');
  h.el('#preview-only').checked=true;h.el('#preview-only').handlers.change();assert.equal(h.el('#refinement-active').textContent,' · 1 active');
  h.events.pagehide({persisted:true});h.api.enableAudio=false;await h.events.pageshow({persisted:true});
  assert.equal(h.el('#preview-only-label').hidden,true);assert.equal(h.el('#preview-only').checked,false,'a filter that can no longer act is cleared');
  assert.equal(h.el('#refinement-active').textContent,'');
});

test('the About dialog derives its collection facts from the loaded data and labels the selected recording separately',async()=>{
  const h=await harness();
  const artists=JSON.parse(await v1Data('artist-records.json'));
  const artistCount=new Set(artists.rows.map(r=>r.artistId)).size,genreCount=sourceGenres(catalog.tracks.map((_,row)=>({row})),catalog.tracks).length;
  assert.equal(h.el('#collection-summary').textContent,`${catalog.tracks.length.toLocaleString()} FMA excerpts · ${artistCount.toLocaleString()} source artist IDs · ${genreCount.toLocaleString()} source genres`);
  assert.equal(artistCount,550);assert.equal(genreCount,14);
  const row=vm.runInContext('rows[1].row',h.context);vm.runInContext(`choose(${row},{explicit:true})`,h.context);
  const t=catalog.tracks[row];assert.equal(h.el('#selected-summary').textContent,`Selected: ${t.title} · ${t.artist} · ${t.genre||'no source genre'}`);assert.equal(h.el('#selected-summary').hidden,false);
  assert.match(h.el('#collection-summary').textContent,/^1,992 FMA excerpts/);
  const html=(await bytes('search-studio/index.html')).toString();
  assert.match(html,/<span data-catalog-count>1,992<\/span> public FMA recording records/);assert.doesNotMatch(html,/id="selection-summary"/);
  assert.match(html,/<details class="refinement"><summary>/);assert.match(html,/<\/nav>\s*<p id="audio-availability" class="fine">[^<]*<\/p><\/div>/);
  const about=html.slice(html.indexOf('<dialog id="about-dialog">'),html.indexOf('</dialog>',html.indexOf('<dialog id="about-dialog">')));
  assert.match(about,/<p class="list-foot">Sound similarity is not a relevance probability\. Source genres are catalog labels; vocals, language and mood are not verified filters\.<\/p>/);
  assert.match(about,/positions do not determine results/);assert.match(about,/zooming in reveals more points and stored index connections/);
  assert.equal([...html.matchAll(/class="list-foot"/g)].length,1);assert.doesNotMatch(html,/map-explainer/);
  // Compositions: every parsed direction has its link (the parse marks it aria-current), and the full scope sentence describes the list.
  for(const direction of ['atlas','list','field'])assert.match(about,new RegExp(`<a href="\\?direction=${direction}">`));
  assert.match(html,/<ol id="results" aria-describedby="result-scope-detail"><\/ol>/);assert.match(html,/<p id="result-scope-detail" class="sr-only"><\/p>/);
  assert.match(html,/<h1>What does it sound like\?<\/h1>/);assert.match(html,/<form id="query-form" novalidate><select id="query-kind" aria-label="Search by">/);
  // The display policy is a refinement option; results stay before the map in the DOM (skip link and keyboard order).
  assert.match(html,/<div class="refinement-controls">[\s\S]*<div class="result-policy">[\s\S]*<\/details>/);assert.ok(html.indexOf('id="results-region"')<html.indexOf('id="map-panel"'));
  assert.match(html,/<div class="results-meta"><span id="engine-label">/);assert.doesNotMatch(html,/<div class="query-kind"><span id="engine-label">/);
  // Both forms opt out of native validation so the page's own clamp (page jump) and limit message (description) are what users see.
  assert.match(html,/<form id="query-form" novalidate>/);assert.match(html,/<form id="page-jump" class="page-jump" novalidate>/);
});
