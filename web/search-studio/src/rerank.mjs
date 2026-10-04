// An explicit display policy, not a new encoder or learned relevance score.
export const POLICY_ID = 'artist-spread-v1';

export function rankCandidates(input, {mode = 'closest', limit = 8} = {}) {
  if (!input || typeof input.catalogId !== 'string' || !input.catalogId ||
      typeof input.encoderSpaceId !== 'string' || !input.encoderSpaceId ||
      typeof input.sourceRankingId !== 'string' || !input.sourceRankingId ||
      typeof input.artistMetadataSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(input.artistMetadataSha256)) {
    throw new Error('Pinned catalog, encoder, ranking and artist metadata identities are required');
  }
  if (!['closest', 'more-artists'].includes(mode) || !Number.isInteger(limit) || limit < 1 || limit > 20) {
    throw new Error('Unsupported display policy');
  }
  const candidates = input.candidates;
  if (!Array.isArray(candidates) || candidates.length > 64) throw new Error('Candidate pool must be bounded');
  const seen = new Set();
  for (const [i, row] of candidates.entries()) {
    if (typeof row.trackId !== 'string' || !row.trackId || seen.has(row.trackId) ||
        !Number.isFinite(row.rawSimilarity) || row.rawSimilarity < -1.00001 || row.rawSimilarity > 1.00001 ||
        row.sourceRank !== i + 1 || typeof row.artistId !== 'string' || !row.artistId ||
        (i && row.rawSimilarity > candidates[i - 1].rawSimilarity)) {
      throw new Error('Candidates must be unique, ordered and carry observed artist identities and raw scores');
    }
    seen.add(row.trackId);
  }
  const baseline = candidates.slice(0, limit);
  const selected = [], deferred = [], artistCounts = new Map();
  for (const row of candidates) {
    if (selected.length >= limit) break;
    if (mode === 'closest' || !artistCounts.has(row.artistId)) {
      selected.push({ ...row, selectionReason: mode === 'closest' ? 'source-ranking' : 'first-candidate-for-artist' });
      artistCounts.set(row.artistId, (artistCounts.get(row.artistId) ?? 0) + 1);
    } else deferred.push(row);
  }
  // Scarce artist coverage must not create a misleading empty list.
  // Backfill only from the same ranked pool, preserving its original order.
  if (selected.length < limit) {
    for (const row of deferred) {
      if (selected.length >= limit) break;
      selected.push({ ...row, selectionReason: 'backfill-from-same-candidate-pool' });
      artistCounts.set(row.artistId, (artistCounts.get(row.artistId) ?? 0) + 1);
    }
  }
  const mean = rows => rows.length ? rows.reduce((sum, r) => sum + r.rawSimilarity, 0) / rows.length : null;
  const selectedIds = new Set(selected.map(r => r.trackId));
  const baselineMean = mean(baseline), selectedMean = mean(selected);
  return {
    schemaVersion: 1, policyId: POLICY_ID, mode,
    catalogId: input.catalogId, encoderSpaceId: input.encoderSpaceId,
    sourceRankingId: input.sourceRankingId, artistMetadataSha256: input.artistMetadataSha256,
    candidateCount: candidates.length, requestedCount: limit,
    results: selected.map((row, i) => ({ ...row, displayRank: i + 1 })),
    diagnostics: {
      baselineArtistCount: new Set(baseline.map(r => r.artistId)).size,
      displayedArtistCount: artistCounts.size,
      baselineMeanSimilarity: baselineMean, displayedMeanSimilarity: selectedMean,
      rawMeanSimilarityDrop: baselineMean === null ? null : baselineMean - selectedMean,
      baselineResultsKept: baseline.filter(r => selectedIds.has(r.trackId)).length,
      maxSourceRankDisplayed: selected.length ? Math.max(...selected.map(r => r.sourceRank)) : null,
      relevanceJudgmentsAvailable: false,
      interpretation: 'Artist breadth and raw model score tradeoff only; no measured relevance or novelty improvement'
    }
  };
}
