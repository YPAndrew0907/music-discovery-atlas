import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {MANIFEST_SHA,ARTIST_METADATA_SHA} from '../web/search-studio/src/studio-release.mjs';
import {RELEASE} from '../web/listen-lab/src/release.mjs';
import {PINS} from '../web/listen-lab/src/pins.mjs';
import {HNSW,exactSearch} from '../web/search-studio/src/hnsw.mjs';
import {BrowserEncoder} from '../web/listen-lab/src/controller.mjs';
const root=new URL('../web/',import.meta.url);
const read=path=>readFile(new URL(path,root));
const json=async path=>JSON.parse(await read(path));
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const manifest=await json('search-studio/data/manifest.json');
const catalog=await json('search-studio/data/catalog.json');
const examples=await json('search-studio/data/examples.json');

test('web manifest pins every collection asset, exact model identities and required browser runtime bytes',async()=>{
  assert.equal(hash(await read('search-studio/data/manifest.json')),MANIFEST_SHA);
  assert.equal(hash(await read('search-studio/data/artist-records.json')),ARTIST_METADATA_SHA);
  for(const pin of Object.values(manifest.files)){
    const bytes=await read('search-studio/data/'+pin.path);assert.equal(bytes.length,pin.bytes);assert.equal(hash(bytes),pin.sha256);
  }
  assert.equal(catalog.tracks.length,manifest.count);assert.equal(catalog.id,RELEASE.catalogId);
  assert.deepEqual(PINS.artifacts,RELEASE.textAssets);
  assert.equal(RELEASE.modelSpace.revision,'c28f2883575e590e04d3146ff0713c2448d691ba');
  for(const pin of RELEASE.textAssets)assert.ok(pin.url.startsWith('https://huggingface.co/Xenova/clap-htsat-unfused/resolve/'+RELEASE.modelSpace.revision+'/'));
  for(const pin of RELEASE.runtimeArtifacts){const bytes=await read('listen-lab/runtime/'+pin.name);assert.equal(bytes.length,pin.bytes);assert.equal(hash(bytes),pin.sha256);}
  assert.equal(hash(await read('listen-lab/src/tokenizer.mjs')),RELEASE.queryProfile.tokenizerImplementationSha256);
  assert.equal(hash(await read('listen-lab/src/encoder.mjs')),RELEASE.queryProfile.encoderImplementationSha256);
});

test('six public recorded examples reproduce their exact results and use the same active release graph',async()=>{
  assert.equal(examples.count,6);assert.equal(examples.examples.length,6);
  assert.equal(examples.graphId,manifest.graphId);assert.equal(examples.indexSha256,manifest.indexSha256);
  assert.deepEqual(examples.examples.map(e=>e.id),['dev-01','dev-02','dev-03','dev-04','dev-05','dev-06']);
  const bytes=await read('search-studio/data/vectors.f32');const vectors=new Float32Array(bytes.buffer,bytes.byteOffset,bytes.byteLength/4);
  const index=HNSW.load(await json('search-studio/data/index.json'),vectors);
  for(const item of examples.examples){
    assert.equal(item.mode,'recorded-example');assert.equal(item.queryVector.length,512);
    const q=new Float32Array(item.queryVector);assert.equal(hash(Buffer.from(q.buffer)),item.queryVectorSha256);
    const found=exactSearch(vectors,q,8);assert.deepEqual(found,item.exactResults);
    const traced=index.search(q,{k:8,ef:32,trace:true,spaceId:manifest.graphId});
    assert.deepEqual(traced.trace.finalResults,item.annResults);assert.ok(traced.trace.events.length>0);
    assert.match(item.source.description,/Public recorded/);assert.equal(item.source.path,undefined);
  }
  const artists=await json('search-studio/data/artist-records.json');
  assert.deepEqual(artists.rows.map(r=>r.trackId),catalog.tracks.map(r=>r.id));
  const layout=await json('search-studio/data/layout.json');assert.equal(layout.positions.length,manifest.count);assert.equal(layout.graphId,manifest.graphId);
});

test('public HTML exposes no audio source; result, inspector and shelf buttons share the verified delivery gate',async()=>{
  const html=(await read('search-studio/index.html')).toString();const app=(await read('search-studio/src/app.mjs')).toString();
  assert.match(html,/<audio id="audio" preload="none"><\/audio>/);
  assert.match(html,/<button id="player-toggle" disabled/);
  assert.equal([...app.matchAll(/data-play=/g)].length,1);
  assert.match(app,/playButton\(r\.row,\{compact:true\}\)/);
  assert.equal([...app.matchAll(/\$\{playButton\(row\)\}/g)].length,2);
  assert.deepEqual([...app.matchAll(/audio\.src\s*=\s*([^;]+);/g)].map(m=>m[1]),['approved.url']);
  assert.match(app,/async function play\(row\)\{const approved=preview\(row\);if\(!approved.available\)/);
  assert.match(app,/engineSelection='server'/);assert.match(app,/loadDeploymentConfig\(\{pageOrigin:location.origin\}\)/);
  assert.match(app,/examples.examples.map/);
  assert.doesNotMatch(html+app,/DS4300|Original course|listen-lab\/compare|listen-lab\/rights/);
});

test('required local HTML links and module dependencies resolve in the public web tree',async()=>{
  const all=[];
  async function visit(dir){for(const entry of await readdir(dir,{withFileTypes:true})){const url=new URL(entry.name+(entry.isDirectory()?'/':''),dir);if(entry.isDirectory())await visit(url);else all.push(url);}}
  await visit(root);
  assert.equal(all.some(url=>/\.(mp3|onnx)$/.test(url.pathname)),false);
  for(const file of all.filter(url=>/\.(mjs|html)$/.test(url.pathname)&&!url.pathname.includes('/runtime/'))){
    const text=(await readFile(file)).toString();
    const matches=file.pathname.endsWith('.mjs')?[...text.matchAll(/(?:from\s*|import\s*)['"]([^'"]+)['"]/g)]:[...text.matchAll(/(?:href|src)="([^"#?][^"]*)"/g)];
    for(const [,value] of matches){
      if(!value.startsWith('.')&&!value.startsWith('/'))continue;
      const url=new URL(value,file);url.hash='';url.search='';
      const target=value.startsWith('/')?new URL(value.slice(1),root):url;
      if(target.pathname.endsWith('/'))target.pathname+='index.html';
      assert.ok((await stat(target)).isFile(),`${file.pathname}: ${value}`);
    }
  }
});

test('optional browser encoder never creates a worker until opt-in and unload rejects stale generations',async()=>{
  const workers=[];const encoder=new BrowserEncoder({factory:()=>{const worker={postMessage(m){this.sent=m;},terminate(){this.terminated=true;}};workers.push(worker);return worker;}});
  assert.equal(workers.length,0);assert.equal(encoder.state,'unloaded');
  const first=encoder.prepare();const old=workers[0];const oldMessage=old.sent;encoder.cancel('Cancelled');
  await assert.rejects(first,{name:'AbortError'});assert.equal(old.terminated,true);
  const second=encoder.prepare();const active=workers[1];old.onmessage({data:{type:'ready',...oldMessage}});assert.equal(encoder.state,'loading');
  active.onmessage({data:{...active.sent,type:'ready'}});await second;assert.equal(encoder.state,'ready');
  encoder.cancel();assert.equal(active.terminated,true);assert.equal(encoder.state,'unloaded');
});

test('every served asset has an explicit Docker and Git export allowance',async()=>{
  const web=JSON.parse(await readFile(new URL('../web-manifest.json',import.meta.url)));
  const docker=new Set((await readFile(new URL('../.dockerignore',import.meta.url),'utf8')).split(/\r?\n/));
  const git=new Set((await readFile(new URL('../.gitignore',import.meta.url),'utf8')).split(/\r?\n/));
  for(const file of web.files){
    assert.ok(docker.has('!web/'+file.path),'Missing Docker build-context allowance: '+file.path);
    assert.ok(git.has('!/web/'+file.path),'Missing Git export allowance: '+file.path);
  }
  assert.ok(git.has('!/tests/web_public_app_handlers.test.mjs'));
});
