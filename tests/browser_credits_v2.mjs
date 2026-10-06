// Optional real-browser acceptance of the paged track credits (GET /collection/credits) on a running v2 server:
// one h1, labelled landmarks, heading order, labelled page jump, skip link and keyboard order, clamping, ID redirects
// that land on the recording's article, the old #fma-N forward of the small static credits page, and no horizontal
// page scroll, at desktop and mobile widths. Requires an installed Playwright and browser; never installs.
// Usage: node tests/browser_credits_v2.mjs ORIGIN [SMALL_PAGE_FILE]
//   ORIGIN: a v2 server (or only its /collection/ routes), e.g. http://127.0.0.1:8792
//   SMALL_PAGE_FILE: optional notices/track-attribution.html of a v2 package (build_web_v2.py), served at its path
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=createRequire(import.meta.url)('playwright');
const origin=String(process.argv[2]??'').replace(/\/$/,''),smallPage=process.argv[3]?await readFile(process.argv[3]):null;
if(!/^https?:\/\/[^/]+$/.test(origin))throw new Error('Usage: node tests/browser_credits_v2.mjs ORIGIN [SMALL_PAGE_FILE]');
const output=process.env.MUSIC_UI_EVIDENCE_DIR??'/tmp/music-credits-v2-browser-evidence';await mkdir(output,{recursive:true});
const browser=await chromium.launch(process.env.MUSIC_UI_CHROMIUM?{executablePath:process.env.MUSIC_UI_CHROMIUM}:{});
const report={origin,viewports:{}};
const position=page=>page.locator('nav').first().locator('p').textContent();
const pages=async page=>Number((await position(page)).match(/of ([\d,]+) ·/)[1].replace(/,/g,''));
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
try{
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport,ignoreHTTPSErrors:true}),page=await context.newPage(),errors=[],checks=[];
    page.on('pageerror',e=>errors.push(e.message));
    const ok=label=>checks.push(label);
    const first=await page.goto(origin+'/collection/credits');
    assert.equal(first.status(),200);assert.match(first.headers()['content-type'],/^text\/html/);ok('first page is HTML');
    const shape=await page.evaluate(()=>({lang:document.documentElement.lang,title:document.title,scripts:document.scripts.length,
      h1:document.querySelectorAll('h1').length,levels:[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h=>Number(h.tagName[1])),
      articles:[...document.querySelectorAll('main article')].map(a=>({id:a.id,h2:a.querySelectorAll('h2').length}))}));
    assert.equal(shape.lang,'en');assert.match(shape.title,/track credits, page 1 of /);assert.equal(shape.scripts,0);assert.equal(shape.h1,1);
    assert.ok(shape.levels.every((level,i)=>!i||level<=shape.levels[i-1]+1),'a heading level is skipped');
    assert.ok(shape.articles.length>0&&shape.articles.every(a=>/^[A-Za-z0-9._-]+$/.test(a.id)&&a.h2===1));ok('lang, title, one h1, heading order, one h2 per credit, no script');
    assert.equal(await page.getByRole('main').count(),1);
    assert.match(await page.getByRole('main').getAttribute('aria-label'),/^Credits, Page 1 of /);
    const navs=await page.getByRole('navigation').evaluateAll(list=>list.map(n=>n.getAttribute('aria-label')));
    assert.equal(navs.length,2);assert.equal(new Set(navs).size,2);assert.ok(navs.every(Boolean));ok('one labelled main, two distinctly labelled navigations');
    assert.equal(await page.getByRole('spinbutton',{name:'Go to page'}).count(),2);
    assert.equal(await page.getByRole('button',{name:'Go'}).count(),2);ok('both page jumps have an accessible name');
    const total=await pages(page);
    // Keyboard: the first Tab reaches the skip link, which is then on screen and moves focus to the credits.
    await page.keyboard.press('Tab');
    const skip=await page.evaluate(()=>{const e=document.activeElement,r=e.getBoundingClientRect();return{text:e.textContent,href:e.getAttribute('href'),x:r.x,y:r.y,w:r.width,h:r.height};});
    assert.equal(skip.text,'Skip to the credits');assert.ok(skip.x>=0&&skip.y>=0&&skip.w>0&&skip.h>0&&skip.x+skip.w<=viewport.width,'the focused skip link is not visible');
    await page.screenshot({path:`${output}/${name}-skip-link-focused.png`});
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'credits');assert.match(page.url(),/#credits$/);ok('Tab shows the skip link; Enter moves focus to the credits');
    await page.goto(origin+'/collection/credits');
    const order=[];for(let i=0;i<9;i++){await page.keyboard.press('Tab');order.push(await page.evaluate(()=>{const e=document.activeElement;return e.textContent.trim()||e.getAttribute('aria-label')||e.id||e.tagName;}));}
    assert.deepEqual(order.slice(0,5),['Skip to the credits','Back to the music map','Next page','Last page','credit-page-top']);
    assert.equal(order[5],'Go');ok('keyboard order: skip link, map link, page links, page jump');
    // Reflow: no horizontal page scroll on the first, a middle and the last page.
    for(const target of [1,Math.ceil(total/2),total]){
      await page.goto(origin+'/collection/credits?page='+target);
      assert.ok(await overflow(page)<=0,`page ${target} scrolls horizontally by ${await overflow(page)} px at ${viewport.width} px`);
    }
    ok(`no horizontal scroll on pages 1, ${Math.ceil(total/2)} and ${total}`);
    await page.screenshot({path:`${output}/${name}-last-page.png`});
    // Clamping and the page jump.
    await page.goto(origin+'/collection/credits?page=999999');assert.match(await position(page),new RegExp(`^Page ${total.toLocaleString('en-US')} of `));
    await page.goto(origin+'/collection/credits?page=0');assert.match(await position(page),/^Page 1 of /);ok('out-of-range pages clamp');
    await page.getByRole('spinbutton',{name:'Go to page'}).first().fill('3');await page.getByRole('spinbutton',{name:'Go to page'}).first().press('Enter');
    await page.waitForURL(/[?&]page=3$/);assert.match(await position(page),/^Page 3 of /);ok('the page jump submits');
    // An ID redirects to the page holding it and the browser lands on that recording's article.
    const target=(await page.locator('main article').nth(7).getAttribute('id'));
    const ident=target.replace('-',':');
    await page.goto(origin+'/collection/credits?id='+encodeURIComponent(ident));
    assert.ok(page.url().endsWith(`/collection/credits?page=3#${target}`),page.url());
    const box=await page.locator('#'+target).evaluate(e=>{const r=e.getBoundingClientRect();return{top:r.top,inner:innerHeight};});
    assert.ok(box.top>=-1&&box.top<box.inner*.5,`article top ${box.top}`);ok('an ID redirects to its page and its article is in view');
    await page.screenshot({path:`${output}/${name}-id-redirect.png`});
    const missing=await page.goto(origin+'/collection/credits?id=fma:999999999');
    assert.equal(missing.status(),404);assert.equal(await page.locator('h1').count(),1);ok('an unknown ID is a 404 page with one h1');
    if(smallPage){
      await page.route(origin+'/notices/track-attribution.html',route=>route.fulfill({body:smallPage,contentType:'text/html'}));
      await page.goto(origin+'/notices/track-attribution.html#'+target);
      await page.waitForURL(new RegExp(`/collection/credits\\?page=3#${target}$`));ok('the small credits page forwards an old #fma-N link');
    }
    assert.deepEqual(errors,[]);report.viewports[name]={viewport,pages:total,checks};
    await context.close();
  }
}finally{await browser.close();}
await writeFile(output+'/browser-credits-v2.json',JSON.stringify(report,null,1)+'\n');
console.log(`v2 credits browser checks passed at ${origin} (${Object.values(report.viewports).map(v=>v.checks.length).join(' + ')} checks, desktop and mobile)`);
