export function validateCatalog(catalog, vectors) {
  if (catalog.schemaVersion !== 1 || catalog.dimensions !== 512 || !Array.isArray(catalog.tracks) || vectors.length !== catalog.tracks.length * 512) throw new Error('Catalog/vector identity mismatch');
  const ids = new Set();
  for (const track of catalog.tracks) {
    if (!track.id || ids.has(track.id) || !track.artist || !track.title || !track.attribution || !/^https?:\/\//.test(track.sourceUrl) || !['CC-BY-3.0','CC-BY-4.0','CC0-1.0'].includes(track.license)) throw new Error('Invalid catalog record');
    ids.add(track.id);
  }
  for (let row = 0; row < catalog.tracks.length; row++) {
    let sum = 0;
    for (const v of vectors.subarray(row*512,(row+1)*512)) { if (!Number.isFinite(v)) throw new Error('Invalid vector'); sum += v*v; }
    if (Math.abs(sum - 1) > 1e-5) throw new Error('Audio vector is not normalized');
  }
  return true;
}
export function exactSearch(query, vectors, tracks, {limit = 12, strictInstrumental = false, excluded = []} = {}) {
  if (!(query instanceof Float32Array) || query.length !== 512 || !query.every(Number.isFinite)) throw new Error('Expected projected 512D vector');
  const norm = Math.hypot(...query); if (Math.abs(norm-1)>1e-5) throw new Error('Query must be normalized');
  const banned = new Set(excluded); const rows = [];
  for (let row=0; row<tracks.length; row++) {
    if (banned.has(tracks[row].id) || (strictInstrumental && tracks[row].verifiedVocals !== false)) continue;
    let score=0; for(let d=0;d<512;d++) score += query[d]*vectors[row*512+d];
    rows.push({id:tracks[row].id,row,score});
  }
  rows.sort((a,b)=>b.score-a.score || (a.id<b.id?-1:a.id>b.id?1:0));
  return rows.slice(0,limit);
}
export function metadataSearch(text, tracks, limit=12) {
  const fold=s=>s.normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase();
  const query=fold(text).trim(); if(!query) return tracks.slice(0,limit).map((t,row)=>({id:t.id,row,score:null}));
  const words=query.split(/\s+/);return tracks.map((t,row)=>({id:t.id,row,score:(fold(t.title)===query?100:0)+(words.every(w=>fold(t.title+' '+t.artist).includes(w))?10:0)})).filter(t=>t.score>0).sort((a,b)=>b.score-a.score||a.row-b.row).slice(0,limit);
}
export const quantile=(numbers,q)=>{if(!numbers.length)return null;const a=[...numbers].sort((a,b)=>a-b);return a[Math.max(0,Math.ceil(q*a.length)-1)];};
