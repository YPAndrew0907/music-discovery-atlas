# Reviewed FMA500 release candidate

This candidate contains 500 real FMA recordings, actual paired 512-dimensional CLAP audio vectors, a rebuilt HNSW index, a new t-SNE map and six re-evaluated public recorded-query examples. It does not claim 5,000 or 10,000 tracks.

## Selection and provenance

- 107 retained original recordings and 393 additional recordings
- 198 official source artist IDs,197 distinct recorded artist names and 10 top-level genres
- 333 tracks recorded as CC BY 4.0,141 as CC BY 3.0 and 26 as CC0
-Every selected recording passed complete audio decoding, exact byte/hash checks and decoded-content uniqueness checks
-Every selected rights row retains source URLs, creator/title attribution, supplied notices, a review record and a pinned local copy of official FMA metadata evidence
-Legacy fma:30702 is excluded from the new release pending resolution of inconsistent track-level and album-level license notices. Its historical record and vector remain unchanged in the original 108 source pack

The rights screen is based on recorded upstream grants and notices, not a guarantee of every underlying right. Metadata is separately attributed under CC BY 4.0. No artist or dataset endorsement is implied.

## Exact release identity

Native release manifest SHA-256: `764eaf5ee9a8e2e651f70a29de122a12b6855d4dcfa80c307b2b36affaf149d4`

Catalog ID: `fma500:49bc40f97a3bcd643ba78d23085af4651632ada2ce4065e6f9fa1ebba74fe578`

Graph ID: `experimental-clap-audio-graph:cbe385038ebccd7acbe47db264b5480b29530236802c32937313ba6cbdaf705d`

The authoritative rights ledger, per-track evidence, dimensional identities, hashes, embedding receipts and build receipt are under `corpus-releases/fma500/`. The original 108 catalog/vector/model pins remain unchanged under `music-search-studio/data/` and `model/`.

## What changed in the application

An explicit package-pinned `active-corpus.json` can select a validated release. Absent or disabled selection retains strict legacy 108 behavior. Active native search consumes the validated immutable asset snapshots; it does not reopen candidate vectors/indexes after validation. The encoder implementation and model/tokenizer identities remain unchanged. No API credential is exposed or created.

The public UI uses the new 500-track identities, real rebuilt graph/layout and updated credits. Automatic graph framing, replay controls, natural-language search, title/artist lookup, neighbor search and verified preview gating remain available. The map is an approximation and never supplies retrieval distances. At 8 neighbors, measured two-dimensional trustworthiness was 0.9531 and average neighbor recall 0.5323; the minimum recall was 0.0, so local map distances must not be read as exact similarity.

The canvas patch reuses its backing store, caches map bounds and avoids quadratic overview hit testing. Synthetic geometry tests are explicitly separate from actual music data.

## Audio delivery

The approved archive contains exactly 500 selected MP3s, full credits and pinned evidence. Audio bytes total 509,588,579. The archive is 514,756,597 bytes with SHA-256 `5d2e464f87a45a35e9318ad444c80b358905bf83ac13589114c43834bae02d21`.

The build-time installer accepts only an explicitly pinned versioned Release asset from the existing music-discovery-atlas repository. It bounds download time/bytes, archive inventory and every extraction, and verifies all audio/credits/evidence before publishing local files. There is no runtime bulk downloader, new host, new account or paid-service fallback.

The actual public audio asset URL is a deployment prerequisite. Never fill it with a guessed URL. The code/index review bundle deliberately ships activation disabled and a separate pending selection record. It must not be deployed until the real returned asset URL is pinned, the package inventory is refreshed and the final checks pass.

## Local verification

- 60 Python tests and 38 Node tests passed on the active 500 candidate
-Actual native Q8 inference returned distinct results for three new descriptions, including new tracks
-Fresh-process readiness:1.73 seconds; three server compute times:32.27,34.40 and 35.81 milliseconds
-Peak process RSS:287.2 MiB
-All 500 preview files verified at installation; sampled original/new byte ranges returned 206 with exact bytes
-Credits contained 500 entries; the quarantined audio route returned 404
-All six saved public query vectors achieved exact/ANN top 8 agreement on the new graph; this is not a listener-relevance measurement

These are bounded shared-cloud process/API tests. Browser rendering, a clean Docker image, actual Render memory/CPU fit, public playback and sleep/wake still require final hosted acceptance. No container CLI was available for a clean local build.

## Reproduction

The offline contract validator is `scripts/verify_corpus_release.py`. Pass the exact release directory and the separately reviewed manifest digest.

`scripts/build_corpus_release.py`, `build_corpus_graph.mjs` and `build_corpus_web.py` rebuild the selected catalog/index/examples/map from approved input records and real embedding bytes. `scripts/package_corpus_audio.py` accepts an audio directory containing exactly the approved selection; it never uploads. The t-SNE build used scikit-learn 1.8.0, NumPy 2.3.5 and one thread. The pinned native runtime remains defined in `requirements.lock` and `runtime-assets.json`.

For the checked-in tests, use `python -m unittest discover -s tests -p 'test_*.py'` in the dependency-complete runtime and `node --test tests/*.test.mjs`. Unit-test corpus fixtures are synthetic and clearly marked; none is included in the 500-recording release.

This 500 release fits the existing static byte limits. The future 5k–10k step still needs additional screened data, embeddings, capacity measurements and a delivery design;10k float 32 vectors alone exceed the current 20,000,000-byte static-asset cap. No limit was silently increased for this release.
