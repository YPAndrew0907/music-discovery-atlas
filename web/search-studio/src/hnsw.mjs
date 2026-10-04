/** Original educational implementation of the HNSW algorithm (Malkov & Yashunin).
 * Static cosine index. No deletions, concurrent insertion, or production claims.
 */
export class Heap {
  constructor(before){this.items=[];this.before=before;}
  get size(){return this.items.length;}
  peek(){return this.items[0];}
  push(x){const a=this.items;a.push(x);let i=a.length-1;while(i){const p=(i-1)>>1;if(!this.before(a[i],a[p]))break;[a[p],a[i]]=[a[i],a[p]];i=p;}}
  pop(){const a=this.items;if(!a.length)return undefined;const first=a[0],last=a.pop();if(a.length){a[0]=last;let i=0;while(true){let j=i,l=2*i+1,r=l+1;if(l<a.length&&this.before(a[l],a[j]))j=l;if(r<a.length&&this.before(a[r],a[j]))j=r;if(j===i)break;[a[i],a[j]]=[a[j],a[i]];i=j;}}return first;}
}
export const nearest=(a,b)=>a.distance-b.distance||a.id-b.id;
export function seededRandom(seed){let state=seed>>>0;return ()=>{state=(state+0x6D2B79F5)>>>0;let t=state;t=Math.imul(t^t>>>15,t|1);t^=t+Math.imul(t^t>>>7,t|61);return ((t^t>>>14)>>>0)/4294967296;};}
export function normalizeVector(v){let norm=0;for(const x of v){if(!Number.isFinite(x))throw new Error('Non-finite vector');norm+=x*x;}if(!norm)throw new Error('Zero vector');const out=new Float32Array(v.length);norm=Math.sqrt(norm);for(let i=0;i<v.length;i++)out[i]=v[i]/norm;return out;}
export function assertUnit(v,dimensions){if(!(v instanceof Float32Array)||v.length!==dimensions)throw new Error('Vector shape mismatch');let n=0;for(const x of v){if(!Number.isFinite(x))throw new Error('Non-finite vector');n+=x*x;}if(Math.abs(n-1)>1e-4)throw new Error('Expected a normalized vector');}
export function cosineDistance(a,b){let dot=0;for(let i=0;i<a.length;i++)dot+=a[i]*b[i];return Math.max(0,Math.min(2,1-dot));}
const cancelled=signal=>{if(signal?.aborted)throw new DOMException('Search cancelled','AbortError');};
export class HNSW {
  constructor({dimensions,M=12,efConstruction=100,seed=43,spaceId=null}={}){
    if(!Number.isInteger(dimensions)||dimensions<1||dimensions>4096||!Number.isInteger(M)||M<2||M>128||!Number.isInteger(efConstruction)||efConstruction<M)throw new Error('Invalid construction settings');
    if(typeof spaceId!=='string'||!spaceId.includes(':')||!spaceId.trim()||spaceId.length>256)throw new Error('A versioned feature-space identity is required');
    this.dimensions=dimensions;this.M=M;this.efConstruction=efConstruction;this.seed=seed;this.spaceId=spaceId;this.random=seededRandom(seed);this.vectors=[];this.links=[];this.entry=-1;this.maxLevel=-1;
  }
  get size(){return this.vectors.length;}
  #distance(q,id,ctx){if(ctx.cache.has(id))return ctx.cache.get(id);const d=cosineDistance(q,this.vectors[id]);ctx.cache.set(id,d);ctx.distanceEvaluations++;return d;}
  #record(ctx,event){if(!ctx.trace)return;if(ctx.events.length<ctx.traceLimit)ctx.events.push({sequence:ctx.events.length,...event,distanceEvaluations:ctx.distanceEvaluations});else ctx.truncated=true;}
  *#layer(q,entries,ef,level,ctx){
    const candidates=new Heap((a,b)=>nearest(a,b)<0),best=new Heap((a,b)=>nearest(a,b)>0),visited=new Set();
    for(const id of entries){const item={id,distance:this.#distance(q,id,ctx)};visited.add(id);candidates.push(item);best.push(item);}
    if(ctx.trace)this.#record(ctx,{type:'enter',level,entryIds:[...entries],ef});
    while(candidates.size){
      cancelled(ctx.signal);const current=candidates.pop();
      if(best.size>=ef&&nearest(current,best.peek())>0){if(ctx.trace)this.#record(ctx,{type:'stop',level,id:current.id,reason:'Frontier exceeds retained distance/ID ordering bound',bound:best.peek().distance});break;}
      const considered=[];ctx.expansions++;
      for(const id of this.links[current.id][level]){
        if(visited.has(id))continue;visited.add(id);const candidate={id,distance:this.#distance(q,id,ctx)},accepted=best.size<ef||nearest(candidate,best.peek())<0;let evicted=null;
        if(accepted){candidates.push(candidate);best.push(candidate);if(best.size>ef)evicted=best.pop().id;}
        if(ctx.trace)considered.push({...candidate,accepted,evicted,via:current.id});
      }
      if(ctx.trace)this.#record(ctx,{type:'expand',level,id:current.id,distance:current.distance,considered,frontier:candidates.items.slice().sort(nearest).slice(0,16),frontierCount:candidates.size,retained:best.items.slice().sort(nearest).slice(0,16),retainedCount:best.size,visitedOnLayer:visited.size,snapshotsLimitedTo:16});
      yield;
    }
    return best.items.slice().sort(nearest);
  }
  #runLayer(q,entries,ef,level,ctx){const iterator=this.#layer(q,entries,ef,level,ctx);let step;do{step=iterator.next();}while(!step.done);return step.value;}
  #select(q,candidates,max){const selected=[];for(const c of candidates.slice().sort(nearest)){if(selected.every(s=>cosineDistance(this.vectors[c.id],this.vectors[s.id])>=c.distance))selected.push(c);if(selected.length===max)break;}return selected.map(c=>c.id);}
  add(vector){
    assertUnit(vector,this.dimensions);const id=this.size,level=Math.min(24,Math.floor(-Math.log(Math.max(this.random(),Number.EPSILON))/Math.log(this.M)));
    this.vectors.push(new Float32Array(vector));this.links.push(Array.from({length:level+1},()=>[]));
    if(id===0){this.entry=0;this.maxLevel=level;return id;}
    const ctx={cache:new Map(),distanceEvaluations:0,expansions:0,trace:false};let entries=[this.entry];
    for(let layer=this.maxLevel;layer>level;layer--)entries=[this.#runLayer(vector,entries,1,layer,ctx)[0].id];
    for(let layer=Math.min(level,this.maxLevel);layer>=0;layer--){
      const found=this.#runLayer(vector,entries,this.efConstruction,layer,ctx),chosen=this.#select(vector,found,this.M);this.links[id][layer]=chosen;
      for(const neighbor of chosen){const adjacent=this.links[neighbor][layer];adjacent.push(id);const max=layer===0?this.M*2:this.M;if(adjacent.length>max)this.links[neighbor][layer]=this.#select(this.vectors[neighbor],adjacent.map(other=>({id:other,distance:cosineDistance(this.vectors[neighbor],this.vectors[other])})),max);}
      entries=found.map(x=>x.id);
    }
    if(level>this.maxLevel){this.entry=id;this.maxLevel=level;}return id;
  }
  *searchSteps(query,{k=10,ef=64,trace=false,traceLimit=2048,signal=null,spaceId=this.spaceId}={}){
    if(spaceId!==this.spaceId)throw new Error('Query and index spaces differ');assertUnit(query,this.dimensions);if(!Number.isInteger(k)||k<1||k>this.size||!Number.isInteger(ef)||ef<k||ef>this.size||!Number.isInteger(traceLimit)||traceLimit<0||traceLimit>10000)throw new Error('Invalid search budget');
    // Snapshot caller bytes so later UI state cannot change an in-flight query.
    const q=new Float32Array(query),ctx={cache:new Map(),distanceEvaluations:0,expansions:0,trace,traceLimit,events:[],truncated:false,signal};let entries=[this.entry];
    for(let level=this.maxLevel;level>=0;level--){
      const iterator=this.#layer(q,entries,level===0?ef:1,level,ctx);let step;do{step=iterator.next();if(!step.done)yield {expansions:ctx.expansions};}while(!step.done);const found=step.value;
      cancelled(ctx.signal);
      if(level===0){const results=found.slice(0,k);if(ctx.trace)this.#record(ctx,{type:'results',level:0,items:results});return {spaceId:this.spaceId,results,stats:{distanceEvaluations:ctx.distanceEvaluations,expansions:ctx.expansions,corpusCount:this.size,ef,k},trace:{schemaVersion:1,algorithm:'hnsw-static-cosine-v1',events:ctx.events,truncated:ctx.truncated,limit:traceLimit,finalResults:results}};}
      entries=[found[0].id];if(ctx.trace)this.#record(ctx,{type:'descend',level,nextLevel:level-1,entryId:entries[0]});
    }
  }
  search(query,options){const iterator=this.searchSteps(query,options);let step;do{step=iterator.next();}while(!step.done);return step.value;}
  async searchAsync(query,options={}){const iterator=this.searchSteps(query,options);let step,count=0;do{step=iterator.next();if(!step.done&&++count%8===0)await (options.yieldControl?.()??new Promise(r=>setTimeout(r,0)));}while(!step.done);return step.value;}
  export(){return {schemaVersion:1,algorithm:'hnsw-static-cosine-v1',dimensions:this.dimensions,M:this.M,efConstruction:this.efConstruction,seed:this.seed,spaceId:this.spaceId,entry:this.entry,maxLevel:this.maxLevel,count:this.size,links:this.links.map(ls=>ls.map(ns=>[...ns]))};}
  static load(graph,matrix){
    if(graph?.schemaVersion!==1||graph.algorithm!=='hnsw-static-cosine-v1'||!Number.isSafeInteger(graph.count)||graph.count<1||graph.count>1000000||!(matrix instanceof Float32Array)||matrix.length!==graph.count*graph.dimensions)throw new Error('Invalid static index payload');
    const index=new HNSW(graph);if(!Array.isArray(graph.links)||graph.links.length!==graph.count||!Number.isInteger(graph.maxLevel)||graph.maxLevel<0||graph.maxLevel>24||!Number.isInteger(graph.entry)||graph.entry<0||graph.entry>=graph.count)throw new Error('Invalid graph structure');
    for(let id=0;id<graph.count;id++){const layers=graph.links[id];if(!Array.isArray(layers)||!layers.length||layers.length>graph.maxLevel+1)throw new Error('Invalid layer structure');for(let l=0;l<layers.length;l++){const ns=layers[l];if(!Array.isArray(ns)||ns.length>(l===0?graph.M*2:graph.M)||new Set(ns).size!==ns.length||ns.some(n=>!Number.isInteger(n)||n<0||n>=graph.count||n===id||!graph.links[n]?.[l]))throw new Error('Invalid graph links');}const v=matrix.slice(id*graph.dimensions,(id+1)*graph.dimensions);assertUnit(v,graph.dimensions);index.vectors.push(v);index.links.push(layers.map(ns=>[...ns]));}
    if(index.links[graph.entry].length!==graph.maxLevel+1)throw new Error('Invalid entry level');index.entry=graph.entry;index.maxLevel=graph.maxLevel;return index;
  }
}
export function exactSearch(matrix,query,k,{excludeId=null}={}){const dims=query.length;if(matrix.length%dims)throw new Error('Matrix shape mismatch');assertUnit(query,dims);const heap=new Heap((a,b)=>nearest(a,b)>0);for(let id=0;id<matrix.length/dims;id++){if(id===excludeId)continue;const item={id,distance:cosineDistance(query,matrix.subarray(id*dims,(id+1)*dims))};if(heap.size<k)heap.push(item);else if(nearest(item,heap.peek())<0){heap.pop();heap.push(item);}}return heap.items.sort(nearest);}
