# Optional anonymous same-origin preview

This mode is implemented for review, **disabled by default, and not deployed**. It wraps the unchanged API and encoder; authenticated hosting remains the default. No shared server credential is exposed to or required by the browser.

Activation requires all of: `MUSIC_SERVICE_MODE=anonymous-preview`, `MUSIC_ENABLE_ANONYMOUS_PREVIEW=1`, one exact `MUSIC_PUBLIC_ORIGIN=https://...` without a path or wildcard, a unique deployment generation, and no `MUSIC_API_AUTH_TOKEN`. The separate `render.anonymous.yaml.example` intentionally sets the opt-in to `0`. Do not activate it until the bundled public UI is served on that exact origin, the dataflow is disclosed and the actual image passes resource checks. The allowlisted static/config gateway and frontend are described in [PUBLIC_APP.md](PUBLIC_APP.md).

## Enforced limits

- Only GET `/v1/manifest` and POST `/v1/search` or `/v1/cancel` enter the anonymous API. `/healthz` independently exposes only readiness
- Exact Host and Origin checks; POST requires the fixed Origin. Cross-site fetch metadata and preflight requests are rejected. No CORS wildcard or cross-origin access headers are added
- Uncompressed JSON only; 4,096 request bytes, a two-second body deadline, no duplicate keys/non-finite JSON numbers, and a strict field allowlist
- Descriptions: at most 512 characters and 2,048 UTF-8 bytes. The unchanged encoder still truncates to 77 tokens and reports truncation
- Results: at most 16; graph search ef at most 32; trace at most 128 recorded events
- One inference worker with at most two admitted searches total, including queued work. Eight-second request deadline. A timed-out native job retains its admission slot until it actually exits
- Six search attempts per minute, 30 per process-hour, and a 30-process-CPU-second hourly cutoff sampled during activity. The cutoff cancels active/queued work cooperatively. Manifest/cancellation share a separate 60-per-minute control allowance so search exhaustion does not prevent cancellation
- Lowercase UUIDv4 request IDs and generation-scoped cancellation. The browser should use `crypto.randomUUID()` for each search. Request IDs act as short-lived, hard-to-guess cancellation handles, not shared credentials or user authentication. They must not be copied into analytics or logs
- Application query and access logging disabled; application log/exception messages are redacted. Provider infrastructure metadata retention remains separate

`/v1/manifest` reports the effective public limits and that audio is disabled. The frontend should use relative same-origin API URLs, display a clear query-transmission notice and unavailable/budget-exhausted states, cancel superseded requests, and discard stale generations. Failed requests must not silently fall back to another service or expose a bearer token.

## What these controls do and do not establish

This is an anonymous demonstration, not an identity boundary: a direct client can supply an Origin header. A determined visitor can consume the shared allowance and make the preview unavailable to others. Rate/CPU accounting is in memory, resets on restart, and is not a durable account quota. The CPU check includes process CPU activity and uses cooperative native cancellation; it is not an OS hard CPU ceiling, denial-of-service guarantee or billing control. Deployment still requires the hosting provider's resource limits and account/billing review. Do not increase limits or upgrade automatically after failure.

The original API's pre-cancellation tombstones remain bounded to 256 and expire after 60 seconds. Unpredictable request IDs reduce cancellation collisions; they do not establish separate anonymous-user sessions. Health and connection handling still consume a small amount of host resource outside the search allowance.

## Verified here

The real FastAPI routes, real graph loader/HNSW index and public/authenticated hosting adapters were wired together with a deterministic fake text encoder. Tests cover origin/host/content bounds, public manifest limits, actual search/trace response identity, authentication preservation, cancellation, queue admission, retained slots after timeouts, hourly/rate/CPU refusal, and body deadlines. No ONNX model was loaded and no performance claim follows from these tests.

Run the full routing checks using an environment with the pinned runtime dependencies and a compatible Starlette test client. The recovered environment's existing httpx client passed, with a deprecation warning recommending httpx2. Test-client dependencies are development-only and were not newly installed or added to the runtime lock. Run only `test_hosting.py` for the dependency-free wrapper checks.
