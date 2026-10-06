// A release-format-v2 web fixture built from the checked-in v1 fma2000 web data with the page's own
// JS rules (exactSearch, HNSW, metadataSearch, refineCandidates, indexConnections): the pinned v2
// manifest, layout sample and example packets, plus an emulation of the server's v2 routes.
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {sourceGenres,refineCandidates} from '../web/search-studio/src/results-view.mjs';
import {indexConnections} from '../web/search-studio/src/search-motion.mjs';
import {HNSW,exactSearch} from '../web/search-studio/src/hnsw.mjs';
import {metadataSearch} from '../web/listen-lab/src/retrieval.mjs';
import {v1Data} from './v1_page_data.mjs';

const root=new URL('../web/',import.meta.url), origin='https://music.example';
const bytes=path=>readFile(new URL(path,root));
const v1=JSON.parse(await v1Data('manifest.json'));
const catalog=JSON.parse(await v1Data('catalog.json'));
const v1Examples=JSON.parse(await v1Data('examples.json'));
const v1Layout=JSON.parse(await v1Data('layout.json'));
const artists=new Map(JSON.parse(await v1Data('artist-records.json')).rows.map(r=>[r.trackId,r.artistId]));
const indexJson=JSON.parse(await v1Data('index.json'));
const vectorBytes=await v1Data('vectors.f32');
const vectors=new Float32Array(vectorBytes.buffer,vectorBytes.byteOffset,vectorBytes.byteLength/4);
const graph=HNSW.load(indexJson,vectors);
const hex=label=>createHash('sha256').update(label).digest('hex');
const sha=data=>createHash('sha256').update(data).digest('hex');
const count=catalog.tracks.length;
const identity={releaseSha256:hex('fixture release.json'),indexSha256:hex('fixture graph.bin'),catalogSha256:hex('fixture catalog.sqlite')};

// ---- the emulated v2 server -------------------------------------------------------------------
const display=row=>{const t=catalog.tracks[row];return{row,id:t.id,title:t.title,artist:t.artist,album:t.album??null,genre:t.genre??null,license:t.license,
  artistId:artists.get(t.id),audioBytes:t.audioBytes,audioSha256:t.audioSha256,position:v1Layout.positions[row]};};
function traceRows(trace,results){const rows=new Set(results.map(r=>r.id));for(const e of trace.events){for(const id of e.entryIds??[])rows.add(id);if(Number.isInteger(e.id))rows.add(e.id);if(Number.isInteger(e.entryId))rows.add(e.entryId);for(const key of ['considered','frontier','retained','items'])for(const c of e[key]??[])rows.add(c.id);}return[...rows].sort((a,b)=>a-b);}
function labelRows(trace){const rows=[];for(const e of trace.events)for(const id of e.type==='enter'?e.entryIds:e.type==='expand'?[e.id]:[])if(!rows.includes(id))rows.push(id);return rows.slice(0,48);}
function packet(q,{exclude=null}={}){
  const exact=exactSearch(vectors,q,16,exclude===null?{}:{excludeId:exclude}),trace=graph.search(q,{k:exclude===null?16:17,ef:32,trace:true}).trace;
  const ranked=exact.map(e=>e.id),labels=labelRows(trace).filter(r=>!ranked.includes(r)&&r!==exclude),rows=traceRows(trace,exact);
  if(exclude!==null&&!rows.includes(exclude))rows.push(exclude);
  return{results:exact.map((e,i)=>({rank:i+1,row:e.id,id:catalog.tracks[e.id].id,cosineSimilarity:1-e.distance})),trace,
    tracks:[...(exclude===null?[]:[exclude]),...ranked,...labels].map(display),layout:{rows,xy:rows.flatMap(r=>v1Layout.positions[r])}};
}
const examplesV2={schemaVersion:2,kind:'music-recorded-examples-v2',releaseSha256:identity.releaseSha256,graphId:v1.graphId,indexSha256:identity.indexSha256,
  queryProfileId:v1Examples.queryProfileId,defaultExampleId:v1Examples.defaultExampleId,count:6,description:v1Examples.description,
  examples:v1Examples.examples.map(e=>({id:e.id,label:e.label,text:e.text,mode:e.mode,queryProfileId:e.queryProfileId,queryVectorSha256:e.queryVectorSha256,...packet(new Float32Array(e.queryVector))}))};
const xs=v1Layout.positions.map(p=>p[0]),ys=v1Layout.positions.map(p=>p[1]),bounds=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];
const layoutV2={schemaVersion:2,kind:'music-layout-sample-v2',releaseSha256:identity.releaseSha256,graphId:v1.graphId,count,sampleCount:count,sampleMethod:'every row',bounds,
  rows:catalog.tracks.map((_,r)=>r),xy:v1Layout.positions.flat(),edges:indexConnections(indexJson.links).flatMap(e=>[e.from,e.to,e.level]),description:'Fixture layout.'};
const layoutBytes=Buffer.from(JSON.stringify(layoutV2)),examplesBytes=Buffer.from(JSON.stringify(examplesV2));
const manifestV2={schemaVersion:2,format:2,kind:'music-collection-web-v2',...identity,catalogId:v1.catalogId,graphId:v1.graphId,vectorsSha256:v1.vectorsSha256,
  orderedIdsSha256:v1.orderedIdsSha256,count,dimensions:512,allowedQueryProfiles:v1.allowedQueryProfiles,collectionApi:'/collection/',searchDefaults:{k:16,ef:32},
  audioPolicy:{mode:'local',origin:null,pathPrefix:null},summary:{artists:new Set(artists.values()).size,genres:sourceGenres(catalog.tracks.map((_,row)=>({row})),catalog.tracks)},
  layout:{count,sampleCount:count,sampleMethod:'every row',bounds,description:'Fixture layout.'},examples:{count:6,defaultExampleId:'dev-01'},
  files:{layout:{path:'layout.json',bytes:layoutBytes.length,sha256:sha(layoutBytes)},examples:{path:'examples.json',bytes:examplesBytes.length,sha256:sha(examplesBytes)}},source:{},scope:'fixture'};
const manifestBytes=Buffer.from(JSON.stringify(manifestV2)),MANIFEST_SHA=sha(manifestBytes);
const serverManifest={catalogId:v1.catalogId,graphId:v1.graphId,indexSha256:identity.indexSha256,catalogSha256:identity.catalogSha256,vectorsSha256:v1.vectorsSha256,
  engineId:v1.allowedQueryProfiles.find(p=>p.kind==='server-live').id,deploymentGeneration:'fixture-generation',maxQueryUtf8Bytes:2048,releaseFormat:2,
  publicPreview:{anonymous:true,maxQueryCharacters:512,searchesPerMinute:6,searchesPerProcessHour:30,audioEnabled:true}};
function bitset(rows){const bits=new Uint8Array(Math.ceil(count/8));for(const r of rows)bits[r>>3]|=1<<(r&7);return Buffer.from(bits).toString('base64');}
const deliverySummary=(rows=catalog.tracks.map((_,r)=>r))=>({schemaVersion:2,kind:'music-audio-delivery-v2',catalogId:v1.catalogId,catalogSha256:identity.catalogSha256,
  releaseSha256:identity.releaseSha256,enabled:true,publicDeliveryVerified:true,mode:'local',available:rows.length,total:count,availableRows:bitset(rows)});
function collectionTracks(params,available){
  if(params.has('rows'))return{rows:params.get('rows').split(',').map(Number).map(display)};
  const q=params.get('q')??'',offset=Number(params.get('offset')??0),limit=Number(params.get('limit')??12);
  const base=q.trim()?metadataSearch(q,catalog.tracks,count).map(r=>({row:r.row})):catalog.tracks.map((_,row)=>({row}));
  const kept=refineCandidates(base,catalog.tracks,{text:params.get('text')??'',genre:params.get('genre')??'',previewOnly:params.get('preview')==='1'},row=>available.has(row));
  return{channel:q.trim()?'lookup':'browse',total:kept.length,baseTotal:base.length,offset,limit,rows:kept.slice(offset,offset+limit).map(r=>display(r.row)),
    ...(params.get('facets')==='1'?{genres:sourceGenres(base,catalog.tracks)}:{})};
}

export {root,origin,bytes,v1,catalog,v1Examples,v1Layout,artists,indexJson,vectors,graph,hex,sha,count,identity,display,traceRows,labelRows,packet,
  examplesV2,bounds,layoutV2,layoutBytes,examplesBytes,manifestV2,manifestBytes,MANIFEST_SHA,serverManifest,bitset,deliverySummary,collectionTracks};
