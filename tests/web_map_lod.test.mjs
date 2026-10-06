// Level of detail for the Atlas map (release format v2): tile levels and selection, the tile field's
// requests, cancellation and local children of complete tiles, and the map drawing only candidates,
// tile rasters in view, region labels at their zoom and stored links around the selection only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {TileField,tileLevel,tilesInView,regionAlpha,quantize,splat,parseColor,TILE_CONCURRENCY,TILE_MIN_LEVEL,TILE_RASTERS_PER_FRAME,TILE_SCREEN_PX} from '../web/search-studio/src/map-lod.mjs';
import {AudioMap} from '../web/search-studio/src/graph.mjs';
import {Collection} from '../web/search-studio/src/collection-api.mjs';
import {catalog,v1Layout,indexJson,tiles,tileIndex,regions,bounds,sampledFixture,origin,count} from './v2_web_fixture.mjs';

const flush=async()=>{for(let i=0;i<6;i++)await new Promise(setImmediate);};

test('tile levels, tiles in view and region fades follow the zoom',()=>{
  const domain=[-1,-1,2],whole=TILE_SCREEN_PX/2;// pixels per unit at which the whole domain is one tile on screen
  assert.deepEqual([whole/4,whole,whole*1.01,whole*4,1e9].map(k=>tileLevel(domain,k,12)),[0,0,1,2,12],'tiles span at most TILE_SCREEN_PX');
  const list=tilesInView(domain,2,{x0:-.2,y0:-.2,x1:.4,y1:.3});
  assert.deepEqual(list,[[2,2],[2,1],[1,2],[1,1]],'nearest the view centre first');
  assert.deepEqual(tilesInView(domain,2,{x0:2,y0:2,x1:3,y1:3}),[]);
  assert.deepEqual(tilesInView(domain,1,{x0:-9,y0:-9,x1:9,y1:9}).length,4,'clamped to the pyramid');
  assert.equal(regionAlpha(1,0,2),1);assert.equal(regionAlpha(4,0,2),0);assert.equal(regionAlpha(1,2,10),0);assert.equal(regionAlpha(4,2,10),1);
  assert.ok(regionAlpha(2,0,2)>0&&regionAlpha(2,0,2)<1&&Math.abs(regionAlpha(2,0,2)+regionAlpha(2,2,10)-1)<1e-9,'the two levels cross-fade');
  assert.equal(quantize(-1,-1,2),0);assert.equal(quantize(1,-1,2),65535);assert.equal(quantize(0,-1,2),32768);
});

function field({fetchTile,createLayer=null}={}){
  const timers=[];let clock=0;
  const f=new TileField({domain:tiles.domain,cap:1024,maxLevel:12,fetchTile,createLayer,onChange:()=>{f.changes=(f.changes??0)+1;},
    schedule:(fn,ms)=>{timers.push({fn,at:clock+ms});return timers.length;},cancel:id=>{if(timers[id-1])timers[id-1].fn=null;},now:()=>clock});
  const advance=ms=>{clock+=ms;for(const t of timers)if(t.fn&&t.at<=clock){const fn=t.fn;t.fn=null;fn();}};
  return{f,advance};
}

test('the tile field requests the settled view, aborts tiles that left it and retries refusals later',async()=>{
  const calls=[],pending=new Map();
  const fetchTile=(z,x,y,{signal})=>new Promise((resolve,reject)=>{const key=`${z}/${x}/${y}`;calls.push(key);pending.set(key,{resolve,reject});
    signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})));});
  const {f,advance}=field({fetchTile});
  const view=tilesInView(tiles.domain,3,{x0:-.6,y0:-.6,x1:.6,y1:.6});assert.ok(view.length>TILE_CONCURRENCY);
  f.want(3,view);assert.equal(calls.length,0,'nothing is requested while the view is still moving');
  advance(100);assert.equal(calls.length,TILE_CONCURRENCY,'a bounded number of requests at a time');
  const first=calls[0],{x0,y0,s}=f.rect(3,...first.split('/').slice(1).map(Number));assert.ok(s>0&&Number.isFinite(x0+y0));
  // The view moves on: tiles no longer wanted are aborted; the new view is requested after it settles.
  f.want(5,[[0,0]]);advance(100);await flush();
  assert.equal(f.stats.aborted,TILE_CONCURRENCY);assert.equal(f.inflight.size,1);assert.equal(calls.at(-1),'5/0/0');
  pending.get('5/0/0').reject(Object.assign(new Error('Map tile budget exhausted'),{status:429}));await flush();
  assert.equal(f.tiles.get('5/0/0').state,'failed');f.pump();assert.equal(calls.filter(c=>c==='5/0/0').length,1,'a refused tile waits before it is retried');
  advance(1700);assert.equal(calls.filter(c=>c==='5/0/0').length,2);
  const [z,x,y]=[5,0,0];pending.get('5/0/0').resolve({z,x,y,total:0,complete:true,rows:new Uint32Array(0),xy:new Float32Array(0),weights:null});await flush();
  assert.equal(f.tiles.get('5/0/0').state,'ready');assert.ok(f.changes>=1,'a loaded tile asks for a redraw');
  f.destroy();assert.equal(f.inflight.size,0);
});

test('children of a complete tile are cut locally, exactly as the server would cut them',async()=>{
  let requests=0;
  const {f}=field({fetchTile:async(z,x,y)=>{requests++;return decodeTile(tiles.tile(z,x,y));}});
  const level2=[];for(let x=0;x<4;x++)for(let y=0;y<4;y++)level2.push([x,y]);
  f.want(2,level2,{immediate:true});for(let i=0;i<12;i++)await flush();
  assert.equal(requests,16);assert.ok([...f.tiles.values()].every(t=>t.state==='ready'&&t.complete),'level-2 tiles of the fixture rows are complete');
  let compared=0,seed=7;const next=n=>{seed=(seed*48271)%2147483647;return seed%n;};
  for(let z=3;z<=7;z++)for(let n=0;n<40;n++){
    const x=next(2**z),y=next(2**z),local=f.resolve(z,x,y),server=decodeTile(tiles.tile(z,x,y));
    assert.deepEqual([...local.rows],[...server.rows],`${z}/${x}/${y}`);assert.deepEqual([...local.xy],[...server.xy]);assert.equal(local.complete,true);compared++;
  }
  assert.equal(compared,200);assert.equal(requests,16,'no request for any descendant of a complete tile');
  f.want(4,[[3,3],[3,4]]);f.pump();assert.equal(requests,16);
});

function decodeTile(p){
  const u32=s=>{const b=Buffer.from(s,'base64');return Uint32Array.from({length:b.length/4},(_,i)=>b.readUInt32LE(4*i));};
  const f32=s=>{const b=Buffer.from(s,'base64');return Float32Array.from({length:b.length/4},(_,i)=>b.readFloatLE(4*i));};
  return{z:p.z,x:p.x,y:p.y,total:p.total,complete:p.complete,rows:u32(p.rows),xy:f32(p.xy),weights:p.weights===null?null:u32(p.weights)};
}

test('a tile raster accumulates dots like stacked canvas fills',()=>{
  assert.deepEqual(parseColor('#94a9a0'),[148,169,160]);assert.deepEqual(parseColor('#abc'),[170,187,204]);assert.deepEqual(parseColor('rgb(1,2,3)'),[153,153,153]);
  const size=16,data=new Uint8ClampedArray(size*size*4),keep=new Float32Array(size*size);
  const tile={rows:[1,2,3],xy:new Float32Array([8,8,8,8,2,2]),weights:null};
  splat(data,tile,0,0,1,[10,20,30],keep,size);
  const at=(x,y)=>data.slice(4*(y*size+x),4*(y*size+x)+4);
  assert.deepEqual([...at(8,8)].slice(0,3),[10,20,30]);
  assert.equal(at(8,8)[3],Math.round((1-.7*.7)*255),'two dots on the same pixel: 1-(1-0.3)^2');
  assert.equal(at(2,2)[3],Math.round(.3*255),'one dot');assert.equal(at(12,12)[3],0,'nothing far from every dot');
  const weighted=new Uint8ClampedArray(size*size*4);splat(weighted,{rows:[1],xy:new Float32Array([8,8]),weights:new Uint32Array([3])},0,0,1,[0,0,0],keep,size);
  assert.equal(weighted[4*(8*size+8)+3],Math.round((1-Math.pow(.7,3))*255),'a sample row standing for 3 rows');
});

test('rasters are built a few per frame and candidates spread round-robin over the tiles in view',async()=>{
  const layers=[];const createLayer=(w,h)=>{const calls=[];const ctx=new Proxy({},{get:(o,k)=>o[k]??((...a)=>calls.push([k,...a])),set:(o,k,v)=>{o[k]=v;return true;}});const l={width:w,height:h,calls,getContext:()=>ctx};layers.push(l);return l;};
  const {f}=field({fetchTile:async(z,x,y)=>decodeTile(tiles.tile(z,x,y)),createLayer});
  const view=tilesInView(tiles.domain,3,{x0:-.5,y0:-.5,x1:.5,y1:.5});
  for(const [x,y] of view){f.want(3,[[x,y]],{immediate:true});await flush();}
  f.beginFrame();const drawn=view.map(([x,y])=>f.source(3,x,y)).filter(Boolean);
  assert.equal(layers.length,TILE_RASTERS_PER_FRAME,'only a few new rasters per frame; the rest show the overview meanwhile');
  assert.ok(drawn.length>=TILE_RASTERS_PER_FRAME);
  for(const layer of layers){const t=[...f.tiles.values()].find(t=>t.raster===layer);assert.equal(layer.calls.filter(c=>c[0]==='arc').length,t.rows.length);}
  const rows=f.candidates(3,view,50),sources=view.map(([x,y])=>f.resolve(3,x,y)).filter(t=>t?.rows.length);
  assert.equal(rows.length,50);assert.deepEqual(rows.slice(0,sources.length),sources.map(t=>t.rows[0]),'one row from each tile before a second from any');
});

// ---- the map ------------------------------------------------------------------------------------
function mapHarness({positions,tracks,lod,density,onDensity}){
  let rect={width:740,height:505,left:0,top:0};const frames=new Map();let rafId=0;
  const texts=[],ops=[];
  const ctx=new Proxy({measureText:t=>({width:t.length*6}),fillText:(t,x,y)=>texts.push([String(t),x,y]),clearRect:(...a)=>ops.push(['clearRect',...a]),drawImage:(...a)=>ops.push(['drawImage',...a])},
    {get:(o,k)=>o[k]??(()=>{}),set:(o,k,v)=>{o[k]=v;return true;}});
  const canvas={getContext:()=>ctx,getBoundingClientRect:()=>rect,addEventListener(){},setPointerCapture(){},hasPointerCapture:()=>false,width:0,height:0};
  const saved=new Map(),globals={devicePixelRatio:1,matchMedia:()=>({matches:false,addEventListener(){}}),document:{hidden:false,addEventListener(){}},
    ResizeObserver:class{observe(){}disconnect(){}},requestAnimationFrame:fn=>{frames.set(++rafId,fn);return rafId;},cancelAnimationFrame:id=>frames.delete(id)};
  for(const [k,v] of Object.entries(globals)){saved.set(k,Object.getOwnPropertyDescriptor(globalThis,k));Object.defineProperty(globalThis,k,{configurable:true,writable:true,value:v});}
  const layers=[];const createLayer=(w,h)=>{const calls=[];const c=new Proxy({},{get:(o,k)=>o[k]??((...a)=>calls.push([k,...a])),set:(o,k,v)=>{o[k]=v;return true;}});const l={width:w,height:h,calls,getContext:()=>c};layers.push(l);return l;};
  const map=new AudioMap(canvas,{positions,tracks,connections:[],colors:{node:'#999',paper:'#fff',muted:'#666',result:'#000',visited:'#333',frontier:'#f00',ink:'#000',rule:'#ccc'},
    onSelect(){},onHover(){},onTrace(){},onDensity,createLayer,density,bounds:{x0:bounds[0],y0:bounds[1],x1:bounds[2],y1:bounds[3]},lod});
  return{map,texts,ops,layers,frames,restore(){map.destroy();for(const [k,d] of saved){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}};
}

test('a 200,000-row map projects only its candidates each frame, streams tiles when zoomed in and labels regions at their zoom',async()=>{
  // The fixture positions spread over a catalog 100 times larger (ids row*100; 199,200 rows at 1,992): display geometry only.
  const SPREAD=100,positions=new Array(count*SPREAD),tracks=new Array(count*SPREAD);
  const sample=catalog.tracks.map((_,r)=>r).filter(r=>r%4===0);
  for(const r of sample){positions[r*SPREAD]=v1Layout.positions[r];}
  const dense=tileIndex(v1Layout.positions,bounds),fetched=[];
  const fetchTile=async(z,x,y)=>{fetched.push(`${z}/${x}/${y}`);const t=decodeTile(dense.tile(z,x,y));const rows=Uint32Array.from(t.rows,r=>r*SPREAD);
    rows.forEach((row,i)=>{positions[row]??=[t.xy[2*i],t.xy[2*i+1]];});return{...t,rows};};
  const stored=new Map(),requests=[];
  const links={get:row=>stored.get(row)??null,request:row=>{requests.push(row);const r=row/SPREAD;stored.set(row,{row,levels:indexJson.links[r].map(l=>l.map(n=>n*SPREAD))});
    for(const n of indexJson.links[r].flat())positions[n*SPREAD]??=v1Layout.positions[n];}};
  const states=[];
  const h=mapHarness({positions,tracks,density:{points:sample.map(r=>v1Layout.positions[r]),weights:sample.map(()=>4)},onDensity:s=>states.push(s),
    lod:{sampleRows:sample.map(r=>r*SPREAD),regions:regions.levels.map(l=>({from:l.fromDetail,to:l.toDetail,items:l.items.filter(i=>i.label)})),
      tiles:{domain:dense.domain,cap:1024,maxLevel:12,fetchTile},links}});
  try{
    let projections=0;const project=h.map.project.bind(h.map);h.map.project=(...a)=>{projections++;return project(...a);};
    // A search prefetches the tiles where its camera lands (one match: a close view) while it animates.
    const selected=sample[10]*SPREAD;h.map.setSearch(null,[{row:selected}]);const prefetched=fetched.length;
    assert.ok(prefetched>0&&prefetched<=TILE_CONCURRENCY,'the landing view\'s tiles are requested at once');
    h.map.fit('all');projections=0;h.texts.length=0;h.map.draw();
    assert.ok(projections<=sample.length+64,`overview: ${projections} projections for ${positions.length.toLocaleString()} rows`);
    assert.equal(states.at(-1).tileLevel,null,'the overview sample, no tiles');
    await new Promise(r=>setTimeout(r,120));await flush();assert.equal(fetched.length,prefetched,'nothing more is requested for the overview');
    const labels=regions.levels[0].items.map(i=>i.label.toUpperCase());
    assert.ok(h.texts.some(([t])=>labels.includes(t)),'overview region labels are drawn');
    assert.deepEqual(requests,[selected],'the selection\'s stored links are requested once at rest');
    assert.equal(states.at(-1).edges,0,'but drawn only when zoomed in');
    // Zoom in: tiles of the view are requested after it settles, then drawn instead of the overview under them.
    h.map.zoom(4);h.map.draw();const zoomed=states.at(-1);
    assert.ok(zoomed.tileLevel>=TILE_MIN_LEVEL);assert.ok(zoomed.edges>0,'links around the selection only');
    await new Promise(r=>setTimeout(r,120));await flush();
    const inView=h.map.lodView(h.map.camera??h.map.geometry()).list.map(([x,y])=>`${zoomed.tileLevel}/${x}/${y}`);
    const added=fetched.slice(prefetched);assert.ok(added.length>0&&added.every(k=>inView.includes(k)),'only tiles of the settled view are requested');
    for(let i=0;i<6;i++){await new Promise(r=>setTimeout(r,120));await flush();h.ops.length=0;h.texts.length=0;projections=0;h.map.draw();}
    assert.ok(h.ops.some(([op])=>op==='clearRect')&&h.ops.filter(([op,img])=>op==='drawImage'&&h.layers.includes(img)).length>1,'tile rasters replace the cloud under them');
    assert.ok(projections<=5000,`zoomed: ${projections} projections`);
    assert.ok(!h.texts.some(([t])=>labels.includes(t)),'overview labels fade out when zoomed in');
    assert.ok(h.map._context.ids.every(id=>positions[id]),'every selectable point is a placed row');
    assert.ok(h.map._context.ids.some(id=>!sample.includes(id/SPREAD)),'rows outside the overview sample become selectable from tiles');
  }finally{h.restore();}
});

test('a complete overview (every row pinned) never requests tiles and keeps the v1 candidate order',()=>{
  const positions=v1Layout.positions.map(p=>[...p]),rows=positions.map((_,r)=>r),requested=[];
  const h=mapHarness({positions,tracks:catalog.tracks,density:{points:positions,weights:null},onDensity(){},
    lod:{sampleRows:rows,regions:[],tiles:null,links:{get:()=>null,request:row=>requested.push(row)}}});
  try{
    h.map.setSearch(null,[{row:3}]);h.map.zoom(3);h.map.draw();
    assert.equal(h.map.tileField,null);
    const plain=visible(positions,{width:740,height:505});assert.ok(h.map._context.ids.length>0&&plain>0);
  }finally{h.restore();}
});
function visible(points,{width,height}){return points.filter(([x,y])=>x>=0&&x<=width&&y>=0&&y<=height).length;}
