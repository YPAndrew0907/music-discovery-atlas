# Approved 2,000-track build hydration

This deployment source uses the exact reviewed 2,000-recording corpus. The Docker build retrieves only pinned byte ranges from the official FMA archives and extracts their unchanged MP3 members. There is no full-archive fallback, new model work, re-encoding, ffmpeg dependency, GitHub audio-release upload, credential change or runtime audio download.

## Exact source and resource bounds

- Corpus release SHA-256: `af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283`
- Hydration plan SHA-256: `cc00b6d1447eb290fe2fb4883580d8ed9b06e451723a5ec79738ca189948a79b`
- 1,551 members of official fma_large.zip and 449 of fma_small.zip
- Exactly 1,951,856,486 planned range bytes; 2,034,768,876 final MP3 bytes
- Six workers, two attempts per member at most, 2.4 GB total reserved transfer including retries
- A supervising process forcibly terminates the entire threaded worker at the 3,600-second deadline, including stuck connect/header/body/decode work
- Complete output and credits are staged privately. Parent-side file hashes, inventory and delivery pins are checked again before the final delivery marker is moved into the image root
- Missing files, changed archive ETags, ignored ranges, redirects, wrong sizes/CRC/SHA, unapproved members, low disk, budget exhaustion and source denials all fail the build

Every request requires exact HTTP206, Content-Range, Content-Length and pinned ETag. ZIP headers must identify the expected member; bounded bzip2 decompression must yield its exact approved byte count, CRC and SHA-256. Source and rights evidence remain pinned. Conflicted historical fma:30702 is absent. No unready or partial collection is activated.

The 2,000 real vectors, paired model, native text profile, indexes, attribution and query/privacy behavior are unchanged. Disabled corpus selection retains the legacy 108 installer; a separately approved single archive retains its existing installer path. The tested four-shard alternative and prior 500/1,000/2,000 local releases remain separate and unchanged.

## Verified before publication

83 Python and 38 Node tests pass. Twelve hydration tests cover malformed/denied transport, retry/deadline budgets, identity mismatches, bounded decompression, low disk and no partial publication. Independent offline slow-connect, trickled HTTP header/body and stalled-decode cases were killed at about 0.402 seconds for a 0.4-second supervisor deadline; all processes/threads and private stages were gone.

All 2,000 cached approved MP3s were staged and independently rehashed with the exact delivery/rights evidence. This local cache test is explicitly not a complete network hydration test. Seven fresh official range samples totaling 7,281,051 bytes passed, including edge member155066. Reused requests were fast in that tiny sample; it does not establish full-build or Render throughput.

Native local acceptance against the staged corpus returned four description searches in 83–93 ms compute, reached readiness in 5.32 seconds and peaked at 318.6 MiB. Cancellation/replacement, token truncation, stale identity rejection, all 2,000 audio/credit records, sampled byte ranges and quarantine exclusion passed. Actual hosted playback remains a separate gate.

## Docker and existing Render route

The same Docker `python` installs requirements.lock and executes hydration. A fail-fast import/version assertion verifies Python3.12.14, requests2.34.2 and bz2 before any audio range download. A mistaken plain-cloud-Python local invocation lacked requests; the corrected project environment has the exact locked dependency. Docker/Podman were unavailable locally, so the actual Render build is the container-level verification gate.

The confirmed existing service uses the Starter build tier and a one-CPU/two-GB serving instance. No tier, instance, credential, security or spending setting is changed. Render documents Starter build resources of two CPUs/eight GB, a 16 GB build-disk limit and a 120-minute build limit. The current connector does not expose the remaining monthly build allowance; builds consume that existing allowance and may incur overage if its limits permit. Do not claim an unseen allowance is unlimited or unused.

The existing service auto-deploys main commits. Publish only this complete tested source; monitor the exact resulting deploy. If a build fails, Render keeps the most recent successful deployment. The verified last live baseline is commit6613599534e8ee51f9b3e43990f55d1ba598fa31. No half-configured source, fabricated asset URL or alternative hosting resource is required.

Official references: https://render.com/docs/build-pipeline , https://render.com/docs/docker , https://render.com/docs/deploys
