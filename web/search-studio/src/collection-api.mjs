// Release format v2: the catalog, vectors and index stay on the server. The page holds a pinned
// manifest, a down-sampled layout for the density cloud and recorded example packets, and reads
// everything else from its own origin a bounded page at a time. Rows it has seen are kept in
// sparse arrays indexed by catalog row, so the rest of the page can keep using catalog.tracks[row].
export const TRACK_ID = /^[A-Za-z0-9._:-]{1,128}$/;
const HEX64 = /^[a-f0-9]{64}$/;
export const COLLECTION_TIMEOUT_MS = 15_000;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const text = (value, max = 4096) => typeof value === 'string' && value.length <= max;
// Little-endian typed values from base64 (the tile payloads), independent of the platform's byte order.
const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;
function decode(base64, kind, length) {
  if (typeof base64 !== 'string' || base64.length > 4 * 1024 * 1024) throw new Error('Invalid map tile');
  const binary = atob(base64), bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  if (bytes.length !== length * 4) throw new Error('Invalid map tile');
  if (LITTLE_ENDIAN) return kind === 'f32' ? new Float32Array(bytes.buffer) : new Uint32Array(bytes.buffer);
  const view = new DataView(bytes.buffer), out = kind === 'f32' ? new Float32Array(length) : new Uint32Array(length);
  for (let i = 0; i < length; i++) out[i] = kind === 'f32' ? view.getFloat32(4 * i, true) : view.getUint32(4 * i, true);
  return out;
}

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
    this.reading = new Map();  // row -> the explicit-rows read in flight that brings it
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

  async get(path, params = {}, {signal = null} = {}) {
    const url = new URL(path, this.api);
    for (const [key, value] of Object.entries(params)) if (value !== '' && value !== null && value !== undefined) url.searchParams.set(key, String(value));
    if (url.origin !== this.api.origin || !url.pathname.startsWith('/collection/')) throw new Error('Collection reads stay on this origin');
    this.requests++;
    const timeout = AbortSignal.timeout(COLLECTION_TIMEOUT_MS);
    const response = await this.fetcher(url, {credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
      signal: signal && AbortSignal.any ? AbortSignal.any([signal, timeout]) : signal ?? timeout});
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

  // Display metadata for explicit rows the page has not seen, 64 a read. A row that a read in flight already
  // asks for is not asked for again: the call waits for that read, so hover, click and any other caller share
  // one request per row. When a read fails, every caller waiting for it fails, and its rows can be asked for again.
  async ensure(rows) {
    const missing = [...new Set(rows)].filter(row => Number.isInteger(row) && row >= 0 && row < this.count && !this.tracks[row]);
    const waits = new Set(missing.filter(row => this.reading.has(row)).map(row => this.reading.get(row)));
    const fresh = missing.filter(row => !this.reading.has(row));
    if (fresh.length) {
      const read = (async () => {
        for (let i = 0; i < fresh.length; i += 64) {
          const part = fresh.slice(i, i + 64), body = await this.get('tracks', {rows: part.join(',')});
          if (!Array.isArray(body?.rows) || body.rows.length !== part.length) throw new Error('Invalid collection rows');
          body.rows.forEach((track, j) => { if (this.absorbTrack(track) !== part[j]) throw new Error('Invalid collection rows'); });
        }
      })();
      for (const row of fresh) this.reading.set(row, read);
      const settled = () => { for (const row of fresh) if (this.reading.get(row) === read) this.reading.delete(row); };
      read.then(settled, settled);
      waits.add(read);
    }
    await Promise.all(waits);
  }

  inFlight(row) { return this.reading.has(row); }

  async neighbors(row) {
    const body = await this.get('neighbors', {row});
    const m = this.manifest;
    if (!body || body.catalogId !== m.catalogId || body.graphId !== m.graphId || body.indexSha256 !== m.indexSha256 ||
        body.releaseSha256 !== m.releaseSha256 || body.row !== row) throw new Error('Neighbor packet identity mismatch');
    return {ranked: this.absorbPacket(body, {k: 16}), trace: body.trace, timingMs: body.timingMs};
  }

  // The pinned map overview (layout schema 3): the density-cloud sample (rows, positions, optional weights),
  // region labels per zoom level and, when the sample is not every row, the tile pyramid that
  // /collection/tiles serves. Stored index links are read per recording from /collection/links.
  loadLayout(layout) {
    const m = this.manifest;
    if (!layout || layout.schemaVersion !== 3 || layout.kind !== 'music-layout-lod-v2' || layout.releaseSha256 !== m.releaseSha256 ||
        layout.graphId !== m.graphId || layout.count !== this.count || !Array.isArray(layout.rows) || layout.rows.length !== layout.sampleCount ||
        layout.xy?.length !== layout.rows.length * 2 || (layout.weights && layout.weights.length !== layout.rows.length) ||
        new Set(layout.rows).size !== layout.rows.length || layout.links !== '/collection/links' ||
        !Array.isArray(layout.bounds) || layout.bounds.length !== 4 || !layout.bounds.every(finite)) throw new Error('Invalid layout sample');
    this.absorbLayout(layout);
    const points = layout.rows.map(row => this.positions[row]);
    const weights = layout.weights ? layout.weights.map(w => (Number.isSafeInteger(w) && w > 0 ? w : 1)) : null;
    const levels = layout.regions?.levels;
    if (!Array.isArray(levels) || levels.length > 4) throw new Error('Invalid map regions');
    const regions = levels.map(level => {
      if (!finite(level?.fromDetail) || !finite(level.toDetail) || level.fromDetail < 0 || level.toDetail <= level.fromDetail ||
          !Array.isArray(level.items) || level.items.length > 256) throw new Error('Invalid map regions');
      const items = level.items.filter(item => {
        if (!finite(item?.x) || !finite(item.y) || !Number.isSafeInteger(item.count) || item.count < 1 ||
            (item.label !== null && !text(item.label, 64))) throw new Error('Invalid map regions');
        return item.label !== null;
      }).map(({x, y, count, label}) => ({x, y, count, label}));
      return {from: level.fromDetail, to: level.toDetail, items};
    });
    let tiles = null;
    if (layout.tiles !== null) {
      const t = layout.tiles;
      if (layout.sampleCount >= this.count || t?.api !== '/collection/tiles' || !Array.isArray(t.domain) || t.domain.length !== 3 ||
          !t.domain.every(finite) || !(t.domain[2] > 0) || !Number.isSafeInteger(t.cap) || t.cap < 1 || t.cap > 65536 ||
          !Number.isSafeInteger(t.maxLevel) || t.maxLevel < 0 || t.maxLevel > 16) throw new Error('Invalid map tiles');
      tiles = {domain: [...t.domain], cap: t.cap, maxLevel: t.maxLevel};
    }
    this.tileSpec = tiles;
    const [x0, y0, x1, y1] = layout.bounds;
    return {points, rows: [...layout.rows], weights, connections: [], regions, tiles, bounds: {x0, y0, x1, y1}, description: layout.description};
  }

  // One map tile: every row of its square when it is complete, else a weighted stratified sample. Rows and
  // positions are checked, and positions must agree with any the page already holds.
  async tile(z, x, y, {signal} = {}) {
    const spec = this.tileSpec;
    if (!spec || !Number.isInteger(z) || z < 0 || z > spec.maxLevel || !Number.isInteger(x) || !Number.isInteger(y) ||
        x < 0 || y < 0 || x >= 2 ** z || y >= 2 ** z) throw new Error('Invalid map tile request');
    const body = await this.get('tiles', {z, x, y}, {signal});
    const count = body?.count;
    if (!body || body.z !== z || body.x !== x || body.y !== y || !Array.isArray(body.domain) || body.domain.some((v, i) => v !== spec.domain[i]) ||
        body.cap !== spec.cap || !Number.isSafeInteger(count) || count < 0 || count > spec.cap || !Number.isSafeInteger(body.total) ||
        body.total < count || body.total > this.count || typeof body.complete !== 'boolean' || (body.complete && body.total !== count) ||
        (body.complete !== (body.weights === null))) throw new Error('Invalid map tile');
    const rows = decode(body.rows, 'u32', count), xy = decode(body.xy, 'f32', 2 * count), weights = body.weights === null ? null : decode(body.weights, 'u32', count);
    for (let i = 0; i < count; i++) {
      const row = rows[i], px = xy[2 * i], py = xy[2 * i + 1], known = this.positions[row];
      if (row >= this.count || (i && row <= rows[i - 1]) || !Number.isFinite(px) || !Number.isFinite(py) || (weights && weights[i] < 1)) throw new Error('Invalid map tile');
      if (known && (known[0] !== px || known[1] !== py)) throw new Error('Map tile position changed');
    }
    for (let i = 0; i < count; i++) if (!this.positions[rows[i]]) this.positions[rows[i]] = [xy[2 * i], xy[2 * i + 1]];
    return {z, x, y, total: body.total, complete: body.complete, rows, xy, weights};
  }

  // The stored index links of one recording, level by level, with positions for it and every linked row.
  async links(row, {signal} = {}) {
    const body = await this.get('links', {row}, {signal}), m = this.manifest;
    if (!body || body.row !== row || body.graphId !== m.graphId || body.indexSha256 !== m.indexSha256 || !Array.isArray(body.levels) ||
        body.levels.length > 32 || body.levels.some(level => !Array.isArray(level) || level.length > 256 ||
          level.some(n => !Number.isInteger(n) || n < 0 || n >= this.count || n === row))) throw new Error('Invalid stored links');
    this.absorbLayout(body.layout);
    if (!this.positions[row] || body.levels.some(level => level.some(n => !this.positions[n]))) throw new Error('Stored links lack positions');
    return {row, levels: body.levels.map(level => [...level])};
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
