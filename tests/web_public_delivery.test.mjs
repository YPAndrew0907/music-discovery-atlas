import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {SERVER_CONFIG, validateDeploymentConfig, loadDeploymentConfig} from '../web/search-studio/src/server-config.mjs';
import {validateAudioDelivery, loadAudioDelivery, previewForTrack, UNAVAILABLE_PREVIEW} from '../web/search-studio/src/audio-delivery.mjs';
const origin='https://music.example';
const config={schemaVersion:1,enabled:true,mode:'anonymous-preview',origin,apiBase:'/v1/',recipient:'This site’s server',privacySummary:'Submitted descriptions are processed on this site.'};
import {v1Data} from './v1_page_data.mjs';
const catalog=JSON.parse(await v1Data('catalog.json'));
const manifest=JSON.parse(await v1Data('manifest.json'));
const expected={catalog,catalogSha256:manifest.files.catalog.sha256,pageOrigin:origin};
const track=catalog.tracks[0], other=catalog.tracks[1];
const row={id:track.id,available:true,url:'/audio/001382.mp3',bytes:track.audioBytes,sha256:track.audioSha256};
const delivery={schemaVersion:1,catalogId:catalog.id,catalogSha256:expected.catalogSha256,enabled:true,publicDeliveryVerified:true,tracks:[row]};

test('deployment defaults disabled and activates only explicit anonymous mode on this exact HTTPS origin',()=>{
  assert.equal(SERVER_CONFIG.enabled,false);
  assert.equal(validateDeploymentConfig({...config,enabled:false},origin),SERVER_CONFIG);
  assert.equal(validateDeploymentConfig(config,origin).endpoint,origin+'/v1/');
  for(const altered of [{enabled:'true'},{schemaVersion:2},{mode:'authenticated'},{mode:'anonymous'},
    {origin:'https://other.example'},{origin:origin+'/'},{apiBase:'https://other.example/v1/'},{apiBase:'/other/'},
    {apiBase:'/v1/'+'?key=secret'},{recipient:''},{privacySummary:null}]) {
    assert.throws(()=>validateDeploymentConfig({...config,...altered},origin));
  }
  assert.throws(()=>validateDeploymentConfig({...config,origin:'http://localhost'},'http://localhost'));
});

test('deployment config has one fixed same-origin route, no redirects/cache and no query payload',async()=>{
  const calls=[];
  const result=await loadDeploymentConfig({pageOrigin:origin,fetcher:async(url,options)=>{
    calls.push({url:String(url),options});return {ok:true,json:async()=>config};
  }});
  assert.equal(result.enabled,true);
  assert.equal(calls[0].url,origin+'/deployment-config.json');
  const {signal,...options}=calls[0].options;
  assert.deepEqual(options,{credentials:'same-origin',mode:'same-origin',cache:'no-store',redirect:'error'});
  // A hung configuration fetch is bounded by a timeout signal; it is not a redirect or cache allowance.
  assert.ok(signal instanceof AbortSignal&&!signal.aborted);
  await assert.rejects(loadDeploymentConfig({pageOrigin:origin,fetcher:async()=>({ok:false})}));
});

test('checked-in delivery manifest and catalog provenance cannot enable any preview',async()=>{
  const disabled=JSON.parse(await readFile(new URL('../audio-delivery.json',import.meta.url)));
  assert.equal(validateAudioDelivery(disabled,expected).size,0);
  assert.deepEqual(previewForTrack(track,new Map()),{available:false,url:null,label:UNAVAILABLE_PREVIEW});
  assert.ok(track.audio); assert.ok(track.sourceUrl);
});

test('only a verified row with matching catalog bytes/hash and explicit URL enables playback',()=>{
  const approved=validateAudioDelivery(delivery,expected);
  assert.equal(approved.size,1);
  assert.deepEqual(previewForTrack(track,approved),{available:true,url:origin+'/audio/001382.mp3',label:'Play'});
  assert.equal(previewForTrack(other,approved).available,false);
  assert.equal(validateAudioDelivery({...delivery,tracks:[{...row,url:origin+row.url}]},expected).size,1);
});

test('audio validation fails closed on inactive, unverified, malformed or substituted delivery',()=>{
  for(const change of [{enabled:false},{enabled:'true'},{publicDeliveryVerified:false},{schemaVersion:2},
    {catalogId:'other'},{catalogSha256:'other'},{tracks:null},{tracks:[row,row]},
    {tracks:[{...row,id:'fma:999999'}]},{tracks:[{...row,bytes:row.bytes+1}]},
    {tracks:[{...row,sha256:'0'.repeat(64)}]},{tracks:[{...row,available:false}]},
    {tracks:[{...row,available:'true'}]}]) assert.equal(validateAudioDelivery({...delivery,...change},expected).size,0);
  for(const url of [track.audio,track.sourceUrl,'https://external.example/audio/001382.mp3',
    '//external.example/audio/001382.mp3','/audio/001383.mp3','/audio/001382.mp3?token=secret',
    '/audio/001382.mp3#fragment','/audio/../audio/001382.mp3','/audio/%30%30%31%33%38%32.mp3',
    'https://user:pass@music.example/audio/001382.mp3','data:audio/mpeg;base64,AA==']) {
    assert.equal(validateAudioDelivery({...delivery,tracks:[{...row,url}]},expected).size,0,url);
  }
  assert.equal(validateAudioDelivery({...delivery,tracks:[{id:track.id,available:false,url:null}]},expected).size,0);
});

test('audio failures do not request alternative routes or inferred sources',async()=>{
  for(const reply of [async()=>{throw new Error('offline');},async()=>({ok:false}),async()=>({ok:true,json:async()=>{throw new Error('not JSON');}})]) {
    const calls=[];
    const result=await loadAudioDelivery({...expected,fetcher:async(url,options)=>{calls.push([String(url),options]);return reply();}});
    assert.equal(result.size,0);assert.equal(calls.length,1);assert.equal(calls[0][0],origin+'/audio-delivery.json');
    assert.equal(calls[0][1].redirect,'error');assert.equal(calls[0][1].cache,'no-store');assert.equal(calls[0][1].mode,'same-origin');
  }
});
