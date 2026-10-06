// Optional real-browser acceptance of the release-format-v2 page against an emulated v2 server
// (tests/v2_web_fixture.mjs), using only pinned local assets. Requires an installed Playwright and
// browser; never installs. Usage: node tests/browser_collection_v2.mjs (MUSIC_UI_CHROMIUM optional)
import {createRequire} from 'node:module';
import {readFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {catalog,count,manifestBytes,layoutBytes,examplesBytes,MANIFEST_SHA,identity,serverManifest,packet,vectors,v1Examples,
  collectionTracks,deliverySummary,manifestV2,tiles,storedLinks,sampledFixture} from './v2_web_fixture.mjs';
const {chromium}=createRequire(import.meta.url)('playwright');
const output=process.env.MUSIC_UI_EVIDENCE_DIR??'/tmp/music-ui-v2-browser-evidence';await mkdir(output,{recursive:true});
const origin='https://music.test',pages=Math.ceil(count/12),available=new Set(catalog.tracks.map((_,r)=>r));
const studioFor=sha=>`export const MANIFEST_SHA='${sha}';\nexport const ARTIST_METADATA_SHA='${identity.catalogSha256}';\n`;
// Two pinned overviews: every row (as fma2000 and fma5777 are built), and a sample of every fourth row with the tile
// pyramid (as a 200K build is), so zooming in streams tiles from the emulated /collection/tiles.
const sampled=sampledFixture(catalog.tracks.map((_,r)=>r).filter(r=>r%4===0));
const variants={complete:{manifest:manifestBytes,layout:layoutBytes,studio:studioFor(MANIFEST_SHA)},
  sampled:{manifest:sampled.manifestBytes,layout:sampled.layoutBytes,studio:studioFor(sampled.MANIFEST_SHA)}};
const json=body=>({status:200,contentType:'application/json',body:JSON.stringify(body)});
const browser=await chromium.launch(process.env.MUSIC_UI_CHROMIUM?{executablePath:process.env.MUSIC_UI_CHROMIUM}:{});
let pageDelayMs=0;// a slow server page, to type into the page box while it is on its way
async function serve(page,log,variant=variants.complete){
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());log.push(url.pathname+url.search);
    if(url.origin!==origin){await route.abort();throw new Error('Off-origin browser request');}
    if(url.pathname==='/deployment-config.json')return route.fulfill(json({schemaVersion:1,enabled:true,mode:'anonymous-preview',origin,apiBase:'/v1/',recipient:'This site’s server',privacySummary:'Fixture server.'}));
    if(url.pathname==='/audio-delivery.json')return route.fulfill(json(deliverySummary()));
    if(url.pathname==='/v1/manifest')return route.fulfill(json(serverManifest));
    if(url.pathname==='/v1/cancel')return route.fulfill(json({}));
    if(url.pathname==='/v1/search'){const body=JSON.parse(request.postData());return route.fulfill(json({...serverManifest,...body,...packet(new Float32Array(v1Examples.examples[3].queryVector)),timingMs:{serverCompute:1},tokenization:{truncated:false}}));}
    if(url.pathname==='/collection/tracks'){if(pageDelayMs)await new Promise(r=>setTimeout(r,pageDelayMs));return route.fulfill(json(collectionTracks(url.searchParams,available)));}
    if(url.pathname==='/collection/links')return route.fulfill(json(storedLinks(Number(url.searchParams.get('row')))));
    if(url.pathname==='/collection/tiles'){const q=url.searchParams;return route.fulfill(json(tiles.tile(Number(q.get('z')),Number(q.get('x')),Number(q.get('y')))));}
    if(url.pathname==='/collection/neighbors'){const row=Number(url.searchParams.get('row'));return route.fulfill(json({...packet(vectors.slice(row*512,(row+1)*512),{exclude:row}),catalogId:manifestV2.catalogId,graphId:manifestV2.graphId,indexSha256:identity.indexSha256,releaseSha256:identity.releaseSha256,row}));}
    const data={'/search-studio/data/manifest.json':variant.manifest,'/search-studio/data/layout.json':variant.layout,'/search-studio/data/examples.json':examplesBytes,'/search-studio/src/studio-release.mjs':Buffer.from(variant.studio)}[url.pathname];
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
    // Stored links are drawn only around the selection, once zoomed in, and read from the server once.
    await page.locator('#overview').click();
    await page.waitForFunction(()=>/selectable · zoom in for links$/.test(document.querySelector('#map-density').textContent));
    for(let i=0;i<3;i++)await page.locator('#zoom-in').click();
    await page.waitForFunction(()=>/selectable · \d+ links of the selection$/.test(document.querySelector('#map-density').textContent));
    assert.equal(log.filter(p=>p.startsWith('/collection/links')).length,1);
    assert.equal(log.filter(p=>p.startsWith('/collection/tiles')).length,0,'every position is pinned: no tiles');
    await page.screenshot({path:`${output}/v2-${name}-zoomed-links.png`});
    await page.locator('#fit').click();
    await page.locator('#browse-collection').click();await page.waitForFunction(p=>document.querySelector('#page-position').textContent===p,`Page 1 of ${pages}`);
    assert.equal(await page.locator('#results > li').count(),12);
    await page.locator('#last-page').click();await page.waitForFunction(p=>document.querySelector('#page-position').textContent===p,`Page ${pages} of ${pages}`);
    assert.equal(await page.locator('#results > li').count(),count-(pages-1)*12);
    await page.locator('#page-number').fill('999');await page.locator('#page-number').press('Enter');
    await page.waitForFunction(p=>document.querySelector('#page-position').textContent===p,`Page ${pages} of ${pages}`);
    // The page-jump race: a server page that lands while someone types keeps the typed number.
    pageDelayMs=700;await page.locator('#first-page').click();
    await page.waitForFunction(()=>document.querySelector('#results-region').getAttribute('aria-busy')==='true');
    await page.locator('#page-number').fill('15');
    await page.waitForFunction(()=>document.querySelector('#page-position').textContent.startsWith('Page 1 of')&&document.querySelector('#results-region').getAttribute('aria-busy')==='false');
    assert.equal(await page.locator('#page-number').inputValue(),'15','a landing page replaced the typed number');
    pageDelayMs=0;await page.locator('#page-number').press('Enter');
    await page.waitForFunction(()=>document.querySelector('#page-position').textContent.startsWith('Page 15 of'));
    assert.equal(await page.locator('#page-number').inputValue(),'15');
    await page.locator('#last-page').click();await page.waitForFunction(p=>document.querySelector('#page-position').textContent===p,`Page ${pages} of ${pages}`);
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
  // Level of detail: a sampled overview streams tiles for the view once zoomed in; region labels and links as above.
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport,reducedMotion:'reduce'}),page=await context.newPage(),errors=[],log=[];
    page.on('pageerror',error=>errors.push(error.message));
    await serve(page,log,variants.sampled);await page.goto(origin+'/search-studio/');
    await page.waitForSelector('body[data-ready="true"]');
    const tileLog=()=>log.filter(p=>p.startsWith('/collection/tiles')),before=tileLog().length;
    await page.locator('#overview').click();await page.waitForTimeout(300);
    // Zoom in until the view needs tiles (at the overview already on a wide map, later on a phone), then let them load.
    for(let clicks=0;tileLog().length===before;clicks++){
      assert.ok(clicks<14,'tiles are requested once zoomed in');await page.locator('#zoom-in').click();await page.waitForTimeout(250);}
    await page.waitForTimeout(600);
    // Tiles start above the overview's level; the fixture's 1,992 rows make level-2 tiles complete, so deeper
    // views are cut locally from loaded tiles instead of requested.
    assert.ok(tileLog().every(p=>/z=([2-9]|1[0-2])&/.test(p)),'only detailed levels are requested');
    const settled=tileLog().length;for(let i=0;i<3;i++)await page.locator('#zoom-in').click();await page.waitForTimeout(600);
    assert.equal(tileLog().length,settled,name+': deeper views inside complete tiles need no request '+JSON.stringify(tileLog().slice(settled-4)));
    const painted=await page.evaluate(()=>{const c=document.querySelector('#map'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<d.length;i+=4)if(d[i]>0)n++;return n/(d.length/4);});
    assert.ok(painted>0.001,'the zoomed-in view is drawn from its tiles');
    await page.screenshot({path:`${output}/v2-${name}-tiles.png`});
    assert.deepEqual(errors,[]);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await context.close();
  }
  console.log('v2 UI browser checks passed (emulated server; density cloud, links around the selection, tiles, browse, paging, lookup, search, neighbors)');
}finally{await browser.close();}
