# Music Discovery Atlas: approved 2,000-track deployment

Read [HYDRATION_2000.md](HYDRATION_2000.md) for exact official-source range pins, bounded all-or-nothing build hydration, current local acceptance and deployment gates. The Docker build verifies all 2,000 audio files before the service can become ready.

The following sections retain earlier local-review history.

# Separate 2,000-track local candidate

Read [CORPUS_2000.md](CORPUS_2000.md) for real corpus identities, local acceptance, memory/performance measurements and deployment boundaries. The accepted 1,000 rows remain an exact unchanged prefix. This candidate is local and unpublished.

The following notes are retained parent-release history.

# Separate 1,000-track local candidate

Read [CORPUS_1000.md](CORPUS_1000.md) for the actual data, acceptance evidence and boundaries. This local candidate preserves the accepted 500 data prefix. It has not been pushed or deployed; the 500 source and archive remain unchanged.

The following 500 notes are retained as parent-release context.

# Music discovery: reviewed 500-track candidate

The current candidate and exact deployment gates are documented in [CORPUS_RELEASE.md](CORPUS_RELEASE.md). It has 500 real, screened FMA recordings and paired CLAP embeddings; deployment remains pending until the actual versioned audio asset URL and existing-host controls are available. The original 108 source pack remains unchanged for history and strict legacy behavior.

The following notes describe the original 108 baseline and its historical measurements; they are not measurements of the 500 candidate.

# CLAP108 music discovery preview

Experimental native CLAP text search over 108 attributed FMA-derived audio vectors. This is a small, biased demonstration catalog, not a production music catalog or a GB–TB retrieval benchmark. Cosine similarity is not a relevance probability, and ANN agreement is not a listener-quality measurement.

This repository contains a simplified public frontend, same-origin API/static gateway, the static vector/index/catalog pack, six public recorded examples, tokenizer/configuration files, the pinned browser runtime and third-party notices. It contains no audio, model weights, private course data, query histories or source Git history. The Docker build fetches one public Q8 text model from its exact Hugging Face revision and verifies 126,603,263 bytes and SHA-256 before using it. No audio tower runs online. There is no bulk audio acquisition or background downloader.

## Status

The portable configuration, frontend and real app/static/API routing checks are prepared. **The actual native service has been measured; no container image or Render deployment has been tested.** Two fresh text-only processes reached readiness in 1.95 s / 1.73 s, with 266.7 MiB OS RSS high-water and 26.2 ms / 62.2 ms median/p95 over ten public-query HTTP responses on the shared cloud CPU. See [measurement scope](NATIVE_MEASUREMENT.md). Render memory/CPU fit, image cold-start, hosted browser behavior, final wheel/system notices and image reproducibility remain unverified. The first authorized build has network effects: one exact public model asset, official package dependencies, and the optional explicitly selected public audio release. Stop if it requires a gated model, credentials, additional service or paid plan.

The original API, encoder and HNSW implementation are byte-identical to the assessed version. The deployment data loader reads only this package and verifies all assets. An outer adapter adds `/healthz`, dynamic `PORT`, single-process admission bounds, generic error logging and a fail-closed credential requirement. The graph manifest removes recorded examples and research paths while retaining its exact audio graph identity. The catalog, vector bytes, row order and index remain unchanged.

## Free Render configuration

`render.yaml` explicitly selects `plan: free`, disables automatic deploys, and adds no database, disk, custom domain or paid service. `/healthz` returns only readiness; all original API routes still require the service bearer credential. Configure that credential only through an approved secret workflow. It must never enter Git, a build argument, a log, or frontend JavaScript. Its value is deliberately absent from this repository.

The default recipe retains authenticated backend access. Its server-search workflow needs an owner-authenticated controller that holds the server credential; a shared bearer token embedded in browser code provides no private access boundary. The bundled public UI leaves server search disabled in that mode. A separate [anonymous same-origin API mode](ANONYMOUS_PREVIEW.md) is implemented behind explicit opt-in, with body deadlines, size/concurrency/rate/CPU bounds. It remains disabled. The UI checks `/deployment-config.json` and the pinned server manifest on this page's own origin. A ready, matching anonymous server is the default for deliberately submitted descriptions. The visible query-transmission notice links to the search privacy page. Typing and loading the page send no query text; a submit before readiness is not queued and must be submitted again once ready. An explicit on-device choice or paused server choice remains in effect for the page session. Recorded examples and the optional pinned browser model remain available.

Render's [Blueprint specification](https://render.com/docs/blueprint-spec) lists Free at 512 MB and 0.1 CPU. Its [free-service documentation](https://render.com/docs/free) states that services sleep after 15 idle minutes, take roughly a minute to restart, use ephemeral filesystems and cannot have persistent disks. Free hours are shared by the workspace. The model belongs in the built image so sleep does not require another download. A free plan does not itself guarantee a zero bill when a payment method and usage overages are enabled; check existing account usage and billing controls before deployment. Do not add a payment method or upgrade as a fallback.

`PORT` defaults to `10000` locally and the service binds `0.0.0.0`, as specified in [Render's web-service docs](https://render.com/docs/web-services). [Health checks](https://render.com/docs/health-checks) have a five-second response requirement. Readiness is set only after model and graph startup succeeds. The health route performs no inference.

## Before the first clean build

1. Resolve the official `python:3.12.14-slim-trixie` image to an immutable digest and record the platform. The versioned tag is listed in the [Docker official-image manifest](https://github.com/docker-library/official-images/blob/master/library/python); its digest has not been resolved here. Override `PYTHON_BASE` with the reviewed digest for a reproducible build.
2. Resolve the exact `requirements.lock` versions into wheel hashes for that image/platform, preserve applicable wheel and system-library notices, and scan the actual resolved image. The lock currently pins versions, not downloaded wheel bytes. Included dependency notices describe the recovered installed versions and are not an inventory of an unbuilt image.
3. Build using the explicit Docker context and model-fetch step. Record image digest, installed versions, fetched model hash, build duration and output size. No secret is needed to obtain this public model. Stop if a gated model or new account/credential is requested.
4. Run the bounded smoke/resource check below before configuring a live frontend endpoint. Keep the existing browser inference path available until real hosting checks pass.

## Bounded clean-container smoke/resource check

Run one container with one worker, a hard 512 MiB memory limit, no swap, and 0.1 CPU. Test two cold starts, one ordinary public development query, one long query that reaches the 77-token cap, one trace request, cancellation/replacement, stale identity rejection and an admission-overflow case. Do not run a sustained benchmark. Capture cgroup current/peak memory, OOM events, readiness duration, one warm response time and exit status. A process-only RSS figure is not the whole container budget.

Require the expected encoder ID, graph ID, catalog/vector/index hashes and result IDs from a known public development fixture. Confirm unauthenticated API requests return 401, health becomes ready only after startup, cancellation frees admission accounting, and no query/auth data appears in application or host logs. Validate the actual owner-authenticated same-origin controller, protected secret handling and rate limiting before connecting the UI. Merely exposing this bearer-protected API does not supply owner login or the frontend proxy.

Stop on any OOM, repeated timeout, host-limit violation, authentication failure, identity mismatch or unexpected charge. If the bounded test fails, retain the browser path and report the measured blocker; do not silently upgrade. Passing a few queries does not establish production capacity or latency guarantees. On Render, also verify real sleep/wake behavior and a query after a cold wake; do not use keep-alive pings to defeat free-tier sleeping.

The measured text-only native service peaked at 266.7 MiB, including verification of 108 real audio files and real model queries. This leaves promising process-level headroom but does not prove a 512 MB container fits after clean dependency resolution, page-cache accounting and hosting overhead.

## Local lightweight checks

`PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p test_hosting.py`

These three dependency-free tests exercise the health/auth routing boundary, startup readiness and log redaction. The full Python suite exercises actual FastAPI/static/config routes, the real graph with a fake encoder, path confinement and synthetic audio fixtures; it needs the existing runtime/test-client dependencies but never loads an ONNX model. Offline Node tests check frontend activation, same-origin contracts and unavailable-audio behavior. These checks do not certify the Docker image or browser rendering.

Read [PUBLIC_APP.md](PUBLIC_APP.md) for the web route/asset contract and remaining end-to-end gates.

## Audio delivery

Search metadata contains provenance paths, not verified public playback URLs. [AUDIO_DELIVERY.md](AUDIO_DELIVERY.md) describes optional verified local inputs, a bounded GitHub Release asset option, and other explicit hosting requirements. `audio-delivery.json` disables playback and contains no invented URLs. Search can remain available for tracks without a verified playable preview.

## Rights and reuse

Read [RIGHTS.md](RIGHTS.md), [track attribution](notices/track-attribution.html), and the full notices. Metadata/audio/vector licenses are separate from software/model licenses. Public visibility does not grant a new license to project-authored code; no blanket repository license has been selected. Existing third-party rights continue to apply.
