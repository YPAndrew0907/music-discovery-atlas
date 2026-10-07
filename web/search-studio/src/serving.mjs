// The serving list's public summary (GET /serving.json): the rows of this catalog the site may serve. The
// server applies the list to search, audio and credits itself; the page applies the same list to what it
// computes in the browser (browse, name lookup, neighbors, recorded examples and on-device search) and to
// the map's labels, so a held recording is never shown as a result.
export const SERVING_SUMMARY_KIND = 'music-serving-summary';

export function validateServing(data, {catalogId, count}) {
  if (!data || data.schemaVersion !== 1 || data.kind !== SERVING_SUMMARY_KIND || data.catalogId !== catalogId ||
      data.total !== count || !Number.isSafeInteger(data.served) || data.served < 1 || data.served > count ||
      data.held !== count - data.served || !Array.isArray(data.rows) || data.rows.length !== data.served ||
      !Array.isArray(data.ids) || data.ids.length !== data.served) {
    throw new Error('The serving list does not match this collection');
  }
  let previous = -1;
  for (const row of data.rows) {
    if (!Number.isSafeInteger(row) || row <= previous || row >= count) throw new Error('Invalid serving list rows');
    previous = row;
  }
  return new Set(data.rows);
}

// null: this server publishes no list (development fixtures), so every row is served. Any other failure
// stops the page rather than show recordings the list may hold.
export async function loadServing({catalogId, count, pageOrigin, fetcher = fetch}) {
  const response = await fetcher(new URL('/serving.json', pageOrigin), {
    credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error('The serving list is unavailable');
  const data = await response.json();
  const rows = validateServing(data, {catalogId, count});
  return {rows, ids: new Set(data.ids), served: data.served};
}

// v1 holds the whole catalog: the served IDs must be exactly those of the served rows.
export function bindServing(serving, tracks) {
  if (serving && (serving.ids.size !== serving.rows.size || [...serving.rows].some(row => !serving.ids.has(tracks[row]?.id)))) {
    throw new Error('The serving list does not match this collection');
  }
  return serving?.rows ?? null;
}
