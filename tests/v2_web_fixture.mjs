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
// Region labels for the fixture: one area per source genre at its rows' centroid (build_web_v2.py uses
// k-means; the page only draws what it is given), plus an unlabelled one.
const byGenre=new Map();catalog.tracks.forEach((t,row)=>{if(t.genre){const g=byGenre.get(t.genre)??[];g.push(row);byGenre.set(t.genre,g);}});
const regionItems=[...byGenre].map(([genre,rows])=>({x:rows.reduce((s,r)=>s+v1Layout.positions[r][0],0)/rows.length,y:rows.reduce((s,r)=>s+v1Layout.positions[r][1],0)/rows.length,
  count:rows.length,label:genre,share:1,genres:[[genre,rows.length]]})).sort((a,b)=>b.count-a.count);
const regions={method:'fixture: one area per source genre',majority:.4,levels:[{clusters:regionItems.length,fromDetail:0,toDetail:2,items:regionItems},
  {clusters:1,fromDetail:2,toDetail:10,items:[{x:0,y:0,count:count,label:null,share:.2,genres:[]}]}]};
// ---- the server's map tiles (collection_v2.TileIndex), for the emulated routes -------------------
const TILE_BITS=16,TILE_CAP=1024,TILE_MAX_LEVEL=12;
const tileDomain=([x0,y0,x1,y1])=>{const side=Math.max(x1-x0,y1-y0,1e-6)*(1+1/512);return[(x0+x1)/2-side/2,(y0+y1)/2-side/2,side];};
const spread=v=>{let out=0;for(let i=0;i<16;i++)if((v>>i)&1)out+=2**(2*i);return out;};
function tileIndex(positions,box){
  const domain=tileDomain(box),[x0,y0,side]=domain,scale=2**TILE_BITS/side,top=2**TILE_BITS-1;
  const codes=positions.map(([x,y])=>spread(Math.max(0,Math.min(top,Math.floor((x-x0)*scale))))+2*spread(Math.max(0,Math.min(top,Math.floor((y-y0)*scale)))));
  const order=codes.map((_,row)=>row).sort((a,b)=>codes[a]-codes[b]||a-b),sorted=order.map(r=>codes[r]);
  const b64=(Kind,values)=>Buffer.from(Kind.from(values).buffer).toString('base64');
  const first=value=>{let lo=0,hi=sorted.length;while(lo<hi){const mid=(lo+hi)>>1;if(sorted[mid]<value)lo=mid+1;else hi=mid;}return lo;};
  return{domain,tile(z,x,y){
    const span=2**(2*(TILE_BITS-z)),prefix=spread(x)+2*spread(y),a=first(prefix*span),b=first((prefix+1)*span);
    const rows=order.slice(a,b),cs=sorted.slice(a,b),total=b-a;
    let chosen,weights=null;
    if(total<=TILE_CAP)chosen=[...rows].sort((p,q)=>p-q);
    else{
      let starts=null;
      for(let depth=1;depth<=TILE_BITS-z;depth++){const unit=2**(2*(TILE_BITS-z-depth)),found=[];cs.forEach((c,i)=>{if(!i||Math.floor(c/unit)!==Math.floor(cs[i-1]/unit))found.push(i);});if(found.length>TILE_CAP)break;starts=found;}
      const cells=starts.map((s,i)=>{const end=i+1<starts.length?starts[i+1]:total;let low=Infinity;for(let j=s;j<end;j++)low=Math.min(low,rows[j]);return{low,n:end-s};}).sort((p,q)=>p.low-q.low);
      chosen=cells.map(c=>c.low);weights=cells.map(c=>c.n);
    }
    return{z,x,y,domain,cap:TILE_CAP,total,complete:weights===null,count:chosen.length,rows:b64(Uint32Array,chosen),
      xy:b64(Float32Array,chosen.flatMap(r=>positions[r])),weights:weights&&b64(Uint32Array,weights)};
  }};
}
const tiles=tileIndex(v1Layout.positions,bounds);
const storedLinks=row=>{const levels=indexJson.links[row].map(l=>[...l]),rows=[...new Set([row,...levels.flat()])].sort((a,b)=>a-b);
  return{schemaVersion:1,kind:'stored-index-links',row,graphId:v1.graphId,indexSha256:identity.indexSha256,levels,layout:{rows,xy:rows.flatMap(r=>v1Layout.positions[r])}};};
// The overview pinned in layout.json. Every row by default (no tiles), as build_web_v2.py writes it at
// this size; with `sample`, an overview of those rows and the tile pyramid, to exercise level of detail.
function lodLayout(sample=null){
  const rows=sample??catalog.tracks.map((_,r)=>r);
  return{schemaVersion:3,kind:'music-layout-lod-v2',releaseSha256:identity.releaseSha256,graphId:v1.graphId,count,sampleCount:rows.length,
    sampleMethod:sample?'fixture sample':'every row',bounds,rows,xy:rows.flatMap(r=>v1Layout.positions[r]),...(sample?{weights:rows.map(()=>4)}:{}),regions,
    tiles:sample?{api:'/collection/tiles',domain:tiles.domain,cap:TILE_CAP,maxLevel:TILE_MAX_LEVEL}:null,links:'/collection/links',description:'Fixture layout.'};
}
const layoutV2=lodLayout();
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

// A manifest + layout pair whose overview is a sample (so the page streams tiles), as a 200K build would pin.
function sampledFixture(sample){
  const layout=lodLayout(sample),layoutData=Buffer.from(JSON.stringify(layout));
  const manifest={...manifestV2,layout:{...manifestV2.layout,sampleCount:sample.length,sampleMethod:'fixture sample',tiles:true},
    files:{...manifestV2.files,layout:{path:'layout.json',bytes:layoutData.length,sha256:sha(layoutData)}}};
  const manifestData=Buffer.from(JSON.stringify(manifest));
  return{layout,layoutBytes:layoutData,manifest,manifestBytes:manifestData,MANIFEST_SHA:sha(manifestData)};
}

export {root,origin,bytes,v1,catalog,v1Examples,v1Layout,artists,indexJson,vectors,graph,hex,sha,count,identity,display,traceRows,labelRows,packet,
  examplesV2,bounds,layoutV2,layoutBytes,examplesBytes,manifestV2,manifestBytes,MANIFEST_SHA,serverManifest,bitset,deliverySummary,collectionTracks,
  regions,tiles,tileIndex,tileDomain,storedLinks,lodLayout,sampledFixture,TILE_CAP};
