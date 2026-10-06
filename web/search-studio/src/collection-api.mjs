// Release format v2: the catalog, vectors and index stay on the server. The page holds a pinned
// manifest, a down-sampled layout for the density cloud and recorded example packets, and reads
// everything else from its own origin a bounded page at a time. Rows it has seen are kept in
// sparse arrays indexed by catalog row, so the rest of the page can keep using catalog.tracks[row].
export const TRACK_ID = /^[A-Za-z0-9._:-]{1,128}$/;
const HEX64 = /^[a-f0-9]{64}$/;
export const COLLECTION_TIMEOUT_MS = 15_000;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const text = (value, max = 4096) => typeof value === 'string' && value.length <= max;

export function validateCollectionManifest(manifest, pageOrigin) {
  const fail = message => { throw new Error('Collection manifest: ' + message); };
  if (!manifest || manifest.format !== 2 || manifest.schemaVersion !== 2) fail('not a v2 collection');
  if (!Number.isSafeInteger(manifest.count) || manifest.count < 1 || manifest.dimensions !== 512) fail('count');
  for (const key of ['releaseSha256', 'indexSha256', 'catalogSha256', 'vectorsSha256', 'orderedIdsSha256'])
    if (!HEX64.test(manifest[key] ?? '')) fail(key);
  if (!text(manifest.catalogId, 256) || !text(manifest.graphId, 256) || !manifest.graphId.startsWith('experimental-clap-audio-graph:')) fail('identity');
  if (manifest.collectionApi !== '/collection/') fail('collection API');
  const api = new URL(manifest.collectionApi, pageOrigin);
  if (api.origin !== new URL(pageOrigin).origin) fail('collection API origin');
  for (const name of ['layout', 'examples']) {
    const pin = manifest.files?.[name];
    if (!pin || pin.path !== name + '.json' || !Number.isSafeInteger(pin.bytes) || !HEX64.test(pin.sha256 ?? '')) fail(name + ' pin');
  }
  const policy = manifest.audioPolicy;
  if (!policy || !['local', 'remote', 'disabled'].includes(policy.mode)) fail('audio policy');
  if (policy.mode === 'remote') {
    const origin = new URL(policy.origin);
    if (origin.protocol !== 'https:' || origin.origin !== policy.origin || !/^\/(?:[A-Za-z0-9_-]{1,64}\/){0,4}$/.test(policy.pathPrefix ?? '')) fail('remote audio origin');
  }
  if (!Array.isArray(manifest.allowedQueryProfiles) || !manifest.allowedQueryProfiles.length) fail('query profiles');
  return manifest;
}

export class Collection {
  constructor({manifest, pageOrigin, fetcher = globalThis.fetch?.bind(globalThis)}) {
    this.manifest = validateCollectionManifest(manifest, pageOrigin);
    this.count = manifest.count;
    this.pageOrigin = pageOrigin;
    this.api = new URL(manifest.collectionApi, pageOrigin);
    this.fetcher = fetcher;
    this.tracks = new Array(this.count);     // sparse: a hole until the row has been seen
    this.positions = new Array(this.count);  // sparse [x, y], shared with the map
    this.rowById = new Map();
    this.artists = new Map();
    this.requests = 0;
  }

  absorbTrack(track) {
    const row = track?.row;
    if (!Number.isInteger(row) || row < 0 || row >= this.count || !TRACK_ID.test(track.id ?? '') ||
        !text(track.title) || !text(track.artist) || (track.album !== null && track.album !== undefined && !text(track.album)) ||
        (track.genre !== null && track.genre !== undefined && !text(track.genre, 256)) || !text(track.license, 64) ||
        !Array.isArray(track.position) || track.position.length !== 2 || !track.position.every(finite) ||
        !Number.isSafeInteger(track.audioBytes) || !HEX64.test(track.audioSha256 ?? '')) throw new Error('Invalid collection row');
    const known = this.tracks[row];
    if (known && known.id !== track.id) throw new Error('Collection row identity changed');
    if (this.rowById.has(track.id) && this.rowById.get(track.id) !== row) throw new Error('Collection row identity changed');
    if (!known) this.tracks[row] = Object.freeze({id: track.id, title: track.title, artist: track.artist, album: track.album ?? null,
      genre: track.genre ?? null, license: track.license, audioBytes: track.audioBytes, audioSha256: track.audioSha256, artistId: track.artistId ?? null});
    this.positions[row] = [track.position[0], track.position[1]];
    this.rowById.set(track.id, row);
    if (typeof track.artistId === 'string' && track.artistId) this.artists.set(track.id, track.artistId);
    return row;
  }

  absorbLayout(layout) {
    const {rows, xy} = layout ?? {};
    if (!Array.isArray(rows) || !Array.isArray(xy) || xy.length !== rows.length * 2) throw new Error('Invalid layout positions');
    rows.forEach((row, i) => {
      if (!Number.isInteger(row) || row < 0 || row >= this.count || !finite(xy[2 * i]) || !finite(xy[2 * i + 1])) throw new Error('Invalid layout positions');
      this.positions[row] = [xy[2 * i], xy[2 * i + 1]];
    });
  }

  // A search, neighbor or example packet: ordered results whose rows arrive with their display
  // metadata, and a position for every row its trace or results mention.
  absorbPacket(packet, {k = 20} = {}) {
    const results = packet?.results;
    if (!Array.isArray(results) || results.length > k || !Array.isArray(packet.tracks)) throw new Error('Invalid search packet');
    for (const track of packet.tracks) this.absorbTrack(track);
    this.absorbLayout(packet.layout);
    results.forEach((r, i) => {
      if (!Number.isInteger(r.row) || this.tracks[r.row]?.id !== r.id || !finite(r.cosineSimilarity) ||
          r.cosineSimilarity < -1.00001 || r.cosineSimilarity > 1.00001 || (i && r.cosineSimilarity > results[i - 1].cosineSimilarity))
        throw new Error('Invalid search results');
    });
    for (const event of packet.trace?.events ?? []) {
      const ids = [...(event.entryIds ?? []), ...(Number.isInteger(event.id) ? [event.id] : []), ...(event.considered ?? []).map(c => c.id),
        ...(event.frontier ?? []).map(c => c.id), ...(event.retained ?? []).map(c => c.id), ...(event.items ?? []).map(c => c.id)];
      if (ids.some(id => !this.positions[id])) throw new Error('Search trace lacks positions');
    }
    return results.map(r => ({row: r.row, score: r.cosineSimilarity}));
  }

  async get(path, params = {}) {
    const url = new URL(path, this.api);
    for (const [key, value] of Object.entries(params)) if (value !== '' && value !== null && value !== undefined) url.searchParams.set(key, String(value));
    if (url.origin !== this.api.origin || !url.pathname.startsWith('/collection/')) throw new Error('Collection reads stay on this origin');
    this.requests++;
    const response = await this.fetcher(url, {credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(COLLECTION_TIMEOUT_MS)});
    let body = null;
    try { body = await response.json(); } catch { /* not JSON */ }
    if (!response.ok) {
      const error = new Error(typeof body?.error === 'string' ? body.error : 'Collection read failed');
      error.status = response.status;
      throw error;
    }
    return body;
  }

  async page({channel, q = '', text: filterText = '', genre = '', previewOnly = false, offset = 0, limit = 12, facets = false}) {
    const body = await this.get('tracks', {q: channel === 'lookup' ? q : '', text: filterText, genre, preview: previewOnly ? 1 : '',
      offset, limit, facets: facets ? 1 : ''});
    if (!body || body.channel !== channel || !Number.isSafeInteger(body.total) || !Number.isSafeInteger(body.baseTotal) ||
        body.offset !== offset || !Array.isArray(body.rows) || body.rows.length > limit || body.total > this.count || body.baseTotal > this.count ||
        (facets && !Array.isArray(body.genres))) throw new Error('Invalid collection page');
    const rows = body.rows.map(track => this.absorbTrack(track));
    if (new Set(rows).size !== rows.length) throw new Error('Invalid collection page');
    const genres = facets ? body.genres.filter(g => text(g?.genre, 256) && Number.isSafeInteger(g.count)).map(g => ({genre: g.genre, count: g.count})) : null;
    return {rows, total: body.total, baseTotal: body.baseTotal, genres};
  }

  async ensure(rows) {
    const missing = [...new Set(rows)].filter(row => Number.isInteger(row) && row >= 0 && row < this.count && !this.tracks[row]);
    for (let i = 0; i < missing.length; i += 64) {
      const part = missing.slice(i, i + 64), body = await this.get('tracks', {rows: part.join(',')});
      if (!Array.isArray(body?.rows) || body.rows.length !== part.length) throw new Error('Invalid collection rows');
      body.rows.forEach((track, j) => { if (this.absorbTrack(track) !== part[j]) throw new Error('Invalid collection rows'); });
    }
  }

  async neighbors(row) {
    const body = await this.get('neighbors', {row});
    const m = this.manifest;
    if (!body || body.catalogId !== m.catalogId || body.graphId !== m.graphId || body.indexSha256 !== m.indexSha256 ||
        body.releaseSha256 !== m.releaseSha256 || body.row !== row) throw new Error('Neighbor packet identity mismatch');
    return {ranked: this.absorbPacket(body, {k: 16}), trace: body.trace, timingMs: body.timingMs};
  }

  // The pinned density-cloud sample: sampled rows, optional weights and the stored index links between them.
  loadLayout(layout) {
    const m = this.manifest;
    if (!layout || layout.releaseSha256 !== m.releaseSha256 || layout.graphId !== m.graphId || layout.count !== this.count ||
        !Array.isArray(layout.rows) || layout.rows.length !== layout.sampleCount || layout.xy?.length !== layout.rows.length * 2 ||
        (layout.weights && layout.weights.length !== layout.rows.length) || !Array.isArray(layout.edges) || layout.edges.length % 3 ||
        !Array.isArray(layout.bounds) || layout.bounds.length !== 4 || !layout.bounds.every(finite)) throw new Error('Invalid layout sample');
    this.absorbLayout(layout);
    const points = layout.rows.map(row => this.positions[row]);
    const weights = layout.weights ? layout.weights.map(w => (Number.isSafeInteger(w) && w > 0 ? w : 1)) : null;
    const sampled = new Set(layout.rows), connections = [];
    for (let i = 0; i < layout.edges.length; i += 3) {
      const [from, to, level] = layout.edges.slice(i, i + 3);
      if (!sampled.has(from) || !sampled.has(to) || from >= to || !Number.isInteger(level) || level < 0) throw new Error('Invalid layout links');
      connections.push({from, to, level});
    }
    const [x0, y0, x1, y1] = layout.bounds;
    return {points, weights, connections, bounds: {x0, y0, x1, y1}, description: layout.description};
  }

  loadExamples(examples) {
    const m = this.manifest;
    if (!examples || examples.releaseSha256 !== m.releaseSha256 || examples.graphId !== m.graphId || examples.indexSha256 !== m.indexSha256 ||
        !Array.isArray(examples.examples) || !examples.examples.length) throw new Error('Invalid recorded examples');
    for (const item of examples.examples) {
      if (!text(item.id, 64) || !text(item.label, 128) || !text(item.text) || !HEX64.test(item.queryVectorSha256 ?? '')) throw new Error('Invalid recorded examples');
      item.ranked = this.absorbPacket(item, {k: 16});
    }
    return examples;
  }
}

// Map-like preview availability (get/size/delete), as previewForTrack() expects, from the v2
// summary's bitset of available rows and the audio policy pinned in the collection manifest.
export class RowDelivery {
  constructor(collection = null, summary = null) {
    this.collection = collection; this.bits = new Uint8Array(0); this.removed = new Set(); this.available = 0; this.mode = 'disabled';
    if (!collection || !summary) return;
    const m = collection.manifest, policy = m.audioPolicy;
    if (summary.schemaVersion !== 2 || summary.kind !== 'music-audio-delivery-v2' || summary.catalogId !== m.catalogId ||
        summary.catalogSha256 !== m.catalogSha256 || summary.releaseSha256 !== m.releaseSha256 || summary.enabled !== true ||
        summary.publicDeliveryVerified !== true || summary.mode !== policy.mode || summary.total !== collection.count) return;
    if (summary.mode === 'remote' && (summary.origin !== policy.origin || summary.pathPrefix !== policy.pathPrefix)) return;
    let bits;
    try { bits = Uint8Array.from(atob(summary.availableRows ?? ''), c => c.charCodeAt(0)); } catch { return; }
    if (bits.length !== Math.ceil(collection.count / 8)) return;
    let count = 0;
    for (const byte of bits) for (let b = byte; b; b &= b - 1) count++;
    if (count !== summary.available) return;
    this.bits = bits; this.available = count; this.mode = summary.mode;
  }

  get size() { return Math.max(0, this.available - this.removed.size); }
  delete(id) { if (this.get(id)) this.removed.add(id); }

  get(id) {
    const c = this.collection, row = c?.rowById.get(id), track = row === undefined ? null : c.tracks[row];
    if (!track || this.removed.has(id) || !(this.bits[row >> 3] >> (row & 7) & 1)) return undefined;
    let url;
    if (this.mode === 'local') {
      if (!/^fma:\d{1,6}$/.test(id)) return undefined;
      url = new URL('/audio/' + id.slice(4).padStart(6, '0') + '.mp3', c.pageOrigin);
      if (url.origin !== new URL(c.pageOrigin).origin) return undefined;
    } else if (this.mode === 'remote') {
      const policy = c.manifest.audioPolicy;
      url = new URL(policy.pathPrefix + track.audioSha256 + '.mp3', policy.origin);
      if (url.origin !== policy.origin || url.search || url.hash) return undefined;
    } else return undefined;
    return Object.freeze({id, url: url.href, bytes: track.audioBytes, sha256: track.audioSha256});
  }
}

export async function loadRowDelivery(collection, {fetcher = collection.fetcher} = {}) {
  try {
    const response = await fetcher(new URL('/audio-delivery.json', collection.pageOrigin), {
      credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error'});
    if (!response.ok) return new RowDelivery();
    return new RowDelivery(collection, await response.json());
  } catch {
    return new RowDelivery();
  }
}
