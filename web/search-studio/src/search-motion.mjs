// A condensed explanation of the recorded traversal, never a replacement trace.
// Each beat names an existing event; edges remain that event's accepted edges.
export function searchBeats(events){
  if(!events.length)return[];
  const chosen=new Map();
  const put=(index,kind,duration)=>{if(index>=0)chosen.set(index,{eventIndex:index,kind,duration});};
  const entry=events.findIndex(e=>e.type==='enter');
  put(entry,'entry',800);
  const expansions=events.map((e,i)=>({e,i})).filter(({e})=>e.type==='expand'&&e.considered?.some(x=>x.accepted));
  if(expansions.length){
    put(expansions[0].i,'branch',540);
    const lowest=Math.min(...expansions.map(x=>x.e.level));
    const local=expansions.filter(x=>x.e.level===lowest);
    const descent=events.findIndex((e,i)=>i>entry&&e.type==='enter'&&e.level===lowest);
    put(descent,'descent',440);
    for(const at of [0,Math.floor((local.length-1)/2),local.length-1])put(local[at].i,'branch',540);
  }
  // Exact display results may include items outside the approximate shortlist.
  // The completion beat reveals those actual supplied results without fake visits.
  put(events.length-1,'results',1600);
  return [...chosen.values()].sort((a,b)=>a.eventIndex-b.eventIndex);
}
export function motionAt(beats,elapsed){
  let start=0;
  for(let i=0;i<beats.length;i++){
    const beat=beats[i];
    if(elapsed<start+beat.duration||i===beats.length-1)return{...beat,index:i,progress:Math.max(0,Math.min(1,(elapsed-start)/beat.duration)),start};
    start+=beat.duration;
  }
  return null;
}
export function beatEdges(event){
  if(event?.type!=='expand')return[];
  return event.considered.filter(x=>x.accepted).sort((a,b)=>a.distance-b.distance||a.id-b.id).slice(0,3).map(x=>({from:event.id,to:x.id}));
}
export function indexConnections(links){
  const pairs=new Map(),count=links.length;
  links.forEach((layers,from)=>layers.forEach((neighbors,level)=>neighbors.forEach(to=>{
    if(!Number.isInteger(to)||to<0||to>=count||from===to)throw new Error('Invalid index connection');
    const a=Math.min(from,to),b=Math.max(from,to),key=a+':'+b;
    const existing=pairs.get(key);
    if(existing)existing.level=Math.max(existing.level,level);
    else pairs.set(key,{from:a,to:b,level});
  })));
  return [...pairs.values()];
}
export function interpolateView(from,to,t){
  if(t<=0)return {...from};if(t>=1)return {...to};
  const lerp=(a,b)=>a+(b-a)*t;
  const size=Math.exp(lerp(Math.log(from.size),Math.log(to.size)));
  const screenX=lerp(from.screenX,to.screenX),screenY=lerp(from.screenY,to.screenY);
  // The destination neighborhood's center follows one straight screen path.
  // Interpolating center and scale independently makes it swing off that path.
  const anchorX=lerp(from.screenX+(to.cx-from.cx)*from.size,to.screenX);
  const anchorY=lerp(from.screenY+(to.cy-from.cy)*from.size,to.screenY);
  return {cx:to.cx-(anchorX-screenX)/size,cy:to.cy-(anchorY-screenY)/size,screenX,screenY,size};
}
