# Deploy plan: release format v2 on fma2000

Status, 2026-10-06: ready for the manager's decision. Nothing has been pushed or deployed, and production (`main` = `35cec9e`, the Atlas page on the v1 fma2000 release) is unchanged. The work is on the local branch `v2-deploy`, which builds on `platform-v2` (see `docs/PLATFORM_V2.md`) and fast-forwards production `main`.

The deploy serves the same 2,000 recordings with the same rankings, scores and search traces. What changes: the page downloads 0.83 MB instead of 10.3 MB and reads the catalog page by page; the server memory-maps the release instead of parsing it; and audio is hashed on first play instead of all 2 GB at every start.

## 1. Decisions to take first

1. **Where the release files come from.** There are two install modes, set by the `source` block of `active-corpus.json`:
   - **bundled** (the plan below): the release directory is committed under `corpus-releases/fma2000-v2/` and copied into the image like today's v1 releases. That commits 8 files, 24.3 MB: `catalog.sqlite` 9.7 MB, `evidence.sqlite` 9.2 MB, `vectors.f32` 4.1 MB, `examples.json` 1.2 MB and four small files. This is the same order as `corpus-releases/fma2000/`, which is already in Git. This branch commits no release data; the activation commit in step 3.2 does.
   - **object-store**: Git holds only the selection, and the image build fetches each file by its SHA-256 from one pinned HTTPS origin. This needs a bucket and an upload, and none exists. It is the mode for anything much larger than fma2000 (section 7).
2. **One push or two.** The plan uses two: the code first, with the v1 selection unchanged, then the activation. Each push is its own rollback point. Pushing both at once also works, but a failure would then not show which half caused it.
3. **The corpus.** fma2000 only. fma5777-v2 exists on the external volume, but its 3,777 added rows rest on an automated rights screen (section 7).

## 2. What is on the branch

`v2-deploy` = `platform-v2` (8 commits, v1 parity) plus these commits:

| Commit | What it adds |
|---|---|
| `d98c3e9` | `scripts/install_release_v2.py` and a Docker step that runs it before any download; the optional `source` block of a v2 selection |
| `fa192a4` | `scripts/hydrate_release_v2.py`; `scripts/hydrate_corpus_audio.py` hands a pinned v2 selection to it instead of failing closed |
| `c1ea00b` | The v1-page tests read the v1 data wherever the tree keeps it, so an activated tree passes every suite |
| `62f6744` | `scripts/activate_release_v2.py`: the activation step and its `--check` |
| `b9c7afe` | `tests/live_check_v2.mjs`: the post-deploy live check |
| `72231ac` | `scripts/verify_audio_publication.py`: the remote-audio publication verifier |
| `ce55c4a` | The activation and gateway tests no longer depend on the tree or volume they run in |
| (the last commit) | This document, and three status lines in `docs/PLATFORM_V2.md` |

**v1 behaviour is unchanged.** For a v1, disabled or absent selection, the new Docker step prints `{"installed": false, ...}` and exits 0. The hydration entrypoint runs its v1 code exactly as before; the new dispatch only takes a pinned, parseable `schemaVersion: 2` file. The v1 server path is untouched. Stage 1 below was proved locally with the unmodified production live check (section 5).

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
     - `audio-preview/`, the 2,000 MP3s;
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

### 3.1 Stage 1: the code, with the v1 selection

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05
cd $B/repo/.worktrees/v2-deploy                                   # the branch's worktree
git fetch origin
git merge-base --is-ancestor origin/main v2-deploy && echo fast-forward   # must print fast-forward
git status --short                                                # must be empty
$B/venv/bin/python -m unittest discover -s tests -p 'test_*.py'   # expect 148 OK
node --test tests/web_*.test.mjs                                  # expect 82 pass
git push origin v2-deploy:main
```

**What Render does.** It builds the image with the v1 selection, so the new installer step is a no-op. Expect the usual build-to-live time: the last two deploys were live 230 s and 244 s after their push. Those times include the 1.95 GB hydration, which runs after `COPY web/` and so repeats on every deploy.

**Check after the deploy:**

```sh
cd /Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/validation/atlas
NODE_PATH=../../tooling/node_modules node live_check_atlas.mjs        # expect LIVE_ATLAS 17/17
curl -s https://music-discovery-atlas.onrender.com/v1/manifest | python3 -c "import json,sys; m=json.load(sys.stdin); print(m['bindingStatus'], m.get('releaseFormat'))"
# expect: verified-corpus-release None
```

### 3.2 Stage 2: activate fma2000-v2 (bundled)

Work in a fresh worktree on `main` after stage 1. The main checkout stays on its own branch, which other work uses. `/Volumes/music20k-apfs` must be mounted, because `fma2000-v2` exists only there.

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05
cd $B/repo && git fetch origin
git worktree add .worktrees/activate-fma2000-v2 -b activate-fma2000-v2 origin/main   # main = the stage 1 head
cd .worktrees/activate-fma2000-v2
$B/venv-3.12.14/bin/python scripts/activate_release_v2.py \
  --release-dir /Volumes/music20k-apfs/releases-v2/fma2000-v2 \
  --expected-manifest-sha256 806b19ed9a6b2f5766f3c0a7316db20466dbff1537b3eb24792c11518266e16c \
  --name fma2000-v2
$B/venv-3.12.14/bin/python scripts/activate_release_v2.py --check   # expect "ok": true
$B/venv/bin/python -m unittest discover -s tests -p 'test_*.py'      # expect 148 OK
node --test tests/web_*.test.mjs                                      # expect 82 pass
NODE_PATH=$B/tooling/node_modules node tests/browser_search_ui.mjs       # v1 page fixture: passes
NODE_PATH=$B/tooling/node_modules node tests/browser_collection_v2.mjs   # v2 page fixture: passes
git status --short        # exactly the files listed below
git add -A && git commit -m "Activate release format v2 on fma2000 (bundled)"
git push origin activate-fma2000-v2:main
```

**Use the server runtime for the activation.** The page data is rebuilt deterministically: the same inputs give byte-identical files. Running it with `venv-3.12.14` (CPython 3.12.14, numpy 2.3.5) reproduces the files that were tested.

**Files the activation changes** (36 entries, printed by the script):

- **Added:** `corpus-releases/fma2000-v2/`, 8 files: `release.json`, `catalog.sqlite`, `evidence.sqlite`, `vectors.f32`, `graph.bin`, `layout.f32`, `graph-manifest.json`, `examples.json`.
- **`active-corpus.json`:** becomes the selection below.
- **Rewritten page data:** `web/search-studio/data/manifest.json`, `layout.json` and `examples.json`; `web/search-studio/src/studio-release.mjs` (the page's manifest pin).
- **Removed page data:** `web/search-studio/data/catalog.json`, `vectors.f32`, `index.json`, `ids.json`, `artist-records.json`. A v2 server refuses a page that ships them.
- **Re-pinned manifests:**
  - `web-manifest.json`: the removed rows are dropped and the changed rows refreshed.
  - `package-manifest.json`: refreshed rows for `active-corpus.json`, `web-manifest.json` and the four page files; the removed rows are dropped.
- **Allowlists:** `.gitignore` and `.dockerignore` each gain the release directory and its 8 files. Both lists are default-deny.

The selection the activation writes:

```json
{
  "schemaVersion": 2,
  "enabled": true,
  "format": "music-corpus-release-v2",
  "directory": "corpus-releases/fma2000-v2",
  "manifestSha256": "806b19ed9a6b2f5766f3c0a7316db20466dbff1537b3eb24792c11518266e16c",
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
node tests/live_check_v2.mjs                     # expect LIVE_CHECK_V2 26/26
```

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
  - the delivery summary: local, 2,000 of 2,000 available;
  - server-side collection pages and neighbors;
  - an exact audio range;
  - the excluded `fma:30702` and the whole-catalog files return 404;
  - on each viewport, the page downloads only the three pinned v2 data files.
- **Search budget.** The run uses 2 live searches and 1 neighbor read.

**Optional latency probe.** It uses 10 of the process's 30 anonymous searches for that hour:

```sh
/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/venv-3.12.14/bin/python \
  /Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/validation/v2-deploy/p95.py \
  https://music-discovery-atlas.onrender.com /Users/yipengandrewwang/SOP_2027/music_app_2026-10-05/validation/v2-deploy/live-production/p95.json 10
```

Then read the service's memory and the deploy's start-to-healthy time from the Render dashboard (section 6).

### 3.4 Rollback

- **Stage 2:**
  - Run `git revert --no-edit <activation commit> && git push origin main`.
  - The revert restores the v1 selection, page data, manifests and allowlists, and removes the release directory.
  - Render rebuilds the v1 image, including the 1.95 GB hydration (about 4 minutes). The v2 deploy keeps serving until the new one is healthy.
- **Stage 1:**
  - Run `git revert --no-edit 35cec9e..<stage 1 head> && git push origin main`. This reverts the 16 commits of `platform-v2` and `v2-deploy`; none of them changes data.
- **A failed build** changes nothing live. Render keeps the most recent successful deploy, and the installer, the hydrator and the server's start-up checks all fail closed.
- **For an emergency only,** the Render dashboard's rollback to the previous deploy is immediate. Follow it with the revert, so that `main` matches what runs.

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

**Tests.** They cover a clean install, resuming after an interruption and after a stopped run, a server that ignores Range, wrong bytes, redirects, refusals, bad headers, the byte budget, the deadline, the supervisor kill and an existing tampered directory. They use only an in-memory origin.

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

- **The external drive.** "My Book" disconnected at about 09:20 local time, after these runs. That unmounted `/Volumes/music20k-apfs`, which holds the converted releases and the staged roots; the receipts are on the internal disk. The activation in section 3.2 needs that volume mounted again: `fma2000-v2` exists only there, and the activation re-verifies its digest.
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
| Suites at the branch head, v1 tree | Python 148 OK, Node 82 pass, both browser fixtures pass |
| Suites at the branch head in an activated tree | Python 148 OK, Node 82 pass, both browser fixtures pass. The external drive had disconnected by then, so this run activated the test suite's own conversion of fma2000 (`9f0f1d58…`, same logical release, other SQLite build). With the real `806b19ed…` release: 140 + 82 at `c1ea00b`; at `72231ac`, 145 of 148 (three activation tests failed on a hard link across volumes, fixed in `ce55c4a` and then passing in that tree) |

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

## 6. Acceptance on Render (1 CPU, 2 GB)

The local figures above are evidence, not acceptance. Record these after stage 2:

| Measure | How | Accept if |
|---|---|---|
| Live check | `tests/live_check_v2.mjs` | 26/26 |
| Hosted playback | in the live check: a desktop and a mobile preview advance past 1.2 s, `/audio/` answers 206, exact range on `/audio/001382.mp3` | all pass |
| Memory | Render metrics, the service's memory after the live check and the latency probe | under 700 MiB (local peak 351 MiB; the v1 baseline measured 370 MiB locally) |
| Cold start | Render deploy log: container start to healthy; then the dashboard's restart of the instance | healthy within Render's health-check window; record the seconds (local 1.5–5.7 s; v1 hashed 2 GB of audio at start) |
| Search latency | `p95.py` with 10 searches | server compute p95 under 150 ms; record round trip |
| Startup CPU against the anonymous budget | `PreviewBudget` counts from process start | note it: v2 used 1.5–1.9 CPU-s locally, v1 4.7 |

If memory, start or latency miss, roll back stage 2 (section 3.4) and keep the measurements.

## 7. Still undecided

1. **An audio host for anything larger than fma2000.**
   - **What exists:** remote mode, the content-addressed key scheme, the publication verifier and its tests. There is no bucket, custom domain, account or budget.
   - **The recommendation:** Cloudflare R2 behind a custom domain (`corpus200k/SOURCES_AND_PLAN.md` 6.7). It needs the user's account and payment decision.
   - **Why:** the build-time pack stops working at about 20K recordings (21 GB against the 16 GB build disk).
2. **Rights for fma5777.** Its 3,777 added rows rest on an automated screen, not a human review. fma5777-v2 is converted and measured, and the code would admit it. Activating it is the user's decision after review. The pinned hydration plan covers only the fma2000 release, so fma5777-v2 would run with previews unavailable until a remote pack is verified.
3. **Hosting the release itself at scale.**
   - At 200K the release is about 1.2–2 GB, so it must use object-store mode. That needs the same bucket decision as the audio.
   - A 200K graph also needs a reviewed connectivity-repair step in the builder (`PLATFORM_V2.md` 9.4).
4. **Smaller follow-ups, each its own review.** None changes this deploy:
   - Start `PreviewBudget` at readiness instead of at process start.
   - Add a per-minute budget, or the FTS5 prefilter, for `/collection/tracks` before larger catalogs.
   - Drop the v1 release history from the image (a lean package): `verify_package()` hashes about 44 MB of old releases at every start.
   - Move the release and audio install steps before `COPY server/ web/`, so code deploys reuse the cached 1.95 GB hydration layer.
   - Pin `PYTHON_BASE` to a digest.
5. **Two things only the dashboard shows.** The service's audio and anonymous-mode environment variables (section 2), and whether the Starter build allowance covers another hydration per deploy.

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
