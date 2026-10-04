import * as ort from '../runtime/ort.wasm.min.mjs';
import {createEncoder} from './encoder.mjs';
import {verifiedFetch} from './assets.mjs';
import {RELEASE} from './release.mjs';
let encoder=null,busy=false;
self.onmessage=async({data:m})=>{if(!Number.isSafeInteger(m.generation)||!Number.isSafeInteger(m.requestId))return;
 const emit=(type,detail={})=>self.postMessage({type,generation:m.generation,requestId:m.requestId,...detail});
 if(busy){emit('error',{error:'Worker busy'});return;}busy=true;
 try{if(m.type==='prepare'){
  const started=performance.now();let downloaded=0;const total=RELEASE.textAssets.reduce((s,a)=>s+a.bytes,0);let model,tokenizer;
  for(const pin of RELEASE.textAssets){const bytes=await verifiedFetch(pin,{onProgress:(received)=>emit('progress',{stage:'download',file:pin.name,received:downloaded+received,total})});downloaded+=bytes.length;if(pin.name==='text_model_quantized.onnx')model=bytes;else tokenizer=JSON.parse(new TextDecoder().decode(bytes));}
  const downloadMs=performance.now()-started;emit('progress',{stage:'initializing',received:downloaded,total});const initStart=performance.now();
  encoder=await createEncoder(ort,model,tokenizer,{wasmPaths:new URL('../runtime/',import.meta.url).href});model=null;tokenizer=null;
  emit('ready',{downloadMs,initializationMs:performance.now()-initStart,prepareMs:performance.now()-started,runtime:RELEASE.queryProfile,encoderSpaceId:RELEASE.encoderSpaceId,workerMemoryObservable:!!performance.memory});
 }else if(m.type==='encode'){
  if(!encoder)throw new Error('Model not loaded');const start=performance.now();const encoded=await encoder.encode(m.text);emit('result',{encoded:{...encoded,spaceId:RELEASE.encoderSpaceId,mode:'experimental-q8-browser-wasm'},encodeMs:performance.now()-start,workerJsHeapBytes:performance.memory?.usedJSHeapSize??null});
 }else throw new Error('Unknown request');
 }catch(e){emit('error',{error:String(e?.message??e)});}finally{busy=false;}};
