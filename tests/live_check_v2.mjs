// Post-deploy live check for the Atlas served from a release-format-v2 release. It keeps the 17
// production checks of validation/atlas/live_check_atlas.mjs (h1, Atlas default, map open, live
// server search with its animation, playback, an /audio/ 206, no page errors on desktop and
// mobile, ?direction=list) and adds the v2 contract: health, the server manifest's v2 binding,
// the local delivery summary, the collection routes, an exact audio range, the excluded row, no
// whole-catalog file, and a page that downloads only the pinned v2 data.
// Optional; never installs anything. Uses 2 live searches and 1 neighbor read, inside the
// anonymous limits (6 searches a minute, 30 per process-hour).
//   NODE_PATH=<dir with playwright> node tests/live_check_v2.mjs
//   MUSIC_UI_ORIGIN        default https://music-discovery-atlas.onrender.com
//   EXPECT_COUNT           default 2000
//   EXPECT_RELEASE         the selection's manifestSha256 (default: fma2000-v2, 806b19ed…)
//   OUT_DIR                default /tmp/music-live-check-v2 (live-check-v2.json and screenshots)
//   MUSIC_UI_INSECURE_TLS  1 only for a local TLS terminator with a self-signed certificate
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
const {chromium, request} = createRequire(import.meta.url)('playwright');
const ORIGIN = process.env.MUSIC_UI_ORIGIN ?? 'https://music-discovery-atlas.onrender.com';
const COUNT = Number(process.env.EXPECT_COUNT ?? 2000);
const RELEASE = process.env.EXPECT_RELEASE ?? '806b19ed9a6b2f5766f3c0a7316db20466dbff1537b3eb24792c11518266e16c';
const OUT = process.env.OUT_DIR ?? '/tmp/music-live-check-v2';
const insecure = process.env.MUSIC_UI_INSECURE_TLS === '1';
await mkdir(OUT, {recursive: true});
const report = {origin: ORIGIN, expectCount: COUNT, expectRelease: RELEASE, startedAt: new Date().toISOString(), checks: []};
const rec = (viewport, name, pass, value) => { report.checks.push({viewport, name, pass: !!pass, value}); console.log(`${pass ? 'PASS' : 'FAIL'} [${viewport}] ${name} ${JSON.stringify(value).slice(0, 200)}`); };
async function check(viewport, name, fn) { try { const {pass, value} = await fn(); rec(viewport, name, pass, value); } catch (e) { rec(viewport, name, false, {error: String(e?.message ?? e).slice(0, 300)}); } }

// ---- server contract (no browser) ----------------------------------------------------------------
const api = await request.newContext({baseURL: ORIGIN, ignoreHTTPSErrors: insecure});
try {
  await check('api', 'healthz ready', async () => { const r = await api.get('/healthz'); return {pass: r.status() === 200 && (await r.json()).ok === true, value: r.status()}; });
  await check('api', 'server manifest binds the selected v2 release', async () => {
    const m = await (await api.get('/v1/manifest')).json();
    const value = {catalogCount: m.catalogCount, releaseFormat: m.releaseFormat, bindingStatus: m.bindingStatus, corpusReleaseSha256: m.corpusReleaseSha256,
                   catalogId: m.catalogId, anonymous: m.publicPreview?.anonymous, audioEnabled: m.publicPreview?.audioEnabled, deploymentGeneration: m.deploymentGeneration};
    return {pass: m.catalogCount === COUNT && m.releaseFormat === 2 && m.bindingStatus === 'verified-corpus-release-v2' && m.corpusReleaseSha256 === RELEASE
      && m.publicPreview?.audioEnabled === true, value};
  });
  await check('api', 'delivery summary: every preview available, local, lazily verified', async () => {
    const d = await (await api.get('/audio-delivery.json')).json();
    return {pass: d.schemaVersion === 2 && d.kind === 'music-audio-delivery-v2' && d.enabled === true && d.publicDeliveryVerified === true && d.mode === 'local'
      && d.available === COUNT && d.total === COUNT && d.releaseSha256 === RELEASE, value: {mode: d.mode, available: d.available, total: d.total, scope: d.deliveryVerificationScope}};
  });
  let first;
  await check('api', 'collection pages come from the server', async () => {
    const page = await (await api.get('/collection/tracks?offset=0&limit=12')).json();
    const last = await (await api.get(`/collection/tracks?offset=${Math.floor((COUNT - 1) / 12) * 12}&limit=12`)).json();
    first = page.rows?.[0];
    return {pass: page.total === COUNT && page.rows.length === 12 && last.rows.length === COUNT - Math.floor((COUNT - 1) / 12) * 12, value: {total: page.total, first: first?.id, lastRows: last.rows.length}};
  });
  await check('api', 'neighbors are computed on the server', async () => {
    const r = await api.get('/collection/neighbors?row=0'); const n = await r.json();
    return {pass: r.status() === 200 && n.results?.length === 16 && n.releaseSha256 === RELEASE && n.trace?.events?.length > 0, value: {status: r.status(), results: n.results?.length, traceEvents: n.trace?.events?.length}};
  });
  await check('api', 'an audio range is served exactly (206)', async () => {
    const route = '/audio/' + first.id.slice(4).padStart(6, '0') + '.mp3';
    const r = await api.get(route, {headers: {Range: 'bytes=0-65535'}});
    const h = r.headers();
    return {pass: r.status() === 206 && h['content-range'] === `bytes 0-65535/${first.audioBytes}` && h['content-type'] === 'audio/mpeg' && (await r.body()).length === 65536,
      value: {route, status: r.status(), contentRange: h['content-range'], contentType: h['content-type']}};
  });
  await check('api', 'the excluded recording and whole-catalog files are not served', async () => {
    const excluded = (await api.get('/audio/030702.mp3')).status(), catalog = (await api.get('/search-studio/data/catalog.json')).status(), vectors = (await api.get('/search-studio/data/vectors.f32')).status();
    return {pass: excluded === 404 && catalog === 404 && vectors === 404, value: {excluded, catalog, vectors}};
  });
} finally { await api.dispose(); }

// ---- the page (the production smoke, unchanged, plus the v2 download check) ----------------------
const browser = await chromium.launch(process.env.MUSIC_UI_CHROMIUM ? {executablePath: process.env.MUSIC_UI_CHROMIUM} : {});
try {
  for (const [name, vp, q] of [['desktop', {width: 1440, height: 1000}, 'warm acoustic guitar with soft vocals'], ['mobile', {width: 390, height: 844}, 'ambient synth pad with rain']]) {
    const ctx = await browser.newContext({viewport: vp, ignoreHTTPSErrors: insecure}); const page = await ctx.newPage(); const net = []; const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/v1/') || u.pathname.startsWith('/audio/') || u.pathname.startsWith('/search-studio/data/') || u.pathname.startsWith('/collection/')) net.push({path: u.pathname, status: r.status()}); });
    await page.goto(ORIGIN + '/search-studio/', {waitUntil: 'domcontentloaded'}); await page.waitForSelector('body[data-ready="true"]', {timeout: 60000});
    const h1 = await page.textContent('h1'); rec(name, 'atlas h1', /What does it sound like/i.test(h1 || ''), h1);
    const dir = await page.evaluate(() => document.body.dataset.direction); rec(name, 'default direction atlas', dir === 'atlas', dir);
    const mapOpen = await page.evaluate(() => document.querySelector('#map-panel')?.open); rec(name, 'map open', mapOpen === true, mapOpen);
    const data = [...new Set(net.filter(x => x.path.startsWith('/search-studio/data/')).map(x => x.path))].sort();
    rec(name, 'page downloads only the pinned v2 data', JSON.stringify(data) === JSON.stringify(['/search-studio/data/examples.json', '/search-studio/data/layout.json', '/search-studio/data/manifest.json']), data);
    await page.waitForFunction(() => document.querySelector('#open-engine').textContent === 'Server ready', null, {timeout: 60000}).catch(() => {});
    await page.evaluate(() => { window.__f = 0; const tick = () => { window.__f++; requestAnimationFrame(tick); }; requestAnimationFrame(tick); });
    const t0 = Date.now(); await page.fill('#query', q); await page.click('#search');
    await page.waitForFunction(() => (document.querySelector('#results-source')?.textContent || '').startsWith('Live search · server') && document.querySelector('#results-region').getAttribute('aria-busy') === 'false', null, {timeout: 60000}).catch(() => {});
    const dt = (Date.now() - t0) / 1000, frames = await page.evaluate(() => window.__f);
    const rows = await page.evaluate(() => document.querySelectorAll('#results > li').length); const scope = await page.textContent('#result-scope');
    rec(name, 'live server results', rows >= 6 && (scope || '').includes(COUNT.toLocaleString('en-US')), {rows, scope: (scope || '').slice(0, 90), seconds: dt.toFixed(1), fps: (frames / dt).toFixed(0)});
    const trace = await page.evaluate(() => document.querySelector('#trace-fill')?.getAttribute('style') || ''); rec(name, 'trace/animation element present', !!(await page.$('#trace-fill')), trace.slice(0, 60));
    await page.screenshot({path: `${OUT}/${name}-results.png`});
    await page.locator('#results [data-play]').first().click().catch(() => {});
    await page.waitForFunction(() => { const a = document.querySelector('#audio'); return a && a.currentTime > 1.2 && !a.error; }, null, {timeout: 25000}).catch(() => {});
    const au = await page.evaluate(() => { const a = document.querySelector('#audio'); return {t: a.currentTime, rs: a.readyState, err: a.error ? a.error.code : null, src: a.getAttribute('src')}; });
    rec(name, 'playback advances', au.t > 1.2 && au.err === null, au);
    rec(name, 'audio 206', net.some(x => x.path.startsWith('/audio/') && x.status === 206), net.filter(x => x.path.startsWith('/audio/')).slice(0, 2));
    rec(name, 'no page errors', errors.length === 0, errors.slice(0, 2));
    await page.screenshot({path: `${OUT}/${name}-playing.png`}); await ctx.close(); await new Promise(r => setTimeout(r, 11000));
  }
  const ctx = await browser.newContext({viewport: {width: 1440, height: 1000}, ignoreHTTPSErrors: insecure}); const page = await ctx.newPage();
  await page.goto(ORIGIN + '/search-studio/?direction=list', {waitUntil: 'domcontentloaded'}); await page.waitForSelector('body[data-ready="true"]', {timeout: 60000});
  const dir = await page.evaluate(() => document.body.dataset.direction); rec('desktop', '?direction=list works', dir === 'list', dir);
  await page.screenshot({path: `${OUT}/desktop-list.png`}); await ctx.close();
} finally { await browser.close(); }
report.passed = report.checks.filter(c => c.pass).length; report.total = report.checks.length; report.finishedAt = new Date().toISOString();
await writeFile(`${OUT}/live-check-v2.json`, JSON.stringify(report, null, 1));
console.log(`LIVE_CHECK_V2 ${report.passed}/${report.total}`);
process.exitCode = report.passed === report.total ? 0 : 1;
