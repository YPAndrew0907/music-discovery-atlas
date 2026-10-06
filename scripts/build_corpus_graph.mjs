// Deterministic local index/recorded-example rebuild over verified real vectors.
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {HNSW,exactSearch} from '../web/search-studio/src/hnsw.mjs';
const [directory,graphId,examplesSource,countText]=process.argv.slice(2);
const count=Number(countText);
if(![500,1000,2000,5777].includes(count))throw new Error('Only explicitly reviewed500/1000/2000/5777 builds are supported');
if(!directory||!graphId||!examplesSource)throw new Error('Expected release directory, graph identity and existing public examples');
const hash=data=>createHash('sha256').update(data).digest('hex');
const bytes=await readFile(directory+'/vectors.f32');
const vectors=new Float32Array(bytes.buffer,bytes.byteOffset,bytes.byteLength/4);
const ids=JSON.parse(await readFile(directory+'/ids.json','utf8'));
if(ids.length!==count||vectors.length!==ids.length*512)throw new Error('Expected the reviewed '+count+'-by-512 release');
const index=new HNSW({dimensions:512,M:12,efConstruction:100,seed:43,spaceId:graphId});
for(let row=0;row<ids.length;row++)index.add(vectors.subarray(row*512,(row+1)*512));
const graphBytes=Buffer.from(JSON.stringify(index.export())+'\n');
await writeFile(directory+'/index.json',graphBytes);
const examples=JSON.parse(await readFile(examplesSource,'utf8'));
const orderedIdsSha256=hash(Buffer.from(JSON.stringify(ids))),vectorsSha256=hash(bytes),indexSha256=hash(graphBytes);
examples.graphId=graphId;examples.indexSha256=indexSha256;
for(const example of examples.examples){
  const q=new Float32Array(example.queryVector);
  if(hash(Buffer.from(q.buffer))!==example.queryVectorSha256)throw new Error('Recorded query bytes changed');
  const exact=exactSearch(vectors,q,8),found=index.search(q,{k:8,ef:32,trace:true,spaceId:graphId});
  Object.assign(example,{graphId,indexSha256,vectorsSha256,orderedIdsSha256,exactResults:exact,
    annResults:found.results,stats:found.stats,trace:found.trace,
    recallAt8:found.results.filter(r=>exact.some(e=>e.id===r.id)).length/8,
    displayResults:exact.map(r=>({...r,trackId:ids[r.id],score:1-r.distance})),
    interpretation:`Existing public recorded Q8 query vector, newly searched against the real ${count}-track release. Exact cosine supplies results; HNSW traversal explains its approximate shortlist. ANN agreement is not listener relevance.`});
}
await writeFile(directory+'/examples.json',JSON.stringify(examples,null,2)+'\n');
console.log(JSON.stringify({count:ids.length,indexSha256,recordedQueries:examples.examples.length,
  minimumRecordedRecall:Math.min(...examples.examples.map(e=>e.recallAt8))}));
