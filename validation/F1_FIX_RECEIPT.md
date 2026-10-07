# F1 fix receipt: hovering the v2 map no longer spends the page budget

2026-10-07, US Eastern time. Nothing was pushed or deployed.

The fix (`8a12082`) and its documents (`df16a36`) were committed on `platform-v2` by an earlier agent. A rate limit stopped that agent before it wrote the proof. This receipt re-checks the fix and proves it on the real page and a real server, from scratch, between 12:42 and 13:02.

The finding comes from the code review of the scale UI: `personal_website_2026-10-05/receipts/relay/scale-ui-code-review/REPORT.md` in the workspace, F1 at line 42. `B` below is `/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05`.

## 1. Result

| | |
|---|---|
| F1 (High) | **Fixed** in `8a12082`, with its documents in `df16a36`. Hovering the map read each unread dot's row at once, and those reads spent the process-wide `/collection/tracks` budget (300 a minute) that browse, lookup and paging need |
| The fix | The page reads a hovered row only after the pointer has rested on its dot for 250 ms, and never asks for a row twice while a read is in flight. `rows=` reads have their own budget, 600 a minute |
| Real UI, ten 1 s 500 px sweeps | **Before** (`777a886`): 42 row reads, 41 of them while the pointer moved, up to 8 in one sweep. **After** (`df16a36`): 2, none while the pointer moved, at most 1 in a sweep. Both crossed the same 42 unread dots |
| 60 s of mousing, then browse, paging and lookup | **After: 200 for all three**, with one visitor mousing and with four at once (2 and 14 row reads a minute). **Before:** one visitor read 133 rows a minute, under the cap, so everything answered 200. Four visitors read 519 a minute, and refusals started at 34.5 s. At 60 s a visitor's paging and lookup got **429** "Collection page budget exhausted", and the page showed "Collection page unavailable" |
| Suites at `df16a36` | Node 95/95, the three API suites 37/37, Python discover 171 OK |
| v2 page checks at `df16a36` | `browser_v2_check` 28/28, `LIVE_CHECK_V2` 27/27, credits 12 + 12 |
| Stage 2 | **GO after stage 1 is live, once the user accepts the CPU budget's residual** (section 8). F1 no longer blocks it |

## 2. What changed

### `8a12082`: code and tests

- **The page** (`web/search-studio/src/app.mjs`).
  - `onHover` reads an unread row only after the pointer has rested on its dot for `HOVER_READ_MS` (250 ms).
  - `AudioMap` calls `onHover` only when the hovered dot changes. It also calls it with `null` on `pointerleave` and on Escape.
  - Every call clears the pending timer. So moving on, leaving the map or pressing Escape drops the read, and the timer reads only if `map.hover` is still that row.
  - A row already being read joins that read at once.
- **The collection client** (`collection-api.mjs`).
  - `Collection.ensure` keeps its reads in flight by row. Hover, click and any other caller therefore send one request per row, and `inFlight(row)` tells the page.
  - A failed read fails every caller waiting for it, and its rows can be asked for again.
- **The server** (`server/collection_v2.py`).
  - `rows_budget = Budget(600)`: `/collection/tracks?rows=` is charged to it instead of the pages' `tracks_budget` (300). When it runs out, the reply is 429 "Collection row budget exhausted".
  - `rows` cannot be combined with any other parameter (400, "rows cannot be combined with paging"). So a `rows=` request cannot run a page or a lookup on the row budget.
- **The tests.**
  - `tests/web_collection_v2.test.mjs`:
    - `ensure` joins reads in flight and retries after a refusal;
    - the page's hover reads wait, drop and join (node:test mock timers);
    - a 1 s, 500 px pointer sweep runs through the real `AudioMap` in the app harness, which can now use the real map instead of `TestAudioMap`.
  - `tests/test_api_v2.py`:
    - 600 `rows=` reads of 1 and 64 rows leave browse, paging, lookups, refinements and facets at 200;
    - a spent page budget leaves `rows=` reads answering;
    - the shared-budget test now expects the split.
- **The pins.** `app.mjs` (`15ab5ee3…`), `collection-api.mjs` (`7bdcff44…`) and `server/collection_v2.py` (`7ff7ec18…`) are re-pinned in `web-manifest.json` and `package-manifest.json`. Re-checked at `df16a36`: both manifests match these files, and neither has a stale row.

### `df16a36`: documents

- **`docs/DEPLOY_PLAN_V2.md`:**
  - lifts the stage 2 hold for F1 (the status and 3.2);
  - expects the new `app.mjs` digest in the stage 1 probe (3.1) and the new suite counts (171 and 95);
  - recounts the CPU residual (section 7, item 6).
- **`docs/PLATFORM_V2.md`:**
  - names the `rows=` budget (sections 4 and 9);
  - moves F1 to the fixed findings (10.8);
  - updates the suite counts (10.7).

`DEPLOY_PLAN_V2.md` 3.2 names this receipt as the proof.

### The review's fix, item by item

| The review asked for | `8a12082` |
|---|---|
| Wait about 250 ms on a point before reading | `HOVER_READ_MS = 250`. The read fires only if the pointer still rests on the same dot |
| Skip rows already in flight | Done in `Collection.ensure` itself, so a click and a hover share one read too |
| Give `rows=` reads (at most 64 primary-key lookups) their own, larger budget | 600 a minute, apart from the pages' 300 |
| A test that sweeps the real map through the app harness | "a 1 s, 500 px pointer sweep across the real map reads at most two rows, at the overview and zoomed in" |

## 3. The budget design, and why 600 a minute

- **Two budgets on one route.** Pages and lookups keep 300 a minute; explicit rows have 600.
  - Neither budget can spend the other. Hovering and clicking can no longer take down browse, lookup or paging, and a spent page budget leaves the map's reads working. `test_api_v2.py` tests both directions, and section 4.4 shows them on a real server.
  - The page budget stays where `b34f0ab` put it. It guards lookups that still scan: 0.40–0.51 s each at 200K rows (`PLATFORM_V2.md` 10.8). Raising it to make room for hover reads would have loosened that guard.
- **The most one visitor's hovering can send.**
  - A read needs a 250 ms rest on an unread dot, so one page sends at most 4 a second, or 240 a minute.
  - A row the page has read is never read again.
  - 600 covers 2½ visitors at that ceiling.
- **What mousing really sends after the fix:** 2 reads a minute for one visitor, and 14 for four visitors at once (section 4.3). At that rate 600 is far away.
- **What it costs.**
  - A hover reads one row: 0.012–0.015 ms of handler CPU in process. A full read of 64 rows costs 0.36–0.51 ms (workspace `validation/f1-hover/receipts/rows-cpu-in-process.txt`).
  - Over HTTP, 600 reads of 64 rows cost the server process 0.34 CPU-s at 11:13 (load average 110), and 0.80 and 0.74 CPU-s at 12:59 and 13:01 (load average 95–130). On this shared machine, CPU seconds vary from run to run; the in-process figures bound the handler's own share.
  - So at its cap the row budget costs about 0.3–0.8 CPU-s a minute, framework included.
  - The deploy plan counts about 0.7 in the anonymous CPU budget's residual (section 7, item 6: about 4.7 CPU-s a minute with every route at its cap, so the hour's 30 CPU-s could drain in about 6½ minutes). At 0.8 that becomes about 4.8 CPU-s and a little over 6 minutes. The decision does not change.
- **What it does not fix.** Every budget is still per process, not per client (review F4).
  - A script that sends `rows=` reads on purpose can still spend the 600. Hover and click reads would then answer 429 until the minute clears.
  - A script can also spend the pages' 300 directly. Neither needs the map.
  - F1 was that ordinary mousing took browse, lookup and paging down for every visitor. That is what is fixed.

## 4. Numbers before and after

**Setup.**
- **Roots.** Each commit is a git worktree in the session scratchpad, put through the image build's steps (`build_worktree_root.sh`):
  - the deploy plan's activation of fma2000-v2 (release format 2.1, `b537a7ac…`) and its `--check`;
  - the installer, the interpreter check, the model check and the plan check;
  - the v2 hydration from the verified local MP3 cache.
  
  Both builds:
  - 38 activation changes, `--check` ok;
  - the FTS5 trigram index verified;
  - 1,992 previews published;
  - `git status --short` prints exactly the 16 lines that `DEPLOY_PLAN_V2.md` 3.2 lists (`f1-hover/build-*.json`).
- **The release and the serving list.** The serving list (`corpus-releases/serving.json`) serves 670 of 1,992 rows.
- **The server.** It starts as the image's CMD would (anonymous preview, audio on), behind the local TLS terminator (`$B/validation/server/tls_proxy.py`), as a fresh process for every run, so every budget starts empty.
- **The interpreter.** The server runs on CPython 3.12.14 (`venv-3.12.14`, the image's interpreter). On `$B/venv` (3.12.13) the encoder refuses to start: "Native runtime differs from reviewed package profile", as `$B/validation/server/engine-id-check-3.12.13.log` recorded on 2026-10-05.
- **The browser.** Playwright 1.63.0, headless Chromium, 1440 × 900.
- **The machine.** It was shared and heavily loaded: load average 50–210. Request counts do not depend on load; CPU seconds do.

### 4.1 The app harness (Node, the real `AudioMap`)

`tests/web_collection_v2.test.mjs` sweeps 500 px at 60 Hz in 1 s across the 870 × 600 px map, at the overview and at 4× zoom, then rests 250 ms:

| Page | Overview: unread dots crossed / read | 4× zoom |
|---|---|---|
| `777a886` (`app.mjs` `1c037ecf…`) | 23 / 23, all while moving | 16 / 16, all while moving |
| `df16a36` (`app.mjs` `15ab5ee3…`) | 23 / 0 | 16 / 1, the dot where the sweep stops |

- The `777a886` row is the same test, run against that commit's `app.mjs` and `collection-api.mjs` with its count assertions turned into output (`f1-hover/suites.txt`).
- The review's own simulation measured 23 and 17.

### 4.2 The real UI: ten sweeps

- **The sweep.** Each sweep opens a fresh page. The pointer crosses 500 px in 1 s with 61 events along a horizontal line through the map's centre, then rests 750 ms.
- **The lines.** Offsets −120, −60, 0, +60 and +120 px, each at the overview and at 4.3× zoom (eight clicks on +).
- **What is counted.** The page's `/collection/tracks?rows=` requests, from the HAR. The unread served dots crossed are read from the tooltip, which shows "…" for a row the page has not read.

| | Overview (5 sweeps) | 4.3× zoom (5 sweeps) | All 10 | While moving | Most in one sweep |
|---|---|---|---|---|---|
| Unread served dots crossed | 1, 8, 5, 4, 5 | 1, 3, 2, 6, 7 | 42 | | |
| Row reads, `777a886` | 1, 8, 5, 4, 5 | 1, 3, 2, 6, 7 | **42** | 41 | 8 |
| Row reads, `df16a36` | 0, 0, 0, 0, 0 | 0, 1, 0, 0, 1 | **2** | **0** | 1 |

- Before the fix, the page read exactly one row for every unread served dot it crossed.
- After it, a sweep reads at most the dot it stops on.
- An earlier run by the previous agent (11:07–11:11, six sweeps; workspace `validation/f1-hover/proof.log`) gave 28 reads before and 1 after.

### 4.3 The real UI: 60 s of mousing

- **The mousing.** One page, or four pages sharing one browser context, mouse continuously for 60 s:
  - zigzag lines 16 px apart at 800 px/s;
  - a 300 ms pause at every turn;
  - a new view every 7 s: the overview, 4.3× zoom, and drags to other areas.
- **At 60 s.** A visitor who never hovers, in a context of its own, browses, goes to the next page and looks up "love". The others keep mousing meanwhile.
- **Then.** The mousing stops, and user 1 does the same three steps.

| | `777a886`, 1 visitor | `df16a36`, 1 visitor | `777a886`, 4 visitors | `df16a36`, 4 visitors |
|---|---|---|---|---|
| Row reads in the first 60 s | 133 | **2** | 519 | **14** |
| Row reads refused (429) | 0 | 0 | 246, the first at 34.5 s | 0 |
| Visitor at 60 s: browse / next page / lookup | 200 / 200 / 200 | **200 / 200 / 200** | 200 / **429** / **429** | **200 / 200 / 200** |
| User 1 right after: browse / next page / lookup | 200 / 200 / 200 | **200 / 200 / 200** | **429** / not sent (the button stayed disabled) / 200 | **200 / 200 / 200** |
| Server CPU while mousing | 0.29 s | 0.21 s | 0.82 s | 0.22 s |

- **What a refused page looks like.** `f1-hover/before-777a886-mousing-4user-visitor-at-60s-next-page.jpg` shows "Collection page unavailable: Collection page budget exhausted. Previous results remain." Its `df16a36` counterpart shows page 2 of 56.
- **Why the HEAD screenshots repeat.** The page renders the same state the same way, so these files are byte-identical, and git stores each pair once:
  - the HEAD screenshots of the one-visitor and four-visitor runs;
  - the two sweep screenshots.
- **The browser contexts.** The four mousing pages share one browser context. uvicorn runs with `limit_concurrency=16` (`server/hosting.py`), and each context keeps up to six keep-alive connections through the 1:1 TLS terminator. A third context meets 503 on its module loads (the previous agent's probe, workspace `validation/f1-hover/contexts-probe.json`; section 6).
- **The earlier run** (11:17–11:22) gave the same picture:
  - one visitor: 133 reads before and 4 after;
  - four visitors: 548 reads before, with 248 refused and the visitor's browse at 429; 16 reads after, with nothing refused.

### 4.4 The row budget on a real server

`rows_budget_check.py` first lists the served rows with 14 page reads. Then it sends `rows=` reads of 64 distinct served rows each, as fast as one connection allows, then a visitor's page reads and one search:

| | `777a886` | `df16a36` (two fresh servers) |
|---|---|---|
| `rows=` reads admitted | 286: the 14 page reads had already spent 14 of the shared 300 | 600 and 600 |
| First refusal | Read 287: 429 "Collection page budget exhausted", `Retry-After` 60 | Read 601: 429 "Collection row budget exhausted", `Retry-After` 59 |
| Browse, next page, lookup, refinement afterwards | 429, 429, 429, 429 | 200, 200, 200, 200 (both runs) |
| Search afterwards | 200 | 200 |

### 4.5 Why the real UI reads fewer rows than the review's sweep

- **The serving list holds 1,322 of the 1,992 rows** (`cc74c38`, which predates the fix). A held dot is unselectable and reads nothing, so only the 670 served rows can be read. One sweep crosses 1–8 unread served dots, not 17–23.
- **So one realistic visitor read 133 rows a minute before the fix.** That is under the 300, so the review's "about 15 s of mousing by one visitor" does not reproduce on the stage 2 selection.
- **Four visitors did spend the budget, within 35 s.** The pages then answered 429 to everyone. The review measured with every row selectable.
- **The risk would grow.** A few concurrent visitors are ordinary traffic. The risk grows as held rows return to service and with any larger release.

## 5. Suites

| | Result |
|---|---|
| The manager, 12:45 | node 95/95. `test_api_v2`, `test_public_api` and `test_public_web` 37/37. `test_release_v2` 4 failures, pre-existing at `777a886` |
| This receipt, `df16a36`: `node --test 'tests/*.test.mjs'` | **95/95** |
| `$B/venv/bin/python -m unittest tests.test_api_v2 tests.test_public_api tests.test_public_web` | **37 OK** |
| `$B/venv/bin/python -m unittest discover -s tests -p 'test_*.py'` | **171 OK**, `test_release_v2` included |
| `node --test --test-reporter=spec tests/web_collection_v2.test.mjs` | 13/13. The sweep test reports "overview: 23 unread dots crossed, 0 read; 4x zoom: 16 unread dots crossed, 1 read" |
| `tests/test_release_v2.py` on `venv-3.12.14` | 4 failures with the shared cache; **21 OK** with an empty `TMPDIR` |
| The previous agent, `df16a36` | Python 171 OK, Node 95 pass, `browser_search_ui` and `browser_collection_v2` pass (workspace `validation/f1-hover/suites/main-checkout-df16a36/`) |

**The four `test_release_v2` failures are a test-cache artifact, not a converter bug, and not F1.**
- **Where the cache lives.** `tests/v2_fixtures.py` caches its reference conversion under `$TMPDIR/music-v2-test-cache/<code_key()>`.
- **What the key misses.** The key covers the source release and the v2 code, but not the SQLite version.
- **What happens.** The cache was written under SQLite 3.50.4 (`$B/venv`). A conversion under 3.54.0 (`venv-3.12.14`) writes a `catalog.sqlite` of the same size that differs in 18,052 bytes. These include the header's library version: bytes 96–99 read 3054000 instead of 3050004.
- **Determinism holds per interpreter.** Each interpreter is deterministic with its own cache: 21 OK on both.
- **The fix,** outside F1: add `sqlite3.sqlite_version` to `code_key()`.

## 6. Open items outside F1

1. **The test cache key** (section 5).
2. **The server interpreter.** Start the server with `venv-3.12.14`; `$B/venv` (3.12.13) cannot start it. The suites run on `$B/venv` because `venv-3.12.14` lacks `httpx`.
3. **Local connection limit.** uvicorn's `limit_concurrency=16` together with the 1:1 local TLS terminator lets about two browser contexts load the page at once; a third meets 503 on module loads. On Render the edge proxy pools its connections to the app, so this local limit says nothing certain about production. It was not measured there.
4. **The scale UI review's other findings.** F3–F5 must be fixed before any release above about 8K rows; F4 includes the per-process budgets (section 3). F6–F13 are lower.
5. **The anonymous CPU budget's residual** (security review B2; `DEPLOY_PLAN_V2.md` section 7, item 6) needs the user's acceptance before stage 2.
6. **Which head to push for stage 1.**
   - `INTEGRATION_RECEIPT_V2.md` section 10 (in the workspace) pushes `777a886` and expects `app.mjs` `1c037ecf…`.
   - `DEPLOY_PLAN_V2.md` 3.1 at `df16a36` expects `15ab5ee3…` and the head named in that receipt.
   - Whichever head the manager pushes, the digest must match it: `777a886` gives `1c037ecf…`; `8a12082` or later gives `15ab5ee3…`.
   - Stage 1 serves the v1 selection, which never reaches F1, so either head serves stage 1.

## 7. How to verify

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05
cd $B/repo
git show --stat 8a12082 df16a36
node --test 'tests/*.test.mjs'                                      # 95 pass
node --test --test-reporter=spec tests/web_collection_v2.test.mjs   # 13 pass; "overview: 23 unread dots crossed, 0 read; 4x zoom: 16 unread dots crossed, 1 read"
$B/venv/bin/python -m unittest tests.test_api_v2 tests.test_public_api tests.test_public_web   # 37 OK
$B/venv/bin/python -m unittest discover -s tests -p 'test_*.py'    # 171 OK
```

**The real-UI proof** takes about 12 minutes, needs Chromium and ports 8861–8872, and uses about 4.2 GB of scratch disk:

1. Copy the scripts in `validation/f1-hover/` to a scratch directory.
2. Set `S` in `run_proof.sh` to that directory. The scripts as committed name this session's scratchpad.
3. Build the two roots:
   ```sh
   ./build_worktree_root.sh 777a886 $S/wt/before-777a886 $S/build/before-777a886
   ./build_worktree_root.sh df16a36 $S/wt/head-df16a36 $S/build/head-df16a36
   ```
4. Run `./run_proof.sh`. It writes `$S/proof.log` and `$S/out/`.
5. Remove the worktrees:
   ```sh
   git -C $B/repo worktree remove --force $S/wt/before-777a886
   git -C $B/repo worktree remove --force $S/wt/head-df16a36
   ```

## 8. Stage 2: recommendation

**GO after stage 1 is live, once the user accepts the CPU budget's residual.** The integration receipt held stage 2 for three things. Each now stands as follows:

1. **The scale UI review's F1: fixed and proved** (section 4):
   - the harness sweep reads 0 and 1 rows, where it read 23 and 16;
   - the real UI reads no row while the pointer moves;
   - 60 s of mousing by one or four visitors leaves browse, paging and lookup at 200;
   - `rows=` reads can no longer spend the page budget.

   F2 was fixed earlier, in `dbb99f1`.
2. **The v2 page's browser checks at the final head: passed at `df16a36`** on fresh local servers (`f1-hover/after-df16a36-*.json`):
   - `browser_v2_check.mjs` with `EXPECT_COUNT=670`: 28/28. It was 27/28 at 11:26, when one desktop live search timed out at load average 72;
   - `live_check_v2.mjs`: 27/27;
   - `browser_credits_v2.mjs`: 12 + 12.
3. **The user's acceptance of the B2 residual: still open.** It is the one condition left (section 3 gives the rows budget's share).

**F3–F5 only matter above about 8K rows.**
- At or below 8,192 rows the pinned overview holds every position (`scripts/build_web_v2.py`, `sample_cap=8192`), so the map never asks for tiles. fma2000-v2 has 1,992 rows.
- None of the 34 pages in this proof requested `/collection/tiles` (the HARs).
- Fix F3–F5 before any larger release.

**Unchanged from the deploy plan.**
- Stage 2 follows stage 1.
- It still runs three things for the first time, and each fails closed:
  - the v2 Docker build;
  - FTS5 trigrams in the image's SQLite 3.46.1 (the 2.0 fallback is proved);
  - the hydration worker as a real subprocess.

## 9. Evidence: `validation/f1-hover/`

| File | What |
|---|---|
| `summary.json` | Every number above, in one place |
| `{before-777a886,after-df16a36}-sweeps.{json,har}` | Section 4.2: per sweep, with the line, the reads, their statuses and the unread dots crossed; the requests |
| `{before-777a886,after-df16a36}-mousing-{1,4}user.{json,har}` | Section 4.3: per user, and the visitor's and user 1's steps with the page's own status text; the requests |
| `{before-777a886,after-df16a36}-rows-budget.json`, `after-df16a36-rows-budget-2.json` | Section 4.4 |
| `after-df16a36-{browser-v2-check,live-check-v2,browser-credits-v2}.json` | Section 8: the v2 page checks |
| `*.jpg` | The sweeps' end view; the 4-visitor runs' refused and answered page reads; HEAD's browse, next page and lookup after 60 s of mousing |
| `build-{before-777a886,head-df16a36}.json` | The two roots' builds |
| `suites.txt` | Section 5, with the harness measurement of the page before the fix |
| `proof.log` | The run log, 12:53–13:02 |
| `f1_hover_proof.cjs`, `run_proof.sh`, `build_worktree_root.sh`, `start_server.sh`, `stop_server.sh`, `rows_budget_check.py` | The tools, as run |

**About the HARs.**
- They are HAR 1.2 files, with one entry per line.
- They list every `/collection/`, `/v1/`, `/audio/` and configuration request, and any request that failed or answered 400 or above. Static modules that answered 200 are left out.
- Headers are cut to `content-type`, `content-length`, `retry-after` and `cache-control`.
- A refused `/collection/` request carries its error body.
- `pageref` names the page, and `_secondsFromStart` times each request from the run's start.

**In the workspace** (`$B/validation/f1-hover/`), outside the repository: the previous agent's runs at 11:07–11:26, its tools, and the in-process CPU measurement.
