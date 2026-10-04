# Public app integration

The public UI is adapted from the simplified search studio. It presents the same 108-track graph and six explicitly labeled public recorded searches. There is no personal course dataset, private deployment configuration, old Git history, audio, or model weight in this tree.

## Served routes

- `/` redirects to `/search-studio/`
- Only files listed in `web-manifest.json` are served under `/search-studio/`, `/listen-lab/` and `/notices/`
- `/deployment-config.json` is constructed from validated deployment settings and readiness; it exposes no credential
- `/audio-delivery.json` is a sanitized, catalog-bound availability response and is disabled by default
- `/v1/manifest`, `/v1/search` and `/v1/cancel` use the existing API through the selected authenticated or explicitly enabled anonymous boundary
- `/healthz` exposes only readiness; the original `/health` retains the chosen API access policy
- `/audio/NNNNNN.mp3` exists only for an explicitly approved, configured and hash-verified local preview file

The gateway refuses unlisted paths and POSTs to static routes. It validates the complete web file inventory at startup and rejects traversal/dot paths, symlinks, unexpected types, hash/size mismatches and an oversized web package. Source files, model/tokenizer directories, Git/configuration files and secrets outside `web/` have no static route. The required browser WASM file is explicitly pinned, not accepted by a broad binary-file rule.

The public UI fetches deployment and audio configuration only from its own origin, with caching disabled and fail-closed validation. Server requests use `/v1/` on that same origin and preserve request IDs, generations, identity checks, cancellation and stale-response suppression. A successful deployment configuration and matching server manifest make server search ready as the default for deliberate form submissions. The visible query-transmission disclosure links to `/notices/search-privacy.html`. Loading, typing, checking availability and switching engines never send the input. A submit before readiness is not queued: it asks the user to submit again when ready. Server failure never silently replays a recorded query. Recorded-example buttons stay explicit and local, and displayed result labels retain their original source even when readiness or engine selection changes. Explicit on-device or paused selection survives readiness completion and page restoration; restoring an unloaded local model requires opt-in again. The server secret is never exposed through public configuration or JavaScript.

The optional browser model retains its exact upstream model/tokenizer hashes and explicit Hugging Face download opt-in. No new model recipient is configured. Its pinned ONNX Runtime Web files and license notices are included. Browser inference/model downloads were not run during this integration.

## Playback availability

Results, map inspector, kept tracks and player entry points use the same availability check. An unlisted, disabled or unverified track says Preview unavailable. Catalog audio paths and historical source pages never become playback URLs automatically. The disabled manifest contains no placeholder playback URL or origin retry.

For a future approved local pack, set `MUSIC_ENABLE_AUDIO_PREVIEWS=1` and an explicit `MUSIC_AUDIO_PACK_DIR`, and supply an enabled, publicly verified delivery manifest bound to the exact catalog. Each available row must use its exact `/audio/NNNNNN.mp3` route and pinned byte count/hash. The directory must contain exactly those selected approved files, with no symlinks, nested directories or extras. The server verifies every clip before enabling routes and rechecks confinement/content before serving a request. The underlying file response supports byte ranges; that was checked only with a synthetic fixture. The exact 108-file archive was prepared separately and actual loopback range delivery passed. No public host delivery has been activated or tested. The optional build-time installer writes a separate verified local-image manifest; MUSIC_AUDIO_MANIFEST_PATH explicitly selects it.

## Remaining end-to-end gates

1. Build the exact image, resolve/record dependencies and image digest, verify component notices, and pass the bounded 512 MB/0.1 CPU startup/query/cancellation/resource checks
2. Select the concrete deployment origin and activation mode, review account/billing limits, and activate only through the separately authorized deployment flow
3. Test the actual rendered UI, all six recorded examples, repeated/cancelled searches, browser-model opt-in, disclosure, stale-response handling, mobile/keyboard layout and real sleep/wake behavior
4. Choose and verify an actual anonymous audio destination or approved image pack before enabling playback; then check real full/range responses, browser playback/seeking and every available track's credit links

Python tests use the actual app/API/graph routes with a fake text encoder. Node tests are offline contract checks. They do not establish real model inference, browser rendering, hosted latency, public audio delivery or production capacity.
