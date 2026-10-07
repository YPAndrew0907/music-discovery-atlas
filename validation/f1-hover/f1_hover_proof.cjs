// F1 of the scale UI code review, proved on a real v2 server (fma2000-v2, release format 2.1, the serving list)
// through the real page in headless Chromium. After validation/f1-hover/tools/hover_proof.cjs in the workspace, with
// a HAR 1.2 request log per run, JPEG screenshots, five sweep lines per view, and page reads at the 60 s mark while
// the others keep mousing.
//
//   sweeps   One fresh page per sweep. The pointer starts off the map, then crosses 500 px of it in 1 s at 60 Hz
//            (61 pointer events) along a horizontal line through the map's centre (offset -120, -60, 0, +60 or
//            +120 px), at the overview and zoomed in with eight clicks on + (1.2^8 = 4.3x), then rests 750 ms where
//            it stopped. Counted: the /collection/tracks?rows= requests the page sends while the pointer moves and
//            including the rest, and (from the tooltip) the dots of unread served rows the pointer crossed.
//   mousing  USERS pages move the pointer over the map at once, continuously: zigzag lines 16 px apart at 800 px/s
//            with a 300 ms pause at every turn, through a sequence of views (the overview, 4.3x zoom, drags to other
//            areas). At 60 s a visitor who never hovers (a browser context of its own) browses, goes to the next page
//            and looks up "love" while the others keep mousing; then the mousing stops and user 1 does the same.
//            The mousing pages share one browser context: uvicorn runs with limit_concurrency=16 (server/hosting.py)
//            and each context keeps up to six keep-alive connections through the TLS terminator, so a third context
//            meets 503s on module loads (validation/f1-hover/contexts-probe.json in the workspace).
//
// The HAR lists every /collection/, /v1/, /audio/ and configuration request, and any other request that failed or
// answered >= 400; static modules that answered 200 are left out. Headers are reduced to those that matter here.
// Usage: NODE_PATH=<tooling>/node_modules node f1_hover_proof.cjs <https-origin> <label> <out-dir> sweeps|mousing [users] [server-pid]
'use strict';
const {chromium} = require('playwright');
const fs = require('fs');
const {execFileSync} = require('child_process');
const [ORIGIN, LABEL, OUT, MODE, USERS = '1', PID = ''] = process.argv.slice(2);
if (!ORIGIN || !LABEL || !OUT || !['sweeps', 'mousing'].includes(MODE)) throw new Error('usage: f1_hover_proof.cjs <origin> <label> <out> sweeps|mousing [users] [pid]');
fs.mkdirSync(OUT, {recursive: true});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const cpu = () => { if (!PID) return null; const text = execFileSync('ps', ['-o', 'cputime=', '-p', PID]).toString().trim();
  return text.replace('-', ':').split(':').map(Number).reduce((a, b) => a * 60 + b, 0); };
const load = () => execFileSync('sysctl', ['-n', 'vm.loadavg']).toString().trim();
const KEPT_HEADERS = ['content-type', 'content-length', 'retry-after', 'cache-control'];

class Har {
  constructor() { this.t0 = Date.now(); this.pages = []; this.entries = []; this.pending = new Set(); }
  attach(page, id, title) {
    this.pages.push({startedDateTime: new Date().toISOString(), id, title, pageTimings: {}});
    const started = new Map();
    page.on('request', r => started.set(r, Date.now()));
    const done = (r, failure) => {
      const t = started.get(r); if (t === undefined) return; started.delete(r);
      const work = this.entry(r, id, t, Date.now(), failure).catch(() => {});
      this.pending.add(work); work.finally(() => this.pending.delete(work));
    };
    page.on('requestfinished', r => done(r, null));
    page.on('requestfailed', r => done(r, r.failure()?.errorText ?? 'failed'));
  }
  async entry(r, pageref, t, end, failure) {
    const u = new URL(r.url()), resp = failure ? null : await r.response(), status = resp ? resp.status() : 0;
    const api = /^\/(collection|v1|audio)\//.test(u.pathname) || ['/serving.json', '/deployment-config.json', '/audio-delivery.json'].includes(u.pathname);
    if (!api && !failure && status < 400) return;
    const all = resp ? await resp.allHeaders() : {};
    const headers = KEPT_HEADERS.filter(k => k in all).map(name => ({name, value: all[name]}));
    const content = {size: all['content-length'] ? Number(all['content-length']) : -1, mimeType: all['content-type'] ?? ''};
    if (resp && status >= 400 && u.pathname.startsWith('/collection/')) content.text = (await resp.text()).slice(0, 200);
    const post = r.postData();
    this.entries.push({pageref, startedDateTime: new Date(t).toISOString(), time: end - t, _secondsFromStart: +((t - this.t0) / 1000).toFixed(3),
      request: {method: r.method(), url: r.url(), httpVersion: 'HTTP/1.1', cookies: [], headers: [],
        queryString: [...u.searchParams].map(([name, value]) => ({name, value})), headersSize: -1, bodySize: post ? Buffer.byteLength(post) : 0},
      response: {status, statusText: resp ? resp.statusText() : '', httpVersion: 'HTTP/1.1', cookies: [], headers, content,
        redirectURL: '', headersSize: -1, bodySize: -1, ...(failure ? {_error: failure} : {})},
      cache: {}, timings: {send: 0, wait: end - t, receive: 0}});
  }
  async write(path, comment) {
    await Promise.all([...this.pending]);
    this.entries.sort((a, b) => a._secondsFromStart - b._secondsFromStart);
    // One entry per line, so the log reads and diffs like a request log.
    const head = JSON.stringify({version: '1.2', creator: {name: 'f1_hover_proof.cjs (Playwright ' + require('playwright/package.json').version + ')', version: '1'},
      comment, pages: this.pages}).slice(0, -1);
    fs.writeFileSync(path, `{"log":${head},"entries":[\n${this.entries.map(e => JSON.stringify(e)).join(',\n')}\n]}}\n`);
  }
}

const newContext = browser => browser.newContext({viewport: {width: 1440, height: 900}, ignoreHTTPSErrors: true});
async function openPage(browser, har, name, shared = null) {
  const ctx = shared ?? await newContext(browser);
  const page = await ctx.newPage(), log = [], errors = [], failed = [];
  har.attach(page, name.replace(/\s+/g, '-'), name);
  page.on('request', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/collection/'))
    log.push({t: Date.now(), path: u.pathname, rows: u.searchParams.get('rows'), query: u.search, status: null, request: r}); });
  page.on('response', r => { const entry = log.find(e => e.request === r.request()); if (entry) entry.status = r.status();
    if (r.status() >= 400) failed.push(r.status() + ' ' + new URL(r.url()).pathname); });
  page.on('requestfailed', r => failed.push('failed ' + new URL(r.url()).pathname + ' ' + (r.failure()?.errorText ?? '')));
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(ORIGIN + '/search-studio/', {waitUntil: 'domcontentloaded'});
  try { await page.waitForSelector('body[data-ready="true"]', {timeout: 60000}); }
  catch (e) { throw new Error(`${name} never became ready; refused or failed: ${JSON.stringify(failed.slice(0, 12))}; errors: ${JSON.stringify(errors.slice(0, 3))}`); }
  await page.waitForFunction(() => document.querySelector('#open-engine').textContent === 'Server ready', null, {timeout: 60000}).catch(() => {});
  // Every change of the tooltip, with its text: '…' is a hovered served dot whose row the page has not read yet.
  await page.evaluate(() => { window.__tips = []; const tip = document.querySelector('#tooltip');
    new MutationObserver(() => window.__tips.push({t: Date.now(), text: tip.textContent, hidden: tip.hidden, left: tip.style.left, top: tip.style.top}))
      .observe(tip, {childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'hidden']}); });
  await page.mouse.move(5, 5);
  await sleep(400);
  return {name, ctx, page, log, errors, failed, close: () => (shared ? page.close() : ctx.close())};
}

// The map's drawing centre, as AudioMap.geometry() computes it from the canvas and its CSS insets.
async function mapGeometry(page) {
  const box = await page.locator('#map').boundingBox();
  const insets = await page.evaluate(() => { const s = getComputedStyle(document.querySelector('#map'));
    const read = (name, fallback) => { const v = parseFloat(s.getPropertyValue(name)); return Number.isFinite(v) ? v : fallback; };
    return {top: read('--map-inset-top', 70), bottom: read('--map-inset-bottom', 125)}; });
  const top = insets.top + 12, bottom = box.height - insets.bottom - 5;
  return {box, insets, cx: box.x + box.width / 2 - 20, cy: box.y + (top + bottom) / 2, top: box.y + insets.top, bottom: box.y + box.height - insets.bottom};
}

const rowReads = (p, from, to = Infinity) => p.log.filter(e => e.path === '/collection/tracks' && e.rows !== null && e.t >= from && e.t <= to);
const shot = async (page, name) => { const file = `${OUT}/${name}.jpg`; await page.screenshot({path: file, type: 'jpeg', quality: 70}); return file.slice(OUT.length + 1); };

async function sweeps() {
  const browser = await chromium.launch(), har = new Har(), results = [];
  try {
    for (const [view, clicks] of [['overview', 0], ['zoom 4.3x', 8]]) {
      for (const dy of [-120, -60, 0, 60, 120]) {
        const p = await openPage(browser, har, `sweep ${view} dy=${dy}`);
        await p.page.click('#overview');
        for (let i = 0; i < clicks; i++) await p.page.click('#zoom-in');
        await p.page.mouse.move(5, 5);
        await sleep(400);
        const g = await mapGeometry(p.page), y = g.cy + dy, x0 = g.cx - 250, density = await p.page.textContent('#map-density');
        const tipsBefore = await p.page.evaluate(() => window.__tips.length);
        const n = 60, t0 = Date.now(), moves = [];
        for (let i = 0; i <= n; i++) {
          const wait = t0 + i * 1000 / n - Date.now();
          if (wait > 0) await sleep(wait);
          await p.page.mouse.move(x0 + i * 500 / n, y);
          moves.push(Date.now());
        }
        const moved = Date.now();
        await sleep(750);
        const tips = (await p.page.evaluate(from => window.__tips.slice(from), tipsBefore)).filter(x => x.t <= moved);
        const crossedUnread = new Set(tips.filter(x => x.text === '…' && !x.hidden).map(x => x.left + ',' + x.top)).size;
        const gaps = moves.slice(1).map((t, i) => t - moves[i]);
        const screenshot = dy === 0 ? await shot(p.page, `sweep-${view.replace(/[^a-z0-9]+/gi, '-')}-dy0`) : null;
        const during = rowReads(p, t0, moved), all = rowReads(p, t0);
        results.push({view, dy, density, line: {x0: Math.round(x0), x1: Math.round(x0 + 500), y: Math.round(y)}, sweepMs: moved - t0,
          maxMoveGapMs: Math.max(...gaps), readsWhileMoving: during.length, readsIncludingRest: all.length,
          statuses: all.map(e => e.status), rows: all.map(e => e.rows), unreadServedDotsCrossed: crossedUnread, pageErrors: p.errors, screenshot});
        console.log(`${LABEL} ${view} dy=${dy}: ${all.length} rows= reads (${during.length} while moving) over ${moved - t0} ms; ` +
          `${crossedUnread} unread served dots crossed; max gap ${Math.max(...gaps)} ms`);
        await p.close();
      }
    }
  } finally { await browser.close(); }
  const sum = key => results.reduce((a, r) => a + r[key], 0);
  const report = {label: LABEL, origin: ORIGIN, mode: 'sweeps', loadavg: load(), sweeps: results,
    summary: {sweeps: results.length, readsIncludingRest: sum('readsIncludingRest'), readsWhileMoving: sum('readsWhileMoving'),
      maxReadsInOneSweep: Math.max(...results.map(r => r.readsIncludingRest)), perSweep: results.map(r => r.readsIncludingRest),
      unreadServedDotsCrossed: sum('unreadServedDotsCrossed'), perSweepCrossed: results.map(r => r.unreadServedDotsCrossed)}};
  fs.writeFileSync(`${OUT}/sweeps.json`, JSON.stringify(report, null, 1) + '\n');
  await har.write(`${OUT}/sweeps.har`, `${LABEL}: ten 1 s, 500 px pointer sweeps, one fresh page each (pageref)`);
  console.log(`${LABEL} SWEEPS ${JSON.stringify(report.summary)}`);
}

// One visitor's mousing over the map until stop() says so.
async function mouse(p, stop, seed) {
  const g = await mapGeometry(p.page);
  const views = [{overview: true}, {zoom: 8}, {drag: [260, 160]}, {drag: [-520, 0]}, {drag: [0, -320]}, {drag: [520, 0]}, {overview: true}, {zoom: 8}, {drag: [-260, 200]}];
  const left = g.box.x + 40, right = g.box.x + g.box.width - 80, span = g.bottom - g.top - 40;
  let v = 0, lines = 0, pauses = 0;
  while (!stop()) {
    const view = views[v % views.length];
    if (view.overview) await p.page.click('#overview');
    for (let i = 0; i < (view.zoom ?? 0); i++) await p.page.click('#zoom-in');
    if (view.drag) { await p.page.mouse.move(g.cx, g.cy); await p.page.mouse.down(); await p.page.mouse.move(g.cx + view.drag[0], g.cy + view.drag[1], {steps: 12}); await p.page.mouse.up(); }
    const viewEnd = Date.now() + 7000;
    let y = g.top + 20 + ((v * 97 + seed * 41) % Math.max(1, span - 130)), dir = 1;
    while (Date.now() < viewEnd && !stop() && y < g.bottom - 16) {
      let x = dir > 0 ? left : right, t = Date.now();
      const end = dir > 0 ? right : left;
      while ((dir > 0 ? x < end : x > end) && Date.now() < viewEnd && !stop()) {
        x += dir * 800 / 60;
        await p.page.mouse.move(x, y);
        t += 1000 / 60;
        const wait = t - Date.now();
        if (wait > 0) await sleep(wait);
      }
      lines++;
      await sleep(300); pauses++;  // the turn
      y += 16; dir = -dir;
    }
    v++;
  }
  await p.page.mouse.move(5, 5);
  return {views: v, lines, pauses};
}

// Browse, the next page and a name lookup, as a visitor clicks them; each step's /collection/tracks status and text.
async function pageReads(p, tag, t0) {
  const steps = [];
  const step = async (name, button, act) => {
    let status = null, url = null, error = null;
    const at = +((Date.now() - t0) / 1000).toFixed(1);
    if (await p.page.locator(button).isDisabled()) status = 'not sent: ' + button + ' is disabled';
    else {
      try {
        const [response] = await Promise.all([p.page.waitForResponse(r => { const u = new URL(r.url()); return u.pathname === '/collection/tracks' && !u.searchParams.has('rows'); }, {timeout: 20000}), act()]);
        status = response.status(); url = new URL(response.url()).search;
        if (status !== 200) error = (await response.json().catch(() => ({}))).error ?? null;
      } catch (e) { status = 'no response: ' + e.message.split('\n')[0]; }
    }
    await sleep(600);
    const ui = await p.page.evaluate(() => ({position: document.querySelector('#page-position').textContent, heading: document.querySelector('#results-heading').textContent,
      status: document.querySelector('#status').textContent, results: document.querySelectorAll('#results > li').length}));
    steps.push({step: name, atSecond: at, status, error, query: url, ui, screenshot: await shot(p.page, `${tag}-${name}`)});
  };
  await step('browse', '#browse-collection', () => p.page.click('#browse-collection', {timeout: 5000}));
  await step('next-page', '#next-page', () => p.page.click('#next-page', {timeout: 5000}));
  await step('lookup', '#search', async () => { await p.page.selectOption('#query-kind', 'lookup'); await p.page.fill('#query', 'love'); await p.page.click('#search', {timeout: 5000}); });
  return steps;
}

async function mousing() {
  const users = Number(USERS), browser = await chromium.launch(), har = new Har();
  try {
    const visitor = await openPage(browser, har, 'visitor');
    const pages = [], shared = await newContext(browser);
    for (let i = 0; i < users; i++) { pages.push(await openPage(browser, har, `user ${i + 1}`, shared)); await sleep(1000); }
    let stopping = false;
    const cpu0 = cpu(), t0 = Date.now(), load0 = load();
    const moving = Promise.all(pages.map((p, i) => mouse(p, () => stopping, i)));
    await sleep(60000);
    const cpu60 = cpu(), visitorSteps = await pageReads(visitor, 'visitor-at-60s', t0);
    stopping = true;
    const paths = await moving, t1 = Date.now(), cpu1 = cpu();
    const userSteps = await pageReads(pages[0], 'user1-after-mousing', t0);
    const perUser = pages.map((p, i) => { const reads = rowReads(p, t0, t1);
      return {user: i + 1, ...paths[i], rowsReads: reads.length, rowsReadsFirst60s: rowReads(p, t0, t0 + 60000).length,
        rowsStatuses: Object.fromEntries([...new Set(reads.map(e => e.status))].map(s => [s, reads.filter(e => e.status === s).length])),
        pageErrors: p.errors, refusedOrFailed: p.failed.slice(0, 20)}; });
    const all429 = [...pages, visitor].flatMap(p => p.log.filter(e => e.t >= t0 && e.status === 429).map(e => ({who: p.name, atSecond: +((e.t - t0) / 1000).toFixed(1), path: e.path, query: e.query.slice(0, 80)})));
    const firstRefusal = all429.length ? Math.min(...all429.map(e => e.atSecond)) : null;
    const report = {label: LABEL, origin: ORIGIN, mode: 'mousing', users, mousingSeconds: (t1 - t0) / 1000, loadavgBefore: load0, loadavgAfter: load(),
      serverCpuSecondsFirst60s: cpu0 === null ? null : +(cpu60 - cpu0).toFixed(2), serverCpuSecondsWhileMousing: cpu0 === null ? null : +(cpu1 - cpu0).toFixed(2),
      rowsReadsFirst60s: perUser.reduce((a, u) => a + u.rowsReadsFirst60s, 0), rowsReadsWhileMousing: perUser.reduce((a, u) => a + u.rowsReads, 0), perUser,
      visitorAt60s: {note: 'a separate browser context that never hovered; the others kept mousing until its three steps were done', steps: visitorSteps},
      user1AfterMousing: {note: 'the first mousing page, right after the mousing stopped', steps: userSteps},
      refusedRequests: all429.length, firstRefusalAtSecond: firstRefusal, refusedPageReads: all429.filter(e => !e.query.includes('rows=')),
      refusedRowsReads: all429.filter(e => e.query.includes('rows=')).length};
    fs.writeFileSync(`${OUT}/mousing-${users}user.json`, JSON.stringify(report, null, 1) + '\n');
    await har.write(`${OUT}/mousing-${users}user.har`, `${LABEL}: ${users} page(s) mousing for 60 s (shared context), the visitor's page reads at 60 s, then user 1's`);
    const fmt = steps => steps.map(s => `${s.step} ${s.status}${s.error ? ' (' + s.error + ')' : ''}`).join(', ');
    console.log(`${LABEL} MOUSING users=${users}: ${report.rowsReadsFirst60s} rows= reads in the first 60 s, ${report.rowsReadsWhileMousing} in ${report.mousingSeconds.toFixed(1)} s ` +
      `(${report.refusedRowsReads} refused, first refusal at ${firstRefusal} s); visitor at 60 s: ${fmt(visitorSteps)}; user 1 after: ${fmt(userSteps)}; server CPU ${report.serverCpuSecondsWhileMousing} s`);
  } finally { await browser.close(); }
}

(MODE === 'sweeps' ? sweeps() : mousing()).catch(e => { console.error(e); process.exit(1); });
