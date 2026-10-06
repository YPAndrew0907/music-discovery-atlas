import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {servesV1,servedManifest,v1Data,V1_MANIFEST_FIXTURE,V1_MANIFEST_SHA,V1_ARTIST_METADATA_SHA,v1StudioRelease} from './v1_page_data.mjs';
import {MANIFEST_SHA,ARTIST_METADATA_SHA} from '../web/search-studio/src/studio-release.mjs';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const web=name=>readFile(new URL('../web/search-studio/data/'+name,import.meta.url));
const release=name=>readFile(new URL('../corpus-releases/fma2000/'+name,import.meta.url));
const studio=()=>readFile(new URL('../web/search-studio/src/studio-release.mjs',import.meta.url),'utf8');

test('the v1 page fixture is the exact v1 page data in a v1 tree and its pins hold in either tree',async()=>{
  const manifest=JSON.parse(await v1Data('manifest.json'));
  assert.equal(manifest.format,undefined);assert.equal(manifest.count,2000);
  for(const pin of Object.values(manifest.files)){
    const bytes=await v1Data(pin.path);assert.equal(bytes.length,pin.bytes,pin.path);assert.equal(hash(bytes),pin.sha256,pin.path);
    assert.deepEqual(await release(pin.path),bytes,'corpus-releases/fma2000 holds the same '+pin.path);
  }
  assert.deepEqual(await release('artist-records.json'),await v1Data('artist-records.json'));
  if(servesV1){
    assert.deepEqual(await readFile(V1_MANIFEST_FIXTURE),await web('manifest.json'),'the fixture is the served v1 page manifest');
    assert.equal(V1_MANIFEST_SHA,MANIFEST_SHA);assert.equal(V1_ARTIST_METADATA_SHA,ARTIST_METADATA_SHA);
    assert.equal(v1StudioRelease,await studio());
  }else{
    assert.equal(servedManifest.format,2);assert.equal(servedManifest.catalogId,manifest.catalogId);
    assert.equal(hash(await web('manifest.json')),MANIFEST_SHA);
  }
});
