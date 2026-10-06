// Refinement is local to an explicitly supplied candidate pool. It never changes
// catalog row identities, model scores, or the server's retrieval contract.
export const RESULT_PAGE_SIZE = 12;
export const foldMetadata = value => String(value ?? '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();

export function sourceGenres(candidates, tracks) {
  const counts = new Map();
  for (const {row} of candidates) {
    const genre = tracks[row].genre;
    if (typeof genre === 'string' && genre.trim()) counts.set(genre, (counts.get(genre) ?? 0) + 1);
  }
  return [...counts].sort(([a], [b]) => a.localeCompare(b)).map(([genre, count]) => ({genre, count}));
}

export function refineCandidates(candidates, tracks, {text = '', genre = '', previewOnly = false} = {}, isPlayable = () => false) {
  const words = foldMetadata(text).trim().split(/\s+/).filter(Boolean);
  return candidates.filter(candidate => {
    const track = tracks[candidate.row];
    return (!genre || track.genre === genre) && (!previewOnly || isPlayable(candidate.row)) &&
      words.every(word => foldMetadata(`${track.title} ${track.artist} ${track.album ?? ''}`).includes(word));
  });
}

export function resultPage(candidates, requestedPage = 0) {
  const pages = Math.ceil(candidates.length / RESULT_PAGE_SIZE);
  const page = Math.max(0, Math.min(Number.isInteger(requestedPage) ? requestedPage : 0, Math.max(0, pages - 1)));
  const start = page * RESULT_PAGE_SIZE;
  return {page, pages, total: candidates.length, start, end: Math.min(start + RESULT_PAGE_SIZE, candidates.length), rows: candidates.slice(start, start + RESULT_PAGE_SIZE)};
}

export function resultScope({channel, catalogCount, candidateCount, page}) {
  const range = page.total ? `${(page.start + 1).toLocaleString()}–${page.end.toLocaleString()} of ${page.total.toLocaleString()}` : '0';
  const shown = `Showing ${range}`;
  if (channel === 'sound' || channel === 'neighbors') {
    return `${shown} from ${candidateCount.toLocaleString()} retrieved sound candidates · searched ${catalogCount.toLocaleString()} recordings. Refinements apply to these candidates, not the full collection.`;
  }
  return `${shown} ${channel === 'browse' ? 'recordings' : 'name matches'} · ${candidateCount.toLocaleString()} ${channel === 'browse' ? 'recordings in the collection' : `title / artist matches across ${catalogCount.toLocaleString()} recordings`}. Refinements use recorded metadata.`;
}
