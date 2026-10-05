# Separate 2,000-track local candidate

The accepted 1,000-track catalog, rights and vector matrix are exact unchanged prefixes. The prior 500 and 1,000 source trees and the original 500 audio archive remain unchanged. This candidate has not been published or deployed.

## Actual corpus

- 2,000 distinct audio recordings and real 512-dimensional paired CLAP vectors
- 1,000 retained recordings plus 1,000 newly acquired, fully decoded recordings
- 551 source artist IDs, 548 artist names and 15 top-level genres
- 1,373 CC BY 4.0, 448 CC BY 3.0 and 179 CC0 license records
- 2,034,768,876 local audio bytes; 7,019,915 bytes of pinned rights evidence
- 2,000 unique audio-file and decoded-PCM hashes; 2,000 unique vector byte hashes
- Ten rejected candidates: four short excerpts and six content duplicates; their replacements were independently checked
- Conflicted historical recording fma:30702 is excluded

This is an upstream-evidence rights screen with retained attribution, source notices, modifications and playback decisions. It cannot independently guarantee every underlying copyright. The open-music sample remains biased; this stage adds recording depth more than artist breadth. It is not a mainstream catalog or a listener relevance evaluation.

## Immutable identities

Baseline repository: https://github.com/YPAndrew0907/music-discovery-atlas

Baseline code commit: `6613599534e8ee51f9b3e43990f55d1ba598fa31`

Release manifest SHA-256: `af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283`

Vectors SHA-256: `12c1dfc0c23f27c14d0530799a284df92bb3a740ef4a22749aab53d00af2ae8d`

Ordered IDs file SHA-256: `43a0444151ed99df4c7663c770c0f9439603c8ebb262dac7e55b50479e7f95d0`

Canonical ordered-ID SHA-256: `d449dd08c0e6592558c35cac7ba4e33c365324003c1019a1f0e09bd4e844a66f`

Every new audio hash matches the approved catalog and disk. Every vector receipt binds the same recording and exact paired model/preprocessor. The strict LF-only handoff audit preserves all 14 literal U+2028 characters in the fma:116154 notice; Unicode separators never truncate a JSONL record.

## Local acceptance

- 71 Python tests and 38 Node tests passed
- Four actual description searches: 97.2–102.5 ms native compute, 108.0–115.3 ms in-process request time
- 4.07 seconds local readiness; 316.6 MiB peak process RSS across measured processes
- Cancellation returned 499, released admission accounting in 3.52 ms, and a replacement generation returned eight results
- Long input was explicitly truncated from 88 to 77 tokens; stale 1,000-catalog requests returned 409
- All 2,000 local preview files and attribution rows validated; three sampled byte ranges returned 206; excluded 030702.mp3 returned 404
- The generated web assets total 26,737,761 bytes under the unchanged 30 MB static cap
- Six fixed-query Node searches: worst median exact scan 1.58 ms and graph traversal 0.70 ms, excluding model/network/browser work
- t-SNE trustworthiness: 0.9679; mean 8-neighbor recall: 0.4595, minimum: 0. Projection never determines retrieval ranking

These are local CPU/API checks. Hosted browser rendering, public playback, Render memory limits, container builds and deployed behavior are unmeasured.

## Packed vector storage

The candidate HNSW implementation stores immutable packed float32 rows rather than one Python float object per component. Scores continue to accumulate in the same binary64 order. Actual 1,000- and 2,000-row differential tests match exact scores and complete graph traces. Independent checks passed 3,192 comparisons, 32 cancellation cases and five mutable-buffer cases. Big-endian handling was reviewed, not executed.

A six-process 1,000-row microbenchmark reduced vector storage from 16.43 MB to 2.24 MB and resident memory by about 14.7 MiB. Median exact scanning increased from 28.1 to 31.1 ms; tracing remained about 7.8 ms. This trades modest compute for lower memory, with unchanged results.

## Reproduce and deployment boundary

The review export has `active-corpus.json` disabled. Its legacy 108 strict pins remain the default. `review/local-tested-selection.json` records the explicit local selection used in acceptance; its audio archive is null because no 2,000-track public asset exists. Do not activate this candidate on the live service without a reviewed delivery plan, actual asset URLs, verified host permissions and fresh full acceptance.

From the source root, run `python -m unittest discover -s tests -p 'test_*.py'` and `node --test tests/*.test.mjs` using the documented project dependencies. The review folder includes native smoke/resilience scripts, storage differential scripts and benchmarks. Real model/audio inputs are separately required for native checks and are omitted from this compact review bundle.

The code/data builders accept the completed ingestion files and paired embedding export, check the exact 1,000 prefix, verify every hash and rights row, rebuild the index, saved examples and unsupervised map, and write a separately pinned release. No synthetic music or invented embeddings are used.
