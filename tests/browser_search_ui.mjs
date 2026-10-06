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
const bytes=async path=>baseline?execFileSync('git',['show',`${baseline}:web/${path}`],{cwd:root,maxBuffer:20_000_000}):readFile(new URL('../web/'+path,import.meta.url));
const browser=await chromium.launch(process.env.MUSIC_UI_CHROMIUM?{executablePath:process.env.MUSIC_UI_CHROMIUM}:{});
try{
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}],['narrow',{width:320,height:740}]]){
    const context=await browser.newContext({viewport,reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
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
    await page.goto('https://music.test/search-studio/');
    await page.waitForSelector('body[data-ready="true"]');
    await page.waitForFunction(()=>document.querySelector('#open-engine').textContent==='Server unavailable');
    await page.screenshot({path:`${output}/${baseline?'before':'after'}-${name}.png`,fullPage:true});
    if(!baseline){
      assert.equal(await page.locator('#results > li').count(),12);
      assert.match(await page.locator('#result-scope').textContent(),/16 retrieved sound candidates/);
      await page.locator('#next-page').click();assert.equal(await page.locator('#results > li').count(),4);
      assert.match(await page.locator('#results .rank').first().textContent(),/13/);
      await page.locator('#browse-collection').click();assert.match(await page.locator('#result-scope').textContent(),/2,000 recordings in the collection/);
      // The page jump clamps out-of-range input in the page itself (no native validation bubble intercepts the submit).
      await page.locator('#page-number').fill('999');await page.locator('#page-number').press('Enter');
      assert.equal(await page.locator('#page-position').textContent(),'Page 167 of 167');assert.equal(await page.evaluate(()=>document.activeElement.id),'results-heading');
      await page.locator('#page-number').fill('0');await page.locator('#page-number').press('Enter');assert.equal(await page.locator('#page-position').textContent(),'Page 1 of 167');
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
      await page.screenshot({path:`${output}/after-${name}-refined.png`,fullPage:true});
    }
    assert.deepEqual(errors,[]);await context.close();
  }
}finally{await browser.close();}
console.log(`UI browser checks passed; screenshots: ${output}. Server/audio are fixtures; no native inference or real playback is claimed.`);
