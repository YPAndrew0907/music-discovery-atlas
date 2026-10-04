# Native text-only service measurement

On 2026-10-04, two fresh Python 3.12.14 processes ran the actual packaged API, Q8 text encoder, HNSW index, 108-row catalog and vectors. The model was already present and hash-verified. All 108 original MP3 files were checked before the audio routes were enabled in an isolated measurement context. No audio tower or Torch was loaded. There was no download or private query.

The service listened only on 127.0.0.1. The client sent actual HTTP requests with a fixed test Host/Origin. Startup-to-health readiness was 1,951.6 ms and 1,730.4 ms. Ten public-description requests (five per process, including long input) took 26.2 ms median and 62.2 ms p95. The OS high-water was 279,625,728 bytes (266.7 MiB); 10 ms polling observed 271,151,104 bytes. These are small-sample observations from a shared AMD EPYC cloud CPU with concurrent low-priority rendering.

The correct model/catalog/graph identities, eight returned results and same-query traces were checked. The second process verified 77-token truncation, 409 stale-identity rejection, cancellation-control response and a 206 MP3 byte-range response matching the original bytes. Concurrent cancellation and queue admission have separate app tests; this measurement did not repeat them.

The first run preserved a harness error: its supposed long prompt had 64 tokens, so it correctly did not truncate and the test assertion failed. Only the prompt fixture was corrected to 80 repetitions of the public word piano for the second process; model/API code was unchanged. Startup and five successful query measurements from the first process are retained. Its later range/stale/cancel checks did not run.

This is not a clean-container/Render result. The OS file cache was not flushed; no image pull, cgroup memory cap, 0.1 CPU throttle, provider cold wake, external TLS/network latency or whole-container peak was measured. The public delivery configuration remains disabled. Numerical retrieval and graph integrity are not subjective relevance judgments.

NATIVE_MEASUREMENT.json contains the aggregate observed values and limits. The exact public model is Xenova/clap-htsat-unfused at c28f2883575e590e04d3146ff0713c2448d691ba, using the pinned Q8 text artifact with SHA-256 1a3df8b197e249816e08415fd040434c44762b2eea7eb7bf8a48a0f0bf3c14e5. ONNX Runtime 1.23.2 CPU, one intra/inter-op thread, sequential execution, all graph optimizations.
