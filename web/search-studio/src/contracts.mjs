const sameKeys = ['catalogId', 'graphId', 'indexSha256', 'catalogSha256', 'vectorsSha256'];

export function validateResponse(data, {requestId, generation, engineId, deploymentGeneration, trackIds, ...expected}) {
  if (!data || data.requestId !== requestId || data.generation !== generation || data.engineId !== engineId ||
      data.deploymentGeneration !== deploymentGeneration || sameKeys.some(k => expected[k] !== undefined && data[k] !== expected[k])) {
    throw new Error('Search response identity mismatch');
  }
  if (!Array.isArray(data.results) || new Set(data.results.map(r => r.id)).size !== data.results.length ||
      data.results.some(r => !trackIds.has(r.id) || !Number.isFinite(r.cosineSimilarity) || r.cosineSimilarity < -1.00001 || r.cosineSimilarity > 1.00001)) {
    throw new Error('Invalid search results');
  }
  return data;
}

export function sameOriginApi(endpoint, pageOrigin) {
  if (!endpoint) throw new Error('No server endpoint configured');
  const origin = new URL(pageOrigin);
  const url = new URL(endpoint, origin);
  if (origin.protocol !== 'https:' || origin.origin !== pageOrigin || url.protocol !== 'https:' ||
      url.origin !== pageOrigin || url.pathname !== '/v1/' || url.search || url.hash || url.username || url.password) {
    throw new Error('A same-origin HTTPS /v1/ endpoint is required');
  }
  return url;
}

export class ServerSearch {
  // Window.fetch rejects a ServerSearch receiver. Bind only the native default;
  // callers' injected fetchers retain their existing behavior.
  constructor({endpoint = null, pageOrigin = globalThis.location?.origin, expected = null, fetcher = globalThis.fetch.bind(globalThis)} = {}) {
    this.endpoint = endpoint; this.pageOrigin = pageOrigin; this.expected = expected; this.fetcher = fetcher;
    this.generation = 0; this.abort = null; this.current = null; this.manifest = null;
  }

  route(name) { return new URL(name, sameOriginApi(this.endpoint, this.pageOrigin)); }

  async connect() {
    this.manifest = null;
    const url = this.route('manifest');
    if (!this.expected || sameKeys.some(k => typeof this.expected[k] !== 'string') || !Array.isArray(this.expected.allowedEngineIds)) {
      throw new Error('Pinned server/catalog identities are required');
    }
    const response = await this.fetcher(url, {credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error'});
    if (!response.ok) throw new Error('Server manifest unavailable');
    const manifest = await response.json();
    if (sameKeys.some(k => manifest[k] !== this.expected[k]) || !this.expected.allowedEngineIds.includes(manifest.engineId) ||
        typeof manifest.deploymentGeneration !== 'string' || !manifest.deploymentGeneration) {
      throw new Error('Server manifest identity mismatch');
    }
    this.manifest = manifest;
    return manifest;
  }

  async search(query, trackIds) {
    if (!this.manifest) throw new Error('Server search is not connected');
    if (typeof query !== 'string' || !query.trim() || query.length > 4096 ||
        new TextEncoder().encode(query).length > (this.manifest.maxQueryUtf8Bytes ?? 8000)) {
      throw new Error('Description exceeds the server input limit');
    }
    // Revalidate the origin at every transmission, including cancellation.
    const url = this.route('search');
    this.cancel();
    const abort = new AbortController(); this.abort = abort;
    const generation = ++this.generation, requestId = crypto.randomUUID(), manifest = this.manifest;
    const body = {query, requestId, generation, engineId: manifest.engineId, catalogId: manifest.catalogId,
      deploymentGeneration: manifest.deploymentGeneration, k: 16, ef: 32, trace: true};
    this.current = {requestId, generation};
    try {
      const response = await this.fetcher(url, {method: 'POST', credentials: 'same-origin', mode: 'same-origin',
        cache: 'no-store', redirect: 'error', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body), signal: abort.signal});
      if (generation !== this.generation) throw new DOMException('Search replaced', 'AbortError');
      if (!response.ok) throw new Error('Server search failed');
      const data = await response.json();
      if (generation !== this.generation) throw new DOMException('Search replaced', 'AbortError');
      return validateResponse(data, {...this.expected, ...body, trackIds});
    } finally {
      if (generation === this.generation) { this.current = null; this.abort = null; }
    }
  }

  cancel() {
    this.generation++; this.abort?.abort(); this.abort = null;
    const request = this.current; this.current = null;
    if (request && this.endpoint) {
      try {
        void this.fetcher(this.route('cancel'), {method: 'POST', credentials: 'same-origin', mode: 'same-origin',
          cache: 'no-store', redirect: 'error', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(request)}).catch(() => {});
      } catch { /* Origin validation fails closed even when cancelling. */ }
    }
  }
}
