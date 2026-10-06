// Optional real-browser acceptance of the release-format-v2 page against an emulated v2 server
// (tests/v2_web_fixture.mjs), using only pinned local assets. Requires an installed Playwright and
// browser; never installs. Usage: node tests/browser_collection_v2.mjs (MUSIC_UI_CHROMIUM optional)
import {createRequire} from 'node:module';
import {readFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {catalog,count,manifestBytes,layoutBytes,examplesBytes,MANIFEST_SHA,identity,serverManifest,packet,vectors,v1Examples,
  collectionTracks,deliverySummary,manifestV2} from './v2_web_fixture.mjs';
const {chromium}=createRequire(import.meta.url)('playwright');
const output=process.env.MUSIC_UI_EVIDENCE_DIR??'/tmp/music-ui-v2-browser-evidence';await mkdir(output,{recursive:true});
const origin='https://music.test',pages=Math.ceil(count/12),available=new Set(catalog.tracks.map((_,r)=>r));
const studio=`export const MANIFEST_SHA='${MANIFEST_SHA}';\nexport const ARTIST_METADATA_SHA='${identity.catalogSha256}';\n`;
const json=body=>({status:200,contentType:'application/json',body:JSON.stringify(body)});
const browser=await chromium.launch(process.env.MUSIC_UI_CHROMIUM?{executablePath:process.env.MUSIC_UI_CHROMIUM}:{});
async function serve(page,log){
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());log.push(url.pathname+url.search);
    if(url.origin!==origin){await route.abort();throw new Error('Off-origin browser request');}
    if(url.pathname==='/deployment-config.json')return route.fulfill(json({schemaVersion:1,enabled:true,mode:'anonymous-preview',origin,apiBase:'/v1/',recipient:'This site’s server',privacySummary:'Fixture server.'}));
    if(url.pathname==='/audio-delivery.json')return route.fulfill(json(deliverySummary()));
    if(url.pathname==='/v1/manifest')return route.fulfill(json(serverManifest));
    if(url.pathname==='/v1/cancel')return route.fulfill(json({}));
    if(url.pathname==='/v1/search'){const body=JSON.parse(request.postData());return route.fulfill(json({...serverManifest,...body,...packet(new Float32Array(v1Examples.examples[3].queryVector)),timingMs:{serverCompute:1},tokenization:{truncated:false}}));}
    if(url.pathname==='/collection/tracks')return route.fulfill(json(collectionTracks(url.searchParams,available)));
    if(url.pathname==='/collection/neighbors'){const row=Number(url.searchParams.get('row'));return route.fulfill(json({...packet(vectors.slice(row*512,(row+1)*512),{exclude:row}),catalogId:manifestV2.catalogId,graphId:manifestV2.graphId,indexSha256:identity.indexSha256,releaseSha256:identity.releaseSha256,row}));}
    const data={'/search-studio/data/manifest.json':manifestBytes,'/search-studio/data/layout.json':layoutBytes,'/search-studio/data/examples.json':examplesBytes,'/search-studio/src/studio-release.mjs':Buffer.from(studio)}[url.pathname];
    if(data)return route.fulfill({body:data,contentType:url.pathname.endsWith('.mjs')?'text/javascript':'application/json'});
    if(url.pathname.startsWith('/search-studio/data/')||url.pathname.startsWith('/audio/')){await route.abort();throw new Error('The v2 page fetched '+url.pathname);}
    const path=url.pathname.replace(/^\//,'').replace(/\/$/,'/index.html');
    if(path.includes('..'))return route.abort();
    try{const body=await readFile(new URL('../web/'+path,import.meta.url)),ext=path.split('.').at(-1);
      return route.fulfill({body,contentType:({html:'text/html',mjs:'text/javascript',css:'text/css',json:'application/json'})[ext]??'application/octet-stream'});}
    catch{return route.fulfill({status:404,body:'Not found'});}
  });
}
const position=page=>page.locator('#page-position').textContent();
try{
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport,reducedMotion:'reduce'}),page=await context.newPage(),errors=[],log=[];
    page.on('pageerror',error=>errors.push(error.message));
    await serve(page,log);await page.goto(origin+'/search-studio/');
    await page.waitForSelector('body[data-ready="true"]');
    await page.waitForFunction(()=>document.querySelector('#open-engine').textContent==='Server ready');
    assert.deepEqual(log.filter(p=>p.startsWith('/search-studio/data/')).sort(),['/search-studio/data/examples.json','/search-studio/data/layout.json','/search-studio/data/manifest.json']);
    assert.equal(await page.locator('#catalog-count').textContent(),'1,992 recordings');
    const painted=await page.evaluate(()=>{const c=document.querySelector('#map'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<d.length;i+=4)if(d[i]>0)n++;return n/(d.length/4);});
    assert.ok(painted>0.01,'the density cloud is drawn');
    await page.screenshot({path:`${output}/v2-${name}-initial.png`});
    await page.locator('#browse-collection').click();await page.waitForFunction(p=>document.querySelector('#page-position').textContent===p,`Page 1 of ${pages}`);
    assert.equal(await page.locator('#results > li').count(),12);
    await page.locator('#last-page').click();await page.waitForFunction(p=>document.querySelector('#page-position').textContent===p,`Page ${pages} of ${pages}`);
    assert.equal(await page.locator('#results > li').count(),count-(pages-1)*12);
    await page.locator('#page-number').fill('999');await page.locator('#page-number').press('Enter');
    await page.waitForFunction(p=>document.querySelector('#page-position').textContent===p,`Page ${pages} of ${pages}`);
    await page.screenshot({path:`${output}/v2-${name}-browse-last.png`});
    await page.locator('#query-kind').selectOption('lookup');await page.locator('#query').fill('love');await page.locator('#search').click();
    await page.waitForFunction(()=>document.querySelector('#results-heading').textContent==='Title / artist matches');
    assert.ok(log.some(p=>p.startsWith('/collection/tracks')&&p.includes('q=love')));
    await page.locator('#query-kind').selectOption('description');await page.locator('#query').fill('a fixture description');await page.locator('#search').click();
    await page.waitForFunction(()=>document.querySelector('#engine-label').textContent==='Live · server'&&document.querySelectorAll('#results > li').length===12);
    await page.locator('#results [data-nearby]').first().click({force:true});
    await page.waitForFunction(()=>document.querySelector('#engine-label').textContent==='Audio neighbors');
    assert.ok(log.some(p=>p.startsWith('/collection/neighbors')));
    await page.screenshot({path:`${output}/v2-${name}-neighbors.png`});
    assert.deepEqual(errors,[]);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await context.close();
  }
  console.log('v2 UI browser checks passed (emulated server; density cloud, browse, paging, lookup, search, neighbors)');
}finally{await browser.close();}
