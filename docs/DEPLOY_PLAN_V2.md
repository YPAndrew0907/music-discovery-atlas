# Deploy plan: release format v2 on fma2000

Status, 2026-10-06: ready for the manager's decision. Nothing has been pushed or deployed, and production (`main` = `35cec9e`, the Atlas page on the v1 fma2000 release) is unchanged. The work is on the local branch `platform-v2`, which now carries `v2-deploy`, `rights-fix-2000` and `v2-scale-ui` as real merges (section 2) and fast-forwards production `main`. `validation/INTEGRATION_RECEIPT_V2.md` in the project workspace records the merge, every suite and the local proof of the merged branch (section 5.2).

**Rights fix (merged from `rights-fix-2000`).** The rights research of 2026-10-06 found eight rows of the live 2,000 that must not be served. fma2000 is rebuilt without them: 1,992 recordings, release `32015637…` (was `af67c98a…`). The digests, counts and commands below are the rebuilt release's; `docs/RIGHTS_QUARANTINE.md` has the list, the reasons and the procedure. Stage 1 takes the eight rows off the live site.

**The adopted rights decision goes further.** The decision of the same day (`rights/RIGHTS_DECISION_2026-10-06.md` section 7 and `rights/DECISION.json` in the project workspace) takes 764 of the 2,000 off now through a request-time suppression list and keeps 1,236 serving. Its section 7.2 also lists site changes for before the next deploy: credits beside the player, the takedown page and others. This branch implements 8 of the 764 and none of the site changes. Both stages remove rows and add none.

**Scale UI and release format 2.1 (merged from `v2-scale-ui`).** The converter now writes release format 2.1 by default: `catalog.sqlite` also carries an FTS5 trigram index that prefilters name lookups (`docs/PLATFORM_V2.md` 10.2). Stage 2 therefore pins the 2.1 conversion of the rebuilt release, `b537a7ac…`. The 2.0 conversion `279cd21b…` stays the fallback (section 3.2). The merge also brings the paged credits at `/collection/credits`, level of detail for the v2 map and module preloads on the page (`PLATFORM_V2.md` section 10).

The deploy serves the same 1,992 recordings with the same rankings, scores and search traces as the v1 release it converts. What changes: the page downloads 0.69 MB instead of 10.3 MB and reads the catalog page by page; the server memory-maps the release instead of parsing it; and audio is hashed on first play instead of all 2 GB at every start.

## 1. Decisions to take first

1. **Where the release files come from.** There are two install modes, set by the `source` block of `active-corpus.json`:
   - **bundled** (the plan below): the release directory is committed under `corpus-releases/fma2000-v2/` and copied into the image like today's v1 releases. That commits 8 files, 24.6 MB: `catalog.sqlite` 10.0 MB (with the 2.1 lookup index), `evidence.sqlite` 9.1 MB, `vectors.f32` 4.1 MB, `examples.json` 1.2 MB and four small files. This is the same order as `corpus-releases/fma2000/`, which is already in Git. This branch commits no release data; the activation commit in step 3.2 does.
   - **object-store**: Git holds only the selection, and the image build fetches each file by its SHA-256 from one pinned HTTPS origin. This needs a bucket and an upload, and none exists. It is the mode for anything much larger than fma2000 (section 7).
2. **One push or two.** The plan uses two: the code first, with the v1 selection unchanged, then the activation. Each push is its own rollback point. Pushing both at once also works, but a failure would then not show which half caused it.
3. **The corpus.** fma2000 only (1,992 recordings since the rights quarantine). fma5777-v2 exists on the external volume, but its 3,777 added rows rest on an automated rights screen (section 7).

## 2. What is on the branch

`platform-v2` = the v2 platform (8 commits, v1 parity), then the merges of `v2-deploy`, `rights-fix-2000` and `v2-scale-ui`, then the integration commits:

| Commit | What it adds |
|---|---|
| `d98c3e9` | `scripts/install_release_v2.py` and a Docker step that runs it before any download; the optional `source` block of a v2 selection |
| `fa192a4` | `scripts/hydrate_release_v2.py`; `scripts/hydrate_corpus_audio.py` hands a pinned v2 selection to it instead of failing closed |
| `c1ea00b` | The v1-page tests read the v1 data wherever the tree keeps it, so an activated tree passes every suite |
| `62f6744` | `scripts/activate_release_v2.py`: the activation step and its `--check` |
| `b9c7afe` | `tests/live_check_v2.mjs`: the post-deploy live check |
| `72231ac` | `scripts/verify_audio_publication.py`: the remote-audio publication verifier |
| `ce55c4a` | The activation and gateway tests no longer depend on the tree or volume they run in |
| `0da3f2a` | An end-to-end test of the v2 hydration build chain, with only the network transport stubbed |
| `b3365d2` and `5c72801` | This document, and three status lines in `docs/PLATFORM_V2.md` |
| `212471c` (rights-fix-2000) | `corpus-releases/quarantine.json`, the reviewed list of 2026-10-06, and its checks |
| `374fed9` | The corpus builders honour the list; the input reconstruction, credits, pins and plan steps join the repository |
| `d7cf8d2` | fma2000 rebuilt without the eight rows (1,992); credits, page data, pins, audio plan and tests follow |
| later rights-fix commits | Tests that hold in an activated tree, `docs/RIGHTS_QUARANTINE.md`, and the updates in this document |
| `b0cc12e` to `504c369` (v2-scale-ui) | Release format 2.1 with its converter, upgrader and verifier; the paged credits; map tiles and stored links; level of detail on the page; module preloads; the page-jump fix; `PLATFORM_V2.md` section 10 |
| `558d6f7`, `6bfae62`, `5376b00` | The three merges, in that order. Eight files conflicted in the last one; each keeps both sides (section 5.2) |
| `fe048b3` | `tests/live_check_v2.mjs` expects the 2.1 conversion `b537a7ac…` by default |
| `fedde26` | Two rights-fix tests and the public link check hold in a tree activated with the merged code |
| `87b6f11` | The activation refuses stale pins and refreshes only its own (security review B1) |
| `b34f0ab` | `/collection/tracks` has a per-minute budget, 300 by default (B2) |
| `09117d2` | The audio publication verifier keeps no cookies, with a test that can fail (C6, T-6) |
| `c38bcc5` and the sweep after it | This document, `PLATFORM_V2.md` and `RIGHTS_QUARANTINE.md` updated to the merged branch. The sweep corrects the suite counts in 3.1 and 3.2 and two leftover status lines, and adds the head check to 3.1 and the non-fast-forward rollback to 3.4 |

**v1 behaviour is unchanged.** For a v1, disabled or absent selection, the new Docker step prints `{"installed": false, ...}` and exits 0. The hydration entrypoint runs its v1 code exactly as before; the new dispatch only takes a pinned, parseable `schemaVersion: 2` file. The v1 server path is untouched. On the v1 page the merged scale UI adds only module preloads and one more map module; its level-of-detail paths are v2-only. Stage 1 below was proved locally with the unmodified production live check, on the merged branch too (section 5.2).

**What has never run:**

- **A Docker build.** There is no Docker or Podman on this Mac, so the first real image build of this branch is the stage 1 push.
- **The v2 hydration over the network.** The 1.95 GB of official ranges have not been fetched through the v2 path. The 1,992-entry plan has not been fetched through the v1 path either; its entries are byte-identical to entries of the 2,000-entry plan that built today's image. That first happens in the stage 2 build. The transport is the unchanged v1 code that built today's image. Everything around it is tested end to end with the network stubbed: the plan binding, the supervised worker, the parent's re-verification and the publication.
- **The v2 hydration worker as a real subprocess.** The build-chain test runs the worker in process with `load_plan_v2` mocked, and the local proofs publish from the cache. So `hydrate_release_v2.py --worker-dir` first runs as a subprocess in the stage 2 build (security review B3). The review read that path and found no bug; a failure fails closed and costs one build.
- **The lookup index in the image.** The installer validates a 2.1 release with FTS5's own integrity check, so the stage 2 build needs FTS5 with the trigram tokenizer in the image's SQLite. That is expected: Debian trixie's libsqlite3 3.46.1, which the official `python:3.12.14-slim-trixie` Python links, is built with `--enable-fts5`, and trigrams exist since SQLite 3.34. No image has been built to prove it. If it is missing, the build fails at `install_release_v2.py`; the fallback is the 2.0 release (section 3.2).
- **Every failure fails closed.** Render keeps the previous deploy.

**The image build, in order** (Dockerfile):

1. `pip install -r requirements.lock` (unchanged).
2. `COPY` the code, data and the three new scripts: `install_release_v2.py`, `hydrate_release_v2.py` and `make_audio_delivery_v2.py`.
3. `RUN python scripts/install_release_v2.py`. In bundled mode it checks:
   - the exact inventory: `release.json` plus the pinned assets, no extras and no symlinks;
   - the manifest digest;
   - every asset's length and SHA-256, including the lazily read `evidence.sqlite` and `examples.json`;
   - the server's own loader;
   - every catalog, rights and evidence row.
   It runs in about 0.3 s at fma2000. Any mismatch fails the build, and Render keeps the running deploy.
4. `RUN python scripts/fetch_model.py --download-model` (unchanged).
5. `RUN ... python scripts/hydrate_corpus_audio.py`. For fma2000-v2 this is the v2 path:
   - **Plan binding.** The reviewed plan binds to the v2 release only because the release is a conversion of the reviewed v1 release. The v1 catalog, ids and rights bytes are rebuilt from the database and must hash to the digests that `release.json` records. The resulting entries equal the v1 plan's.
   - **Download.** The same 1.95 GB of official FMA byte ranges are fetched with the same transport, budget and deadline.
   - **Outputs.** The output names are unchanged:
     - `audio-preview/`, the 1,992 MP3s;
     - `audio-release-credits/`, byte-identical to the v1 credits;
     - `audio-delivery.verified.json`, now in the v2 local format.
   - **Checks before publication.** Every MP3 is re-hashed and every credit re-checked. The server's own `AudioDeliveryV2` must accept the manifest before the single publication step.
6. `CMD python server/hosting.py`.
   - **At start.** It verifies the package and streams the 24 MB of core release assets.
   - **Audio.** It checks the audio inventory and sizes at start, and hashes each MP3 on its first request.

**Service environment: no change needed.** The live service (configured in the Render dashboard, not by `render.yaml`) plays previews, so it already points at the hydrator's outputs. The v2 build writes the same paths:

| Variable | Value |
|---|---|
| `MUSIC_ENABLE_AUDIO_PREVIEWS` | `1` |
| `MUSIC_AUDIO_PACK_DIR` | `/app/audio-preview` |
| `MUSIC_AUDIO_MANIFEST_PATH` | `/app/audio-delivery.verified.json` |

The anonymous-preview variables also stay as they are.

- **Confirm in the dashboard first.** These values could not be read from here, so confirm them before stage 2.
- **If they differ, the deploy fails closed.** For example, a manifest path pointing at the disabled `audio-delivery.json` makes the v2 server refuse to start. Render then keeps the previous deploy.

## 3. Steps

### 3.1 Stage 1: the code and the rights fix, with the v1 selection

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05
cd $B/repo                                                        # the main checkout
git fetch origin
git rev-parse --abbrev-ref HEAD                                   # must print platform-v2
git merge-base --is-ancestor origin/main platform-v2 && echo fast-forward   # must print fast-forward
git status --short                                                # must be empty
git rev-parse platform-v2                                         # must print the head in validation/INTEGRATION_RECEIPT_V2.md
$B/venv/bin/python -m unittest discover -s tests -p 'test_*.py'   # expect 170 OK
node --test tests/web_*.test.mjs                                  # expect 92 pass
git push origin platform-v2:main
```

**What else stage 1 ships.** The merged scale UI. On the v1 page that is the module preloads and the extra map module. The v2 code (format 2.1, paged credits, tiles and links) is never reached with a v1 selection. Section 5.2 proved this code on a stage 1 root (later commits change only documents): the unmodified production check passed 17/17. Any new test of the v1 page must read its data through `tests/v1_page_data.mjs`, or it fails once v2 is active.

**What Render does.** It builds the image with the v1 selection, so the new installer step is a no-op. The v1 selection is now the rebuilt 1,992-track release, and the hydration fetches only its 1,992 planned ranges; the quarantined recordings are never fetched. Expect the usual build-to-live time: the last two deploys were live 230 s and 244 s after their push. Those times include the 1.95 GB hydration, which runs after `COPY web/` and so repeats on every deploy.

**A brief cutover risk, accepted.** While the old and new instances overlap, a page may load the new `app.mjs` and then ask the old instance for `collection-api.mjs` or `map-lod.mjs`. It gets a 404 and stays on its loading state until reloaded (security review A3). A reload fixes it. Loading both modules lazily would remove the risk; that is a follow-up.

**First prove that the new build is live.** `live_check_atlas.mjs` and the binding line also pass against today's deploy, so they cannot tell a failed or skipped stage 1 build from a live one (security review A2). These probes can. On 2026-10-06 production answered `2000 af67c98a…`, 404, 404, `0ff395de…` and 206 for a quarantined preview (`validation/v2-integrate/final/receipts/production-today/`).

```sh
O=https://music-discovery-atlas.onrender.com
curl -s $O/v1/manifest | python3 -c "import json,sys; m=json.load(sys.stdin); print(m['catalogCount'], m['corpusReleaseSha256'])"
# expect: 1992 32015637189671d9f2fa44429bd8baa56696439fa6b8f1967337c2d10bbfbe42   (old deploy: 2000 af67c98a…)
for f in collection-api.mjs map-lod.mjs; do curl -s -o /dev/null -w "$f %{http_code}\n" $O/search-studio/src/$f; done
# expect: 200 for each   (old deploy: 404)
curl -s $O/search-studio/src/app.mjs | shasum -a 256
# expect: a7fadc1d54b79fe8ae6240b8a219217bdd1611ec3a1f5aeb02b8a62299a61b7b   (old deploy: 0ff395de…)
for id in 001382 093518 093519 093520 093521 098077 125279 154569; do curl -s -o /dev/null -w "$id %{http_code}\n" $O/audio/$id.mp3; done
# expect: 404 for each quarantined recording   (old deploy: 200 or 206)
```

Also confirm in the Render dashboard that the deploy of the pushed commit (`git rev-parse platform-v2`) is Live. The server exposes no commit identifier, so the release digest and these files stand in for one.

**Stop rule.** The last two deploys were live 230 s and 244 s after their push. If the probes still show the old values 10 minutes after the push, stop. Do not start stage 2. Read the Render build log first. A failed build changes nothing live.

**Then the regular checks:**

```sh
cd /Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/validation/atlas
NODE_PATH=../../tooling/node_modules node live_check_atlas.mjs        # expect LIVE_ATLAS 17/17
curl -s https://music-discovery-atlas.onrender.com/v1/manifest | python3 -c "import json,sys; m=json.load(sys.stdin); print(m['bindingStatus'], m.get('releaseFormat'))"
# expect: verified-corpus-release None
```

### 3.2 Stage 2: activate fma2000-v2 (bundled)

Work in a fresh worktree on `main` after stage 1. The main checkout stays on its own branch, which other work uses.

The release is regenerated from Git with the server runtime. The conversion is byte-deterministic for a given runtime. With the merged converter, the rebuilt 1,992-track release converts to release format 2.1, `b537a7ac…`, in 12 s and 156 MB, and two independent conversions gave byte-identical files. That digest is what the activation pins, so the source of the bytes does not matter. Another SQLite writes other database bytes: the tests' runtime (3.12.13, SQLite 3.50.4) converts to a different digest, so use `venv-3.12.14`. (The fma2000-v2 on the external volume is the superseded 2,000-row conversion `806b19ed…`; do not use it.)

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05
cd $B/repo && git fetch origin
git worktree add .worktrees/activate-fma2000-v2 -b activate-fma2000-v2 origin/main   # main = the stage 1 head
cd .worktrees/activate-fma2000-v2
$B/venv-3.12.14/bin/python scripts/convert_release_v1_to_v2.py --source-dir corpus-releases/fma2000 \
  --expected-manifest-sha256 32015637189671d9f2fa44429bd8baa56696439fa6b8f1967337c2d10bbfbe42 \
  --output-dir $B/validation/v2-deploy/regen/fma2000-v2 > /dev/null   # the output directory must not exist yet
shasum -a 256 $B/validation/v2-deploy/regen/fma2000-v2/release.json  # expect b537a7ac…
$B/venv-3.12.14/bin/python scripts/activate_release_v2.py \
  --release-dir $B/validation/v2-deploy/regen/fma2000-v2 \
  --expected-manifest-sha256 b537a7ace86ea6eebdd95b2d4cfc908e75487aeef8408295330d02c3e278d740 \
  --name fma2000-v2
$B/venv-3.12.14/bin/python scripts/activate_release_v2.py --check   # expect "ok": true, lookupIndex fts5-trigram-v1
$B/venv/bin/python -m unittest discover -s tests -p 'test_*.py'      # expect 170 OK
node --test tests/web_*.test.mjs                                      # expect 92 pass
NODE_PATH=$B/tooling/node_modules node tests/browser_search_ui.mjs       # v1 page fixture: passes
NODE_PATH=$B/tooling/node_modules node tests/browser_collection_v2.mjs   # v2 page fixture: passes
git status --short        # exactly the files listed below
git add -A && git commit -m "Activate release format v2 on fma2000 (bundled)"
git push origin activate-fma2000-v2:main
```

**Fallback to release format 2.0.** Use it if the stage 2 build fails at `install_release_v2.py` because the image's SQLite lacks FTS5 trigrams (section 2). Add `--no-lookup-index` to the convert command, with a new output directory. Then pin `279cd21b116f084001162c8e10177511e7ffda89064189328e31d73c5fea68b7` in the activation, and run the live check with `EXPECT_RELEASE` set to it. On the merged branch that activation, its `--check` and the installer pass (section 5.2).

**Use the server runtime for the activation.** The page data is rebuilt deterministically: the same inputs give byte-identical files. Running it with `venv-3.12.14` (CPython 3.12.14, numpy 2.3.5) reproduces the files that were tested.

**The activation touches only its own pins** (security review B1). It refuses to start while any package or web pin is stale, so it needs a clean checkout. It refreshes only the rows of the files it rewrites, and it refuses any other stale pin. Compare its printed list with the 38 entries in `validation/v2-integrate/final/receipts/activated-09117d2/changed.txt`. `git status --short` must print exactly these 16 lines:

```text
 M .dockerignore
 M .gitignore
 M active-corpus.json
 M package-manifest.json
 M web-manifest.json
 M web/notices/track-attribution.html
 D web/search-studio/data/artist-records.json
 D web/search-studio/data/catalog.json
 M web/search-studio/data/examples.json
 D web/search-studio/data/ids.json
 D web/search-studio/data/index.json
 M web/search-studio/data/layout.json
 M web/search-studio/data/manifest.json
 D web/search-studio/data/vectors.f32
 M web/search-studio/src/studio-release.mjs
?? corpus-releases/fma2000-v2/
```

**Files the activation changes** (38 entries, printed by the script):

- **Added:** `corpus-releases/fma2000-v2/`, 8 files: `release.json`, `catalog.sqlite`, `evidence.sqlite`, `vectors.f32`, `graph.bin`, `layout.f32`, `graph-manifest.json`, `examples.json`.
- **`active-corpus.json`:** becomes the selection below.
- **Rewritten page data:**
  - `web/search-studio/data/manifest.json`, `layout.json` (schema 3) and `examples.json`;
  - `web/search-studio/src/studio-release.mjs`, the page's manifest pin;
  - `web/notices/track-attribution.html`, which becomes the small page that links to the paged credits at `/collection/credits`.

  The page-data manifest becomes `cebbefa8…` (`f6b89f93…` with the 2.0 fallback).
- **Removed page data:** `web/search-studio/data/catalog.json`, `vectors.f32`, `index.json`, `ids.json`, `artist-records.json`. A v2 server refuses a page that ships them.
- **Re-pinned manifests:**
  - `web-manifest.json`: the removed rows are dropped and the changed rows refreshed.
  - `package-manifest.json`: refreshed rows for `active-corpus.json`, `web-manifest.json`, the four page files and the credits page; the removed rows are dropped.
- **Allowlists:** `.gitignore` and `.dockerignore` each gain the release directory and its 8 files. Both lists are default-deny.

The selection the activation writes:

```json
{
  "schemaVersion": 2,
  "enabled": true,
  "format": "music-corpus-release-v2",
  "directory": "corpus-releases/fma2000-v2",
  "manifestSha256": "b537a7ace86ea6eebdd95b2d4cfc908e75487aeef8408295330d02c3e278d740",
  "source": {"kind": "bundled"}
}
```

**The release files are pinned by the selection, not the package manifest.** The chain runs: `package-manifest.json` pins `active-corpus.json`, which pins the `release.json` digest, which pins every asset. `verify_package()` therefore does not hash them a second time at start.

**The v1 release history stays.** `corpus-releases/fma500` to `fma2000` remain in Git and in the image, so rollback is a plain revert. Section 7 lists dropping them from the image as a startup saving.

### 3.3 After the stage 2 deploy

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05
cd $B/repo/.worktrees/activate-fma2000-v2        # any checkout that has tests/live_check_v2.mjs
NODE_PATH=$B/tooling/node_modules OUT_DIR=$B/validation/v2-deploy/live-production \
node tests/live_check_v2.mjs                     # expect LIVE_CHECK_V2 26/26 (EXPECT_RELEASE=279cd21b… after the 2.0 fallback)
NODE_PATH=$B/tooling/node_modules MUSIC_UI_EVIDENCE_DIR=$B/validation/v2-deploy/live-production/credits \
node tests/browser_credits_v2.mjs https://music-discovery-atlas.onrender.com web/notices/track-attribution.html
                                                 # expect "v2 credits browser checks passed ... (12 + 12 checks ...)"
```

Neither live check reads the paged credits, so the second command checks them in a real browser: the landmarks, headings and keyboard order, the page jump and its clamping, the ID redirects, the small page's forward of old `#fma-N` links, and no horizontal scroll at desktop and phone widths.

**What the 26 checks cover:**

- **Production checks (17).** The same 17 checks as `live_check_atlas.mjs`, on desktop 1440×1000 and mobile 390×844:
  - the h1;
  - the Atlas default and the open map;
  - live server search with its animation;
  - playback advancing and an `/audio/` 206;
  - no page errors;
  - `?direction=list`.
- **v2 checks (9):**
  - `/healthz`;
  - `/v1/manifest` with `releaseFormat` 2, `verified-corpus-release-v2` and the expected release digest;
  - the delivery summary: local, 1,992 of 1,992 available;
  - server-side collection pages and neighbors;
  - an exact audio range;
  - the excluded `fma:30702`, every recording on the quarantine list and the whole-catalog files return 404;
  - on each viewport, the page downloads only the three pinned v2 data files.
- **Search budget.** The run uses 2 live searches and 1 neighbor read. With the optional probe below it uses 12 of the process's 30 anonymous searches in the hour after the deploy (6 a minute), so run both off-peak.

**Optional latency probe.** It uses 10 of the process's 30 anonymous searches for that hour:

```sh
/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/venv-3.12.14/bin/python \
  /Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/validation/v2-deploy/p95.py \
  https://music-discovery-atlas.onrender.com /Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/validation/v2-deploy/live-production/p95.json 10
```

Then read the service's memory and the deploy's start-to-healthy time from the Render dashboard (section 6).

### 3.4 Rollback

This clone has no local `main` branch; production is `origin/main`. So every rollback works in a throwaway worktree of `origin/main` and pushes `HEAD:main`, which is a fast-forward (security review A1). The sequences below were run on 2026-10-06 up to the push, against local commits standing in for `origin/main`. Each left exactly the intended tree (`validation/v2-integrate/final/receipts/rollback-sim/`).

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05
cd $B/repo && git fetch origin
R=rollback-$(date +%Y%m%d-%H%M)
git worktree add --detach .worktrees/$R origin/main     # main as deployed
cd .worktrees/$R
```

- **Stage 2 only:**

  ```sh
  git revert --no-edit <activation commit>    # restores the v1 selection, page data, pins and allowlists
  git push origin HEAD:main
  ```

  Render rebuilds the v1 image, including the 1.95 GB hydration (about 4 minutes). The v2 deploy keeps serving until the new one is healthy.
- **Stage 1, and stage 2 with it if it landed:**
  - Stage 1 carries the rights fix. Returning to `35cec9e` puts the eight quarantined recordings back on the live site, so do it only on the user's decision, as the stop-gap before a rights-only backport.
  - `git revert 35cec9e..<stage 1 head>` does not work: the range holds three merge commits, and git refuses to revert a merge without `-m`. Restore the tree instead, as a new commit on top of `origin/main`:

    ```sh
    git read-tree -u --reset 35cec9e    # the tree production ran before stage 1; also removes a v2 release
    git commit -m "Return main to the tree of 35cec9e"
    git push origin HEAD:main
    ```

  - `git push origin 35cec9e:main` is refused: it is not a fast-forward. Moving `main` back to `35cec9e` itself takes a force push, which GitHub accepts here because `main` has no branch protection: `git push --force-with-lease=main:<stage 1 head> origin 35cec9e:main`. It drops the stage commits from `main`'s history; they stay on `platform-v2`. The forward commit above keeps the history and is the default. Both were run against a local bare copy standing in for GitHub, together with the stage 1 push itself (`validation/v2-integrate/final/receipts/rollback-sim/push-sim.txt`).
  - **The rights-only backport** onto `35cec9e` would carry:
    - the quarantine list;
    - `corpus-releases/fma2000`;
    - the page data, credits and audio plan;
    - the two plan pins in `scripts/hydrate_corpus_audio.py`;
    - the package and web pins.

    It is not prepared or tested.
  - The commits of `platform-v2`, `v2-deploy` and `v2-scale-ui` change no release data; the `rights-fix-2000` commits do.
- **A failed build** changes nothing live. Render keeps the most recent successful deploy, and the installer, the hydrator and the server's start-up checks all fail closed.
- **For an emergency only,** the Render dashboard's rollback to the previous deploy is immediate. Follow it at once with the matching push above, so that `main` matches what runs; otherwise the next push to `main` redeploys the bad head. After stage 1, the previous deploy is the 2,000-track one, so a dashboard rollback past stage 1 brings the quarantined recordings back until the backport is live.
- Afterwards: `cd $B/repo && git worktree remove .worktrees/$R`.

## 4. Object-store mode (for the record; not used for fma2000)

**Selection:**

```json
"source": {"kind": "object-store", "origin": "https://<bucket custom domain>", "pathPrefix": "/music-atlas/releases/"}
```

**Activation.** Run `scripts/activate_release_v2.py ... --source object-store --origin ... --prefix ...`. It commits no release files; the release directory must not exist in Git. `--check --release-dir <local copy>` verifies the tree.

**Upload.** Upload `release.json` under the key `<prefix><manifestSha256>`, and each asset under `<prefix><its sha256>`. Serve them over HTTPS with no redirect and no login. The write key stays on the operator's machine.

**The install at build** (`install_release_v2.py`):

- **Transfer.** A supervised worker downloads into `corpus-releases/.v2-install-<name>/`, a dot directory that no selection can name, under a 3,600 s absolute deadline.
- **Byte budget.** Twice the release, retries included.
- **Strict responses.** It requires exact status, length and identity encoding, and refuses redirects.
- **Resume.** A partial object resumes with a Range request.
- **Verification.** The parent re-hashes every object, assembles the release in the stage and runs the bundled-mode verification.
- **Publication.** Only then does one `rename` publish `corpus-releases/<name>`. An existing directory is verified and never replaced.

**Tests.** They run against an in-memory origin only.

- **Covered:** a clean install; resuming after an interruption and after a stopped run; a server that ignores Range; wrong bytes, redirects, refusals and bad headers on the 200 path; the byte budget; an existing tampered directory.
- **Not covered** (security review T-1 to T-5):
  - The supervised worker never runs: every test installs unsupervised.
  - The deadline test expires before the first request.
  - The kill test kills a stand-in process.
  - Faults on the 206 resume path are untested.

**Before the first bucket.** The security review of 2026-10-06 lists work for object-store and remote-audio mode, which neither stage uses. It is all open:

- **C1:** only network errors should be retried, and the cause should be printed.
- **C2:** immutable objects and a scheduled re-verification of remote audio.
- **C3:** hash staged objects before use.
- **C4:** keep a valid partial when the deadline expires.
- **C5:** a strict origin grammar.
- **C6:** the verifier's absolute deadline and its publish race. Its cookies are fixed in `09117d2`.
- **C7:** see section 7.
- **C8:** a stage lock.
- **D1 to D5:** hardening.
- **Tests:** the test gaps above.

**Remote audio.** After uploading `<sha256>.mp3` objects (`Content-Type: audio/mpeg`, immutable caching), run:

```sh
venv-3.12.14/bin/python scripts/verify_audio_publication.py --release-dir <release> --expected-manifest-sha256 <sha> \
  --origin https://<audio custom domain> --prefix /<pack>/ --output audio-delivery.remote.json --report verify-report.json
```

**What the verifier checks.** It fetches every object anonymously:

- 200 with no redirect;
- `audio/mpeg`;
- identity encoding;
- exact length and SHA-256;
- a 206 Range check on a sample;
- the rights row: approved, public playback approved with attribution, and an admitted CC licence with its exact URL.

**When it writes the manifest.** Only after every row passes, and only after the server's parser accepts the result.

**Turning remote previews on is a separate, later change.** Nothing on this branch does it. It needs four things:

- **The page data.** Activate with `--audio-mode remote --audio-origin <origin> --audio-prefix <prefix>`. The page accepts only a delivery summary whose mode, origin and prefix match the ones it pins.
- **The manifest in the image.** Commit the verified manifest, for example `audio-delivery.remote.json`, with a Dockerfile `COPY` and a `.dockerignore` allowance. The image copies only named files.
- **The service environment.** Set `MUSIC_AUDIO_MANIFEST_PATH=/app/audio-delivery.remote.json` and remove `MUSIC_AUDIO_PACK_DIR`. Remote mode refuses a local directory, so the server would not start.
- **The result.** The gateway then adds the origin to `media-src`, and the server never fetches or serves the objects.

**Do not commit the other remote manifest.** `make_audio_delivery_v2.py --mode remote` writes a manifest without fetching anything.

## 5. Local proof (pre-deploy evidence)

**Setup.**

- **The external drive.** "My Book" disconnected at about 09:20 local time, after these runs. That unmounted `/Volumes/music20k-apfs` and the staged roots on it; the receipts are on the internal disk. The activation no longer needs that volume, because the release regenerates from Git with the same digest (section 3.2).
- **Machine and branch.** This Mac (M3 Max, shared, load 15–90 with swap nearly full), branch `v2-deploy` at `62f6744`. The later commits add the live check, the verifier, two test fixes and docs; none of them changes the image.
- **No Docker or downloads.** The root was built the way the image build would be, by `validation/v2-deploy/build_root.sh`:
  - `git archive`, then the stage 2 activation command and `--check`;
  - then the Dockerfile RUN steps in order:
    - the installer (bundled, verified in 0.34 s);
    - the interpreter check;
    - `fetch_model.py` (the pinned model was copied in first, so it only verified it);
    - `hydrate_corpus_audio.py --verify-plan` through the real entrypoint.
- **Audio.** The audio was published by the same v2 hydration functions from the verified local MP3 cache (`audio2000`) instead of the official ranges. Every file was hashed twice, and the server's parser accepted the manifest.
- **Server start.** `validation/v2-deploy/start_server.sh` starts `server/hosting.py` as the image's CMD would:
  - the Dockerfile ENV, the root as working directory;
  - anonymous preview, audio on from the hydrated paths;
  - behind the TLS terminator `validation/server/tls_proxy.py`.

| Check | Result |
|---|---|
| `tests/live_check_v2.mjs` (the post-deploy command) | **26/26** |
| `validation/atlas/live_check_atlas.mjs`, unmodified apart from origin, output folder and accepting the local certificate | **17/17** |
| `platform_v2/tools/browser_v2_check.mjs` (browse, paging, lookup, refinement, neighbors, playback, animation; desktop and mobile) | **28/28** |
| Stage 1: v1 selection on this branch, same steps (installer no-op, v1 plan), unmodified production check | **17/17**, `bindingStatus` `verified-corpus-release` |
| Suites at the branch head, v1 tree | Python 149 OK, Node 82 pass, both browser fixtures pass |
| Suites in a tree activated at the branch head with the real release (`806b19ed…`, regenerated from Git) | Python 149 OK, Node 82 pass, both browser fixtures pass, `--check` ok, 36 changes, page manifest `7e837997…` as in the proof root |

**Measurements** (fma2000-v2, bundled, `validation/v2-deploy/receipts/proof-summary.json`):

| | Run 1 | Run 2 | Run 3 | Stage 1 (v1, same code) |
|---|---|---|---|---|
| Spawn to `/healthz` 200 (s) | 5.66 | 5.40 | 1.54 | 9.97 |
| CPU at readiness (s) | 1.91 | 1.74 | 1.49 | 4.66 |
| RSS at readiness (MiB) | 342.8 | 336.8 | 336.2 | 337.8 |
| Peak RSS (MiB) | 351.3 (after 28 searches and the browser runs) | 342.2 | 341.5 | 370.2 |
| Load average at readiness | 31 | 15 | 16 | 40 |

**Search latency.** The test ran 20 anonymous searches through the TLS terminator, in the page's request shape: k 16, ef 32, trace on, traceLimit 128. They were paced for the 6-per-minute limit. Results (nearest-rank):

- **Round trip:** p50 28.0 ms and p95 32.0 ms (max 41.8 ms).
- **Server compute:** p50 15.0 ms and p95 18.6 ms.

The disk cache was warm (the release and audio had just been written), and this Mac is much faster per core than Render's shared CPU.

### 5.1 Re-run on the rebuilt release (rights-fix-2000, 2026-10-06)

**How it was run.** The same proof, with `validation/rights-fix-2000/build_root.sh`: a copy of `build_root.sh` with the release, digest and root as parameters, because the original names the offline volume and the superseded `806b19ed…`.

- **Roots.** Built from `rights-fix-2000` at `d7cf8d2`. The later commits change tests and documents, plus the corpus-releases README and its package pin; the server's `verify_package()` passes on fresh v1 and activated exports of the final head.
- **Audio.** Published from the verified local cache (`audio2000`) by the image build's own hydration functions.
- **Servers.** Started with `start_server.sh` (copied so its runs land in `validation/rights-fix-2000/runs/`), anonymous mode, behind the TLS terminator.
- **Machine.** Heavily loaded: load average 94 to 950 during these runs.

| Check | Result |
|---|---|
| Stage 1 root (v1 selection = release `32015637…`): installer, `--verify-plan`, v1 hydration from the cache | installer no-op; plan verified, 1,992 entries, 1,945,985,122 range bytes; 1,992 files published |
| Stage 1 server: `/v1/manifest` | `catalogCount` 1992, `verified-corpus-release`, `32015637…` |
| Stage 1 server: the eight quarantined previews and fma:30702 | 404 each; a kept row (`/audio/001383.mp3`) answers 206 |
| Stage 1: `live_check_atlas.mjs` (local copy) | **17/17** |
| v2 root: activation (`279cd21b…`), `--check`, installer, interpreter, model, `--verify-plan`, v2 hydration from the cache | all pass; 36 changes; bundled release verified in 0.17 s; 1,992 files hashed and accepted by the server's parser |
| v2: `tests/live_check_v2.mjs` (the post-deploy command; it now also requests every quarantined preview) | **26/26** |
| v2: `live_check_atlas.mjs` (local copy) | **17/17** |
| v2: `platform_v2/tools/browser_v2_check.mjs` (EXPECT_COUNT 1992: 166 pages, a full last page, lookup, refinement, neighbors, playback, animation) | **28/28** |
| Suites at the branch head, v1 tree | Python 158 OK, Node 82 pass, both browser fixtures pass |
| Suites in a tree activated at the branch head (`279cd21b…`, page manifest `ca2b0495…`) | Python 158 OK, Node 82 pass, both browser fixtures pass, `--check` ok |
| Reproducibility (`validation/rights-fix-2000/repro.sh`): the scripted removal re-run in a fresh export | every release, page, credits and plan file byte-identical; pins `--check` finds nothing to change |

**Measurements**, single runs on the loaded machine:

| | Stage 1 (v1, 1,992) | v2 (1,992) |
|---|---|---|
| Spawn to `/healthz` 200 (s) | 6.6 | 2.1 |
| CPU at readiness (s) | 4.1 | 1.7 |
| RSS at readiness (MiB) | 409.5 | 340.6 |
| Peak RSS (MiB) | 415.0 | 349.6 |
| Load average at readiness | 94 | 234 |

**Two earlier failures in an activated tree, both resolved.**

- `tests/test_corpus_rebuild_tools.py` did not import there; it is fixed in `7693d67`.
- At a load average near 900, `test_timeout_keeps_slot_until_fixture_really_exits` (a 20 ms request deadline) answered 504, and `browser_collection_v2.mjs` timed out waiting for the page. Both passed on the re-run above.

The latency probe (`p95.py`) was not re-run: at these load averages its timings would say nothing about Render.

### 5.2 Re-run on the merged branch (v2-integrate, 2026-10-06)

**How it was run.**

- **Tools.** `validation/v2-integrate/final/tools/` holds copies of the 5.1 tools with three changes:
  - roots on the external volume, because the internal disk was full;
  - the merged commit and the 2.1 release by default;
  - a plain copy of the model.
- **Code.** The final code head is `09117d2`. The later commits change only documents, which neither the image nor the pins contain.
- **Servers.** One at a time, behind the TLS terminator, in anonymous mode, with audio from the hydrated paths.
- **Machine.** Heavily loaded: load average 20 to 450, swap nearly full. Every figure below names its load.

| Check | Result |
|---|---|
| Merges | `558d6f7` (v2-deploy) and `6bfae62` (rights-fix-2000) have exactly their branch's tree. `5376b00` (v2-scale-ui) resolved eight conflicted files, each keeping both sides |
| Conversion with the merged converter (server runtime) | 2.1 `b537a7ac…` in 12.0 s and 156 MB, byte-identical to an independent earlier run. Parity oracle 66 queries and lookup oracle 148 cases, 0 mismatches each. `--no-lookup-index` gives `279cd21b…`, byte-identical to 5.1 |
| Suites at `09117d2`, v1 tree | Python 170 OK, Node 92 pass, both browser fixtures pass |
| Suites at `09117d2`, in a tree activated with `b537a7ac…` | `--check` ok, lookup index verified. 38 changes, the 16 `git status` lines of 3.2, page manifest `cebbefa8…`. Python 170 OK, Node 92 pass, both browser fixtures pass |
| Found only by the merge, fixed in `fedde26` | In an activated tree, two rights-fix tests and the public link check failed (Python 2, Node 1). The credits page there becomes the small page that links to `/collection/credits` |
| Stage 1 root at `09117d2` | Installer no-op. Plan verified: 1,992 entries, 1,945,985,122 range bytes. 1,992 files published |
| Stage 1 server | API checks 5/5: manifest `1992 32015637…`; the eight listed previews and fma:30702 answer 404; an exact range answers 206; the credits page has no listed article. The liveness probes of 3.1 give the new build's values. `live_check_atlas.mjs` **17/17** |
| v2 root at `09117d2` | Activation with the B1 pre-check: 38 changes, `--check` ok. The installer verified the bundled 2.1 release, including FTS5's integrity check, in 0.16 s. `--verify-plan` bound the plan to `b537a7ac…`. 1,992 files hashed and accepted |
| v2 server | API checks 5/5. `tests/live_check_v2.mjs` **26/26**. `live_check_atlas.mjs` **17/17**. `browser_v2_check.mjs` **28/28**. `tests/browser_credits_v2.mjs` **12 + 12** |
| Collection page budget (B2), on a fresh v2 process | 300 admitted; the 301st refused with 429 and `Retry-After` 60. 0.38 CPU-s for the 301 requests. A search right after answered 200 |
| 2.0 fallback at `09117d2` | Activation of `279cd21b…`, `--check` and the installer all pass; page manifest `f6b89f93…` |
| Reproducibility (`repro.sh` of 5.1, at `fedde26`; the builders have not changed since) | Every release, page, credits and plan file byte-identical; pins `--check` finds nothing to change |
| Rollback commands (3.4) | Each sequence leaves the intended tree. `git revert 35cec9e..<head>` is refused at the first merge |

**Measurements.** Single runs. RSS excludes compressed pages, so with swap nearly full it is a lower bound.

| | Stage 1 (v1, 1,992) | v2 (2.1, 1,992) |
|---|---|---|
| Spawn to `/healthz` 200 (s) | 29.5 at load 50. Also 42.6 at 100; 69.0 and 152.9 at 380–450 | 4.9 at load 64, 7.7 at 33. Also 10.6 at 201, 24.1 at 149 |
| CPU at readiness (s) | 4.0 (4.3 to 9.1 at higher load) | 1.7 and 1.8 (2.2 and 3.5 at higher load) |
| RSS at readiness / peak (MiB) | 368 / 375 (also 374 / 381) | 338 / 347 (also 328 / 337, 339 / 347, 286 / 298) |
| Search, 10 anonymous, round trip p50 / p95 (ms) | 70.2 / 96.2 at load 44–98 | 27.2 / 49.7 at 46–78; 30.4 / 47.4 at 94–149 |
| Server compute p50 / p95 (ms) | 59.8 / 74.9 | 15.0 / 33.5; 16.9 / 30.4 |

Two probes were repeated at lower load; the first attempts' receipts are kept:

- At load 300 to 450 the v1 probe measured 572 / 3,316 ms.
- In one v2 attempt at load 110–240, a search hit the 8 s anonymous deadline and answered 504.

**Frame rate at 4× CPU.** Measured with `platform_v2/scale_ui/tools/measure_ui.mjs`, one pass per cell, the v2 page proxied to the real v2 server. "Replays" are three recorded-example animations; "explore" is zoom, drag-pan and zoom again.

| Page | Viewport | Replays (fps) | Explore (fps) | 1-min load |
|---|---|---|---|---|
| v2 (2.1) | desktop | 56.2, 59.2, 57.3; repeat 59.0, 58.5, 57.4 | 49.0 (one 850 ms stall); repeat 57.6 | 241; 124 |
| v2 (2.1) | mobile | 45.4, 48.6, 43.5; repeat 11.3 (one 5.2 s stall), 51.3, 58.8 | 31.7 (one 3.8 s stall); repeat 60.0 | 197; 122 |
| v1 (stage 1) | desktop | 47.5, 55.1, 39.2 | 59.4 | 211 |
| v1 (stage 1) | mobile | 51.0, 39.4, 51.6 | 55.7 | 147 |

The stalls follow the machine's swap, not the page. They move between phases and runs; the p95 frame stayed at 16.8 ms in the stalled runs, and the repeats reach 57–60 fps. On v2 the page downloads 0.69 MB of data; on v1, 10.3 MB. At 4× CPU the first map frame came 1.1–2.4 s after navigation on v2 and 1.6–3.2 s on v1.

## 6. Acceptance on Render (1 CPU, 2 GB)

The local figures above are evidence, not acceptance. Record these after stage 2:

| Measure | How | Accept if |
|---|---|---|
| Live check | `tests/live_check_v2.mjs` | 26/26 |
| Paged credits | `tests/browser_credits_v2.mjs` (section 3.3) | 12 + 12 checks pass |
| Hosted playback | in the live check: a desktop and a mobile preview advance past 1.2 s, `/audio/` answers 206, exact range on the first row's file (`/audio/001383.mp3`) | all pass |
| Memory | Render metrics, the service's memory after the live check and the latency probe | under 700 MiB (local peak 351 MiB, 347 MiB on the merged branch; the v1 baseline measured 370–381 MiB locally) |
| Cold start | Render deploy log: container start to healthy; then the dashboard's restart of the instance | healthy within Render's health-check window; record the seconds (local 1.5–5.7 s; v1 hashed 2 GB of audio at start) |
| Search latency | `p95.py` with 10 searches | server compute p95 under 150 ms; record round trip |
| Startup CPU against the anonymous budget | `PreviewBudget` counts from process start | note it: v2 used 1.5–1.9 CPU-s locally, v1 4.7 |

If memory, start or latency miss, roll back stage 2 (section 3.4) and keep the measurements.

## 7. Still undecided

1. **An audio host for anything larger than fma2000.**
   - **What exists:** remote mode, the content-addressed key scheme, the publication verifier and its tests. There is no bucket, custom domain, account or budget.
   - **The recommendation:** Cloudflare R2 behind a custom domain (`corpus200k/SOURCES_AND_PLAN.md` 6.7). It needs the user's account and payment decision.
   - **Why:** the build-time pack stops working at about 20K recordings (21 GB against the 16 GB build disk).
2. **Rights for fma5777.** Its 3,777 added rows rest on an automated screen, not a human review. The research names further fma5777 rows to remove or hold (`docs/RIGHTS_QUARANTINE.md` section 4); they go on the quarantine list before any activation. fma5777-v2 is converted and measured, and the code would admit it. Activating it is the user's decision after review. The pinned hydration plan covers only the fma2000 release, so the build would publish no audio. In the live service environment (previews on, pointing at the verified manifest) the server would then refuse to start (security review C7). Such a release needs previews turned off in the environment, or a verified remote pack.
3. **Hosting the release itself at scale.**
   - At 200K the release is about 1.2–2 GB, so it must use object-store mode. That needs the same bucket decision as the audio.
   - A 200K graph also needs a reviewed connectivity-repair step in the builder (`PLATFORM_V2.md` 9.4).
4. **Smaller follow-ups, each its own review.** None changes this deploy:
   - Start `PreviewBudget` at readiness instead of at process start.
   - Charge the anonymous budget only for search CPU (item 6).
   - Give the v2 hydration worker its deadline less five minutes, so the parent's verification of 2 GB keeps time to publish; v1 has the same coupling (security review B4).
   - Load `collection-api.mjs` and `map-lod.mjs` lazily (A3, section 3.1).
   - Optionally expose the deployed commit (Render's `RENDER_GIT_COMMIT`) in `/v1/manifest`, so the stage 1 probe can name it.
   - Drop the v1 release history from the image (a lean package): `verify_package()` hashes about 44 MB of old releases at every start.
   - Move the release and audio install steps before `COPY server/ web/`, so code deploys reuse the cached 1.95 GB hydration layer.
   - Pin `PYTHON_BASE` to a digest.
5. **Two things only the dashboard shows.** The service's audio and anonymous-mode environment variables (section 2), and whether the Starter build allowance covers another hydration per deploy.
6. **The anonymous search budget counts all process CPU** (security review B2).
   - **How it bites.** `PreviewBudget` allows 30 CPU-seconds an hour and counts every route's CPU. Once that is spent, every visitor's search answers 429 for the rest of the hour. v1 has the same weakness through static files and audio; v2 adds the `/collection/` routes.
   - **What is fixed.** Since `b34f0ab` every collection route has a per-minute cap: pages and lookups 300 (new), credit pages 300, neighbors 60, tiles 2,400, links 600.
   - **Measured on fma2000, through the TLS terminator.** 300 page requests of the review's worst case (`?preview=1&q=a`) cost 0.38 CPU-s. The 301st was refused (429, `Retry-After` 60), and a search right after still answered 200. So pages alone can no longer drain the hour.
   - **What remains.** With every route at its cap, a client could still spend about 4 CPU-s a minute and drain the budget in about 7 minutes. This estimate uses handler CPU measured in process plus about 0.5 ms of framework overhead per request.
   - **The proper fix.** Charge the budget only for search CPU (thread CPU around encode and search). It changes v1 too and needs its own review.
   - **The decision.** Going ahead with stage 2 before that fix accepts the risk at demo traffic.

## 8. Evidence

- **Receipts:** `validation/v2-deploy/receipts/`:
  - `build-context.json`;
  - `activate.out` and `activate-check.out`;
  - `install.out`;
  - `hydrate-verify-plan` (`hydrate-plan.out`) and `hydrate-cache.out`;
  - `p95-anonymous.json`;
  - `proof-summary.json`;
  - `stage1/`.
- **Server runs:** `validation/v2-deploy/runs/proof-{1,2,3}` and `stage1-v1`. Each holds `pids.json`, `memory.json`, `rss.log`, the server logs and `env.txt`.
- **Browser checks:**
  - `validation/v2-deploy/live-local/` (26/26);
  - `live-atlas-original/` and `live-atlas-stage1/` (17/17 each);
  - `browser-v2-check/` (28/28).
- **Tools:** `validation/v2-deploy/build_root.sh`, `hydrate_from_cache.py`, `start_server.sh`, `stop_server.sh`, `p95.py` and `live_check_atlas_local.mjs`.
- **Rights-fix re-run (5.1):** `validation/rights-fix-2000/`:
  - `rebuild.sh` and `receipts/rebuild/` (the rebuild, the identity check against the reviewed release);
  - `repro.sh` and `receipts/repro/`;
  - `receipts/v2/convert.json`;
  - `build_root.sh`, `hydrate_v1_from_cache.py` and `receipts/build-root-v1/`, `receipts/build-root-v2/`;
  - `runs/stage1-v1/`, `runs/proof-v2-1/`;
  - `live/` (live checks and screenshots);
  - `logs/` (every suite run);
  - `receipts/quarantined-audio-local-check.json`.
- **Integration re-run (5.2):** `validation/INTEGRATION_RECEIPT_V2.md` and `validation/v2-integrate/final/`:
  - `tools/` (the copies of the proof scripts it ran, with what changed);
  - `receipts/` (conversions, activations, roots, reproduction, the 2.0 fallback, rollback simulations, production's values today, the image's SQLite build flags);
  - `suites/` (every suite run);
  - `runs/` and `live/` (servers, live checks, budgets and screenshots);
  - `fps/` (the 4× CPU frame-rate passes).
