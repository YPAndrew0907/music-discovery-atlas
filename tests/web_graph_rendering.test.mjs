import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {AudioMap,visibleGraphContext} from '../web/search-studio/src/graph.mjs';
import {indexConnections} from '../web/search-studio/src/search-motion.mjs';

const data=new URL('../web/search-studio/data/',import.meta.url);
const read=async name=>JSON.parse(await readFile(new URL(name,data)));
const catalog=await read('catalog.json'),layout=await read('layout.json'),index=await read('index.json');

function harness({positions=layout.positions,tracks=catalog.tracks,connections=indexConnections(index.links)}={}){
  let rect={width:740,height:505,left:0,top:0},dimensions={width:0,height:0},writes={width:0,height:0},rafId=0;
  const frames=new Map(),handlers={},notifications=[];
  const ctx=new Proxy({measureText:text=>({width:text.length*6})},{get:(o,k)=>o[k]??(()=>{}),set:(o,k,v)=>{o[k]=v;return true;}});
  const canvas={getContext:()=>ctx,getBoundingClientRect:()=>rect,addEventListener:(name,fn)=>{handlers[name]=fn;},setPointerCapture(){},hasPointerCapture:()=>false};
  for(const key of ['width','height'])Object.defineProperty(canvas,key,{get:()=>dimensions[key],set:v=>{dimensions[key]=v;writes[key]++;}});
  const previous=new Map();
  const globals={devicePixelRatio:1,matchMedia:()=>({matches:false,addEventListener(){}}),document:{hidden:false,addEventListener(){}},
    ResizeObserver:class{constructor(fn){this.callback=fn;}observe(){}disconnect(){}},
    requestAnimationFrame:fn=>{frames.set(++rafId,fn);return rafId;},cancelAnimationFrame:id=>frames.delete(id)};
  for(const [key,value] of Object.entries(globals)){previous.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});}
  const map=new AudioMap(canvas,{positions,tracks,connections,colors:{},onSelect(){},onHover(){},onTrace:state=>notifications.push(state)});
  return{map,canvas,frames,writes,handlers,notifications,resize(next){rect={...rect,...next};map.resize.callback();},restore(){map.destroy();for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}};
}

test('real active-release graph reuses the canvas backing store until CSS size or DPR changes',()=>{
  const h=harness();try{
    assert.deepEqual(h.writes,{width:1,height:1});
    for(let n=0;n<5;n++)h.map.draw();
    h.map.zoom(1.2);h.map.fit('all');
    assert.deepEqual(h.writes,{width:1,height:1});
    h.resize({width:800});assert.deepEqual(h.writes,{width:2,height:1});
    globalThis.devicePixelRatio=2;h.map.draw();assert.deepEqual(h.writes,{width:3,height:2});
    assert.equal(h.canvas.width,1600);assert.equal(h.canvas.height,1010);
  }finally{h.restore();}
});

test('automatic framing preserves exact result extents, pan/zoom geometry and all-track reset',()=>{
  const h=harness();try{
    const ids=[0,12,27,55],rows=ids.map(row=>({row}));h.map.setSearch(null,rows);
    const xs=ids.map(id=>layout.positions[id][0]),ys=ids.map(id=>layout.positions[id][1]);
    const g=h.map.camera;
    assert.equal(g.cx,(Math.min(...xs)+Math.max(...xs))/2);assert.equal(g.cy,(Math.min(...ys)+Math.max(...ys))/2);
    assert.equal(g.size,Math.min((740-170)/Math.max(.25,Math.max(...xs)-Math.min(...xs)),(505-130-82-30)/Math.max(.25,Math.max(...ys)-Math.min(...ys))));
    h.map.scale=1.7;h.map.offset=[17,-29];
    for(let id=0;id<layout.positions.length;id++){
      const p=layout.positions[id];assert.deepEqual(h.map.point(id),[g.screenX+(p[0]-g.cx)*g.size*1.7+17,g.screenY+(p[1]-g.cy)*g.size*1.7-29]);
    }
    h.map.fit('all');assert.equal(h.map.focus,'all');assert.equal(h.map.scale,1);assert.deepEqual(h.map.offset,[0,0]);
    h.map.setSearch(null,[]);assert.deepEqual(h.map.geometry('results'),h.map.geometry('all'));
  }finally{h.restore();}
});

test('visited-prefix cache remains exact when replay rewinds, skips, or replaces a search',()=>{
  const h=harness();try{
    const events=[{type:'enter',entryIds:[3]},{type:'expand',id:3,considered:[{id:7},{id:21}]},{type:'expand',id:7,considered:[{id:5}]}];
    h.map.setSearch({events},[{row:3}]);assert.deepEqual([...h.map.visitedThroughCursor()],[3,7,21,5]);
    h.map.cursor=1;assert.deepEqual([...h.map.visitedThroughCursor()],[3]);
    h.map.cursor=2;assert.deepEqual([...h.map.visitedThroughCursor()],[3,7,21]);
    h.map.cursor=3;assert.deepEqual([...h.map.visitedThroughCursor()],[3,7,21,5]);
    h.map.setSearch({events:[{type:'enter',entryIds:[0]}]},[{row:0}]);assert.deepEqual([...h.map.visitedThroughCursor()],[0]);
    h.map.setSearch(null,[]);assert.equal(h.map.visitedThroughCursor().size,0);
  }finally{h.restore();}
});

test('replaced or paused graph animation cannot mutate the newer camera or traversal',()=>{
  const h=harness();try{
    const trace={events:[{type:'enter',entryIds:[3]},{type:'expand',id:3,level:0,considered:[{id:7,accepted:true,distance:.2}]},{type:'complete'}]};
    h.map.setSearch(trace,[{row:3},{row:7}],{animate:true});assert.equal(h.map.playing,true);
    const stale=[...h.frames.values()][0];h.map.setSearch(null,[{row:27}]);
    const camera={...h.map.camera};stale(performance.now()+5000);
    assert.deepEqual(h.map.camera,camera);assert.deepEqual(h.map.results,[27]);assert.equal(h.map.playing,false);
    h.map.setSearch(trace,[{row:3},{row:7}],{animate:true});h.map.pause();assert.equal(h.frames.size,0);
    h.map.finish();assert.equal(h.map.elapsed,h.map.duration);assert.deepEqual(h.map.camera,h.map.searchView());
  }finally{h.restore();}
});

test('10k synthetic geometry fixture projects each point once per frame and never rescans bounds',()=>{
  // Geometry stress only: these points are not music records, embeddings or a catalog expansion.
  let coordinateReads=0;
  const positions=Array.from({length:10_000},(_,i)=>new Proxy([i%100,Math.floor(i/100)],{get:(p,key)=>{if(key==='0'||key==='1')coordinateReads++;return p[key];}}));
  const tracks=positions.map((_,i)=>({title:'Synthetic geometry '+i}));
  const connections=positions.flatMap((_,i)=>Array.from({length:6},(_,n)=>({from:i,to:(i+n+1)%positions.length,level:0})));
  const h=harness({positions,tracks,connections});try{
    const project=h.map.project.bind(h.map);let calls=0;h.map.project=(...args)=>{calls++;return project(...args);};
    coordinateReads=0;for(let n=0;n<50;n++)h.map.geometry('all');assert.equal(coordinateReads,0);
    h.map.draw();assert.equal(calls,10_000);assert.equal(coordinateReads,20_000);assert.deepEqual(h.writes,{width:1,height:1});
    const geometry=h.map.geometry.bind(h.map);let bounds=0;
    h.map.geometry=(...args)=>{bounds++;return geometry(...args);};calls=0;
    h.map.hit({clientX:370,clientY:200});assert.equal(bounds,1);assert.equal(calls,0);assert.ok(h.map._visibleIds.length<=320);
  }finally{h.restore();}
});

test('5k context display has hard density budgets and uses only real points and stored links',()=>{
  const points=Array.from({length:5000},(_,i)=>[20+(i%100)*7,80+Math.floor(i/100)*5]);
  const connections=points.map((_,i)=>({from:i,to:(i+1)%points.length,level:0}));
  const important=[17,36,47];
  const overview=visibleGraphContext(points,connections,{width:740,height:505,important});
  assert.ok(overview.ids.length<=320);assert.equal(overview.edges.length,0);
  for(const id of important)assert.ok(overview.ids.includes(id));
  const detail=visibleGraphContext(points,connections,{width:740,height:505,detail:2,important});
  assert.ok(detail.ids.length<=600);assert.ok(detail.edges.length<=160);
  for(const edge of detail.edges){assert.ok(connections.includes(edge));assert.ok(detail.ids.includes(edge.from)&&detail.ids.includes(edge.to));}
});

test('manual map stages and keyboard Home stop motion and select deliberate camera bounds',()=>{
  const h=harness();try{
    h.map.setSearch({events:[{type:'enter',entryIds:[3]}]},[{row:3}],{animate:true});
    h.map.fit('all');assert.equal(h.map.playing,false);assert.equal(h.map.focus,'all');assert.equal(h.notifications.at(-1).completed,true);
    h.map.select(3);h.map.fit('selected');
    assert.equal(h.map.geometry().cx,layout.positions[3][0]);assert.equal(h.map.geometry().cy,layout.positions[3][1]);
    h.handlers.keydown({key:'Home',preventDefault(){}});assert.equal(h.map.focus,'all');
    h.handlers.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(h.map.offset[0],-35);
    const rect=h.canvas.getBoundingClientRect();
    const hidden=layout.positions.findIndex((_,id)=>!h.map._visibleIds.includes(id));
    if(hidden>=0){const point=h.map.point(hidden),hit=h.map.hit({clientX:point[0]+rect.left,clientY:point[1]+rect.top});assert.notEqual(hit,hidden);}
  }finally{h.restore();}
});

test('map number labels retain the same absolute display ranks as paged results',()=>{
  const h=harness();try{h.map.setSearch(null,[{row:3,displayRank:13},{row:7,displayRank:14}]);assert.equal(h.map.resultRanks.get(3),13);assert.equal(h.map.resultRanks.get(7),14);}finally{h.restore();}
});
