# Separate 1,000-track local candidate

This is a local acceptance candidate, not a deployed release. The accepted 500-track data is its exact unchanged prefix. The 500 source and audio archive were independently rehashed and remain unchanged. No GitHub or Render action was attempted for this candidate.

## Actual data

- 1,000 distinct real FMA recordings with 1,000 real 512-dimensional CLAP audio vectors
- 500 unchanged accepted recordings plus 500 additional screened recordings
- 548 source artist IDs, 545 recorded artist names and 14 top-level genres
- Recorded licenses: 612 CC BY 4.0, 286 CC BY 3.0 and 102 CC0
- Every selected audio byte hash and decoded-PCM hash is unique; complete decoding passed
- All 1,000 rights rows have per-track source evidence, attribution, retained notices and an explicit playback review decision
- Conflicted legacy fma:30702 remains excluded

The rights screen preserves upstream evidence and qualifications. It is not a guarantee of every underlying right, and the sample remains biased toward the available permissively licensed FMA material.

## Identity and preservation

Release manifest SHA-256: `f278752268eb78ceeb74b66c7ea86ba80406c5850dd16d0e78f9a1720f1c31f5`

Vector SHA-256: `a010e04960804f652012763a84827e2e7d8d4380ba869febdc2b3d6b6efb7f6b`

Canonical ordered-ID SHA-256: `5eaaa35f3e29386816d1ecc9638cdb7ab94f5f643a095b46b7b7e0a5ae58a1cd`

The full 500-row catalog and rights prefix is structurally identical to its accepted parent. Its vector prefix is byte-identical. The new index and projection are deliberately rebuilt over all 1,000 vectors; this does not alter the original 500 index or map.

## Local acceptance

- 64 Python tests and 38 Node tests passed
- Four actual native-model description searches returned distinct results, including additional tracks
- Fresh-process readiness: 2.50 seconds
- Those four searches used 44.90–52.37 ms server compute; network and browser time are excluded
- Peak process RSS across smoke/resilience checks: 302.7 MiB
- All 1,000 local audio files were verified; sampled original and additional byte ranges returned 206 with exact bytes
- Credits contained 1,000 entries; the quarantined audio route returned 404
- Admitted cancellation returned 499 and freed the slot in 2.68 ms; replacement generation succeeded
- An 88-token description was capped at 77 tokens; the old 500 catalog identity was rejected with 409
- The static web package is 20,584,073 bytes, within the unchanged 30,000,000-byte cap; vector bytes are 2,048,000

These are bounded shared-cloud process/API checks. They do not establish browser rendering, clean-container limits, public-host performance, or a live deployment. No extra inference service or account was used.

## Retrieval and map limits

Exact cosine remains the displayed ranking. Across six existing public query fixtures, the worst approximate HNSW top-eight agreement was seven of eight; the UI explains when an exact result lies outside the approximate shortlist. This is index agreement, not listener relevance.

The new t-SNE projection has measured eight-neighbor trustworthiness 0.9546, average neighbor recall 0.4608 and minimum recall 0.0. It loses information and never supplies retrieval distances. No perfect map preservation or musical-quality score is claimed.

## Reproduction and boundaries

The release, rights ledger, evidence and model identities are under `corpus-releases/fma1000/`. Validate using `scripts/verify_corpus_release.py` with its exact manifest digest and `--max-tracks 1000`. The builders write only this separate candidate directory. Synthetic unit-test fixtures remain temporary and are not music records in this dataset.

The compact review export disables activation. Model weights and the 986,348,600 bytes of audio are intentionally excluded. No new 1,000-track audio archive was built or published. Its local playback test uses the separately verified approved audio directory.

The existing deployment-approval blocker remains separate. Nothing here authorizes pushing, publishing or changing the 500 candidate. Larger local tranches must retain explicit rights, byte, memory and static-delivery checks; the current hosting targets have not been silently raised.
