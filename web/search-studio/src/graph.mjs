import {searchBeats,motionAt,beatEdges,indexConnections,interpolateView} from './search-motion.mjs';
export function placeLabel(point,textWidth,boxes,anchors,width,height){
  const candidates=[];
  for(const side of [1,-1])for(const dy of [-18,30,-46,58,-74,86]){
    const x=Math.max(8,Math.min(width-textWidth-14,side===1?point[0]+16:point[0]-textWidth-16));
    const y=Math.max(84,Math.min(height-142,point[1]+dy));
    const rect={x:x-5,y:y-13,w:textWidth+10,h:23};
    const overlap=boxes.filter(b=>rect.x<b.x+b.w&&rect.x+rect.w>b.x&&rect.y<b.y+b.h&&rect.y+rect.h>b.y).length;
    const covered=anchors.filter(([px,py])=>Math.hypot(px-Math.max(rect.x,Math.min(px,rect.x+rect.w)),py-Math.max(rect.y,Math.min(py,rect.y+rect.h)))<13).length;
    candidates.push({x,y,rect,score:covered*10000+overlap*1000+Math.abs(dy)+(side===-1?5:0)});
  }
  return candidates.sort((a,b)=>a.score-b.score)[0];
}
export class AudioMap {
  constructor(canvas,{positions,tracks,connections=[],colors,onSelect,onHover,onTrace}){this.canvas=canvas;this.ctx=canvas.getContext('2d');this.positions=positions;this.tracks=tracks;this.connections=connections;this.colors=colors;this.onSelect=onSelect;this.onHover=onHover;this.onTrace=onTrace;this.scale=1;this.offset=[0,0];this.focus='results';this.selected=0;this.inspected=null;this.results=[];this.events=[];this.cursor=0;this.elapsed=0;this.duration=1;this.beats=[];this.reveal=1;this.playing=false;this.raf=0;this.motionGeneration=0;this.camera=null;this.cameraTransition=null;this.hover=null;this.pointer=null;this.motion=matchMedia('(prefers-reduced-motion: reduce)');this.abort=new AbortController();const opt={signal:this.abort.signal};this.resize=new ResizeObserver(()=>{this.camera=null;this.cameraTransition=null;this.draw();});this.resize.observe(canvas);canvas.addEventListener('pointerdown',e=>{if(this.pointer)return;this.cameraTransition=null;this.pointer={id:e.pointerId,x:e.clientX,y:e.clientY,offset:[...this.offset],moved:false};canvas.setPointerCapture(e.pointerId);},opt);canvas.addEventListener('pointermove',e=>{if(this.pointer){if(e.pointerId!==this.pointer.id)return;const dx=e.clientX-this.pointer.x,dy=e.clientY-this.pointer.y;if(Math.hypot(dx,dy)>5)this.pointer.moved=true;if(this.pointer.moved){this.offset=[this.pointer.offset[0]+dx,this.pointer.offset[1]+dy];this.draw();}return;}const id=this.hit(e);if(id!==this.hover){this.hover=id;this.onHover(id,e);this.draw();}},opt);canvas.addEventListener('pointerup',e=>{const p=this.pointer;if(!p||e.pointerId!==p.id)return;this.pointer=null;if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);if(!p.moved){const id=this.hit(e);if(id!==null)this.onSelect(id);}},opt);for(const event of ['pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>{this.pointer=null;},opt);canvas.addEventListener('pointerleave',()=>{this.hover=null;this.onHover(null);this.draw();},opt);canvas.addEventListener('keydown',e=>{if(e.key==='Home'){e.preventDefault();this.fit();}if(e.key==='+'||e.key==='='){e.preventDefault();this.zoom(1.2);}if(e.key==='-'){e.preventDefault();this.zoom(1/1.2);}if(e.key==='Escape'){this.hover=null;this.onHover(null);this.draw();}},opt);document.addEventListener('visibilitychange',()=>{if(document.hidden&&this.playing)this.pause();},opt);this.motion.addEventListener('change',()=>{if(this.motion.matches)this.finish();},opt);this.draw();}
  geometry(mode=this.focus){const ids=mode==='results'&&this.results.length?this.results:this.tracks.map((_,i)=>i);const points=ids.map(i=>this.positions[i]);const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);const top=82,bottom=this.height-130;return{cx:(x0+x1)/2,cy:(y0+y1)/2,screenX:this.width/2-20,screenY:(top+bottom)/2,size:Math.min((this.width-170)/Math.max(.25,x1-x0),(bottom-top-30)/Math.max(.25,y1-y0))};}
  point(id){const p=this.positions[id],g=this._drawingGeometry??this.camera??this.geometry();return[g.screenX+(p[0]-g.cx)*g.size*this.scale+this.offset[0],g.screenY+(p[1]-g.cy)*g.size*this.scale+this.offset[1]];}
  hit(e){const r=this.canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;let best=null,d=14;for(const id of [...this.results,...this.tracks.map((_,i)=>i)]){const p=this.point(id);if(p[1]<70||p[1]>this.height-125)continue;const n=Math.hypot(x-p[0],y-p[1]);if(n<d){d=n;best=id;}}return best;}
  setSearch(trace,rows,{animate=false}={}){
    const viewport=this.canvas.getBoundingClientRect();this.width=viewport.width;this.height=viewport.height;
    const base=this.width&&this.results.length?this.camera??this.geometry():null;
    const previous=base?{...base,size:base.size*this.scale,screenX:base.screenX+this.offset[0],screenY:base.screenY+this.offset[1]}:null;
    this.pause();this.events=trace?.events??[];this.results=rows.map(r=>r.row);
    this.beats=searchBeats(this.events);this.duration=this.beats.reduce((sum,b)=>sum+b.duration,0)||1;
    this.cursor=this.events.length;this.elapsed=this.duration;this.reveal=1;
    this.selected=rows[0]?.row??null;this.inspected=null;
    this.focus='results';this.scale=1;this.offset=[0,0];this.camera=this.searchView();this.cameraTransition=null;
    if(animate&&this.events.length&&!this.motion.matches){
      this.prepareCamera(previous);this.elapsed=0;this.cursor=0;this.reveal=0;this.replay();
    }else{this.draw();this.notify();}
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
  select(id,{explicit=false}={}){this.selected=id;if(explicit)this.inspected=id;this.draw();}
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
  fit(mode='results'){this.camera=null;this.cameraTransition=null;this.focus=mode;this.scale=1;this.offset=[0,0];this.draw();}
  zoom(f){this.cameraTransition=null;this.scale=Math.max(.6,Math.min(4,this.scale*f));this.draw();}
  draw(){
    const rect=this.canvas.getBoundingClientRect();this.width=rect.width;this.height=rect.height;
    if(!this.width||!this.height)return;
    const dpr=Math.min(devicePixelRatio||1,2);this.canvas.width=Math.round(this.width*dpr);this.canvas.height=Math.round(this.height*dpr);
    const ctx=this.ctx,colors=this.colors;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,this.width,this.height);
    this._drawingGeometry=this.camera??this.geometry();
    ctx.save();ctx.beginPath();ctx.rect(0,70,this.width,this.height-195);ctx.clip();
    const beat=motionAt(this.beats,this.elapsed),done=this.elapsed>=this.duration;
    const event=beat?this.events[beat.eventIndex]:null;
    const visited=new Set();
    for(const e of this.events.slice(0,this.cursor)){
      if(e.type==='enter')for(const id of e.entryIds??[])visited.add(id);
      if(e.type==='expand'){visited.add(e.id);for(const c of e.considered)visited.add(c.id);}
    }
    const frontier=new Set(event?.frontier?.map(x=>x.id)??[]);
    // All context lines are deduplicated stored index links, never proximity guesses.
    // Offscreen links and subpixel segments are omitted only as level-of-detail.
    const overviewSize=this.geometry('all').size;
    for(const edge of this.connections){
      const p=this.point(edge.from),q=this.point(edge.to);
      if((p[0]<0&&q[0]<0)||(p[0]>this.width&&q[0]>this.width)||(p[1]<70&&q[1]<70)||(p[1]>this.height-125&&q[1]>this.height-125)||Math.hypot(p[0]-q[0],p[1]-q[1])<2)continue;
      const examined=visited.has(edge.from)&&visited.has(edge.to);
      ctx.strokeStyle=examined&&!done?colors.visited:colors.node;
      const detail=Math.min(2,(this._drawingGeometry.size*this.scale)/overviewSize);
      ctx.globalAlpha=done?.11+detail*.035:examined?.4:edge.level>0?.33:.2+detail*.045;ctx.lineWidth=edge.level>0?1:.7;
      ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(...q);ctx.stroke();
    }
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
    ctx.globalAlpha=1;
    for(let id=0;id<this.tracks.length;id++){
      const [x,y]=this.point(id),rank=this.results.indexOf(id),explicit=id===this.inspected;
      const reveal=rank<0?0:done||explicit?1:Math.max(0,Math.min(1,(this.reveal-rank/Math.max(1,this.results.length)*.45)/.55));
      const selected=id===this.selected&&reveal>0,active=id===activeId;
      ctx.globalAlpha=visited.has(id)?(done?.5:.85):.55;ctx.fillStyle=visited.has(id)?colors.visited:colors.node;
      ctx.beginPath();ctx.arc(x,y,2.6,0,Math.PI*2);ctx.fill();
      if(!done&&frontier.has(id)){
        ctx.globalAlpha=.85;ctx.strokeStyle=colors.frontier;ctx.lineWidth=1.3;
        ctx.beginPath();ctx.arc(x,y,4.5,0,Math.PI*2);ctx.stroke();
      }
      if(active){
        const pulse=Math.sin(Math.PI*(beat?.progress??0));ctx.globalAlpha=.12+.12*pulse;ctx.fillStyle=colors.frontier;
        ctx.beginPath();ctx.arc(x,y,15+4*pulse,0,Math.PI*2);ctx.fill();ctx.globalAlpha=1;
        ctx.fillStyle=colors.paper;ctx.strokeStyle=colors.frontier;ctx.lineWidth=2;
        ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();ctx.stroke();
      }
      if(reveal>0){
        ctx.globalAlpha=reveal;ctx.fillStyle=colors.result;ctx.beginPath();ctx.arc(x,y,(selected?8:6)*(.7+.3*reveal),0,Math.PI*2);ctx.fill();
        if(selected){ctx.strokeStyle=colors.result;ctx.lineWidth=1;ctx.beginPath();ctx.arc(x,y,12,0,Math.PI*2);ctx.stroke();}
        ctx.font='11px Arial';ctx.fillStyle=colors.paper;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(rank+1),x,y+.5);
      }
      if(id===this.hover){ctx.globalAlpha=1;ctx.strokeStyle=colors.result;ctx.lineWidth=1;ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.stroke();}
    }
    ctx.globalAlpha=1;
    const labels=done||this.reveal>.65?[this.selected,...this.results.filter(id=>id!==this.selected).slice(0,this.width<500?1:3)]:[this.inspected,activeId];
    const boxes=[];
    for(const id of [...new Set(labels)]){
      if(id===null||id===undefined)continue;
      const p=this.point(id),rank=this.results.indexOf(id),isActive=id===activeId;
      const prefix=isActive?(beat.kind==='entry'?'Start · ':beat.kind==='descent'?'Look closer · ':''):(rank>=0?String(rank+1)+'. ':'');
      let title=prefix+this.tracks[id].title;if(title.length>35)title=title.slice(0,33)+'…';
      ctx.font=(isActive||id===this.selected?'600 ':'')+'12px Arial';const textWidth=ctx.measureText(title).width;
      const placed=placeLabel(p,textWidth,boxes,[...new Set([this.selected,activeId,...this.results])].filter(i=>Number.isInteger(i)).map(i=>this.point(i)),this.width,this.height),{x,y}=placed;boxes.push(placed.rect);
      ctx.fillStyle=colors.paper;ctx.globalAlpha=.96;ctx.fillRect(x-5,y-13,textWidth+10,23);ctx.globalAlpha=1;
      ctx.textAlign='left';ctx.textBaseline='alphabetic';ctx.fillStyle=isActive?colors.frontier:id===this.selected?colors.ink:colors.muted;ctx.fillText(title,x,y+3);
      ctx.strokeStyle=colors.rule;ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(p[0]+7,p[1]);ctx.lineTo(x-4,y);ctx.stroke();
    }
    ctx.restore();this._drawingGeometry=null;
  }
  destroy(){this.pause();this.abort.abort();this.resize.disconnect();}
}
