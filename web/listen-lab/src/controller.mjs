export class BrowserEncoder {
  constructor({factory=()=>new Worker(new URL('./worker.mjs',import.meta.url),{type:'module'}),status=()=>{}}={}){this.factory=factory;this.status=status;this.generation=0;this.id=0;this.worker=null;this.pending=null;this.state='unloaded';}
  set(state,detail={}){this.state=state;this.status({state,...detail});}
  prepare(){this.cancel('Starting model');const w=this.factory();this.worker=w;const generation=this.generation;
    w.onmessage=({data:d})=>{if(this.worker!==w||d.generation!==generation||d.requestId!==this.pending?.id)return;
      if(d.type==='progress'){this.status({state:this.state,...d});return;}
      const p=this.pending;this.pending=null;
      if(d.type==='error'){w.terminate();this.worker=null;this.generation++;this.set('error',{error:d.error});p.reject(new Error(d.error));return;}
      if(d.type!==p.expected){w.terminate();this.worker=null;this.generation++;this.set('error',{error:'Unexpected response'});p.reject(new Error('Unexpected response'));return;}
      this.set('ready',d);p.resolve(d);
    };w.onerror=()=>{if(this.worker===w&&this.generation===generation)this.fail('The browser model worker stopped unexpectedly');};w.onmessageerror=()=>{if(this.worker===w&&this.generation===generation)this.fail('The model response could not be read');};this.set('loading');return this.send('prepare',{},'ready');}
  send(type,detail,expected){return new Promise((resolve,reject)=>{const id=++this.id;this.pending={id,resolve,reject,expected};try{this.worker.postMessage({type,generation:this.generation,requestId:id,...detail});}catch(e){this.fail(e.message);}});}
  encode(text){if(this.state!=='ready'||this.pending)return Promise.reject(new Error('Load the model and wait for the current request'));if(typeof text!=='string'||!text.trim()||text.length>4096)return Promise.reject(new Error('Enter a description of 1–4096 characters'));this.set('encoding');return this.send('encode',{text},'result');}
  fail(error){const p=this.pending;this.pending=null;this.worker?.terminate();this.worker=null;this.generation++;this.set('error',{error});p?.reject(new Error(error));}
  cancel(reason='Model unloaded'){this.generation++;const p=this.pending;this.pending=null;this.worker?.terminate();this.worker=null;this.set('unloaded',{reason});p?.reject(new DOMException(reason,'AbortError'));}
}
