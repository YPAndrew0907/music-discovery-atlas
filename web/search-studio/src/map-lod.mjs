// Level of detail for the Atlas map at 20K–200K recordings (release format v2). The overview is the
// pinned density sample with region labels; zooming in streams point tiles for the viewport from
// /collection/tiles, and stored index links are drawn only around the recording in focus. Display
// only: nothing here changes a search result, a ranking, a stored link or a recording's position.
export const TILE_SCREEN_PX=256;   // a level is chosen so each tile spans at most this many CSS pixels
export const TILE_RASTER_PX=256;   // each tile is rasterised once into a square of this many pixels
export const TILE_MIN_LEVEL=2;     // above this level a tile sample is denser than the overview sample
export const TILE_CONCURRENCY=3,TILE_CACHE=256,TILE_RASTERS=96,TILE_CANDIDATES=4096,TILE_SETTLE_MS=90,TILE_RASTERS_PER_FRAME=2;
const TILE_BITS=16;

export function tileLevel(domain,pixelsPerUnit,maxLevel){
  const z=Math.ceil(Math.log2(Math.max(1e-12,domain[2]*pixelsPerUnit/TILE_SCREEN_PX)));
  return Math.max(0,Math.min(maxLevel,z));
}
// Tiles of level z that meet the layout rectangle `view`, nearest the view's centre first.
export function tilesInView(domain,z,view){
  const n=2**z,s=domain[2]/n,[x0,y0]=domain;
  if(view.x1<x0||view.y1<y0||view.x0>x0+domain[2]||view.y0>y0+domain[2])return[];
  const cell=(v,o)=>Math.max(0,Math.min(n-1,Math.floor((v-o)/s)));
  const ax=cell(view.x0,x0),bx=cell(view.x1,x0),ay=cell(view.y0,y0),by=cell(view.y1,y0),list=[];
  const cx=(view.x0+view.x1)/2,cy=(view.y0+view.y1)/2;
  for(let x=ax;x<=bx;x++)for(let y=ay;y<=by;y++)list.push([x,y,Math.hypot(x0+(x+.5)*s-cx,y0+(y+.5)*s-cy)]);
  return list.sort((a,b)=>a[2]-b[2]).map(([x,y])=>[x,y]);
}
// The server's Morton quantisation (collection_v2.TileIndex), so a child of a complete tile is exactly
// the tile the server would send.
export function quantize(value,origin,side){
  const top=2**TILE_BITS-1;return Math.max(0,Math.min(top,Math.floor((value-origin)*(2**TILE_BITS/side))));
}
// Labels fade in and out over about ±15% of a level's zoom range.
export function regionAlpha(detail,from,to){
  const ramp=(v,a,b)=>{const t=Math.max(0,Math.min(1,(v-a)/(b-a)));return t*t*(3-2*t);};
  const l=Math.log2(Math.max(1e-9,detail));
  const rise=from>0?ramp(l,Math.log2(from)-.2,Math.log2(from)+.2):1,fall=1-ramp(l,Math.log2(to)-.2,Math.log2(to)+.2);
  return Math.min(rise,fall);
}

export class TileField{
  constructor({domain,cap,maxLevel,fetchTile,onChange=()=>{},createLayer=null,color='#999',schedule=(fn,ms)=>setTimeout(fn,ms),cancel=id=>clearTimeout(id),now=()=>Date.now()}){
    Object.assign(this,{domain,cap,maxLevel,fetchTile,onChange,createLayer,color,schedule,cancel,now});
    this.tiles=new Map();this.inflight=new Map();this.wanted=null;this.timer=0;this.frame=0;this.rasterBudget=TILE_RASTERS_PER_FRAME;this.rasters=0;
    this.stats={requested:0,loaded:0,synthesized:0,failed:0,aborted:0,rasterised:0};
  }
  key(z,x,y){return z+'/'+x+'/'+y;}
  rect(z,x,y){const s=this.domain[2]/2**z;return{x0:this.domain[0]+x*s,y0:this.domain[1]+y*s,s};}
  ready(z,x,y){const t=this.tiles.get(this.key(z,x,y));return t?.state==='ready'?t:null;}
  // A ready tile, or one made locally from a complete ancestor (every row is known; no request needed).
  resolve(z,x,y){
    const own=this.ready(z,x,y);if(own){own.used=this.frame;return own;}
    for(let d=1;d<=z;d++){
      const a=this.ready(z-d,x>>d,y>>d);if(!a)continue;
      if(!a.complete)return null;
      const keep=[];
      for(let i=0;i<a.rows.length;i++){
        const qx=quantize(a.xy[2*i],this.domain[0],this.domain[2])>>(TILE_BITS-z),qy=quantize(a.xy[2*i+1],this.domain[1],this.domain[2])>>(TILE_BITS-z);
        if(qx===x&&qy===y)keep.push(i);
      }
      const t={z,x,y,state:'ready',complete:true,total:keep.length,rows:Uint32Array.from(keep,i=>a.rows[i]),
        xy:Float32Array.from(keep.flatMap(i=>[a.xy[2*i],a.xy[2*i+1]])),weights:null,raster:null,used:this.frame,local:true};
      this.tiles.set(this.key(z,x,y),t);this.stats.synthesized++;this.evict();return t;
    }
    return null;
  }
  // What to draw for tile (z,x,y): its own raster, or the matching part of an ancestor's raster, or null
  // (the overview cloud shows there). At most TILE_RASTERS_PER_FRAME rasters are built per frame.
  source(z,x,y){
    for(let d=0;d<=Math.min(z,3);d++){
      const t=d?this.ready(z-d,x>>d,y>>d):this.resolve(z,x,y);
      if(!t)continue;
      const canvas=this.raster(t);if(!canvas)continue;
      const part=TILE_RASTER_PX/2**d;
      return{canvas,sx:(x-((x>>d)<<d))*part,sy:(y-((y>>d)<<d))*part,size:part,tile:t};
    }
    return null;
  }
  raster(t){
    t.used=this.frame;
    if(t.raster)return t.raster;
    if(!this.createLayer||this.rasterBudget<=0){if(this.createLayer)this.redrawSoon();return null;}
    const canvas=this.createLayer(TILE_RASTER_PX,TILE_RASTER_PX),ctx=canvas?.getContext?.('2d');if(!ctx)return null;
    this.rasterBudget--;const {x0,y0,s}=this.rect(t.z,t.x,t.y),k=TILE_RASTER_PX/s;
    ctx.clearRect(0,0,TILE_RASTER_PX,TILE_RASTER_PX);ctx.fillStyle=this.color;
    // As the overview cloud: one soft dot per row, or the alpha of w overlapping dots for a sample row.
    for(let i=0;i<t.rows.length;i++){ctx.globalAlpha=t.weights?1-Math.pow(.7,t.weights[i]):.3;ctx.beginPath();ctx.arc((t.xy[2*i]-x0)*k,(t.xy[2*i+1]-y0)*k,1.5,0,Math.PI*2);ctx.fill();}
    ctx.globalAlpha=1;t.raster=canvas;this.rasters++;this.stats.rasterised++;this.evictRasters();return canvas;
  }
  beginFrame(){this.frame++;this.rasterBudget=TILE_RASTERS_PER_FRAME;}
  redrawSoon(){if(!this.redrawPending){this.redrawPending=true;this.schedule(()=>{this.redrawPending=false;this.onChange();},16);}}
  // Rows of the tiles in view, round-robin across tiles so the selectable points spread over the view.
  candidates(z,list,limit=TILE_CANDIDATES){
    const sources=[],seen=new Set();
    for(const [x,y] of list){const t=this.resolve(z,x,y)??this.ancestor(z,x,y);if(t&&!seen.has(t)){seen.add(t);sources.push(t);}}
    const rows=[];
    for(let i=0;rows.length<limit;i++){let any=false;for(const t of sources)if(i<t.rows.length){any=true;rows.push(t.rows[i]);if(rows.length>=limit)break;}if(!any)break;}
    return rows;
  }
  ancestor(z,x,y){for(let d=1;d<=Math.min(z,3);d++){const t=this.ready(z-d,x>>d,y>>d);if(t)return t;}return null;}
  // The latest viewport's tiles. Requests start after the view has been still for TILE_SETTLE_MS (or at once
  // for a prefetch); tiles no longer wanted are aborted; complete ancestors make requests unnecessary.
  want(z,list,{immediate=false}={}){
    if(z<TILE_MIN_LEVEL)list=[];
    const keys=list.map(([x,y])=>this.key(z,x,y)).join(',');
    if(this.wanted?.keys===keys&&!immediate)return;
    this.wanted={z,list,keys};this.cancel(this.timer);
    if(immediate)this.pump();else this.timer=this.schedule(()=>this.pump(),TILE_SETTLE_MS);
  }
  pump(){
    const w=this.wanted;if(!w)return;
    const want=new Set(w.list.map(([x,y])=>this.key(w.z,x,y)));
    for(const [key,controller] of this.inflight)if(!want.has(key)){controller.abort();this.inflight.delete(key);this.tiles.delete(key);this.stats.aborted++;}
    for(const [x,y] of w.list){
      if(this.inflight.size>=TILE_CONCURRENCY)break;
      const key=this.key(w.z,x,y),known=this.tiles.get(key);
      if(known&&(known.state==='ready'||known.state==='loading'||(known.state==='failed'&&this.now()<known.retryAt)))continue;
      if(this.resolve(w.z,x,y))continue;
      this.load(w.z,x,y);
    }
  }
  load(z,x,y){
    const key=this.key(z,x,y),controller=new AbortController();
    this.inflight.set(key,controller);this.tiles.set(key,{z,x,y,state:'loading'});this.stats.requested++;
    this.fetchTile(z,x,y,{signal:controller.signal}).then(tile=>{
      if(this.inflight.get(key)!==controller)return;
      this.inflight.delete(key);this.tiles.set(key,{...tile,state:'ready',raster:null,used:this.frame});this.stats.loaded++;this.evict();this.onChange();this.pump();
    },error=>{
      if(this.inflight.get(key)!==controller)return;
      this.inflight.delete(key);
      // A refused (429) or failed tile is retried later; until then the overview or an ancestor shows there.
      this.tiles.set(key,{z,x,y,state:'failed',retryAt:this.now()+(error?.status===429?1500:10000)});this.stats.failed++;
      this.schedule(()=>this.pump(),error?.status===429?1600:10100);
    });
  }
  evict(){
    if(this.tiles.size<=TILE_CACHE)return;
    const wanted=new Set(this.wanted?.list.map(([x,y])=>this.key(this.wanted.z,x,y))??[]);
    const old=[...this.tiles.entries()].filter(([k,t])=>t.state==='ready'&&!wanted.has(k)).sort((a,b)=>a[1].used-b[1].used);
    for(const [k,t] of old.slice(0,this.tiles.size-TILE_CACHE)){if(t.raster)this.rasters--;this.tiles.delete(k);}
  }
  evictRasters(){
    if(this.rasters<=TILE_RASTERS)return;
    const held=[...this.tiles.values()].filter(t=>t.raster).sort((a,b)=>a.used-b.used);
    for(const t of held.slice(0,this.rasters-TILE_RASTERS)){t.raster=null;this.rasters--;}
  }
  destroy(){this.cancel(this.timer);for(const c of this.inflight.values())c.abort();this.inflight.clear();this.wanted=null;}
}
