export const UNAVAILABLE_PREVIEW = 'Preview unavailable';

// Catalog audio paths and source pages describe provenance, not delivery.
export function validateAudioDelivery(data, {catalog, catalogSha256, pageOrigin}) {
  const unavailable = new Map();
  if (!data || data.schemaVersion !== 1 || data.catalogId !== catalog.id ||
      data.catalogSha256 !== catalogSha256 || data.enabled !== true ||
      data.publicDeliveryVerified !== true) return unavailable;
  if (!Array.isArray(data.tracks) || data.tracks.length > catalog.tracks.length) return unavailable;
  const origin = new URL(pageOrigin);
  if (origin.protocol !== 'https:' || origin.origin !== pageOrigin) return unavailable;
  const known = new Map(catalog.tracks.map(track => [track.id, track]));
  const approved = new Map(), seen = new Set();
  for (const entry of data.tracks) {
    if (!entry || typeof entry.id !== 'string' || !known.has(entry.id) || seen.has(entry.id)) return unavailable;
    seen.add(entry.id);
    if (entry.available !== true) {
      if (entry.available !== false || (entry.url !== null && entry.url !== undefined)) return unavailable;
      continue;
    }
    const track = known.get(entry.id);
    if (entry.bytes !== track.audioBytes || entry.sha256 !== track.audioSha256 ||
        !Number.isSafeInteger(entry.bytes) || entry.bytes <= 0 ||
        !/^[a-f0-9]{64}$/.test(entry.sha256) || !/^fma:\d+$/.test(entry.id) ||
        typeof entry.url !== 'string') return unavailable;
    const path = '/audio/' + entry.id.slice(4).padStart(6, '0') + '.mp3';
    // Accept only this explicit mapping. No credentials, query strings, aliases,
    // percent-encoding, path traversal or fallback source URLs are interpreted.
    if (entry.url !== path && entry.url !== pageOrigin + path) return unavailable;
    const url = new URL(entry.url, origin);
    if (url.origin !== pageOrigin || url.pathname !== path || url.search || url.hash ||
        url.username || url.password) return unavailable;
    approved.set(entry.id, Object.freeze({id: entry.id, url: url.href, bytes: entry.bytes, sha256: entry.sha256}));
  }
  return approved;
}

export async function loadAudioDelivery({catalog, catalogSha256, pageOrigin, fetcher = fetch}) {
  try {
    const response = await fetcher(new URL('/audio-delivery.json', pageOrigin), {
      credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
    });
    if (!response.ok) return new Map();
    return validateAudioDelivery(await response.json(), {catalog, catalogSha256, pageOrigin});
  } catch {
    return new Map();
  }
}

export function previewForTrack(track, delivery) {
  const approved = track && delivery.get(track.id);
  return approved ? {available: true, url: approved.url, label: 'Play'} :
    {available: false, url: null, label: UNAVAILABLE_PREVIEW};
}
