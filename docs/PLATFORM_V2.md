# Platform v2: release format, server and page for 20K–200K recordings

Status, 2026-10-06: implemented on the local branch `platform-v2` and measured on this Mac. Not pushed or deployed. The live site and the repository default are unchanged: `active-corpus.json` still selects the v1 fma2000 release, and the Dockerfile and hydration are untouched.

This document makes the v2 sketch in `corpus200k/SOURCES_AND_PLAN.md` (section 6.4) concrete for this codebase. It records what was built, what was measured and what a deployment would still need.

## 1. Why v2

At 200K recordings the current design does not fit Render's 2 GB instance. The sourcing study measured the parts:

- **Vectors and graph:** 1,449 MiB, because v1 builds one Python object per vector component and link.
- **Catalog and rights:** about 2.9 GiB of parsed JSON.
- **Hard caps:** the validator stops at 10,000 tracks and 16 MB per JSON file; the web gateway stops at 20 MB per file and 30 MB in total.
- **Browser download:** the page downloads the whole catalog and vector file on every visit (10.3 MB at 2,000, about 89 MB at 20K).
- **Audio at startup:** the server re-hashes every MP3 (2 GB today, 6.1 GB at 5,777, about 21 GB at 20K).

v2 changes the storage and the API around the same data and the same algorithms. Rankings, scores and search traces stay exactly what v1 produces.

## 2. The release format

A v2 release is one directory. Its identity is the SHA-256 of `release.json`, supplied out of band by the reviewed selection, as in v1.

| File | Content | Verified |
|---|---|---|
| `release.json` | Pinned manifest: identities, asset pins, graph header, layout metadata, source provenance, build environment | digest equals the selection's `manifestSha256` |
| `catalog.sqlite` | `tracks` (small display and search columns), `records` (the exact catalog and rights row of each track, compact JSON), `meta` (headers, counts, ordered-ID digest, v1 source digests) | streamed SHA-256 at startup |
| `evidence.sqlite` | One blob per rights evidence file (`path`, `bytes`, `sha256`, `data`) | per row, when read, against the pin in the verified rights row |
| `vectors.f32` | Byte-identical to v1: N × 512 little-endian float32 | streamed SHA-256, then memory-mapped; unit rows re-checked (vectorised) |
| `graph.bin` | The HNSW links as int32 CSR: a 68-byte header, node→layer offsets, layer→neighbor offsets, neighbors, in stored order | streamed SHA-256, then memory-mapped; structure re-checked (vectorised) |
| `layout.f32` | N × 2 float32 map positions, exactly the v1 `layout.json` values | streamed SHA-256, then memory-mapped |
| `graph-manifest.json` | Byte-identical to v1 `manifest.json` (graph identity, the one native query profile) | streamed SHA-256; graph identity re-derived |
| `examples.json` | Byte-identical to v1 (recorded query vectors); used only by the web build | size at startup, SHA-256 when the web build reads it |

**Schema (`server/release_v2.py`, `CATALOG_SCHEMA`).** The large source rows sit in `records`, so a browse or lookup reads only the narrow `tracks` table. `tracks` holds:

- `row`, `id`, `title`, `artist`, `album`, `genre`, `license`, `artist_id`, `audio_bytes`, `audio_sha256`;
- three folded search columns, `fold_title`, `fold_title_artist` and `fold_text`, computed with the page's own fold (NFKD, combining marks removed, lower case).

`tracks(genre, row)` is indexed.

**Identities.** Logical identities stay the same across formats, so a converted release is the same catalog and graph:

- `catalogId`;
- `graphId`;
- `vectorsSha256`;
- `orderedIdsSha256`;
- `graphManifestSha256`.

The two artifact digests now name the files the v2 server actually verifies:

- `catalogSha256` is the digest of `catalog.sqlite`;
- `indexSha256` is the digest of `graph.bin`.

The v1 digests (release, catalog, rights, ids, index, layout, artist records, examples) are recorded under `source` in `release.json` and in `meta`.

**Selection and caps.** `active-corpus.json` with `schemaVersion: 2` selects a v2 release. It has these fields:

- `enabled: true`;
- `format: "music-corpus-release-v2"`;
- `directory`, as `corpus-releases/<name>` with no symlinks;
- `manifestSha256`;
- `limits`, which is optional.

A `schemaVersion: 1` file still goes through the unchanged v1 code, and the v1 code rejects a v2 file. Validator caps are per-release configuration:

| Limit (`limits` key) | Default | Ceiling |
|---|---|---|
| `maxTracks` | 10,000 | 1,000,000 |
| `catalogBytes` | 64 MB | 8 GB |
| `evidenceBytes` | 64 MB | 16 GB |
| `graphBytes` | 16 MB | 512 MB |
| `graphManifestBytes` | 16 MB | 64 MB |
| `examplesBytes` | 8 MB | 64 MB |

Vector and layout sizes are exact functions of the track count. The defaults admit fma2000 and fma5777 without configuration; anything larger has to be configured explicitly and reviewed.

## 3. Where each check runs

| When | What | Code |
|---|---|---|
| Conversion | The v1 source is validated with the unchanged v1 validator at explicit budgets, then the conversion is proved (see below) | `scripts/convert_release_v1_to_v2.py` |
| Offline verification | Loader checks, then every catalog, rights and evidence row against the v1 rules | `scripts/verify_release_v2.py` (`validate_rows`) |
| Startup | Manifest digest; streamed SHA-256 of every core asset (constant memory); vectorised checks of unit vectors, finite layout, CSR bounds, degree budgets, no self or duplicate links, entry level and layer-0 reachability; database identity, contiguous rows and the ordered-ID digest; graph identity against the pinned model pair and query profile | `server/release_v2.py` (`load_release_v2`) |
| First use | Evidence blobs (per row); local audio files (on first request, then whenever the file's identity changes) | `release_v2.evidence_for_row`, `collection_v2.AudioDeliveryV2` |

**Proofs run by the converter before publishing.** All of these passed for both releases (`platform_v2/receipts/convert-*.json`):

- the database rebuilds the v1 catalog, rights and artist objects exactly, and even the catalog JSON bytes;
- every evidence blob is byte-identical;
- the CSR decodes to the v1 index links;
- layout and vectors are unchanged;
- the server's own loader accepts the result;
- the row validator passes;
- the parity oracle (section 6) finds no difference.

**Determinism.** Converting twice gives byte-identical files. This needs the same SQLite version, so conversion uses the server runtime (CPython 3.12.14, SQLite 3.54.0) and records the versions in `build`.

**The row validator is a line-by-line mirror of `corpus_release.validate_rights`.** A test applies seven tampered rows to both validators and requires the same verdict and message, including the v1 quirk that re-raises a bad timestamp as "Invalid review timestamp".

**Startup does not re-run semantic validation.** It relies on the pinned digests of files that passed it at build time. That is the trade that removes per-row JSON parsing at startup. `verify_release_v2.py` re-runs the full check on any machine.

## 4. Server

**Engine and graph.** `loopback_service.Engine` takes the v2 branch when the selection is v2:

- `ids` come from the database;
- `vectors` is the memory map;
- `catalog_sha` is the `catalog.sqlite` digest.

`api.load_graph` returns `search_v2.GraphV2`, a subclass of the unchanged `hnsw_trace.HNSW` that never calls its constructor (that constructor is the 1,449 MiB tuple build at 200K). It sets the attributes `search()` reads, with two changes:

- `vectors[row]` is a float32 memoryview slice of the mapped file, the same type v1 iterates;
- `links[row][level]` is an int32 memoryview slice of the CSR, in stored order.

`HNSW.search` itself runs unchanged, so traces are identical by construction. The oracle and tests confirm it.

**Exact top-k, bit-identical to v1.** `GraphV2.exact_search` works in five steps:

1. A float32 numpy matrix–vector product over the mapped vectors proposes the top M rows, M = max(4k, 64).
2. Those rows are rescored with `hnsw_trace.cosine_distance` (binary64 accumulation in dimension order) and sorted by (distance, row).
3. The result is accepted only when the k-th exact distance is strictly below 1 − (f_M + ε). Here f_M is the lowest float32 score in the proposal and ε = 1.25e-4. That ε is four times the γ₅₁₂ bound on float32 dot-product error for unit vectors, which holds for any summation order.
4. If the check fails, the proposal widens.
5. If it never passes, a full rescore uses `np.cumsum`. Accumulation is strictly sequential, so it matches the Python loop bit for bit; a test checks this on all 2,000 rows.

In every oracle and benchmark query the first proposal was accepted (no widening, no full rescore).

**`/v1/search` in v2.** Results, scores, trace and diagnostics are the v1 values. Because the page holds no catalog or layout, each reply adds two fields:

- `tracks`: display rows for the results and for labelled trace nodes;
- `layout`: 2-D positions for every row the trace or results mention.

`/v1/manifest` gains `releaseFormat: 2`. Its `bindingStatus` is `verified-corpus-release-v2`, and it stays far below the boundary's 32 KB cap.

**Collection reads (`server/collection_v2.py`), served by `WebGateway`.** These are GET-only, need no bearer credential and are the same public metadata the v1 page fetched as static files:

- `GET /collection/tracks?offset&limit[&q][&text][&genre][&preview=1][&facets=1]` serves browse (catalog order) and title/artist lookup.
  - Lookup follows the page's `metadataSearch`: a folded exact title scores 100, every word in "title artist" scores 10, and results are ordered by score, then row.
  - Refinement follows `refineCandidates`: every folded word must appear in "title artist album", and the genre must match exactly.
  - `facets=1` returns genre counts over the unrefined set, like `sourceGenres`.
  - `limit` is at most 48.
- `GET /collection/tracks?rows=…` returns up to 64 explicit rows.
- `GET /collection/neighbors?row=R` is the v1 page's neighbor computation on the server: exact top 16 excluding R, plus the trace at k 17, ef 32, trace limit 2,048. It is limited to 60 per minute per process.
- Every route rejects unknown or oversized parameters with 400 and runs on a two-thread executor with at most 8 pending reads; beyond that it returns 429.
- A test checks lookup, refinement and facets against the page's own JS on nine queries, including accented and empty-result cases.

**Audio delivery keeps the v1 contract** (an explicit, catalog-bound manifest; anything unlisted is "Preview unavailable") with two v2 modes:

- **local:** files in one directory.
  - Startup checks the inventory (exact names, no symlinks or extras) and sizes only.
  - Each file's SHA-256 is verified on its first request, off the event loop. After that only its (device, inode, size, mtime) identity is checked; any change triggers re-verification, and a mismatch returns 404.
  - `scripts/make_audio_delivery_v2.py` hashes every file at build time.
- **remote:** content-addressed objects `https://<origin><prefix><sha256>.mp3` on one exact pinned origin.
  - The server never fetches or serves them, and the gateway adds `media-src 'self' <origin>` to the CSP.
  - Publication-time verification is the operator's step (section 8).
- `/audio-delivery.json` becomes a compact summary for the page: mode, counts, and a base64 bitset of available rows (33 KB at 200K).

**Unchanged:** `encoder.py` (its digest is part of the engine ID), `hnsw_trace.py`, `corpus_release.py` rules, `active_corpus.py`, `public_boundary.py`, the anonymous limits, `Dockerfile`, `render.yaml` and every hydration and installer script. `.gitignore` and `.dockerignore` (both allowlists) and `package-manifest.json` list the three new server modules. Without that, an image built from this branch would fail to import `release_v2`.

## 5. Page

**v1 path.** A v1 data manifest takes exactly the existing code path. In `app.mjs` the v1 statements only gain a v2 ternary or an early v2 return, and the 73 existing Node tests are unchanged and pass.

**What a v2 page downloads.** When the pinned `data/manifest.json` (still checked against `MANIFEST_SHA`) declares `format: 2`, the page loads only three files, built by `scripts/build_web_v2.py` (each also pinned in the manifest):

- **`manifest.json`:** identities, counts, genre summary, the audio policy and the file pins.
- **`layout.json`:** the density-cloud sample and the stored index links between sampled rows, in the order `indexConnections()` produces them.
  - At or below 8,192 recordings it is every position, so the map draws exactly what v1 drew.
  - Above that, it keeps the lowest row of each occupied cell of a stratified grid, weighted by the rows it stands for.
- **`examples.json`:** the six recorded queries as complete search packets: exact ranking, trace, display rows and positions, computed by the v2 server code.

**Everything else comes through the API** (`web/search-studio/src/collection-api.mjs`):

- Browse, title/artist lookup and all three refinements are server pages with the same page arithmetic (167 pages at 2,000, 482 at 5,777; the page jump clamps).
- Live search, neighbors and examples are packets that carry their own rows and positions.
- Rows the page has seen are kept in sparse arrays indexed by catalog row.
- Every row, page and packet is validated: identity, bounds, order, and positions for every traced row.
- Preview URLs come from the server's bitset under the audio policy pinned in the manifest. Catalog audio paths never become URLs.
- On-device search is disabled for a v2 collection, with the reason shown. It needs every vector in the browser.

**Map (`graph.mjs`).** Bounds come from the full-collection bounds in the manifest. The cloud is rastered from the weighted sample: a point standing for w rows draws with alpha 1 − 0.7^w, the alpha of w overlapping v1 dots. Rows the page has not loaded are neither drawn nor labelled.

**Download size per visit (uncompressed):**

| | fma2000 | fma5777 |
|---|---|---|
| v1 data (catalog, vectors, index, layout, examples, artists) | 10.3 MB | 26.9 MB |
| v2 data (manifest, layout sample with links, example packets) | 0.83 MB | 1.40 MB |

After that, a browse page is about 5 KB and a search reply about 105 KB, most of it the trace. For a live search that trace is the one the v1 server already sent; for neighbors and recorded examples it replaces the trace the v1 page computed locally.

## 6. Parity

- **Exact `/v1/search`:** same IDs, and scores equal bit for bit (the requirement was within 1e-5).
  - Unit tests run 40 real-API comparisons on fma2000 (v1 app versus v2 app with the same query vectors).
  - The real-server runs in section 7 compare the 20 benchmark queries end to end with the real text model.
  - Identity fields differ only where section 2 says they should.
- **Trace (sampled oracle):** each conversion runs 66 queries: the six recorded vectors, 48 seeded catalog rows used as queries with self-exclusion, and 12 seeded random unit vectors. For each it compares v1 `HNSW` against `GraphV2` at k 16, ef 32, trace limit 2,048, for both exact top-k and the complete trace. Unit tests add further k, ef and trace-limit combinations. Mismatches: 0 exact and 0 trace, for fma2000 and fma5777.
- **Browser and server trace:** in v2 the browser no longer computes traces, so v1's browser/server bit-parity becomes a server-side oracle against the same reference. `HNSW_SOURCE_SHA` still pins the JS that built the graph, and the existing web tests still check the six JS example traces.
- **Lookup and refinement:** pinned against the page's own JS on the same data.

## 7. Local measurements

**Setup.**
- Machine: this Mac (M3 Max, 16 cores, 48 GB), shared with other workloads. Load averages ranged from 70 to 560, recorded per run.
- Server: `server/hosting.py` on CPython 3.12.14 (`venv-3.12.14`) with the real Q8 text model, in authenticated mode, so the 20 timed searches meet no public rate limit.
- Roots: disposable roots made with `git archive` on the APFS sparse bundle on the external drive (`/Volumes/music20k-apfs/stage/v2/`).
- Previews were enabled from the same packs in every run:
  - v1 hashes every file at startup;
  - v2 checks inventory and sizes at startup and hashes each file on first play.
- Workload: the first 20 descriptions of the earlier fma5777 benchmark, in the page's request shape (k 16, ef 32, trace on). p50 and p95 are nearest-rank.
- Rounds:
  - rounds 1–2 ran back to back at loads of 240–560;
  - rounds 3–4 ran when the load fell to 70–150, and are the comparable figures;
  - E, the v1 fma5777 root from `corpus-20k`, is the like-for-like v1 reference at 5,777.
- Tools and receipts are in `platform_v2/`: `tools/stage_root.py`, `start_server.sh`, `bench.py`, `parity.py`, `summarize.py`; `runs/*`; `receipts/*.json`.

**Headline (round 3, same conditions):**

| | fma2000 v1 | fma2000 v2 | fma5777 v1 | fma5777 v2 | fma5777 v2, lean package |
|---|---|---|---|---|---|
| Ready (s) | 8.8 | 10.5 | 100.9 | 14.5 | 3.7 |
| CPU at readiness (s) | 3.8 | 1.7 | 9.5 | 1.8 | 1.4 |
| Peak RSS (MiB) | 416 | 356 | 489 | 392 | 395 |
| Search round trip p50 / p95 (ms) | 64 / 68 | 26 / 30 | 149 / 155 | 31 / 120 (round 4: 29 / 52) | 26 / 33 |
| Exact top-k p50 (ms) | 43.8 | 1.7 | 127.3 | 1.9 | 1.8 |

- **Search.** v2 is 2.5× faster at 2,000 and about 5× faster at 5,777 at p50. All of the gain is exact top-k: pure Python over every row in v1, versus numpy plus 64 rescored rows in v2. The trace step is unchanged code and costs the same.
- **Startup.** v2 uses half the startup CPU at 2,000 and a fifth at 5,777.
  - At 5,777, v1 readiness is dominated by re-hashing 6.1 GB of audio. On a cold external drive it was not ready within 6.5 minutes (round 1); it took 178 s in round 2 and 101 s in round 3.
  - **Wall-clock readiness is I/O-bound** on this volume and follows cache state.
  - The lean package, which removes the v1 release history from `verify_package()` (143 package files instead of 3,684), is ready in 3.5–3.7 s.
- **Memory.** Peak RSS is 60 MiB lower at 2,000 and 97 MiB lower at 5,777 than v1 in the same round. RSS on macOS moves with memory pressure; across all rounds v2 fma2000 was 336–356 MiB and v2 fma5777 360–392 MiB.

**All real-server runs:**

| Run | Configuration | Ready (s) | CPU at ready (s) | Peak RSS (MiB) | Search round trip p50 / p95 (ms) | Server compute p50 / p95 (ms) | Exact top-k p50 (ms) | Trace p50 (ms) | Load (1 min) |
|---|---|---|---|---|---|---|---|---|---|
| r1-A | fma2000 v1 · main 35cec9e | 25.3 | 9.5 | 395.8 | 218 / 520 | 188 / 431 | 145.1 | 20.3 | 559 |
| r2-A | fma2000 v1 · main 35cec9e | 45.3 | 9.6 | 395.7 | 469 / 1167 | 446 / 1140 | 276.3 | 26.4 | 279 |
| r1-B | fma2000 v1 · platform-v2 | 26.9 | 8.9 | 379.9 | 244 / 446 | 222 / 424 | 173.6 | 21.7 | 492 |
| r2-B | fma2000 v1 · platform-v2 | 33.8 | 9.8 | 379.6 | 222 / 661 | 193 / 600 | 139.5 | 25.3 | 342 |
| r3-B | fma2000 v1 · platform-v2 | 8.8 | 3.8 | 416.3 | 64 / 68 | 57 / 60 | 43.8 | 6.8 | 152 |
| r1-C | fma2000 v2 · platform-v2 | 60.1 | 4.5 | 336.4 | 72 / 85 | 44 / 52 | 4.9 | 19.6 | 337 |
| r2-C | fma2000 v2 · platform-v2 | 18.5 | 4.4 | 347.1 | 79 / 197 | 46 / 60 | 4.9 | 20.7 | 341 |
| r3-C | fma2000 v2 · platform-v2 | 10.5 | 1.7 | 356.1 | 26 / 30 | 15 / 19 | 1.7 | 6.8 | 124 |
| r4-C | fma2000 v2 · platform-v2 | 7.8 | 1.9 | 348.7 | 26 / 36 | 15 / 18 | 1.6 | 6.8 | 90 |
| r1-D | fma5777 v2 · platform-v2 | 58.7 | 4.7 | 362.3 | 85 / 306 | 51 / 245 | 4.8 | 26.8 | 241 |
| r2-D | fma5777 v2 · platform-v2 | 29.2 | 4.7 | 359.6 | 455 / 795 | 327 / 513 | 5.3 | 209.9 | 465 |
| r3-D | fma5777 v2 · platform-v2 | 14.5 | 1.8 | 391.5 | 31 / 120 | 19 / 45 | 1.9 | 8.6 | 104 |
| r4-D | fma5777 v2 · platform-v2 | 10.6 | 1.8 | 382.9 | 29 / 52 | 17 / 23 | 1.8 | 7.8 | 81 |
| r2-E | fma5777 v1 · corpus-20k | 178.1 | 22.0 | 467.7 | 519 / 1576 | 494 / 1513 | 453.4 | 23.7 | 513 |
| r3-E | fma5777 v1 · corpus-20k | 100.9 | 9.5 | 489.1 | 149 / 155 | 141 / 146 | 127.3 | 7.5 | 70 |
| r3-L | fma5777 v2 · lean package | 3.7 | 1.4 | 395.4 | 26 / 33 | 15 / 20 | 1.8 | 7.5 | 105 |
| r4-L | fma5777 v2 · lean package | 3.5 | 1.4 | 378.1 | 26 / 32 | 16 / 20 | 1.8 | 7.7 | 71 |
| s1-S | SYNTHETIC 200,000 v2 · lean (not music) | 4.0 | 3.0 | 875.0 | 32 / 48 | 22 / 31 | 8.0 | 6.6 | 127 |
| s2-S | SYNTHETIC 200,000 v2 · lean (not music) | 2.9 | 2.8 | 872.8 | 40 / 84 | 24 / 49 | 8.0 | 7.1 | 123 |

**Collection reads** (round 3 and the synthetic run; p50 of five calls, ms):

| | browse page 1 | last page | lookup "love" | refine text + genre | 12 explicit rows | neighbors |
|---|---|---|---|---|---|---|
| fma2000 v2 | 0.9 | 0.6 | 1.7 | 0.6 | 0.6 | 10.0 (92 KB) |
| fma5777 v2 (lean) | 1.0 | 0.8 | 3.3 | 0.9 | 0.7 | 11.4 (103 KB) |
| synthetic 200K v2 | 7.9 | 17.4 | 341 (full scan) | 31.9 | 0.7 | 18.8 (111 KB) |

**Synthetic 200,000-row load test.** This is not music, and is never to be published.

What it is (`platform_v2/tools/synthetic_v2_200k.py`; data under `/Volumes/music20k-apfs/scale/v2-synthetic-200000/`, marked SYNTHETIC):

- 200,000 vectors made from fma2000 vectors plus seeded noise;
- a real graph built by the repo's own JS HNSW (M 12, efConstruction 100, seed 43) in 941 s;
- catalog, rights and evidence rows sized like the real ones: `catalog.sqlite` 904 MB, `evidence.sqlite` 829 MB, `vectors.f32` 410 MB.

Measured with the real v2 code:

- **Loader:** verifies 1.33 GB of core assets in 1.9–2.2 s warm, with 562–567 MiB RSS without the model. 410 MB of that is the mapped vectors, which every exact search touches.
- **Exact top-k (k 16):** 8.1 / 10.5 ms p50/p95, and every proposal was accepted.
- **Traced search (ef 32):** 7.5 / 17.3 ms, with 250 distance evaluations at p50.
- **Real server with the model:** ready in 2.9–4.0 s (2.8–3.0 CPU-s, warm cache, lean package), peak RSS 873–875 MiB, search round trip 32–40 / 48–84 ms p50/p95.

That fits Render's 2 GB instance. It is above the sourcing study's 0.6–0.8 GiB estimate, by the resident vectors.

The run found three things:

1. **A startup memory problem, now fixed.** The vectorised graph check first left about 260 MB of int64 temporaries resident at 200K. Checking in blocks fixed it (commit `74317aa`).
2. **Orphan nodes in large graphs.** The JS builder left **75 of 200,000 nodes with no incoming layer-0 link**. Both the v1 validator and the v2 loader reject such a graph, as they should, so a real 200K build needs a reviewed, pinned connectivity-repair step in the graph builder. This run used a test-only repair that links each orphan from its nearest reachable node with spare degree.
3. **Name lookup is a full scan of the narrow `tracks` table:** 0.34–0.41 s for a rare word, 0.1 s for a common one. Fine at 5,777 (3–10 ms); at 200K it wants the FTS5 or trigram prefilter in section 9.

**Parity on the real servers** (`platform_v2/receipts/parity-round1.json`, `parity-round2.json`, with the real model):

- **main versus platform-v2 on v1 fma2000:** `/v1/manifest` is identical, and all 20 `/v1/search` responses are byte-identical once `timingMs` is removed (compared as the server's own serialised JSON).
- **v1 versus v2 on fma2000:** no ID mismatches; the maximum score difference is 0.0; no trace mismatches; identity fields differ only by the documented split; v2 adds `tracks`, `layout` and `releaseFormat`.
- **Web files, main versus this branch:** 47 identical, every data file identical. Changed: `app.mjs` and `graph.mjs`. Added: `collection-api.mjs`.

**Browser checks.**

- **Fixture runs, both passing:**
  - `tests/browser_search_ui.mjs`: the v1 page, Atlas and `?direction=list`, unchanged.
  - `tests/browser_collection_v2.mjs`: the v2 page against an emulated server.
- **Real servers:** fma2000-v2 and fma5777-v2 in anonymous mode behind the TLS terminator from `validation/server/`, with the real model and lazily verified audio (`platform_v2/tools/browser_v2_check.mjs`). **28 of 28 checks** on each, desktop 1440×1000 and mobile 390×844:
  - **Data loaded:** only the pinned v2 data (825 KB at 2,000; 1.40 MB at 5,777), with no catalog, vectors, index or ids and no collection call at load.
  - **Density cloud:** drawn on 4–18% of the canvas.
  - **Browse:** 167 and 482 pages; last pages of 8 and 5 rows; the page jump clamps; the genre filter is applied with collection-wide facets.
  - **Server requests:** title/artist lookup and neighbors go to `/collection/`.
  - **Search:** live server search returns 12 rows with their own metadata.
  - **Playback:** a preview plays after its first-request hash.
  - **Errors:** no page errors and no horizontal overflow.
  - **Animation:** the server-search animation runs through all its phases at 60 fps, with a 16.7 ms p95 frame.
  - Screenshots are in `platform_v2/browser/`.

## 8. Build and hydration for an object-store pack (proposed, not applied)

Today the Dockerfile runs `scripts/hydrate_corpus_audio.py`, which:

- is pinned to the fma2000 release and plan;
- fetches 1.95 GB of official FMA byte ranges;
- bakes 2,000 MP3s into the image;
- leaves the server to re-hash all of them at every start.

**For a v2 selection it fails closed today.** `selected_corpus` rejects `schemaVersion: 2`, so the build stops before any download. That is the right behaviour until the steps below are reviewed. Nothing here changes the live build.

**What a v2 deployment changes:**

1. **Release data.**
   - fma2000-v2 is 24 MB, so it could be committed (fma5777-v2 is 67 MB). At 200K, `catalog.sqlite` is about 0.6–1 GB, `evidence.sqlite` about 0.7 GB (about 0.2 GB if blobs are zlib-compressed, which is an open option) and `vectors.f32` 410 MB, so the release cannot live in Git.
   - Git keeps `release.json` and the selection.
   - A new build step, `scripts/install_release_v2.py`, would download each asset named in `release.json` from one pinned HTTPS object-store prefix. It would enforce the asset's exact byte count and SHA-256, a total byte cap and a deadline, write to a private stage, run `load_release_v2` on the stage, and only then move it into `corpus-releases/<name>`.
   - Like the hydrator, it should run in a supervised worker with an absolute deadline.
2. **Audio, remote mode (recommended for 20K and above).**
   - Nothing is hydrated and the image carries no audio.
   - The operator uploads `<sha256>.mp3` objects with `Content-Type: audio/mpeg` and immutable caching, using a write key that stays on the operator's machine.
   - The operator then runs a publication verifier (not written yet). It fetches every URL anonymously and checks status, exact length, SHA-256, MIME type, a sampled Range 206 and the absence of redirects, then writes the v2 delivery manifest that is committed beside `release.json`.
   - Render bandwidth then carries only pages and API replies.
3. **Audio, build-time pack (bounded sizes only).**
   - Generalise the hydrator to a v2 plan, or download a pinned pack archive from the same object store, verify every file at build, and write the local-mode delivery manifest.
   - The server then checks inventory and sizes at start and hashes each file on first play.
   - At 6.1 GB this probably fits the 16 GB Starter build disk; at 21 GB it does not.
4. **Dockerfile ordering.**
   - Move the release and audio install steps before `COPY server/` and `COPY web/`, keyed only by `release.json`, the selection and the delivery manifest. Code changes then reuse the cached data layers instead of re-downloading them.
   - Use `PYTHON_BASE` pinned to a digest as today. `requirements.lock` is unchanged (SQLite and numpy are already there).
   - The v2 server needs no SQLite extension today. The Debian trixie base is expected to ship SQLite 3.46 with FTS5 (relevant only to the lookup prefilter in section 9); this was not verified in this work.
5. **Environment.**
   - `MUSIC_AUDIO_MANIFEST_PATH` points at the v2 delivery manifest.
   - `MUSIC_ENABLE_AUDIO_PREVIEWS=1`.
   - `MUSIC_AUDIO_PACK_DIR` only in local mode.
   - Everything else in `render.yaml` is unchanged.

**Startup cost that remains.**

- `verify_package()` still hashes every packaged file: 205 MB today, of which the model is 127 MB and old v1 release directories most of the rest. A v2 image should drop `corpus-releases/fma500` to `fma2000` from the package.
- The v2 loader streams the core assets once: 14 MB at fma2000, 40 MB at fma5777, about 1.2 GB at 200K. On this M3 that is projected at about 1–2 s per GB at 200K (section 7). A 200K deployment that wants a faster start could pin per-page digests and verify lazily, which is not built.

## 9. Open items and decisions

1. **Rights.** Unchanged by this work: the 3,777 fma5777 rows still rest on an automated screen. fma5777-v2 inherits exactly the v1 rows.
2. **Host for audio.** Remote mode is implemented and tested against fixtures only. No bucket exists, and the publication verifier and the release installer in section 8 are not written.
3. **Anonymous CPU budget.**
   - `PreviewBudget` starts counting at process start, so startup CPU uses part of the first hour's 30 CPU-seconds. v2 startup CPU is measured in section 7.
   - Starting the budget at readiness is a one-line change. It would change v1 behaviour, so it is left for review.
   - v2 startup costs 1.4–1.9 CPU-seconds warm (v1: 3.8–9.8 at 2,000, 9.5–22 at 5,777).
   - Neighbor exploration now costs server CPU (about 10–20 ms each) and counts against the same hourly budget.
   - `/collection/tracks` has a concurrency bound (8 pending, then 429) but no per-minute budget; only neighbors has one.
     - At 5,777 a lookup costs 3–10 ms.
     - At 200K a rare-word lookup costs 0.34–0.41 s of CPU. In anonymous mode that would drain the same 30 CPU-seconds per hour that live search needs.
     - Add a per-minute read budget (or the FTS5 prefilter) before serving 200K anonymously.
4. **200K page features not built.**
   - Region labels, neighbourhood centroids and quadtree point tiles (the level-of-detail plan in SOURCES_AND_PLAN 6.6). The single weighted sample is in place.
   - A paged credits page: the static `track-attribution.html` is 10 MB at 5,777.
   - FTS5 or trigram acceleration for lookups. Lookup today scans the narrow `tracks` table: 3–10 ms at 5,777, but 0.34–0.41 s at 200K (section 7). The plan:
     - an FTS5 `trigram` index over the folded columns, used only as a prefilter for words of three or more characters, keeping the existing `instr` conditions so results stay identical;
     - it changes `catalog.sqlite`, and therefore release identities.
   - Graph connectivity at scale. The repo's JS builder left 75 of 200,000 synthetic nodes unreachable on layer 0. v1 and v2 both reject that, so a 200K build needs a reviewed orphan-repair step pinned with the builder.
5. **Measurement caveat.** Section 7's timings were taken on a shared machine (load 70–560 on 16 cores) and an external-drive APFS volume. Bytes, CPU-seconds, RSS and parity are the reliable figures. Render's single shared CPU will be slower per query; its hosted RSS, cold start and playback remain the deployment's own acceptance checks.
6. **Commit trailer.** The workflow asked for a "Claude Fable 5.1" trailer. The session ran as Claude Opus 5.5, and the commits say so.
