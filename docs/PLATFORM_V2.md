# Platform v2: release format, server and page for 20K–200K recordings

Status, 2026-10-06: implemented on the local branch `platform-v2` and measured on this Mac. Not pushed or deployed. The live site is unchanged. The repository default still selects a v1 fma2000 release: since the rights quarantine, the rebuilt 1,992-track one. For that selection the Dockerfile and hydration steps added by `v2-deploy` change nothing.

Since 2026-10-06 `platform-v2` also carries three branches, merged in this order:

- **`v2-deploy`.** It makes v2 deployable: the release installer, the v2 build hydration, the activation step, the audio publication verifier and the post-deploy live check. `docs/DEPLOY_PLAN_V2.md` has the steps, the local proof and the acceptance numbers.
- **`rights-fix-2000`.** fma2000 rebuilt without the eight quarantined recordings (`docs/RIGHTS_QUARANTINE.md`).
- **`v2-scale-ui`.** Section 10.

Fixes from the security review followed (`DEPLOY_PLAN_V2.md` section 2). Sections 1 to 9 describe the platform as first built. Where they say something is unchanged or not written, they describe it before those merges, and section 10 supersedes them where it says so.

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

**Minor versions.** A minor version is additive and named in `release.json` (`"minorVersion"`). A 2.0 release has no such key and is read exactly as before; an unknown minor version is refused. Release format 2.1, on `v2-scale-ui`, adds only an FTS5 trigram lookup index to `catalog.sqlite` (section 10.2).

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
- On `v2-scale-ui`, `GET /collection/tiles` and `/collection/links` serve the map's level of detail, and `GET /collection/credits` serves the track credits page by page. Each has its own per-minute budget (section 10).
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
- **`layout.json`:** the density-cloud sample and the stored index links between sampled rows, in the order `indexConnections()` produces them. On `v2-scale-ui` it is schema 3: the same sample, region labels and the tile pyramid, without the links (section 10.1).
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

On `v2-scale-ui` the layout carries no links, so fma2000's v2 data is 0.68 MB (section 10.1).

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
  - round 5 re-measured the two v2 roots staged from the final commit (`4f2c0ee`, load about 100). Earlier rounds ran on `a3fefb5` or `38942a2`, which have the same server code apart from the block-wise graph check;
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
| r5-C | fma2000 v2 · platform-v2 | 2.4 | 1.6 | 361.9 | 25 / 29 | 15 / 18 | 1.7 | 6.7 | 108 |
| r1-D | fma5777 v2 · platform-v2 | 58.7 | 4.7 | 362.3 | 85 / 306 | 51 / 245 | 4.8 | 26.8 | 241 |
| r2-D | fma5777 v2 · platform-v2 | 29.2 | 4.7 | 359.6 | 455 / 795 | 327 / 513 | 5.3 | 209.9 | 465 |
| r3-D | fma5777 v2 · platform-v2 | 14.5 | 1.8 | 391.5 | 31 / 120 | 19 / 45 | 1.9 | 8.6 | 104 |
| r4-D | fma5777 v2 · platform-v2 | 10.6 | 1.8 | 382.9 | 29 / 52 | 17 / 23 | 1.8 | 7.8 | 81 |
| r5-D | fma5777 v2 · platform-v2 | 5.0 | 1.7 | 371.3 | 26 / 33 | 15 / 21 | 1.8 | 7.9 | 101 |
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

## 8. Build and hydration for an object-store pack (proposed here; implemented on `v2-deploy`, see `DEPLOY_PLAN_V2.md`)

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
2. **Host for audio.** Remote mode is implemented and tested against fixtures only. No bucket exists. The publication verifier and the release installer from section 8 are written and tested on `v2-deploy` (`DEPLOY_PLAN_V2.md`).
3. **Anonymous CPU budget.**
   - `PreviewBudget` starts counting at process start, so startup CPU uses part of the first hour's 30 CPU-seconds. v2 startup CPU is measured in section 7.
   - Starting the budget at readiness is a one-line change. It would change v1 behaviour, so it is left for review.
   - v2 startup costs 1.4–1.9 CPU-seconds warm (v1: 3.8–9.8 at 2,000, 9.5–22 at 5,777).
   - Neighbor exploration now costs server CPU (about 10–20 ms each) and counts against the same hourly budget.
   - `/collection/tracks` had a concurrency bound (8 pending, then 429) but no per-minute budget. Neighbors had one, and on `v2-scale-ui` so do tiles, links and credits. Since `b34f0ab` it has one too: 300 a minute (`DEPLOY_PLAN_V2.md` section 7, item 6).
     - At 5,777 a lookup costs 3–10 ms.
     - At 200K a rare-word lookup costs 0.34–0.41 s of CPU. In anonymous mode that would drain the same 30 CPU-seconds per hour that live search needs.
     - Add a per-minute read budget before serving 200K anonymously. Release format 2.1's FTS5 prefilter (section 10.2) takes rare-word lookups to milliseconds, but a word in every row, or one of one or two letters, still scans.
4. **200K page features.** Built on `v2-scale-ui` (section 10); `platform-v2` alone has none of them:
   - Region labels and quadtree point tiles, with stored links drawn only around the recording in focus (10.1). Neighbourhood centroids are not built.
   - The paged credits page (10.3). The static `track-attribution.html` is 10 MB at 5,777.
   - The FTS5 trigram prefilter for lookups, as release format 2.1 (10.2): used only for words of three or more characters, with the `instr` conditions kept, so results stay identical. It changes `catalog.sqlite`, and therefore release identities; `--no-lookup-index` still writes 2.0.
   - Graph connectivity at scale. The repo's JS builder left 75 of 200,000 synthetic nodes unreachable on layer 0. v1 and v2 both reject that, so a 200K build needs a reviewed orphan-repair step pinned with the builder.
5. **Measurement caveat.** Section 7's timings were taken on a shared machine (load 70–560 on 16 cores) and an external-drive APFS volume. Bytes, CPU-seconds, RSS and parity are the reliable figures. Render's single shared CPU will be slower per query; its hosted RSS, cold start and playback remain the deployment's own acceptance checks.
6. **Commit trailer.** The workflow asked for a "Claude Fable 5.1" trailer. The session ran as Claude Opus 5.5, and the commits say so.

## 10. Scale UI and release format 2.1 (branch `v2-scale-ui`)

Status, 2026-10-06: implemented on the local branch `v2-scale-ui` (built on `platform-v2`), tested and measured on this Mac, then merged into `platform-v2` after `v2-deploy` and `rights-fix-2000` (10.7). Not pushed or deployed. This section supersedes what sections 2, 4, 5 and 9 say about the map overview, name lookups, the credits page and the open page features.

| Commit | What it adds |
|---|---|
| `b0cc12e` | The page jump keeps a typed number when a server page lands meanwhile (10.4) |
| `da97c3c` | Release format 2.1: an FTS5 trigram prefilter for name lookups; converter, upgrader and verifier (10.2) |
| `68dcd6c` | Track credits served page by page from `/collection/credits` (10.3) |
| `b88ab98` | `/collection/tiles` and `/collection/links` for the map's level of detail (10.1) |
| `4eda892` | Level of detail on the page: region labels, streamed tiles, links around the focus; `layout.json` schema 3 (10.1) |
| `da1ef62` | Cheaper zoomed-in frames at 200K: larger tiles, pixel splats, no hidden overview (10.1) |
| `e038654` | Module preloads, so the new map module costs the first paint no round trip (10.5) |
| `ca3b5c8` | Credit pages wrap long source URLs; a real-browser check of the credit pages (10.3) |
| (this commit) | This section, and pointers to it from sections 2, 4, 5 and 9 |

### 10.1 Map level of detail

The graph-first Atlas keeps its design. Every change below sits behind the v2-only `lod` option of `AudioMap`, so a v1 data manifest takes the unchanged code.

**Overview (`layout.json` schema 3, `music-layout-lod-v2`).**

- The pinned density sample is unchanged: every position up to 8,192 rows, otherwise the weighted stratified sample.
- It now carries region labels, and the tile pyramid when the sample is not every row.
- The stored links between sampled rows are no longer shipped. They were 505 KB of fma5777's 764 KB layout.
- fma2000 page data: 825,502 → 684,420 bytes per visit (`layout.json` 239,719 → 98,607).

**Region labels (`build_web_v2.py`).**

- A seeded k-means of every layout position runs at two levels: 12 areas from the whole-collection view up to 2× detail, and 48 areas from 2× to 10×.
- An area is named by its most common source genre when that genre holds at least 40% of its recordings. "Unknown" never names an area.
- On the page, labels fade in and out over ±15% of their zoom range. They never cover a track label or a match, are not repeated within 160 px, and dim while a search replays.
- The About text and the map's accessible label say these are catalog labels, not learned genres.
- Labelled areas: fma2000 7 and 39; the synthetic 100K release (10.6) 7 and 37.

**Tiles (`GET /collection/tiles?z&x&y`, `collection_v2.TileIndex`).**

- A quadtree over the layout, cut from one Morton ordering of the rows. It is built on first use, in about 20 ms at 200K.
- A tile with at most 1,024 rows is complete. A fuller tile is a weighted stratified sample: the lowest row of each occupied cell at the finest sub-level with at most 1,024 cells.
- Rows, positions and weights travel as little-endian base64: about 7 KB per tile at 200K, at most 21 KB. Replies sit in a bounded LRU.
- Tests walk the whole pyramid of fma2000. Complete tiles partition the catalog exactly once, and sample weights add up to each tile's row count.

**Tiles on the page (`map-lod.mjs`).**

- When the overview is a sample, zooming in picks the level whose tiles span at most 512 CSS pixels and streams the tiles of the settled view.
- Requests wait for a 90 ms settle, run at most three at a time, abort when a tile leaves the view, and retry after a refusal.
- Children of a complete tile are cut locally with the server's own quantisation, so deep zoom needs no request.
- Each tile is rasterised once, into 256 × 256 pixels of accumulated soft dots (alpha 1 − (1 − a)^n per pixel, the look of stacked canvas fills), at most three per frame. It replaces the overview cloud under it.
- A tile that has not arrived shows its loaded ancestor or the overview.
- A search requests the tiles where its camera will land while it animates.

**Per-frame cost.**

- A frame considers the trace's visited rows, then the overview sample or the rows of the tiles in view (round-robin across tiles, at most 2,048).
- It projects each row only when it considers it, so a frame costs O(candidates), not O(catalog).
- At or below 8,192 rows the candidates are every row in row order, as before.

**Links around the focus (`GET /collection/links?row=R`).**

- The reply holds one recording's stored index links, level by level in stored order, with the positions of every linked row.
- The page reads the selected recording's links once (it keeps 64) and draws them only around it.
- They appear once the view is zoomed to 1.6× detail, and never during a replay.

**Budgets.** Tiles 2,400 and links 600 per minute per process. Both share the collection routes' queue bound.

### 10.2 Release format 2.1: the lookup prefilter

At 200K rows a title/artist lookup scanned the narrow `tracks` table with `instr()` for every word (section 7: 0.34–0.41 s for a rare word).

**What 2.1 adds.** One thing in `catalog.sqlite`: an external-content FTS5 index with case-sensitive trigrams over the folded "title artist album" column (`tracks_fts`). `release.json` names `"minorVersion": 1`.

**The index only narrows; `instr()` still decides.**

- `page_query` uses it as a prefilter, `row IN` the index's matches.
- The `instr()` conditions, the ordering and the facets are unchanged, so every page is identical with or without it. Lookup words are tested on "title artist", a prefix of the indexed column; the verifier checks that prefix property row by row.
- Words shorter than three characters cannot use a trigram index and still scan.
- A bounded probe scans instead when a phrase matches a tenth of the catalog or more, where the scan is the faster plan.

**Compatibility.**

- A 2.0 release has no `minorVersion` key and loads and scans exactly as before. Unknown minor versions are refused.
- At startup a 2.1 catalog must declare the exact index and its meta row. Its content is covered by `catalogSha256`, like every other table.
- A runtime without FTS5 trigrams still serves 2.1, by scanning.

**Tools.**

- **Converter.** `convert_release_v1_to_v2.py` writes 2.1 by default. `--no-lookup-index` writes 2.0, byte-identical to the reviewed fma2000-v2 release (`806b19ed…`). For the 1,992-track release of the rights quarantine, the two conversions are 2.1 `b537a7ac…` (the deploy's pin) and 2.0 `279cd21b…`.
- **Upgrader.** `scripts/upgrade_release_v2.py` turns a verified 2.0 directory into 2.1 without re-reading v1. A converted and an upgraded 2.1 catalog are byte-identical.
- **Proofs.** Both run FTS5's integrity check against every row and a sampled lookup oracle, comparing index and scan pages, before publishing.
- **Verifier.** `verify_release_v2.py` (`validate_rows`) re-runs the integrity check on a private copy, since the check needs a writable database.

**Size and cost.**

| Release | catalog.sqlite 2.0 → 2.1 | Upgrade | Oracle |
|---|---|---|---|
| fma2000 | 9.70 → 10.08 MB | converted directly: 5.6 s, 191 MB peak RSS | 148 cases, 0 mismatches |
| fma5777 | 27.55 → 28.55 MB | 2.2 s | 148 cases, 0 mismatches |
| SYNTHETIC 100K (10.6) | 184.5 → 203.5 MB | 27.9 s, 363 MB peak RSS | 148 cases, 0 mismatches; verifier 4.8 s, 336 MB |
| SYNTHETIC 200K (round 1) | 903.9 → 940.3 MB | 121 s | 76 cases, 0 mismatches |

**Lookups through the real `/collection/tracks` route, SYNTHETIC 100K.** p50 of 7 calls over HTTP, ms. 2.0 scan versus 2.1 index; both helpers had to return identical rows, totals and facets.

| Case | Matches | 2.0 scan p50 (ms) | 2.1 index p50 (ms) |
|---|---|---|---|
| browse page 1 | 100,000 | 1.77 | 2.77 |
| browse last page | 100,000 | 21.5 | 31.89 |
| rare title number | 1 | 82.47 | 2.11 |
| an artist name and a number ("artist 0042") | 119 | 123.93 | 13.1 |
| common real word | 1,174 | 208.82 | 5.94 |
| two real words | 2 | 114.08 | 1.69 |
| medium real word | 847 | 83.07 | 4.03 |
| two-letter word (no trigram) | 0 | 82.65 | 58.93 |
| word + refinement + genre | 129 | 55.45 | 24.22 |
| refinement only, rare | 13 | 53.43 | 1.58 |
| 12 explicit rows | 12 | 0.75 | 0.75 |

The 1-minute load was 150 during the run, and every case returned identical pages from both releases. Browse never uses the index; its two releases differ only by run-to-run noise and file layout.

**In-process at 200K (round 1, `page_query`).**

- Rare and narrow phrases: 0.35–7.6 ms with the index, against 56–589 ms by scan.
- A word in every row, a two-letter word or a single letter still scan, at 0.40–0.51 s. That is why section 9.3 asked for a per-minute budget for `/collection/tracks` before serving 200K anonymously. It has one since `b34f0ab`, which bounds the drain but does not remove it at 200K (10.8).

### 10.3 Track credits, page by page

The static `track-attribution.html` carries every credit in one file: 3.8 MB at 2,000 recordings, 10 MB at 5,777, hundreds of MB at 200K.

**What a v2 server serves (`GET /collection/credits?page=N`).** A plain HTML page with no script. It holds:

- the static page's heading and introduction;
- the credit articles of fifty consecutive catalog rows, byte for byte as the static generator writes them;
- navigation at the top and at the end: the position, First/Previous/Next/Last links (`rel` prev and next) and a labelled page-jump form.

Out-of-range pages clamp. The page has a skip link and a `main` landmark labelled with its position.

**Redirects and errors.**

- `?id=fma:N` redirects (303) to the page that holds the recording, at its `#fma-N` anchor.
- An unknown ID gets a 404 page; bad parameters get a 400 page.
- Credit pages have their own budget (300 per minute) and share the collection queue bound.

**In the page and the package.**

- `build_web_v2.py` replaces `notices/track-attribution.html` in a v2 package with a 0.9 KB page. It links to the paged credits and forwards old `#fma-N` links there.
- On a v2 collection, the result list's Credit & license links, the player's credit link and the About link go to the paged credits.
- The checked-in v1 page is unchanged.

**Same content.** A test reads all 40 pages of fma2000 through the gateway and requires the concatenated articles to equal the static page's 2,000 articles exactly.

**Accessible.** `tests/browser_credits_v2.mjs` checks the real route in Chromium at 1440 × 1000 and 390 × 844:

- one h1, heading order, one h2 per credit;
- one labelled `main` and two distinctly labelled navigations;
- labelled page jumps;
- the first Tab shows the skip link, and Enter moves focus to the credits;
- keyboard order;
- no horizontal scroll on the first, a middle and the last page;
- clamping and the page jump;
- an ID redirect landing on its article;
- the 404 page;
- the small page forwarding an old `#fma-N` link.

**What the check found.**

- The first run failed reflow. Source URLs run to 220 characters (median 96) as link text, and pages 1, 20 and 40 overflowed by 58–198 px at 1440 px and by 782–922 px at 390 px.
- The paged style now wraps article text anywhere (`ca3b5c8`). Afterwards: 24 of 24 checks.
- The checked-in v1 static page has the same overflow and is left unchanged.
- Run it against any v2 server: `node tests/browser_credits_v2.mjs ORIGIN [small-page file]`. The forward of old `#fma-N` links is checked only when the file is given.

**Cost.** fma2000's 40 pages are 74–218 KB each (median 93 KB) and render in 1.2 ms at the median (3.7 ms at most), in-process on this Mac. Pages of the lean synthetic rows (10.6) are 26–27 KB and take 1.3–1.6 ms over HTTP at 100K rows.

### 10.4 Page jump: a typed number survives a landing page

A v2 browse or lookup page arrives asynchronously, and every render wrote the current page into the page box. A page that landed while someone typed there replaced the number they had typed.

A number typed into the box is now a draft:

- Renders update everything else and leave the draft alone until it is submitted.
- Any navigation clears it: the page buttons, a submitted jump, a new result set, a refinement or the display policy.
- Escape restores the current page.

**Tests.**

- A Node test types into the box while the next page is in flight.
- The v2 browser fixture runs at 1440 and 390 px. It delays a server page by 700 ms, types 15 meanwhile, checks that the box still reads 15 when the page lands, then submits it.
- Both fail on the previous `app.mjs`.

### 10.5 First paint: module preloads

`graph.mjs` imports `map-lod.mjs`, which made the page's module graph one level deeper for v1 and v2 alike: one more serial request before `app.mjs` can run. On a link with 40 ms per request that cost more than the 141 KB the schema-3 layout saves. First paint on broadband had regressed by 7–21 ms against `platform-v2`.

`index.html` now names all 15 modules of the static import closure of `app.mjs` with `<link rel="modulepreload">`, so they load in parallel with the entry. A test derives the closure from the sources and requires the preload list to equal it.

**Measured on fma2000-v2** (5 interleaved runs per cell, medians; 1-minute load 177–332; the table is in 10.6):

- Broadband, with both widths and both CPU rates: data ready 85–114 ms sooner and first map frame 85–120 ms sooner than `platform-v2`. Preloading only `map-lod.mjs` gave 35–64 ms.
- Local: unchanged, to 6–17 ms sooner.

### 10.6 Measurements

**Fixture.** `platform_v2/scale_ui/tools/measure_ui.mjs`, the `tests/browser_collection_v2.mjs` pattern:

- **What is served.** Chromium (Playwright 1.63) loads a built web root through route interception. `/collection/*` is proxied to the real routes: `collection_v2.CollectionRoutes` of this branch, over a real release, started by `tools/collection_helper.py`. Its light mode maps no vectors and kept 10–49 MB RSS.
- **Throttling.** The CPU is throttled through CDP. Broadband adds 40 ms per request plus 20 Mbit/s.
- **Viewports.** Desktop is 1440 × 1000. Mobile is 390 × 844 at device scale 2, with touch.
- **Sides.** "Before" is `platform-v2` (`540f20f`): its page, and its `build_web_v2.py` over the same catalog.
- **Interleaving.** Runs alternate ABAB/BAAB, and each run records the 1-minute load average.
- **Measures.**
  - First paint: FCP, data ready (`body[data-ready]`) and the first map frame after it.
  - fps: requestAnimationFrame intervals through three recorded-example replays (search animations, about 4.7 s each), then an explore sequence: from the overview, 8 zoom steps, a 1.2 s drag-pan, 4 more zoom steps.
- **Medians.** fps is reported as the median of the per-sequence averages. The p50 frame is 16.7 ms (vsync) except under heavy load, so the averages, the p95 frame and the counts of frames over 50 ms carry the information.
- **Tools and receipts.** `platform_v2/scale_ui/` holds `tools/`, `receipts/` (round 1, the cut-off run) and `round2/` (configs, run scripts, receipts and screenshots).

**The synthetic release (SYNTHETIC, measurement only, deleted afterwards).**

- `round2/tools/synthetic_release_lean.py` generated 100,000 rows in the scratchpad: rows 0–1,999 take fma2000's display text, vectors and positions; the rest are jittered copies (σ 0.02) of a seeded random fma2000 parent, inheriting its genre and licence, with titles of 1–4 words from the fma2000 title vocabulary.
- The graph is seeded random with a layer-0 ring, so it is structurally valid but its traces are not representative. Every row shares one small evidence blob.
- It was built in 7.7 s (645 MB peak RSS, most of it the loader's pass over the mapped vectors).
- `upgrade_release_v2.py` made it 2.1, and `verify_release_v2.py` accepted every row.
- `build_web_v2.py` built its page data: 1,306,865 bytes per visit, 4,991 sampled cells, tiles on; `platform-v2` builds 1,356,040 bytes.
- The release and both web roots were deleted after the round, as was the 200K lookup catalog that the cut-off run left in `platform_v2/scale_ui/releases/` (209 MB).

**fps at 4× CPU, all runs** (desktop and mobile; 3 interleaved runs per cell; "1-min load" is the load average at each run):

| Release | Viewport | Page | Replays: fps median (min) | Replay frames > 50 ms | Explore: fps median (min) | Explore frames > 50 ms | Tiles per explore | Runs | 1-min load |
|---|---|---|---|---|---|---|---|---|---|
| fma2000-v2 (round 1) | desktop | platform-v2 | 55.5 (52.2) | 5 of 2438 | 59.8 (59.5) | 0 of 1163 | 0 | 3 | 120–164 |
| fma2000-v2 (round 1) | desktop | v2-scale-ui | 57.1 (52.7) | 5 of 2497 | 59.2 (58.4) | 2 of 1121 | 0 | 3 | 136–176 |
| fma2000-v2 (round 1) | mobile | platform-v2 | 58.8 (55.4) | 4 of 2583 | 59.5 (58.7) | 0 of 1169 | 0 | 3 | 102–166 |
| fma2000-v2 (round 1) | mobile | v2-scale-ui | 59.2 (54.9) | 4 of 2577 | 60 (59.8) | 0 of 1095 | 0 | 3 | 106–166 |
| SYNTHETIC 100K, real release (round 2) | desktop | platform-v2 | 46.3 (32.8) | 34 of 2118 | 55.3 (54) | 8 of 1449 | 0 | 3 | 28–86 |
| SYNTHETIC 100K, real release (round 2) | desktop | v2-scale-ui | 59 (37.6) | 27 of 2578 | 58.4 (49.3) | 17 of 1346 | 57 | 3 | 15–155 |
| SYNTHETIC 100K, real release (round 2) | mobile | platform-v2 | 31.9 (28.1) | 40 of 1796 | 47.1 (47.1) | 56 of 1272 | 0 | 3 | 16–337 |
| SYNTHETIC 100K, real release (round 2) | mobile | v2-scale-ui | 50.6 (42) | 12 of 2318 | 59.7 (58) | 0 of 1168 | 2 | 3 | 14–346 |
| SYNTHETIC 200K, emulated (round 1) | desktop | platform-v2 | 49 (43.6) | 23 of 2202 | 58.6 (58.4) | 0 of 1173 | 0 | 3 | 43–86 |
| SYNTHETIC 200K, emulated (round 1) | desktop | v2-scale-ui | 54.8 (50.6) | 9 of 2409 | 56.8 (51.1) | 14 of 1125 | 54 | 3 | 32–85 |
| SYNTHETIC 200K, emulated (round 1) | mobile | platform-v2 | 31 (26.9) | 55 of 1475 | 54.3 (54) | 8 of 1202 | 0 | 3 | 36–83 |
| SYNTHETIC 200K, emulated (round 1) | mobile | v2-scale-ui | 56.3 (53.7) | 10 of 2481 | 59.8 (59.4) | 0 of 1122 | 2 | 3 | 48–108 |

**The same, only the runs taken at a 1-minute load below 60** (SYNTHETIC 100K, real release):

| Release | Viewport | Page | Replays: fps median (min) | Replay frames > 50 ms | Explore: fps median (min) | Explore frames > 50 ms | Tiles per explore | Runs | 1-min load |
|---|---|---|---|---|---|---|---|---|---|
| SYNTHETIC 100K, real release (round 2) | desktop | platform-v2 | 56.5 (43.4) | 7 of 1577 | 57.6 (55.3) | 2 of 775 | 0 | 2 | 28–46 |
| SYNTHETIC 100K, real release (round 2) | desktop | v2-scale-ui | 59.2 (59) | 4 of 1733 | 59.0 (58.4) | 0 of 705 | 57 | 2 | 15–21 |
| SYNTHETIC 100K, real release (round 2) | mobile | platform-v2 | 59.4 (57.5) | 1 of 861 | 59.8 (59.8) | 0 of 382 | 0 | 1 | 16–16 |
| SYNTHETIC 100K, real release (round 2) | mobile | v2-scale-ui | 56.0 (47.8) | 4 of 1617 | 58.9 (58) | 0 of 792 | 2 | 2 | 14–52 |

**What the fps numbers say.**

- **fma2000: no cost.** The scale UI costs nothing on fma2000. Replays and explore run at 55–60 fps at 4× on both widths, as on `platform-v2`; the overview is every row, so no tiles are drawn.
- **100K and 200K: the gap.** At 100K and 200K rows, `platform-v2`'s replays fall to 46–49 fps on desktop and 31–32 fps on a phone. This branch keeps 55–59 and 51–56, with fewer frames over 50 ms, while streaming 54–57 tiles per desktop explore sequence.
- **At low load: 60 fps on desktop.** At a load below 60 on the 100K release, this branch replays at 59.0–59.8 fps and explores at 58.4–59.7 fps on desktop, which is vsync-limited 60 Hz. The phone runs explore at 58–59.7. Phone replays reached 59.4–60 at load 14 and 47.8–52.6 at load 52.
- **At low load, both pages are close.** In the same conditions `platform-v2` replays at 57.5–59.8 fps at loads 16–28, and at 43–55 at load 46. The gap between the pages grows with contention.
- **Not done: a gated repeat.** A repeat on the final commit, with each run gated on a load below 60, was prepared twice (`round2/run_gated_round.sh`, `round2/tools/measure_ui_gated.mjs`, `round2/configs/gated-*`). The machine's load rose to 570–930 and no gated run started.
- **The page code measured.** Round 2 measured `e038654`'s page and round 1 `da1ef62`'s; the map code is the same. `ca3b5c8` changed only the credits page.

**First paint, fma2000-v2** (medians of 5 interleaved runs, ms; this branch with the committed set of 15 module preloads, measured in a different order):

| Viewport | CPU | Network | platform-v2: ready / map frame | v2-scale-ui: ready / map frame | Δ map frame | 1-min load |
|---|---|---|---|---|---|---|
| desktop | 1× | local | 72.6 / 70.4 | 66.4 / 70.3 | -0.1 | 178–332 |
| desktop | 1× | broadband | 578.1 / 583.2 | 479.7 / 483.3 | -99.9 | 177–332 |
| desktop | 4× | local | 253.5 / 308.5 | 245.1 / 291.8 | -16.7 | 177–332 |
| desktop | 4× | broadband | 737 / 788.4 | 622.7 / 671.9 | -116.5 | 177–326 |
| mobile | 1× | local | 69.9 / 70.5 | 63.9 / 70.4 | -0.1 | 188–326 |
| mobile | 1× | broadband | 572.1 / 568.8 | 481.4 / 483.5 | -85.3 | 188–326 |
| mobile | 4× | local | 241 / 275.5 | 231.2 / 258.6 | -16.9 | 188–323 |
| mobile | 4× | broadband | 725.5 / 772 | 621.4 / 651.9 | -120.1 | 178–332 |

**First paint, SYNTHETIC 100K** (medians of 3 interleaved runs, ms, under heavy load):

| Viewport | CPU | Network | platform-v2: ready / map frame | v2-scale-ui: ready / map frame | Δ map frame | 1-min load |
|---|---|---|---|---|---|---|
| desktop | 1× | local | 242.8 / 259.9 | 174.6 / 192.9 | -67.0 | 291–369 |
| desktop | 1× | broadband | 901 / 934.7 | 848.8 / 1009.2 | +74.5 | 291–345 |
| desktop | 4× | local | 716.3 / 885.5 | 602.4 / 717.7 | -167.8 | 291–345 |
| desktop | 4× | broadband | 1472.3 / 1571.8 | 1345 / 1438.6 | -133.2 | 292–345 |
| mobile | 1× | local | 175.7 / 193.7 | 206.4 / 209.5 | +15.8 | 294–331 |
| mobile | 1× | broadband | 1001.7 / 1010.6 | 781.9 / 804 | -206.6 | 294–331 |
| mobile | 4× | local | 573.2 / 728.7 | 609.4 / 708.7 | -20.0 | 289–331 |
| mobile | 4× | broadband | 1379 / 1486.1 | 1119.4 / 1184.8 | -301.3 | 289–325 |

### 10.7 The integration with `v2-deploy` and `rights-fix-2000`

Merged on 2026-10-06 on `v2-integrate` and fast-forwarded into `platform-v2`. `validation/INTEGRATION_RECEIPT_V2.md`, in the project workspace, has the details.

- **The merges.** Real merges, in this order: `558d6f7` (v2-deploy), `6bfae62` (rights-fix-2000), `5376b00` (this branch). Eight files conflicted in the last one; each keeps both sides.
- **The release digest.** The deploy pins the 2.1 conversion of the 1,992-track release, `b537a7ac…` (`DEPLOY_PLAN_V2.md` 3.2). `--no-lookup-index` reproduces the 2.0 conversion `279cd21b…`, the fallback if the image's SQLite lacks FTS5 trigrams. On the 2,000-row release these were `aa54e992…` and `806b19ed…`.
- **The page data.** After activation the page-data manifest is `cebbefa8…`, or `f6b89f93…` with the 2.0 release. The activation lists 38 changes, the credits page among them.
- **The paged credits.** Their introduction states the quarantine exclusion that the static page states. `CREDITS_EXCLUSIONS` equals `build_corpus_credits.QUARANTINE_EXCLUSION`, and `test_api_v2` keeps the two equal and expects 404 for the listed IDs. The server never reads the list, so the sentence holds only for a release built with the list applied. The converter and the activation do not check that; it is an open item.
- **What only the merge showed.** In a tree activated with the merged code, two rights-fix tests and the public link check failed: the web credits page there becomes the small page. Fixed in `fedde26`.
- **The security review of `v2-deploy`.** Three fixes followed, each in its own commit:
  - `87b6f11`: the activation refreshes only its own pins;
  - `b34f0ab`: a per-minute budget for `/collection/tracks`;
  - `09117d2`: the verifier keeps no cookies.
- **The serving list, added after the integration.** A request-time list holds 1,322 of fma2000's 1,992 recordings (`DEPLOY_PLAN_V2.md` section 2; `RIGHTS_QUARANTINE.md` 1.1). In v2 it reaches every collection route:
  - pages and lookups, through a SQL function in `page_query`;
  - neighbors, through an `allowed` set in both exact searches;
  - tiles, which are built over served rows only;
  - links, `rows=` reads and the paged credits; a held row answers 404.

  Held dots of the pinned overview sample stay on the map, unlabelled and unselectable.
- **The tests to run.** Both browser fixtures and the Python (170) and Node (92) suites. After stage 2, also `node tests/browser_credits_v2.mjs https://music-discovery-atlas.onrender.com web/notices/track-attribution.html`, because neither live check reads the credits pages.

### 10.8 Still open

- **The code review of the scale UI** (2026-10-06, `personal_website_2026-10-05/receipts/relay/scale-ui-code-review/REPORT.md` in the workspace). It keeps the branch merged and confirms release format 2.1, the prefilter, the paged credits and the page-jump fix. Open:
  - **F1 (High), before v2 goes public:** a hover on the map reads its row from `/collection/tracks` at once and repeats reads in flight, and that route's 300-a-minute budget is shared by every visitor, so about 15 seconds of mousing exhausts it. The fix: a 250 ms hover delay with in-flight dedupe in the page, a separate larger budget for `rows=` reads, and a test through the real map.
  - **Before a release above about 8K rows** (tiles switch on): F3, the 8× zoom cap, which keeps 200K tiles at sample density from the overview (phone z=2, desktop z=3); F4, tiles, links, credits and pages sharing one 2-worker, 8-slot queue with `no-store` replies and per-process budgets; F5, region labels at a 40% genre share with the share not shown.
  - **Lower:** F6, the credits behind a global budget; F7, no context links on the fma2000-v2 map; F8, main-thread rasterising and about 90 MB of canvases; F9, `Retry-After` ignored and failed tiles never evicted; F10, control characters in catalog text; F11, release-format nits; F12, native validation on the credits page jump; F13, failure-path test gaps.
  - **Fixed:** F2, a NUL in a lookup word answered 500 on 2.1 (`dbb99f1`).
- **The tracks budget.** Since `b34f0ab`, `/collection/tracks` has a per-minute budget (300). The 2.1 index takes rare and medium lookups from hundreds of milliseconds to a few at 100K–200K. But a word in every row, or one of one or two letters, still scans: 0.40–0.51 s at 200K in-process. A browse page near the end costs an OFFSET walk: 22–32 ms over HTTP at 100K. At 200K the cap alone would let lookups spend the anonymous CPU budget quickly; the anonymous budget counts all process CPU (`DEPLOY_PLAN_V2.md` section 7, item 6).
- **Measurement conditions.** fps and first paint were measured on a shared Mac whose load swung between 14 and 930 within minutes. The interleaved before/after pairs are the comparable figures; Render's single CPU will be slower per frame of server work, though the page's frame cost is the browser's own.
- **Synthetic graphs.** The synthetic graphs are random (100K) or JS-built with a test-only orphan repair (200K, round 1). A real 200K build still needs the reviewed connectivity repair (9.4).
- **The v1 credits page.** The checked-in v1 page still overflows on phones; it is v1 and left unchanged.
