import {searchBeats,motionAt,beatEdges,indexConnections,interpolateView} from './search-motion.mjs';
import {TileField,tileLevel,tilesInView,regionAlpha,TILE_MIN_LEVEL} from './map-lod.mjs';
export const DEFAULT_INSETS=Object.freeze({top:70,bottom:125});
export function placeLabel(point,textWidth,boxes,anchors,width,height,insets=DEFAULT_INSETS){
  const candidates=[];
  for(const side of [1,-1])for(const dy of [-18,30,-46,58,-74,86]){
    const x=Math.max(8,Math.min(width-textWidth-14,side===1?point[0]+16:point[0]-textWidth-16));
    const y=Math.max(insets.top+14,Math.min(height-insets.bottom-17,point[1]+dy));
    const rect={x:x-5,y:y-13,w:textWidth+10,h:23};
    const overlap=boxes.filter(b=>rect.x<b.x+b.w&&rect.x+rect.w>b.x&&rect.y<b.y+b.h&&rect.y+rect.h>b.y).length;
    const covered=anchors.filter(([px,py])=>Math.hypot(px-Math.max(rect.x,Math.min(px,rect.x+rect.w)),py-Math.max(rect.y,Math.min(py,rect.y+rect.h)))<13).length;
    candidates.push({x,y,rect,score:covered*10000+overlap*1000+Math.abs(dy)+(side===-1?5:0)});
  }
  return candidates.sort((a,b)=>a.score-b.score)[0];
}
function boundsFor(positions,ids=null){
  let x0=Infinity,x1=-Infinity,y0=Infinity,y1=-Infinity;
  const include=p=>{x0=Math.min(x0,p[0]);x1=Math.max(x1,p[0]);y0=Math.min(y0,p[1]);y1=Math.max(y1,p[1]);};
  if(ids)for(const id of ids)include(positions[id]);else positions.forEach(point=>include(point));
  return {x0,x1,y0,y1};
}
// A display budget, never a change to the graph or catalog. Every retained
// point is an actual row and every retained edge is an existing index link.
export const LINKS_PER_VISIBLE_POINT=0.6,MAX_VISIBLE_LINKS=160;
// With `order` (release format v2 level of detail) only those candidate ids are considered, in that order,
// and `pointOf(id)` projects each one on demand, so a frame costs O(candidates), not O(catalog).
export function visibleGraphContext(points,connections,{width,height,detail=1,important=[],top=DEFAULT_INSETS.top,bottom=DEFAULT_INSETS.bottom,order=null,pointOf=null}={}){
  const nodeBudget=detail<1.6?320:600,cell=detail<1.6?14:9,at=pointOf??(id=>points[id]);
  const ids=[],seen=new Set(),cells=new Set(),priority=new Set(important.filter(Number.isInteger));
  const add=id=>{
    if(seen.has(id)||ids.length>=nodeBudget)return;
    const p=at(id);if(!p)return;
    const [x,y]=p;if(x<0||x>width||y<top||y>height-bottom)return;
    const key=Math.floor(x/cell)*1e6+Math.floor(y/cell);
    if(!priority.has(id)&&cells.has(key))return;
    cells.add(key);seen.add(id);ids.push(id);
  };
  for(const id of priority)add(id);
  if(order){for(const id of order){if(ids.length>=nodeBudget)break;add(id);}}
  else for(let id=0;id<points.length&&ids.length<nodeBudget;id++)add(id);
  // Links are budgeted against the points actually shown, so a tight Matches view is not
  // covered by the full 160-link allowance; links touching results, the selection, the
  // hover or the frontier are kept first.
  const edgeBudget=detail<1.6?0:Math.min(MAX_VISIBLE_LINKS,Math.round(ids.length*LINKS_PER_VISIBLE_POINT));
  const preferred=[],other=[];
  if(edgeBudget)for(const edge of connections){
    if(!seen.has(edge.from)||!seen.has(edge.to))continue;
    const p=at(edge.from),q=at(edge.to);if(Math.hypot(p[0]-q[0],p[1]-q[1])<2)continue;
    const target=priority.has(edge.from)||priority.has(edge.to)?preferred:other;
    if(target.length<edgeBudget)target.push(edge);
  }
  return {ids,edges:[...preferred,...other].slice(0,edgeBudget),nodeBudget,edgeBudget};
}
// A cached raster of every layout position as soft alpha dots, drawn under the budgeted
// interactive points so the whole collection reads as a cloud at any catalog size with one
// drawImage per frame and no link mesh. Display only: it never changes the point or link
// budgets, the hit targets or the stored connections. Rasterized in layout space once per
// zoom bucket (octaves of the overview scale) and cached; the camera moves it with the same
// transform as the points, so dots stay about the same size on screen at every zoom.
export const DENSITY_MAX_SIDE=2048,DENSITY_BUCKETS=4;
export const densityBucket=detail=>Math.max(0,Math.min(DENSITY_BUCKETS-1,Math.round(Math.log2(Math.max(1,detail)))));
function defaultLayerFactory(width,height){
  if(typeof OffscreenCanvas==='function')return new OffscreenCanvas(width,height);
  if(typeof document!=='undefined'&&typeof document.createElement==='function'){const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;return canvas;}
  return null;
}
// Raster pixels per layout unit for a bucket: its device pixels (zoom × overview), capped to maxSide.
export function densityResolution(bounds,{overviewSize,dpr=1,zoom=1,maxSide=DENSITY_MAX_SIDE}={}){
  const w=Math.max(.25,bounds.x1-bounds.x0),h=Math.max(.25,bounds.y1-bounds.y0),pad0=.04*Math.max(w,h);
  return Math.max(1e-6,Math.min(Math.max(1e-6,overviewSize*zoom)*dpr,maxSide/(Math.max(w,h)+2*pad0)));
}
export function rasterizeDensity(positions,bounds,{overviewSize,dpr=1,zoom=1,color='#999',createLayer=defaultLayerFactory,maxSide=DENSITY_MAX_SIDE,weights=null}={}){
  const w=Math.max(.25,bounds.x1-bounds.x0),h=Math.max(.25,bounds.y1-bounds.y0);
  const pad0=.04*Math.max(w,h),screen=Math.max(1e-6,overviewSize*zoom);
  const res=densityResolution(bounds,{overviewSize,dpr,zoom,maxSide});
  // About 1.2 CSS px per dot at the bucket's zoom (never below 0.75 raster px when capped).
  const radius=Math.max(.75,1.2*res/screen),pad=Math.max(pad0,2*radius/res);
  const width=Math.ceil((w+2*pad)*res),height=Math.ceil((h+2*pad)*res);
  const canvas=createLayer(width,height);const ctx=canvas?.getContext?.('2d');
  if(!ctx)return null;
  const x0=bounds.x0-pad,y0=bounds.y0-pad;
  ctx.clearRect(0,0,width,height);ctx.fillStyle=color;ctx.globalAlpha=.3;
  // One fill per dot so overlapping dots accumulate into density. A weighted sample point (release
  // format v2) stands for w rows: it draws with the alpha of w overlapping dots, 1-(1-0.3)^w.
  positions.forEach((p,i)=>{if(weights)ctx.globalAlpha=1-Math.pow(.7,weights[i]);ctx.beginPath();ctx.arc((p[0]-x0)*res,(p[1]-y0)*res,radius,0,Math.PI*2);ctx.fill();});
  ctx.globalAlpha=1;
  return {canvas,x0,y0,spanX:width/res,spanY:height/res,res,zoom,points:positions.length};
}
export class AudioMap {
  // lod (release format v2): {sampleRows, regions, tiles:{domain,cap,maxLevel,fetchTile}|null, links:{get,request}|null}.
  constructor(canvas,{positions,tracks,connections=[],colors,onSelect,onHover,onTrace,onDensity=()=>{},insets=DEFAULT_INSETS,createLayer=defaultLayerFactory,density=null,bounds=null,lod=null}){this.lod=lod;this.tileField=lod?.tiles?new TileField({...lod.tiles,createLayer,color:colors?.node||'#999',onChange:()=>this.requestDraw()}):null;this._textWidths=new Map();this.readInsets=typeof insets==='function'?insets:()=>insets;this.insets=this.measureInsets();this.createLayer=createLayer;this._density=new Map();this.canvas=canvas;this.ctx=canvas.getContext('2d');this.positions=positions;this.density=density;this.allBounds=bounds?{...bounds}:boundsFor(positions);this.resultBounds=null;this.tracks=tracks;this.connections=connections;this.colors=colors;this.onSelect=onSelect;this.onHover=onHover;this.onTrace=onTrace;this.onDensity=onDensity;this._visibleIds=[];this._hitPoints=[];this.scale=1;this.offset=[0,0];this.focus='results';this.selected=0;this.inspected=null;this.results=[];this.resultRanks=new Map();this.events=[];this.visited=new Set();this.visitedCursor=0;this.cursor=0;this.elapsed=0;this.duration=1;this.beats=[];this.reveal=1;this.playing=false;this.raf=0;this.motionGeneration=0;this.camera=null;this.cameraTransition=null;this.hover=null;this.pointer=null;this.motion=matchMedia('(prefers-reduced-motion: reduce)');this.abort=new AbortController();const opt={signal:this.abort.signal};this.resize=new ResizeObserver(()=>{this.insets=this.measureInsets();this._density.clear();this.cancelWarm();this.camera=null;this.cameraTransition=null;this.draw();});this.resize.observe(canvas);canvas.addEventListener('pointerdown',e=>{if(this.pointer)return;this.cameraTransition=null;this.pointer={id:e.pointerId,x:e.clientX,y:e.clientY,offset:[...this.offset],moved:false};canvas.setPointerCapture(e.pointerId);},opt);canvas.addEventListener('pointermove',e=>{if(this.pointer){if(e.pointerId!==this.pointer.id)return;const dx=e.clientX-this.pointer.x,dy=e.clientY-this.pointer.y;if(Math.hypot(dx,dy)>5)this.pointer.moved=true;if(this.pointer.moved){this.offset=[this.pointer.offset[0]+dx,this.pointer.offset[1]+dy];this.draw();}return;}const id=this.hit(e);if(id!==this.hover){this.hover=id;this.onHover(id,e);this.draw();}},opt);canvas.addEventListener('pointerup',e=>{const p=this.pointer;if(!p||e.pointerId!==p.id)return;this.pointer=null;if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);if(!p.moved){const id=this.hit(e);if(id!==null)this.onSelect(id);}else if(this.lod)this.draw();},opt);for(const event of ['pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>{this.pointer=null;},opt);canvas.addEventListener('pointerleave',()=>{this.hover=null;this.onHover(null);this.draw();},opt);canvas.addEventListener('keydown',e=>{if(e.key==='Home'){e.preventDefault();this.fit('all');}if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();this.cameraTransition=null;this.offset[0]+=e.key==='ArrowLeft'?35:e.key==='ArrowRight'?-35:0;this.offset[1]+=e.key==='ArrowUp'?35:e.key==='ArrowDown'?-35:0;this.draw();}if(e.key==='+'||e.key==='='){e.preventDefault();this.zoom(1.2);}if(e.key==='-'){e.preventDefault();this.zoom(1/1.2);}if(e.key==='Escape'){this.hover=null;this.onHover(null);this.draw();}},opt);document.addEventListener('visibilitychange',()=>{if(document.hidden&&this.playing)this.pause();},opt);this.motion.addEventListener('change',()=>{if(this.motion.matches)this.finish();},opt);this.draw();}
  geometry(mode=this.focus){
    let bounds=mode==='results'&&this.resultBounds?this.resultBounds:this.allBounds;
    if(mode==='selected'&&Number.isInteger(this.selected)){const p=this.positions[this.selected],radius=Math.max(this.allBounds.x1-this.allBounds.x0,this.allBounds.y1-this.allBounds.y0)/8;bounds={x0:p[0]-radius,x1:p[0]+radius,y0:p[1]-radius,y1:p[1]+radius};}
    const {x0,x1,y0,y1}=bounds;
    const top=this.insets.top+12,bottom=this.height-this.insets.bottom-5;
    return{cx:(x0+x1)/2,cy:(y0+y1)/2,screenX:this.width/2-20,screenY:(top+bottom)/2,size:Math.max(1,Math.min((this.width-170)/Math.max(.25,x1-x0),(bottom-top-30)/Math.max(.25,y1-y0)))};
  }
  measureInsets(){const value=this.readInsets?.()??DEFAULT_INSETS,top=Number(value?.top),bottom=Number(value?.bottom);return{top:Number.isFinite(top)&&top>=0?top:DEFAULT_INSETS.top,bottom:Number.isFinite(bottom)&&bottom>=0?bottom:DEFAULT_INSETS.bottom};}
  densityLayer(dpr,detail){
    const overviewSize=this.geometry('all').size,zoom=2**densityBucket(detail);
    // Buckets whose capped resolution coincides share one raster.
    const key=densityResolution(this.allBounds,{overviewSize,dpr,zoom}).toPrecision(6)+'@'+dpr;
    if(!this._density.has(key)){
      this._density.set(key,rasterizeDensity(this.density?.points??this.positions,this.allBounds,{overviewSize,dpr,zoom,color:this.colors?.node||'#999',createLayer:this.createLayer,weights:this.density?.weights??null}));
      if(this._density.size>DENSITY_BUCKETS)this._density.delete(this._density.keys().next().value);
    }
    return this._density.get(key);
  }
  // Rasterise every zoom bucket in idle time, once per layout size and DPR, so search animations never
  // pay for a first build. A hidden or zero-sized map stops the pass; the next draw restarts it.
  warmDensity(dpr){
    if(this._warming||this._warmedDpr===dpr||typeof requestIdleCallback!=='function')return;
    this._warming=true;let bucket=0;
    const next=()=>{
      this._warmTask=0;
      if(this.abort.signal.aborted||!this.width||!this.height){this._warming=false;return;}
      if(bucket>=DENSITY_BUCKETS){this._warming=false;this._warmedDpr=dpr;return;}
      this.densityLayer(dpr,2**bucket++);this._warmTask=requestIdleCallback(next,{timeout:1500});
    };
    this._warmTask=requestIdleCallback(next,{timeout:1500});
  }
  cancelWarm(){if(this._warmTask&&typeof cancelIdleCallback==='function')cancelIdleCallback(this._warmTask);this._warmTask=0;this._warming=false;this._warmedDpr=null;}
  project(id,g){const p=this.positions[id];return[g.screenX+(p[0]-g.cx)*g.size*this.scale+this.offset[0],g.screenY+(p[1]-g.cy)*g.size*this.scale+this.offset[1]];}
  point(id){return this._drawingPoints?.[id]??this.project(id,this._drawingGeometry??this.camera??this.geometry());}
  hit(e){
    const r=this.canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,g=this.camera??this.geometry();let best=null,d=14;
    const inspect=id=>{const p=this._hitPoints[id]??this.project(id,g);if(p[1]<this.insets.top||p[1]>this.height-this.insets.bottom)return;const n=Math.hypot(x-p[0],y-p[1]);if(n<d){d=n;best=id;}};
    // Keep result-first tie behavior without allocating a second catalog-sized array.
    for(const id of this.results)if(this._visibleIds.includes(id))inspect(id);
    for(const id of this._visibleIds)if(!this.results.includes(id))inspect(id);
    return best;
  }
  visitedThroughCursor(){
    if(this.cursor<this.visitedCursor){this.visited.clear();this.visitedCursor=0;}
    for(;this.visitedCursor<this.cursor;this.visitedCursor++){
      const e=this.events[this.visitedCursor];
      if(e.type==='enter')for(const id of e.entryIds??[])this.visited.add(id);
      if(e.type==='expand'){this.visited.add(e.id);for(const c of e.considered)this.visited.add(c.id);}
    }
    return this.visited;
  }
  setSearch(trace,rows,{animate=false,preserveView=false}={}){
    if(preserveView){this.updateResults(rows);return;}
    const viewport=this.canvas.getBoundingClientRect();this.width=viewport.width;this.height=viewport.height;
    const base=this.width&&this.results.length?this.camera??this.geometry():null;
    const previous=base?{...base,size:base.size*this.scale,screenX:base.screenX+this.offset[0],screenY:base.screenY+this.offset[1]}:null;
    this.pause();this.events=trace?.events??[];this.visited.clear();this.visitedCursor=0;this.setRows(rows);
    this.beats=searchBeats(this.events);this.duration=this.beats.reduce((sum,b)=>sum+b.duration,0)||1;
    this.cursor=this.events.length;this.elapsed=this.duration;this.reveal=1;
    this.selected=rows[0]?.row??null;this.inspected=null;
    this.focus='results';this.scale=1;this.offset=[0,0];this.camera=this.searchView();this.cameraTransition=null;
    // Level of detail: the tiles where this search will land are requested now, while it animates.
    if(this.tileField&&this.width){const dest=this.lodView(this.camera);if(dest.tiles)this.tileField.want(dest.z,dest.list,{immediate:true});}
    if(animate&&this.events.length&&!this.motion.matches){
      this.prepareCamera(previous);this.elapsed=0;this.cursor=0;this.reveal=0;this.replay();
    }else{this.draw();this.notify();}
  }
  // An explicit null display rank marks rows that are matches but not a ranking (name lookup).
  setRows(rows){this.results=rows.map(r=>r.row);this.resultRanks=new Map(rows.map((r,i)=>[r.row,r.displayRank===null?null:r.displayRank??i+1]));this.resultBounds=this.results.length?boundsFor(this.positions,this.results):null;}
  // Refinement, paging and display-policy changes replace the highlighted rows but keep the
  // viewer's camera, zoom, pan, selection and trace position exactly as they were.
  updateResults(rows){
    const viewport=this.canvas.getBoundingClientRect();this.width=viewport.width;this.height=viewport.height;
    if(this.width&&this.height&&!this.camera&&!this.cameraTransition)this.camera=this.geometry();
    this.setRows(rows);
    if(!this.playing)this.draw();
  }
  searchView(){return this.geometry('results');}
  prepareCamera(previous=null){
    this.cameraTransition={from:previous??this.camera??this.geometry(),overview:this.geometry('all'),to:this.searchView()};
    this.camera=this.cameraTransition.from;
  }
  updateCamera(){
    if(!this.cameraTransition)return;
    const beat=motionAt(this.beats,this.elapsed),plan=this.cameraTransition;
    let from=plan.from,to=plan.overview,t=Math.min(1,this.elapsed/800);
    if(beat?.kind==='results'){from=plan.overview;to=plan.to;t=Math.min(1,beat.progress/.95);}
    const ease=t*t*(3-2*t);
    this.camera=interpolateView(from,to,ease);
    if(this.elapsed>=this.duration){this.camera=plan.to;this.cameraTransition=null;}
  }
  select(id,{explicit=false}={}){if(this.focus==='selected'&&this.selected!==id)this.offset=[0,0];this.selected=id;if(explicit)this.inspected=id;this.draw();}
  notify(){const beat=motionAt(this.beats,this.elapsed);this.onTrace({playing:this.playing,cursor:this.cursor,total:this.events.length,event:this.events[Math.max(0,this.cursor-1)]??null,reducedMotion:this.motion.matches,completed:this.elapsed>=this.duration,progress:this.elapsed/this.duration,phase:beat?.kind,beat:beat?.index,beatCount:this.beats.length});}
  replay(){
    if(this.motion.matches){this.finish();return;}
    if(!this.events.length||this.playing)return;
    if(this.elapsed>=this.duration){this.prepareCamera();this.cursor=0;this.elapsed=0;this.reveal=0;}
    this.playing=true;const generation=++this.motionGeneration,started=performance.now(),elapsed=this.elapsed;
    this.draw();this.notify();
    const tick=t=>{
      if(!this.playing||generation!==this.motionGeneration)return;
      this.elapsed=Math.min(this.duration,elapsed+Math.max(0,t-started));
      const beat=motionAt(this.beats,this.elapsed);this.cursor=beat?beat.eventIndex+1:0;
      this.reveal=beat?.kind==='results'?Math.min(1,Math.max(0,(beat.progress-.25)/.6)):0;
      this.updateCamera();this.draw();
      if(this.elapsed>=this.duration)this.playing=false;
      this.notify();if(this.playing)this.raf=requestAnimationFrame(tick);
    };
    this.raf=requestAnimationFrame(tick);
  }
  pause(){this.playing=false;this.motionGeneration++;cancelAnimationFrame(this.raf);this.notify();}
  finish(){this.pause();this.cursor=this.events.length;this.elapsed=this.duration;this.reveal=1;this.camera=this.searchView();this.cameraTransition=null;this.draw();this.notify();}
  fit(mode='results'){this.pause();this.cursor=this.events.length;this.elapsed=this.duration;this.reveal=1;this.camera=null;this.cameraTransition=null;this.focus=mode;this.scale=1;this.offset=[0,0];this.draw();this.notify();}
  zoom(f){this.pause();this.cameraTransition=null;this.scale=Math.max(.6,Math.min(8,this.scale*f));this.draw();}
  // Level of detail: the tile level and the tiles in view (with a 10% margin) for a camera.
  lodView(g){
    const f=this.tileField;if(!f||!this.width)return{tiles:false};
    const k=g.size*this.scale,z=tileLevel(f.domain,k,f.maxLevel);
    if(z<TILE_MIN_LEVEL)return{tiles:false,z};
    const inv=(sx,sy)=>[g.cx+(sx-g.screenX-this.offset[0])/k,g.cy+(sy-g.screenY-this.offset[1])/k];
    const [ax,ay]=inv(0,this.insets.top),[bx,by]=inv(this.width,this.height-this.insets.bottom),mx=(bx-ax)*.1,my=(by-ay)*.1;
    return{tiles:true,z,list:tilesInView(f.domain,z,{x0:ax-mx,y0:ay-my,x1:bx+mx,y1:by+my})};
  }
  // Each tile in view replaces the overview cloud under it with its own raster (or its loaded ancestor's);
  // tile edges are snapped to device pixels so neighbouring tiles meet without seams.
  drawTiles(ctx,g,dpr,view,alpha){
    const f=this.tileField,k=g.size*this.scale,s=f.domain[2]/2**view.z;f.beginFrame();
    const sx=i=>Math.round((g.screenX+(f.domain[0]+i*s-g.cx)*k+this.offset[0])*dpr)/dpr,sy=j=>Math.round((g.screenY+(f.domain[1]+j*s-g.cy)*k+this.offset[1])*dpr)/dpr;
    ctx.globalAlpha=alpha;
    for(const [x,y] of view.list){
      const src=f.source(view.z,x,y);if(!src)continue;
      const x0=sx(x),x1=sx(x+1),y0=sy(y),y1=sy(y+1);
      ctx.clearRect(x0,y0,x1-x0,y1-y0);ctx.drawImage(src.canvas,src.sx,src.sy,src.size,src.size,x0,y0,x1-x0,y1-y0);
    }
    ctx.globalAlpha=1;
  }
  textWidth(ctx,text){let w=this._textWidths.get(text);if(w===undefined){w=ctx.measureText(text).width;this._textWidths.set(text,w);}return w;}
  // Region labels: the most common source genre of an area, shown only within its level's zoom range,
  // never over a track label or a match, and not repeated nearby. Dimmed while a search replays.
  drawRegions(ctx,g,detail,boxes,anchors,replaying){
    const regions=this.lod?.regions;if(!regions?.length)return;
    const k=g.size*this.scale,low=this.height-this.insets.bottom,placed=[];
    ctx.font='600 11px Arial';ctx.textAlign='center';ctx.textBaseline='middle';ctx.lineJoin='round';
    for(const level of regions){
      const alpha=regionAlpha(detail,level.from,level.to)*(replaying?.55:1);if(alpha<.02)continue;
      for(const item of level.items){
        const x=g.screenX+(item.x-g.cx)*k+this.offset[0],y=g.screenY+(item.y-g.cy)*k+this.offset[1];
        if(x<0||x>this.width||y<this.insets.top+10||y>low-10)continue;
        const text=item.label.toUpperCase(),w=this.textWidth(ctx,text),r={x:x-w/2-4,y:y-9,w:w+8,h:18};
        if(r.x<2||r.x+r.w>this.width-2||placed.some(p=>p.text===text&&Math.hypot(p.x-x,p.y-y)<160))continue;
        if(boxes.some(b=>r.x<b.x+b.w&&r.x+r.w>b.x&&r.y<b.y+b.h&&r.y+r.h>b.y)||anchors.some(([px,py])=>px>r.x-6&&px<r.x+r.w+6&&py>r.y-6&&py<r.y+r.h+6))continue;
        boxes.push(r);placed.push({text,x,y});
        ctx.globalAlpha=alpha*.9;ctx.strokeStyle=this.colors.paper;ctx.lineWidth=3;ctx.strokeText(text,x,y);
        ctx.globalAlpha=alpha;ctx.fillStyle=this.colors.muted;ctx.fillText(text,x,y);
      }
    }
    ctx.globalAlpha=1;ctx.textAlign='left';ctx.textBaseline='alphabetic';
  }
  requestDraw(){if(this.playing||this._redraw||this.abort.signal.aborted)return;this._redraw=requestAnimationFrame(()=>{this._redraw=0;this.draw();});}
  draw(){
    const rect=this.canvas.getBoundingClientRect();this.width=rect.width;this.height=rect.height;
    if(!this.width||!this.height)return;
    const dpr=Math.min(devicePixelRatio||1,2),pixelWidth=Math.round(this.width*dpr),pixelHeight=Math.round(this.height*dpr);
    // Assigning either dimension clears and reallocates the backing store. Resize only when needed.
    if(this.canvas.width!==pixelWidth)this.canvas.width=pixelWidth;
    if(this.canvas.height!==pixelHeight)this.canvas.height=pixelHeight;
    const ctx=this.ctx,colors=this.colors,{top,bottom}=this.insets,low=this.height-bottom;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,this.width,this.height);
    this._drawingGeometry=this.camera??this.geometry();
    const lod=this.lod;
    if(!lod)this._drawingPoints=this.positions.map((_,id)=>this.project(id,this._drawingGeometry));
    ctx.save();ctx.beginPath();ctx.rect(0,top,this.width,Math.max(0,low-top));ctx.clip();
    const beat=motionAt(this.beats,this.elapsed),done=this.elapsed>=this.duration;
    const event=beat?this.events[beat.eventIndex]:null;
    const visited=this.visitedThroughCursor();
    const frontier=new Set(event?.frontier?.map(x=>x.id)??[]);
    // All context lines are deduplicated stored index links, never proximity guesses.
    // Offscreen links and subpixel segments are omitted only as level-of-detail.
    const g=this._drawingGeometry,overviewSize=this.geometry('all').size,detail=(g.size*this.scale)/overviewSize;
    const currentId=!done?(event?.type==='expand'?event.id:event?.entryIds?.[0]):null;
    const important=[...this.results,this.selected,this.hover,currentId,...frontier];
    let context,view=null,focus=null;
    if(lod){
      // Release format v2: candidates are the trace's visited rows, then the overview sample or the rows of the
      // tiles in view; each is projected only when considered. Links are drawn only around the selection.
      const projected=[],pointOf=id=>{let p=projected[id];if(p===undefined&&this.positions[id]){p=this.project(id,g);projected[id]=p;}return p;};
      view=this.lodView(g);
      focus=done&&Number.isInteger(this.selected)?lod.links?.get(this.selected)??null:null;
      const order=view.tiles?[...visited,...this.tileField.candidates(view.z,view.list)]:[...visited,...lod.sampleRows];
      context=visibleGraphContext(projected,[],{width:this.width,height:this.height,detail,top,bottom,important:[...important,...(focus?focus.levels.flat():[])],order,pointOf});
      this._drawingPoints=projected;
    }else context=visibleGraphContext(this._drawingPoints,this.connections,{width:this.width,height:this.height,detail,top,bottom,important});
    this._visibleIds=context.ids;this._hitPoints=this._drawingPoints;this._context=context;
    // The collection cloud: every position, cached, faded as the budgeted points take over when zoomed in.
    const layer=this.densityLayer(dpr,detail),cloudAlpha=detail<=1.6?1:Math.max(.4,1-(detail-1.6)*.25);
    if(layer){this.warmDensity(dpr);
      const k=g.size*this.scale;ctx.globalAlpha=cloudAlpha;
      ctx.drawImage(layer.canvas,g.screenX+(layer.x0-g.cx)*k+this.offset[0],g.screenY+(layer.y0-g.cy)*k+this.offset[1],layer.spanX*k,layer.spanY*k);
    }
    if(view?.tiles)this.drawTiles(ctx,g,dpr,view,cloudAlpha);
    // During replay only the trace's own accepted edges and the result anchors are drawn;
    // stored context links return once the replay is done, and only when zoomed in.
    const contextEdges=done?context.edges:[];
    const focusEdges=focus&&detail>=1.6?focus.levels.flatMap((level,n)=>level.map(to=>({from:focus.row,to,level:n}))):[];
    this.onDensity({visible:context.ids.length,edges:lod?focusEdges.length:contextEdges.length,total:this.tracks.length,replaying:!done,cloud:!!layer,...(lod?{tileLevel:view.tiles?view.z:null}:{})});
    if(lod&&!this.playing&&!this.cameraTransition&&!this.pointer){
      this.tileField?.want(view.tiles?view.z:0,view.tiles?view.list:[]);
      if(done&&Number.isInteger(this.selected)&&!focus)lod.links?.request(this.selected);
    }
    const strokeGroup=(edges,style)=>{
      if(!edges.length)return;ctx.beginPath();
      for(const edge of edges){
        const p=this.point(edge.from),q=this.point(edge.to);
        if((p[0]<0&&q[0]<0)||(p[0]>this.width&&q[0]>this.width)||(p[1]<top&&q[1]<top)||(p[1]>low&&q[1]>low)||Math.hypot(p[0]-q[0],p[1]-q[1])<2)continue;
        ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);
      }
      ctx.strokeStyle=style.color;ctx.globalAlpha=style.alpha;ctx.lineWidth=style.width;ctx.stroke();
    };
    // One path per style: the same pixels as per-edge strokes, at a fraction of the draw calls.
    strokeGroup(contextEdges.filter(e=>e.level>0),{color:colors.node,alpha:.11+detail*.035,width:1});
    strokeGroup(contextEdges.filter(e=>!(e.level>0)),{color:colors.node,alpha:.11+detail*.035,width:.7});
    // v2: the stored links of the selected recording only, a little stronger than the old context mesh.
    strokeGroup(focusEdges.filter(e=>e.level>0),{color:colors.node,alpha:.42,width:1.2});
    strokeGroup(focusEdges.filter(e=>!(e.level>0)),{color:colors.node,alpha:.34,width:.8});
    // Keep the context quiet. Only recent real accepted branches form the trail.
    const trail=this.beats.slice(Math.max(0,(beat?.index??0)-3),beat?.index??0);
    ctx.strokeStyle=colors.visited;ctx.lineWidth=1;
    for(let n=0;n<trail.length;n++)for(const edge of beatEdges(this.events[trail[n].eventIndex])){
      const p=this.point(edge.from),q=this.point(edge.to);ctx.globalAlpha=done?.08:.12+n*.08;
      ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(...q);ctx.stroke();
    }
    const currentEdges=!done&&beat?.kind==='branch'?beatEdges(event):[];
    const activeId=!done&&beat?.kind!=='results'?(event?.type==='expand'?event.id:event?.entryIds?.[0]):null;
    const growth=Math.min(1,Math.max(0,((beat?.progress??0)-.15)/.65));
    const eased=growth*growth*(3-2*growth);
    for(const edge of currentEdges){
      const p=this.point(edge.from),q=this.point(edge.to);ctx.globalAlpha=.8;ctx.strokeStyle=colors.frontier;ctx.lineWidth=1.8;
      ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(p[0]+(q[0]-p[0])*eased,p[1]+(q[1]-p[1])*eased);ctx.stroke();
    }
    // Points: base dots and frontier rings are batched per style; results, the active visit,
    // the selection and the hover are drawn on top of them individually.
    const rankOf=new Map(this.results.map((id,i)=>[id,i])),plain=[],seenDots=[],rings=[],marks=[];
    for(const id of context.ids){
      const [x,y]=this.point(id);
      if(x< -20||x>this.width+20||y<top-20||y>low+20)continue;
      const rank=rankOf.get(id)??-1;
      (visited.has(id)?seenDots:plain).push(x,y,rank>=0?2.6:1.7);
      if(!done&&frontier.has(id))rings.push(x,y);
      if(rank>=0||id===activeId||id===this.selected||id===this.hover||id===this.inspected)marks.push(id);
    }
    const fillDots=(dots,color,alpha)=>{if(!dots.length)return;ctx.beginPath();for(let i=0;i<dots.length;i+=3){ctx.moveTo(dots[i]+dots[i+2],dots[i+1]);ctx.arc(dots[i],dots[i+1],dots[i+2],0,Math.PI*2);}ctx.fillStyle=color;ctx.globalAlpha=alpha;ctx.fill();};
    fillDots(plain,colors.node,.55);fillDots(seenDots,colors.visited,done?.5:.85);
    if(rings.length){ctx.beginPath();for(let i=0;i<rings.length;i+=2){ctx.moveTo(rings[i]+4.5,rings[i+1]);ctx.arc(rings[i],rings[i+1],4.5,0,Math.PI*2);}ctx.globalAlpha=.85;ctx.strokeStyle=colors.frontier;ctx.lineWidth=1.3;ctx.stroke();}
    ctx.globalAlpha=1;
    for(const id of marks){
      const [x,y]=this.point(id);
      const rank=rankOf.get(id)??-1,explicit=id===this.inspected;
      const reveal=rank<0?0:done||explicit?1:Math.max(0,Math.min(1,(this.reveal-rank/Math.max(1,this.results.length)*.45)/.55));
      const selected=id===this.selected&&reveal>0,active=id===activeId;
      if(active){
        const pulse=Math.sin(Math.PI*(beat?.progress??0));ctx.globalAlpha=.12+.12*pulse;ctx.fillStyle=colors.frontier;
        ctx.beginPath();ctx.arc(x,y,15+4*pulse,0,Math.PI*2);ctx.fill();ctx.globalAlpha=1;
        ctx.fillStyle=colors.paper;ctx.strokeStyle=colors.frontier;ctx.lineWidth=2;
        ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();ctx.stroke();
      }
      if(reveal>0){
        ctx.globalAlpha=reveal;ctx.fillStyle=colors.result;ctx.beginPath();ctx.arc(x,y,(selected?8:6)*(.7+.3*reveal),0,Math.PI*2);ctx.fill();
        if(selected){ctx.strokeStyle=colors.result;ctx.lineWidth=1;ctx.beginPath();ctx.arc(x,y,12,0,Math.PI*2);ctx.stroke();}
        const number=this.resultRanks.get(id);
        if(number!==null&&number!==undefined){ctx.font='11px Arial';ctx.fillStyle=colors.paper;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(number),x,y+.5);}
      }else if(id===this.selected){
        ctx.globalAlpha=1;ctx.fillStyle=colors.result;ctx.beginPath();ctx.arc(x,y,3.5,0,Math.PI*2);ctx.fill();
        ctx.strokeStyle=colors.result;ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.stroke();
      }
      if(id===this.hover){ctx.globalAlpha=1;ctx.strokeStyle=colors.result;ctx.lineWidth=1;ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.stroke();}
    }
    ctx.globalAlpha=1;
    const labels=done||this.reveal>.65?[this.selected,...this.results.filter(id=>id!==this.selected).slice(0,this.width<500?1:3)]:[this.inspected,activeId];
    const boxes=[],shown=new Set(context.ids);
    for(const id of [...new Set(labels)]){
      if(id===null||id===undefined||!shown.has(id))continue;
      const p=this.point(id),rank=rankOf.get(id)??-1,isActive=id===activeId;
      const number=rank>=0?this.resultRanks.get(id):null;
      const prefix=isActive?(beat.kind==='entry'?'Start · ':beat.kind==='descent'?'Look closer · ':''):(number!==null&&number!==undefined?String(number)+'. ':'');
      if(!this.tracks[id])continue;
      let title=prefix+this.tracks[id].title;if(title.length>35)title=title.slice(0,33)+'…';
      ctx.font=(isActive||id===this.selected?'600 ':'')+'12px Arial';const textWidth=ctx.measureText(title).width;
      const placed=placeLabel(p,textWidth,boxes,[...new Set([this.selected,activeId,...this.results])].filter(i=>Number.isInteger(i)).map(i=>this.point(i)),this.width,this.height,this.insets),{x,y}=placed;boxes.push(placed.rect);
      ctx.fillStyle=colors.paper;ctx.globalAlpha=.96;ctx.fillRect(x-5,y-13,textWidth+10,23);ctx.globalAlpha=1;
      ctx.textAlign='left';ctx.textBaseline='alphabetic';ctx.fillStyle=isActive?colors.frontier:id===this.selected?colors.ink:colors.muted;ctx.fillText(title,x,y+3);
      ctx.strokeStyle=colors.rule;ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(p[0]+7,p[1]);ctx.lineTo(x-4,y);ctx.stroke();
    }
    if(lod)this.drawRegions(ctx,g,detail,boxes,[...new Set([this.selected,activeId,...this.results])].filter(i=>Number.isInteger(i)&&this.positions[i]).map(i=>this.point(i)),!done);
    ctx.restore();this._drawingGeometry=null;this._drawingPoints=null;
  }
  destroy(){this.pause();this.abort.abort();this.resize.disconnect();this.cancelWarm();this.tileField?.destroy();if(this._redraw)cancelAnimationFrame(this._redraw);}
}
