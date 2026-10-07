"""Search over a verified v2 release without materialising Python vector or link objects.

Ranking is exact and bit-identical to hnsw_trace.HNSW.exact_search (the v1 server):
a float32 numpy pass over the memory-mapped matrix only proposes candidates, every proposed
row is rescored with hnsw_trace.cosine_distance (binary64 accumulation in dimension order),
and the result is accepted only when a rigorous float32 error bound proves that no row
outside the proposal can reach the k-th exact distance. Otherwise the proposal widens, ending
in a full rescore whose numpy accumulation is sequential and therefore identical.

The search trace is hnsw_trace.HNSW.search itself, unchanged: GraphV2 only replaces the
storage it reads (`vectors[row]` and `links[row][level]`) with views into the mapped files.
"""
from array import array
import math

import numpy as np

from hnsw_trace import HNSW, _float32, _integer, assert_unit, cosine_distance

# |fl32(x.y) - x.y| <= gamma_n * sum|x_i y_i| <= gamma_n * |x| |y| for any summation order,
# gamma_n = n u / (1 - n u), u = 2^-24. Unit rows (+-1e-5) and a unit query (+-1e-4): about
# 3.1e-5 at n = 512. A 4x margin absorbs norm slack and BLAS blocking differences.
FLOAT32_DOT_ERROR = 1.25e-4


class _Rows:
    """vectors[row] -> 1-D float32 memoryview of that row, the type HNSW iterates in v1."""
    __slots__ = ('_flat', '_dims', '_count')

    def __init__(self, matrix):
        self._count, self._dims = matrix.shape
        self._flat = memoryview(np.ascontiguousarray(matrix).reshape(-1)).cast('B').cast('f')

    def __len__(self):
        return self._count

    def __getitem__(self, row):
        if not 0 <= row < self._count:
            raise IndexError(row)
        return self._flat[row * self._dims:(row + 1) * self._dims]


class _Layers:
    __slots__ = ('_links', '_first', '_levels')

    def __init__(self, links, first, levels):
        self._links, self._first, self._levels = links, first, levels

    def __len__(self):
        return self._levels

    def __getitem__(self, level):
        if not 0 <= level < self._levels:
            raise IndexError(level)
        offsets = self._links._offsets
        index = self._first + level
        return self._links._neighbors[offsets[index]:offsets[index + 1]]


class _Links:
    """links[row][level] -> int32 memoryview slice of the CSR neighbor array, stored order."""
    __slots__ = ('_nodes', '_offsets', '_neighbors', '_count')

    def __init__(self, node_layers, layer_offsets, neighbors):
        self._nodes = memoryview(np.ascontiguousarray(node_layers)).cast('B').cast('i')
        self._offsets = memoryview(np.ascontiguousarray(layer_offsets)).cast('B').cast('i')
        self._neighbors = memoryview(np.ascontiguousarray(neighbors)).cast('B').cast('i')
        self._count = len(node_layers) - 1

    def __len__(self):
        return self._count

    def __getitem__(self, row):
        if not 0 <= row < self._count:
            raise IndexError(row)
        first = self._nodes[row]
        return _Layers(self, first, self._nodes[row + 1] - first)


def exact_rescore(matrix, query, rows):
    """hnsw_trace.cosine_distance for many rows: float32 inputs widened to binary64, products
    are exact, and np.cumsum accumulates strictly in dimension order like the Python loop."""
    q64 = np.asarray(query, dtype=np.float32).astype(np.float64)
    block = np.asarray(matrix[rows], dtype=np.float32).astype(np.float64)
    dots = np.cumsum(block * q64, axis=1)[:, -1] if len(rows) else np.empty(0)
    return [max(0.0, min(2.0, 1.0 - float(dot))) for dot in dots]


class GraphV2(HNSW):
    """HNSW search/trace over a ReleaseV2 (no per-row Python objects, no link tuples)."""

    def __init__(self, release):  # deliberately does not call HNSW.__init__ (the v1 tuple build)
        graph = release.graph
        self.release = release
        self.dimensions = int(release.dimensions)
        self.size = int(release.count)
        self.space_id = self.index_space_id = release.graph_id
        self.entry = int(graph['entry'])
        self.max_level = int(graph['maxLevel'])
        self.matrix = release.vectors
        self.vectors = _Rows(release.vectors)
        self.links = _Links(release.node_layers, release.layer_offsets, release.neighbors)
        self.stats = {'proposals': 0, 'widened': 0, 'fullRescores': 0}

    def allowed_mask(self, allowed):
        """A boolean row mask for a set of allowed rows, cached for the long-lived serving list."""
        cached = getattr(self, '_allowed', None)
        if cached is None or cached[0] is not allowed:
            mask = np.zeros(self.size, dtype=bool)
            mask[np.fromiter(allowed, dtype=np.int64, count=len(allowed))] = True
            self._allowed = cached = (allowed, mask)
        return cached[1]

    def exact_search(self, query, k=8, exclude_id=None, allowed=None):
        """Same contract and output as hnsw_trace.HNSW.exact_search, bit for bit, allowed included."""
        query = self._query(query)
        if not _integer(k, 1, self.size):
            raise ValueError('Invalid search budget')
        if exclude_id is not None and not _integer(exclude_id, 0, self.size - 1):
            raise ValueError('Invalid excluded row ID')
        q32 = np.frombuffer(query.tobytes(), dtype=np.float32)
        approximate = self.matrix @ q32
        mask = None if allowed is None else self.allowed_mask(allowed)
        if mask is not None:
            approximate[~mask] = -np.inf
        if exclude_id is not None:
            approximate[exclude_id] = -np.inf
        available = (self.size if mask is None else int(mask.sum())) - (
            exclude_id is not None and (mask is None or bool(mask[exclude_id])))
        k = min(k, available)
        proposal = max(4 * k, 64)
        self.stats['proposals'] += 1
        while proposal < available:
            rows = np.argpartition(-approximate, proposal - 1)[:proposal]
            # Rows outside the proposal have a float32 dot <= floor, hence an exact distance
            # >= 1 - (floor + error). Strictly smaller k-th distances are provably complete.
            floor = float(np.min(approximate[rows]))
            ranked = sorted(({'id': int(row), 'distance': cosine_distance(query, self.vectors[int(row)])} for row in rows),
                            key=lambda item: (item['distance'], item['id']))[:k]
            bound = max(0.0, min(2.0, 1.0 - (floor + FLOAT32_DOT_ERROR)))
            if ranked[-1]['distance'] < bound:
                return ranked
            proposal *= 4
            self.stats['widened'] += 1
        self.stats['fullRescores'] += 1
        rows = [row for row in range(self.size) if row != exclude_id and (mask is None or mask[row])]
        distances = []
        for start in range(0, len(rows), 8192):
            distances.extend(exact_rescore(self.matrix, query, rows[start:start + 8192]))
        return sorted(({'id': row, 'distance': distance} for row, distance in zip(rows, distances)),
                      key=lambda item: (item['distance'], item['id']))[:k]


def trace_rows(trace, results):
    """Every row a trace or result list mentions, so the page can place it without a layout."""
    rows = {item['id'] for item in results}
    for event in (trace or {}).get('events', []):
        rows.update(event.get('entryIds', ()))
        for key in ('id', 'entryId'):
            if isinstance(event.get(key), int):
                rows.add(event[key])
        for key in ('considered', 'frontier', 'retained', 'items'):
            rows.update(item['id'] for item in event.get(key, ()))
    rows.update(item['id'] for item in (trace or {}).get('finalResults', ()))
    return rows


def label_rows(trace, limit=48):
    """Rows the animation may label (entry points and expanded nodes), bounded."""
    rows = []
    for event in (trace or {}).get('events', []):
        for row in ([*event.get('entryIds', ())] if event.get('type') == 'enter' else
                    [event['id']] if event.get('type') == 'expand' else []):
            if row not in rows:
                rows.append(row)
    return rows[:limit]
