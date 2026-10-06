// Release format v2 in the page: the collection client, the map with a sparse layout and a weighted
// density sample, and the checked-in app driven against an emulated v2 server. The emulated server
// is built from the v1 fma2000 web data with the page's own JS rules (exactSearch, HNSW, metadataSearch,
// refineCandidates), so every page, packet and position it returns is the one the v1 page computed.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {sourceGenres,refineCandidates,resultPage,resultScope,compactResultScope,RESULT_PAGE_SIZE} from '../web/search-studio/src/results-view.mjs';
import {reviewQueryLimits} from '../web/search-studio/src/query-limits.mjs';
import {indexConnections} from '../web/search-studio/src/search-motion.mjs';
import {rankCandidates} from '../web/search-studio/src/rerank.mjs';
import {ServerSearch,publicCharacterLimit} from '../web/search-studio/src/contracts.mjs';
import {SERVER_CONFIG,loadDeploymentConfig} from '../web/search-studio/src/server-config.mjs';
import {loadAudioDelivery,previewForTrack,UNAVAILABLE_PREVIEW} from '../web/search-studio/src/audio-delivery.mjs';
import {HNSW,exactSearch} from '../web/search-studio/src/hnsw.mjs';
import {Collection,RowDelivery,TRACK_ID,loadRowDelivery,validateCollectionManifest} from '../web/search-studio/src/collection-api.mjs';
import {AudioMap} from '../web/search-studio/src/graph.mjs';
import {RELEASE} from '../web/listen-lab/src/release.mjs';
import {metadataSearch} from '../web/listen-lab/src/retrieval.mjs';

import {root,origin,bytes,v1,catalog,v1Examples,v1Layout,artists,indexJson,vectors,graph,hex,sha,count,identity,display,traceRows,labelRows,packet,
  examplesV2,bounds,layoutV2,layoutBytes,examplesBytes,manifestV2,manifestBytes,MANIFEST_SHA,serverManifest,bitset,deliverySummary,collectionTracks} from './v2_web_fixture.mjs';

// ---- collection client ------------------------------------------------------------------------
test('collection manifest validation pins identities, same-origin reads and the audio policy',()=>{
  assert.equal(validateCollectionManifest(manifestV2,origin),manifestV2);
  for(const change of [{format:1},{collectionApi:'https://elsewhere.example/collection/'},{indexSha256:'x'},{audioPolicy:{mode:'remote',origin:'http://cdn.example',pathPrefix:'/'}},
    {files:{layout:manifestV2.files.layout}},{count:0}])
    assert.throws(()=>validateCollectionManifest({...manifestV2,...change},origin),/Collection manifest/);
  assert.equal(validateCollectionManifest({...manifestV2,audioPolicy:{mode:'remote',origin:'https://cdn.example',pathPrefix:'/fma2000/'}},origin).audioPolicy.mode,'remote');
});

test('rows, pages, packets and the layout sample are validated before they reach the page',async()=>{
  const calls=[],c=new Collection({manifest:manifestV2,pageOrigin:origin,fetcher:async url=>{url=new URL(url);calls.push(url);
    if(url.pathname==='/collection/tracks')return new Response(JSON.stringify(collectionTracks(url.searchParams,new Set())));
    return new Response(JSON.stringify({...packet(vectors.slice(7*512,8*512),{exclude:7}),catalogId:v1.catalogId,graphId:v1.graphId,indexSha256:identity.indexSha256,releaseSha256:identity.releaseSha256,row:7}));}});
  const page=await c.page({channel:'browse',offset:1992,limit:12,facets:true});
  assert.deepEqual([page.rows.length,page.total,page.baseTotal,page.genres.length],[8,count,count,manifestV2.summary.genres.length]);
  assert.equal(c.tracks[1999].id,catalog.tracks[1999].id);assert.deepEqual(c.positions[1999],v1Layout.positions[1999]);assert.equal(c.tracks[0],undefined);
  await c.ensure([0,1,0,1999]);assert.equal(calls.at(-1).searchParams.get('rows'),'0,1');assert.equal(c.tracks[1].title,catalog.tracks[1].title);
  const found=await c.neighbors(7);assert.equal(found.ranked.length,16);assert.ok(found.ranked.every(r=>c.tracks[r.row]&&c.positions[r.row]));
  assert.ok(calls.every(u=>u.origin===origin&&u.pathname.startsWith('/collection/')));
  c.absorbTrack(display(3));assert.throws(()=>c.absorbTrack({...display(3),id:'fma:999999999'}),/identity changed/);
  assert.throws(()=>c.absorbTrack({...display(4),id:catalog.tracks[3].id}),/identity changed/);
  assert.throws(()=>c.absorbTrack({...display(3),position:[0,Infinity]}),/Invalid collection row/);
  const bad=packet(new Float32Array(v1Examples.examples[0].queryVector));bad.layout={rows:[],xy:[]};
  assert.throws(()=>new Collection({manifest:manifestV2,pageOrigin:origin}).absorbPacket(bad),/lacks positions/);
  const layout=new Collection({manifest:manifestV2,pageOrigin:origin}).loadLayout(layoutV2);
  assert.deepEqual(layout.connections,indexConnections(indexJson.links));assert.equal(layout.points.length,count);assert.equal(layout.weights,null);
  assert.throws(()=>new Collection({manifest:manifestV2,pageOrigin:origin}).loadLayout({...layoutV2,edges:[5,5,0]}),/Invalid layout links/);
});

test('preview availability comes from the pinned policy and the server bitset, never from catalog paths',async()=>{
  const c=new Collection({manifest:manifestV2,pageOrigin:origin});[0,1,2,3].forEach(r=>c.absorbTrack(display(r)));
  const delivery=new RowDelivery(c,deliverySummary([1,3]));
  assert.equal(delivery.size,2);assert.equal(delivery.get(catalog.tracks[0].id),undefined);
  const approved=delivery.get(catalog.tracks[1].id);assert.equal(approved.url,origin+'/audio/'+catalog.tracks[1].id.slice(4).padStart(6,'0')+'.mp3');
  assert.deepEqual(previewForTrack(c.tracks[3],delivery).available,true);delivery.delete(catalog.tracks[3].id);assert.equal(delivery.size,1);
  for(const change of [{releaseSha256:hex('other')},{mode:'remote'},{available:3},{availableRows:'AAAA'},{enabled:false}])
    assert.equal(new RowDelivery(c,{...deliverySummary([1,3]),...change}).size,0);
  const remote=new Collection({manifest:{...manifestV2,audioPolicy:{mode:'remote',origin:'https://cdn.example',pathPrefix:'/fma2000/'}},pageOrigin:origin});remote.absorbTrack(display(1));
  const r=new RowDelivery(remote,{...deliverySummary([1]),mode:'remote',origin:'https://cdn.example',pathPrefix:'/fma2000/'});
  assert.equal(r.get(catalog.tracks[1].id).url,'https://cdn.example/fma2000/'+catalog.tracks[1].audioSha256+'.mp3');
  assert.equal(new RowDelivery(remote,{...deliverySummary([1]),mode:'remote',origin:'https://evil.example',pathPrefix:'/fma2000/'}).size,0);
  assert.equal((await loadRowDelivery(c,{fetcher:async()=>{throw new TypeError('offline');}})).size,0);
});

// ---- the real map over a sparse layout --------------------------------------------------------
test('the map draws the density cloud from the weighted sample and places search rows it has never seen',()=>{
  const ctx=new Proxy({measureText:t=>({width:t.length*6})},{get:(o,k)=>o[k]??(()=>{}),set:(o,k,v)=>{o[k]=v;return true;}});
  const canvas={getContext:()=>ctx,getBoundingClientRect:()=>({width:740,height:505,left:0,top:0}),addEventListener(){},width:0,height:0};
  const saved={};const globals={devicePixelRatio:1,matchMedia:()=>({matches:false,addEventListener(){}}),document:{hidden:false,addEventListener(){}},
    ResizeObserver:class{observe(){}disconnect(){}},requestAnimationFrame:()=>1,cancelAnimationFrame(){}};
  for(const [k,v] of Object.entries(globals)){saved[k]=Object.getOwnPropertyDescriptor(globalThis,k);Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});}
  try{
    const c=new Collection({manifest:manifestV2,pageOrigin:origin}),sample=[0,10,500,1999],layers=[];
    const points=sample.map(r=>{c.positions[r]=v1Layout.positions[r];return c.positions[r];});
    const createLayer=(w,h)=>{const calls=[];const lctx=new Proxy({},{get:(o,k)=>o[k]??((...a)=>calls.push([k,...a])),set:(o,k,v)=>{if(k==='globalAlpha')calls.push(['alpha',v]);o[k]=v;return true;}});const l={width:w,height:h,calls,getContext:()=>lctx};layers.push(l);return l;};
    const map=new AudioMap(canvas,{positions:c.positions,tracks:c.tracks,connections:[],colors:{},onSelect(){},onHover(){},onTrace(){},createLayer,
      density:{points,weights:[1,3,1,2]},bounds:{x0:bounds[0],y0:bounds[1],x1:bounds[2],y1:bounds[3]}});
    map.draw();
    const arcs=layers[0].calls.filter(([n])=>n==='arc').length,alphas=layers[0].calls.filter(([n])=>n==='alpha').map(a=>+a[1].toFixed(3));
    assert.equal(arcs,4);assert.deepEqual(alphas.slice(1,5),[.3,.657,.3,.51]);
    const ex=examplesV2.examples[0];c.absorbPacket(ex,{k:16});
    map.setSearch(ex.trace,ex.results.map((r,i)=>({row:r.row,displayRank:i+1})));map.draw();
    assert.equal(map.results.length,16);assert.deepEqual(map.allBounds,{x0:bounds[0],y0:bounds[1],x1:bounds[2],y1:bounds[3]});
    for(const id of map._context.ids)assert.ok(c.positions[id],'only placed rows are drawn');
    map.destroy();
  }finally{for(const [k,d] of Object.entries(saved)){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});

// ---- the checked-in app against the emulated v2 server ----------------------------------------
const source=(await bytes('search-studio/src/app.mjs')).toString().replace(/^import .*;\n/gm,'')
  .replaceAll('import.meta.url',JSON.stringify(origin+'/search-studio/src/app.mjs')).replace('await init();','');
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate);};
async function until(condition){const deadline=performance.now()+3000;while(performance.now()<deadline){if(condition())return;await new Promise(r=>setTimeout(r,2));}assert.fail('Fixture did not reach the expected state');}
async function harness({available=catalog.tracks.map((_,r)=>r)}={}){
  const elements=new Map(),events={},clicks={},calls=[],searches=[];
  const availableSet=new Set(available);
  function el(key){
    if(!elements.has(key))elements.set(key,{dataset:{},style:{},handlers:{},attributes:{},value:key==='#query-kind'?'description':'',src:'',paused:true,hidden:false,checked:false,
      textContent:'',innerHTML:'',addEventListener(name,fn){this.handlers[name]=fn;},setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){this[name]='';},
      focus(){},scrollIntoView(){},closest(){return null;},showModal(){this.open=true;},close(){this.open=false;},pause(){this.paused=true;},load(){this.paused=true;},async play(){this.paused=false;}});
    return elements.get(key);
  }
  const fetcher=async(url,options={})=>{
    url=new URL(url);const body=options.body?JSON.parse(options.body):null;calls.push({url:url.href,path:url.pathname,params:url.searchParams,body});
    assert.equal(url.origin,origin,'No off-origin requests');
    if(url.pathname==='/deployment-config.json')return new Response(JSON.stringify({schemaVersion:1,enabled:true,mode:'anonymous-preview',origin,apiBase:'/v1/',recipient:'This site’s server',privacySummary:'Fixture.'}));
    if(url.pathname==='/audio-delivery.json')return new Response(JSON.stringify(deliverySummary(available)));
    if(url.pathname==='/v1/manifest')return new Response(JSON.stringify(serverManifest));
    if(url.pathname==='/v1/cancel')return new Response('{}');
    if(url.pathname==='/v1/search'){searches.push(body);return new Response(JSON.stringify({...serverManifest,...body,...packet(new Float32Array(v1Examples.examples[2].queryVector)),timingMs:{serverCompute:1},tokenization:{truncated:false}}));}
    if(url.pathname==='/collection/tracks')return new Response(JSON.stringify(collectionTracks(url.searchParams,availableSet)));
    if(url.pathname==='/collection/neighbors'){const row=Number(url.searchParams.get('row'));return new Response(JSON.stringify({...packet(vectors.slice(row*512,(row+1)*512),{exclude:row}),catalogId:v1.catalogId,graphId:v1.graphId,indexSha256:identity.indexSha256,releaseSha256:identity.releaseSha256,row}));}
    const data={'/search-studio/data/manifest.json':manifestBytes,'/search-studio/data/layout.json':layoutBytes,'/search-studio/data/examples.json':examplesBytes}[url.pathname];
    assert.ok(data,'The v2 page must not fetch '+url.pathname);
    return new Response(data);
  };
  let map;
  class TestAudioMap{constructor(canvas,options){map=this;this.options=options;this.searches=[];this.hover=null;}select(){}fit(){}draw(){}destroy(){}finish(){}zoom(){}replay(){}pause(){}setSearch(trace,rows,options){this.searches.push({trace,rows,options});}}
  class Encoder{constructor({status}){this.status=status;this.state='unloaded';}set(s){this.state=s;this.status({state:s});}async prepare(){this.set('ready');}encode(){throw new Error('no local encoder in v2');}cancel(){this.set('unloaded');}}
  const context=vm.createContext({BrowserEncoder:Encoder,AudioMap:TestAudioMap,
    ServerSearch:class extends ServerSearch{constructor(o){super({...o,fetcher});}},loadDeploymentConfig:o=>loadDeploymentConfig({...o,fetcher}),
    loadAudioDelivery:o=>loadAudioDelivery({...o,fetcher}),previewForTrack,UNAVAILABLE_PREVIEW,SERVER_CONFIG,
    Collection:class extends Collection{constructor(o){super({...o,fetcher});}},TRACK_ID,loadRowDelivery:c=>loadRowDelivery(c,{fetcher}),RESULT_PAGE_SIZE,
    sourceGenres,refineCandidates,resultPage,resultScope,compactResultScope,reviewQueryLimits,indexConnections,MANIFEST_SHA,ARTIST_METADATA_SHA:identity.catalogSha256,rankCandidates,HNSW,exactSearch,RELEASE,metadataSearch,publicCharacterLimit,
    fetch:fetcher,crypto:webcrypto,TextDecoder,TextEncoder,Float32Array,Uint8Array,URL,Blob,DOMException,performance,AbortSignal,Response,setTimeout,atob,
    getComputedStyle:()=>({getPropertyValue:()=> '#000'}),
    document:{body:{dataset:{}},activeElement:null,querySelector:el,querySelectorAll:selector=>selector==='[data-needs-catalog]'?[el('#search'),el('#enable-local'),el('#browse-collection')]:[],addEventListener(name,fn){clicks[name]=fn;}},
    location:{href:origin+'/search-studio/',origin},window:{addEventListener(name,fn){events[name]=fn;}}});
  vm.runInContext(source,context);
  await vm.runInContext('init()',context);
  assert.equal(context.document.body.dataset.ready,'true',el('#status').textContent);
  const submit=async text=>{el('#query').value=text;el('#query-form').handlers.submit({preventDefault(){}});await flush();};
  const click=async dataset=>{const button={dataset};clicks.click({target:{closest:()=>button}});await flush();};
  return{el,calls,searches,map:()=>map,submit,click,context,collectionCalls:()=>calls.filter(c=>c.path.startsWith('/collection/'))};
}

test('v2 bootstrap loads only the pinned manifest, layout sample and example packets; the catalog stays on the server',async()=>{
  const h=await harness();
  const data=h.calls.filter(c=>c.path.startsWith('/search-studio/data/')).map(c=>c.path).sort();
  assert.deepEqual(data,['/search-studio/data/examples.json','/search-studio/data/layout.json','/search-studio/data/manifest.json']);
  assert.equal(h.collectionCalls().length,0,'the first view is the pinned recorded example');
  const m=h.map();assert.equal(m.options.tracks.length,count);assert.equal(m.options.density.points.length,count);
  assert.deepEqual(m.options.connections,indexConnections(indexJson.links));assert.equal(m.searches.length,1);
  assert.equal(m.searches[0].rows.length,12);assert.equal(h.el('#catalog-count').textContent,'2,000 recordings');
  assert.equal(h.el('#engine-label').textContent,'Recorded example');assert.equal(h.el('#enable-local').disabled,true);
  assert.equal(h.el('#audio-availability').hidden,true);assert.equal(h.el('#open-engine').textContent,'Server ready');
  assert.match(h.el('#collection-summary').textContent,/2,000 FMA excerpts · \d+ source artist IDs · 14 source genres · read page by page/);
});

test('browse, paging, page jump and refinements are server pages with the v1 page arithmetic',async()=>{
  const h=await harness();
  await h.click({});h.el('#browse-collection').onclick();assert.equal(h.el('#results-region').attributes['aria-busy'],'true','a server page is loading');
  await until(()=>h.el('#page-position').textContent==='Page 1 of 167');assert.equal(h.el('#results-region').attributes['aria-busy'],'false');
  const browse=h.collectionCalls().at(-1);assert.equal(browse.params.get('offset'),'0');assert.equal(browse.params.get('facets'),'1');
  assert.match(h.el('#result-scope-detail').textContent,/Showing 1–12 of 2,000 recordings · 2,000 recordings in the collection/);
  assert.equal(h.map().searches.at(-1).rows.length,0,'a browse page is not drawn as matches');
  h.el('#next-page').onclick();await until(()=>h.el('#page-position').textContent==='Page 2 of 167');
  assert.equal(h.collectionCalls().at(-1).params.get('offset'),'12');assert.equal(h.collectionCalls().at(-1).params.get('facets'),null);
  h.el('#last-page').onclick();await until(()=>h.el('#page-position').textContent==='Page 167 of 167');
  assert.match(h.el('#page-indicator').textContent,/1,993–2,000 \/ 2,000/);
  h.el('#page-number').value='999';h.el('#page-jump').handlers.submit({preventDefault(){}});await until(()=>h.collectionCalls().some(c=>c.params.get('offset')==='11976'));
  await until(()=>h.el('#page-position').textContent==='Page 167 of 167');
  h.el('#genre-filter').value='Folk';h.el('#genre-filter').handlers.change();
  const folk=refineCandidates(catalog.tracks.map((_,row)=>({row})),catalog.tracks,{genre:'Folk'});
  await until(()=>h.el('#page-position').textContent===`Page 1 of ${Math.ceil(folk.length/12)}`);
  assert.equal(h.collectionCalls().at(-1).params.get('genre'),'Folk');
  h.el('#refine-text').value='no such name 000000';h.el('#refine-text').handlers.input();
  await until(()=>h.el('#results-empty').hidden===false);assert.equal(h.el('#page-position').textContent,'No results');
});

test('a server page that lands while someone types into the page box keeps the typed number until it is submitted',async()=>{
  const h=await harness();
  h.el('#browse-collection').onclick();await until(()=>h.el('#page-position').textContent==='Page 1 of 167');
  assert.equal(h.el('#page-number').value,'1');
  // Next is clicked, and the page box is edited while that page is still on its way from the server.
  h.el('#next-page').onclick();assert.equal(h.el('#results-region').attributes['aria-busy'],'true');
  h.el('#page-number').value='15';h.el('#page-number').handlers.input();
  await until(()=>h.el('#page-position').textContent==='Page 2 of 167');
  assert.equal(h.el('#page-number').value,'15','the landing page did not replace the typed number');
  assert.equal(h.el('#results-region').attributes['aria-busy'],'false');
  // Escape gives the draft up and shows the current page again.
  h.el('#page-number').handlers.keydown({key:'Escape'});assert.equal(h.el('#page-number').value,'2');
  // A submitted draft is a navigation like any other: the box then follows the page shown.
  h.el('#page-number').value='15';h.el('#page-number').handlers.input();h.el('#page-jump').handlers.submit({preventDefault(){}});
  await until(()=>h.el('#page-position').textContent==='Page 15 of 167');assert.equal(h.el('#page-number').value,'15');
  h.el('#page-number').value='7';h.el('#page-number').handlers.input();h.el('#previous-page').onclick();
  await until(()=>h.el('#page-position').textContent==='Page 14 of 167');assert.equal(h.el('#page-number').value,'14','Previous clears an unsubmitted draft');
});

test('title/artist lookup and audio neighbors run on the server with v1 semantics; search packets carry their rows',async()=>{
  const h=await harness();
  h.el('#query-kind').value='lookup';h.el('#query-kind').handlers.change();assert.match(h.el('#search-processing').textContent,/matched on this server/);
  await h.submit('love');const expected=metadataSearch('love',catalog.tracks,count);
  await until(()=>h.el('#results-heading').textContent==='Title / artist matches'&&h.el('#page-indicator').textContent.includes(`/ ${expected.length}`));
  assert.equal(h.collectionCalls().at(-1).params.get('q'),'love');
  assert.equal(h.map().searches.at(-1).rows[0].row,expected[0].row);assert.equal(h.map().searches.at(-1).rows[0].displayRank,null);
  await h.click({nearby:String(expected[0].row)});await until(()=>h.el('#engine-label').textContent==='Audio neighbors');
  const exact=exactSearch(vectors,vectors.slice(expected[0].row*512,(expected[0].row+1)*512),16,{excludeId:expected[0].row});
  assert.deepEqual(h.map().searches.at(-1).rows.map(r=>r.row),exact.slice(0,12).map(e=>e.id));
  assert.equal(h.collectionCalls().at(-1).path,'/collection/neighbors');
  h.el('#query-kind').value='description';h.el('#query-kind').handlers.change();
  await h.submit('a deliberately submitted description');await until(()=>h.el('#engine-label').textContent==='Live · server');
  assert.equal(h.searches.length,1);const want=exactSearch(vectors,new Float32Array(v1Examples.examples[2].queryVector),16);
  assert.deepEqual(h.map().searches.at(-1).rows.map(r=>r.row),want.slice(0,12).map(e=>e.id));
  assert.match(h.el('#results').innerHTML,new RegExp(catalog.tracks[want[0].id].title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/&/g,'&amp;')));
  // Credits are read page by page from the server; the link names the recording, the server finds its page.
  assert.ok(h.el('#results').innerHTML.includes(`href="/collection/credits?id=${encodeURIComponent(catalog.tracks[want[0].id].id)}"`));
  assert.ok(!h.el('#results').innerHTML.includes('track-attribution.html'));
});

test('a partial preview pack is reported and filterable through the server',async()=>{
  const h=await harness({available:[0,1,2,3,4]});
  assert.equal(h.el('#audio-availability').textContent,'5 of 2,000 recordings have verified previews.');assert.equal(h.el('#preview-only-label').hidden,false);
  h.el('#browse-collection').onclick();await until(()=>h.el('#page-position').textContent==='Page 1 of 167');
  h.el('#preview-only').checked=true;h.el('#preview-only').handlers.change();await until(()=>h.el('#page-position').textContent==='Page 1 of 1');
  assert.equal(h.collectionCalls().at(-1).params.get('preview'),'1');assert.match(h.el('#page-indicator').textContent,/1–5 \/ 5/);
});
