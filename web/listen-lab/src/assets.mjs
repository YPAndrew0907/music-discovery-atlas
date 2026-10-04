export const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
export async function verifiedFetch(pin,{onProgress=()=>{},signal,fetchImpl=fetch}={}) {
  const response=await fetchImpl(pin.url,{signal,credentials:'omit',referrerPolicy:'no-referrer'});
  if(!response.ok) throw new Error(`Download failed (${response.status}): ${pin.name}`);
  const length=response.headers.get('content-length'); if(length && Number(length)!==pin.bytes) throw new Error(`Unexpected download size: ${pin.name}`);
  if(!response.body) throw new Error('Streaming download unavailable');
  const buffer=new Uint8Array(pin.bytes); const reader=response.body.getReader(); let received=0;
  try {while(true){const {done,value}=await reader.read();if(done)break;if(received+value.length>buffer.length)throw new Error('Download exceeded its exact size');buffer.set(value,received);received+=value.length;onProgress(received,pin.bytes);}
    if(received!==pin.bytes)throw new Error(`Incomplete download: ${pin.name}`);
    if(await hash(buffer)!==pin.sha256)throw new Error(`Integrity check failed: ${pin.name}`);
    return buffer;
  }catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
}
