// Optional real-browser acceptance, using only pinned local assets and explicit
// HTTP fixtures. Requires an already-installed Playwright/browser; never installs.
// With an installed system browser: MUSIC_UI_CHROMIUM=/usr/bin/chromium node tests/browser_search_ui.mjs
// Compare baseline: MUSIC_UI_BASELINE=176a5b26b945b10154beae39860dee129066fca5 node tests/browser_search_ui.mjs
import {createRequire} from 'node:module';
import {readFile,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const {chromium}=createRequire(import.meta.url)('playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const baseline=process.env.MUSIC_UI_BASELINE;
if(baseline&&!/^[a-f0-9]{40}$/.test(baseline))throw new Error('Expected an exact baseline commit ID');
const output=process.env.MUSIC_UI_EVIDENCE_DIR??'/tmp/music-ui-browser-evidence';await mkdir(output,{recursive:true});
import {servesV1,v1Data,v1StudioRelease} from './v1_page_data.mjs';
// Once a v2 release is active this tree serves the v2 page data; the v1 page under test still gets the v1 data.
const v1Page=path=>servesV1?null:path==='search-studio/src/studio-release.mjs'?Buffer.from(v1StudioRelease):path.startsWith('search-studio/data/')?v1Data(path.slice('search-studio/data/'.length)):null;
const bytes=async path=>baseline?execFileSync('git',['show',`${baseline}:web/${path}`],{cwd:root,maxBuffer:20_000_000}):(await v1Page(path))??readFile(new URL('../web/'+path,import.meta.url));
const browser=await chromium.launch(process.env.MUSIC_UI_CHROMIUM?{executablePath:process.env.MUSIC_UI_CHROMIUM}:{});
async function serve(page){
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!=='https://music.test'){await route.abort();throw new Error('Off-origin browser request');}
      if(url.pathname==='/deployment-config.json')return route.fulfill({json:{schemaVersion:1,enabled:false}});
      if(url.pathname==='/audio-delivery.json')return route.fulfill({json:{enabled:false}});
      if(url.pathname.startsWith('/v1/')){await route.abort();throw new Error('Unexpected API transmission');}
      const path=url.pathname.replace(/^\//,'').replace(/\/$/,'/index.html');
      if(path.includes('..'))return route.abort();
      try{
        const body=await bytes(path),ext=path.split('.').at(-1);
        return route.fulfill({body,contentType:({html:'text/html',mjs:'text/javascript',css:'text/css',json:'application/json'})[ext]??'application/octet-stream'});
      }catch{return route.fulfill({status:404,body:'Not found'});}
    });
}
async function open(page,query=''){
    await page.goto('https://music.test/search-studio/'+query);
    await page.waitForSelector('body[data-ready="true"]');
    await page.waitForFunction(()=>document.querySelector('#open-engine').textContent==='Server unavailable');
}
try{
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}],['narrow',{width:320,height:740}]]){
    // List composition (?direction=list): the results-first layout keeps every assertion it had as the default.
    const context=await browser.newContext({viewport,reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await serve(page);await open(page,'?direction=list');
    await page.screenshot({path:`${output}/${baseline?'before':'after'}-list-${name}.png`,fullPage:true});
    if(!baseline){
      assert.equal(await page.evaluate(()=>document.body.dataset.direction),'list');
      assert.equal(await page.locator('#results > li').count(),12);
      assert.match(await page.locator('#result-scope').textContent(),/16 retrieved sound candidates/);
      await page.locator('#next-page').click();assert.equal(await page.locator('#results > li').count(),4);
      assert.match(await page.locator('#results .rank').first().textContent(),/13/);
      await page.locator('#browse-collection').click();assert.match(await page.locator('#result-scope').textContent(),/1,992 recordings in the collection/);
      // The page jump clamps out-of-range input in the page itself (no native validation bubble intercepts the submit).
      await page.locator('#page-number').fill('999');await page.locator('#page-number').press('Enter');
      assert.equal(await page.locator('#page-position').textContent(),'Page 166 of 166');assert.equal(await page.evaluate(()=>document.activeElement.id),'results-heading');
      await page.locator('#page-number').fill('0');await page.locator('#page-number').press('Enter');assert.equal(await page.locator('#page-position').textContent(),'Page 1 of 166');
      // The refinement panel starts collapsed on phones (like the map) and open on desktop.
      assert.equal(await page.locator('.refinement').getAttribute('open'),name==='desktop'?'':null);
      await page.locator('.refinement').evaluate(el=>{el.open=true;});
      await page.locator('#refine-text').fill('no such name 000000');await page.locator('#results-empty').waitFor({state:'visible'});
      await page.locator('#empty-browse').click();assert.equal(await page.locator('#results > li').count(),12);
      assert.equal(await page.evaluate(()=>document.activeElement.id),'results-heading');
      assert.equal(await page.getByRole('combobox',{name:'Search by',exact:true}).count(),1);
      await page.locator('#query-kind').selectOption('lookup');assert.match(await page.locator('#query').getAttribute('placeholder'),/title or artist/);
      const artist=await page.locator('#results .track-select > span').first().textContent();
      await page.locator('#query').fill(artist);await page.locator('#query').press('Enter');assert.ok(await page.locator('#results > li').count()>0);
      await page.locator('#results .track-details summary').first().click();await page.locator('#results [data-keep]').first().click();
      assert.equal(await page.locator('#results .track-details').first().getAttribute('open'),'');
      if(name!=='desktop')assert.equal(await page.locator('#map-panel').getAttribute('open'),null);
      await page.locator('#map-panel').evaluate(el=>{el.open=true;});await page.locator('#overview').click();
      assert.match(await page.locator('#map-density').textContent(),/zoom in for connections/);
      const scrollWidth=await page.evaluate(()=>document.documentElement.scrollWidth);assert.ok(scrollWidth<=viewport.width+1,'No horizontal page overflow');
      await page.locator('#map').focus();await page.keyboard.press('Home');await page.keyboard.press('ArrowRight');await page.keyboard.press('+');
      await page.screenshot({path:`${output}/after-list-${name}-refined.png`,fullPage:true});
    }
    assert.deepEqual(errors,[]);await context.close();
    if(!baseline)await atlasPass(name,viewport);
  }
}finally{await browser.close();}

// Atlas composition (the default): graph-first, map open on every width, compact list beside or below it.
async function atlasPass(name,viewport){
  const context=await browser.newContext({viewport,reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await serve(page);await open(page);
  await page.screenshot({path:`${output}/after-atlas-${name}.png`,fullPage:true});
  assert.equal(await page.evaluate(()=>document.body.dataset.direction),'atlas');assert.equal(await page.locator('h1').textContent(),'What does it sound like?');
  assert.equal(await page.locator('#map-panel').getAttribute('open'),'','the map is open on every width');
  assert.equal(await page.locator('.refinement').getAttribute('open'),null,'refinements start as one collapsed bar');
  const map=await page.locator('#map').boundingBox(),list=await page.locator('#results').boundingBox();
  if(name==='desktop'){assert.ok(map.width>=viewport.width*.5&&map.height>=400,'map stage takes the left of the first screen');assert.ok(map.x+map.width<=list.x,'list beside the map');}
  else{assert.ok(Math.abs(map.height-300)<=1,'300 px map strip on phones');assert.ok(map.y+map.height<=list.y,'map strip above the list');}
  const fold=await page.evaluate(()=>{const rows=[...document.querySelectorAll('#results > li')],clip=document.querySelector('.results-scroll').getBoundingClientRect();return{firstTop:Math.round(rows[0].getBoundingClientRect().top+scrollY),inFirstScreen:rows.filter(r=>{const b=r.getBoundingClientRect();return b.top<innerHeight-24&&b.top<clip.bottom-24;}).length,pageHeight:document.documentElement.scrollHeight};});
  if(name==='desktop'){assert.ok(fold.inFirstScreen>=5,`at least 5 rows in the first desktop screen (${fold.inFirstScreen})`);assert.ok(fold.pageHeight<=viewport.height+60,`about one screen tall (${fold.pageHeight})`);}
  if(name==='mobile')assert.ok(fold.firstTop<844,`first row inside the first 390×844 screen (${fold.firstTop})`);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth)<=viewport.width+1,'No horizontal page overflow');
  // Compact, honest scope: the visible line is short; the full sentence is the list's description.
  assert.equal(await page.locator('#result-scope').textContent(),'1–12 of 16 · searched 1,992');
  assert.match(await page.locator('#result-scope-detail').textContent(),/^Showing 1–12 of 16 from 16 retrieved sound candidates · searched 1,992 recordings\. Refinements apply to these candidates, not the full collection\.$/);
  assert.equal(await page.locator('#results').getAttribute('aria-describedby'),'result-scope-detail');assert.match(await page.locator('.refinement > summary').textContent(),/Refine these 16 candidates/);
  // Row actions only on the selected row; selecting another row moves them.
  assert.equal(await page.locator('#results [data-keep]:visible').count(),1);assert.equal(await page.locator('#results li.selected [data-keep]:visible').count(),1);
  await page.locator('#results .track-select').nth(2).click();assert.equal(await page.locator('#results li').nth(2).getAttribute('class').then(c=>/selected/.test(c)),true);
  assert.equal(await page.locator('#results [data-keep]:visible').count(),1);await page.locator('#results li.selected [data-keep]').click();assert.match(await page.locator('#shelf-count').textContent(),/^1$/);
  // 12 + 4 paging, First/Last and the jump reach every page; focus returns to the heading.
  await page.locator('#next-page-top').click();assert.equal(await page.locator('#results > li').count(),4);assert.match(await page.locator('#results .rank').first().textContent(),/13/);
  assert.equal(await page.locator('#result-scope').textContent(),'13–16 of 16 · searched 1,992');assert.equal(await page.evaluate(()=>document.activeElement.id),'results-heading');
  await page.locator('#browse-collection').click();assert.equal(await page.locator('#result-scope').textContent(),'1–12 of 1,992 recordings');
  await page.locator('#page-number').fill('999');await page.locator('#page-number').press('Enter');assert.equal(await page.locator('#page-position').textContent(),'Page 166 of 166');
  await page.locator('#first-page').click();assert.equal(await page.locator('#page-position').textContent(),'Page 1 of 166');await page.locator('#last-page').click();assert.equal(await page.locator('#page-position').textContent(),'Page 166 of 166');
  // The collapsed refinement bar opens and keeps its honest scope; More artists lives inside it.
  await page.locator('.refinement > summary').click();assert.equal(await page.locator('.refinement').getAttribute('open'),'');assert.ok(await page.locator('.refinement-controls #spread-results').isVisible());
  await page.locator('#refine-text').fill('no such name 000000');await page.locator('#results-empty').waitFor({state:'visible'});
  await page.locator('#empty-browse').click();assert.equal(await page.locator('#results > li').count(),12);
  await page.locator('#query-kind').selectOption('lookup');const artist=await page.locator('#results .track-select > span').first().textContent();
  await page.locator('#query').fill(artist);await page.locator('#query').press('Enter');assert.ok(await page.locator('#results > li').count()>0);assert.match(await page.locator('#result-scope').textContent(),/name matches$/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth)<=viewport.width+1,'No horizontal page overflow after refining');
  await page.screenshot({path:`${output}/after-atlas-${name}-refined.png`,fullPage:true});
  // The About dialog lists every composition; the List link loads the results-first layout without errors.
  await page.locator('#open-about').click();await page.locator('#about-dialog summary').last().click();
  assert.equal(await page.locator('#about-dialog nav a[aria-current="page"]').textContent(),'Atlas');assert.equal(await page.locator('#about-dialog nav a').count(),3);
  assert.deepEqual(errors,[]);await context.close();
  // The graph search animates on every viewport, phones included (motion allowed).
  const moving=await browser.newContext({viewport,reducedMotion:'no-preference'}),motion=await moving.newPage(),motionErrors=[];
  motion.on('pageerror',error=>motionErrors.push(error.message));await serve(motion);await open(motion);
  const trace=await motion.evaluate(async()=>{const $=s=>document.querySelector(s),widths=[],notes=new Set();document.querySelectorAll('#examples [data-example]')[1].click();const t0=performance.now();
    while(performance.now()-t0<8000){widths.push(parseFloat($('#trace-fill').style.width));notes.add($('#trace-note').textContent);if($('#trace-note').textContent==='Ready'&&performance.now()-t0>300)break;await new Promise(r=>requestAnimationFrame(r));}
    return{first:widths[1],max:Math.max(...widths),samples:widths.length,notes:[...notes]};});
  assert.ok(trace.first<50&&trace.max===100,`#trace-fill advances during the replay (${JSON.stringify(trace)})`);assert.ok(trace.notes.some(n=>['Starting','Exploring','Narrowing','Matches'].includes(n)),'trace phases shown');
  assert.deepEqual(motionErrors,[]);await moving.close();
}
console.log(`UI browser checks passed (Atlas default and ?direction=list); screenshots: ${output}. Server/audio are fixtures; no native inference or real playback is claimed.`);
