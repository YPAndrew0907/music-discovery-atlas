"""Read-only, dependency-free port of src/hnsw.mjs's static HNSW search.

HNSW(graph, vectors).search(query, k=8, ef=32, trace=True) returns the JS
schema. Vectors may be flat values, rows, or little-endian float32 bytes.
Inputs are copied to float32, then distances accumulate in Python float
(IEEE binary64), in dimension order, exactly as the JavaScript reference.

The graph identity describes the index's audio vector space. It is metadata,
not the text encoder's identity: callers must validate compatible encoders.
"""

from array import array
from collections.abc import Mapping
import math
from numbers import Integral, Real
import sys


ALGORITHM = "hnsw-static-cosine-v1"


class SearchCancelled(RuntimeError):
    """Cancellation equivalent to the JS reference's AbortError."""

    name = "AbortError"


def _integer(value, minimum, maximum):
    return isinstance(value, Integral) and not isinstance(value, bool) and minimum <= value <= maximum


def _float32(values):
    """Snapshot one flat vector; raw bytes always have little-endian order."""
    try:
        if isinstance(values, (bytes, bytearray)):
            result = array("f")
            result.frombytes(values)
            if sys.byteorder != "little":
                result.byteswap()
            return result
        if isinstance(values, memoryview) and values.format in ("B", "b", "c"):
            return _float32(values.tobytes())
        values = list(values)
        if any(not isinstance(value, Real) or isinstance(value, bool) for value in values):
            raise ValueError("Vector values must be numeric")
        return array("f", values)
    except (TypeError, OverflowError, BufferError) as error:
        raise ValueError("Invalid float32 vector") from error


def assert_unit(vector, dimensions):
    if len(vector) != dimensions:
        raise ValueError("Vector shape mismatch")
    norm = 0.0
    for value in vector:
        if not math.isfinite(value):
            raise ValueError("Non-finite vector")
        norm += value * value
    if abs(norm - 1.0) > 1e-4:
        raise ValueError("Expected a normalized vector")


def cosine_distance(a, b):
    """JS cosineDistance, deliberately not numpy.dot or a float32 reduction."""
    dot = 0.0
    for left, right in zip(a, b):
        dot += float(left) * float(right)
    return max(0.0, min(2.0, 1.0 - dot))


def _key(item):
    return item["distance"], item["id"]


class _Heap:
    """The JS binary heap, including its child and equal-priority rules."""

    def __init__(self, before):
        self.items = []
        self.before = before

    def __len__(self):
        return len(self.items)

    def peek(self):
        return self.items[0]

    def push(self, item):
        items = self.items
        items.append(item)
        i = len(items) - 1
        while i:
            parent = (i - 1) >> 1
            if not self.before(items[i], items[parent]):
                break
            items[parent], items[i] = items[i], items[parent]
            i = parent

    def pop(self):
        items = self.items
        if not items:
            return None
        first, last = items[0], items.pop()
        if items:
            items[0] = last
            i = 0
            while True:
                chosen, left, right = i, 2 * i + 1, 2 * i + 2
                if left < len(items) and self.before(items[left], items[chosen]):
                    chosen = left
                if right < len(items) and self.before(items[right], items[chosen]):
                    chosen = right
                if chosen == i:
                    break
                items[i], items[chosen] = items[chosen], items[i]
                i = chosen
        return first


class HNSW:
    """An immutable-by-search snapshot of an already constructed graph."""

    def __init__(self, graph, vectors):
        if not isinstance(graph, Mapping):
            raise ValueError("Invalid static index payload")
        count, dimensions = graph.get("count"), graph.get("dimensions")
        degree, construction = graph.get("M"), graph.get("efConstruction")
        if (not _integer(graph.get("schemaVersion"), 1, 1) or graph.get("algorithm") != ALGORITHM
                or not _integer(count, 1, 1_000_000)
                or not _integer(dimensions, 1, 4096)
                or not _integer(degree, 2, 128)
                or not _integer(construction, degree, 2**53 - 1)):
            raise ValueError("Invalid static index payload")
        space_id = graph.get("indexSpaceId", graph.get("spaceId"))
        if not isinstance(space_id, str) or ":" not in space_id or not space_id.strip() or len(space_id) > 256:
            raise ValueError("A versioned feature-space identity is required")
        links, entry, max_level = graph.get("links"), graph.get("entry"), graph.get("maxLevel")
        if (not isinstance(links, (list, tuple)) or len(links) != count
                or not _integer(max_level, 0, 24) or not _integer(entry, 0, count - 1)):
            raise ValueError("Invalid graph structure")
        for layers in links:
            if not isinstance(layers, (list, tuple)) or not 1 <= len(layers) <= max_level + 1:
                raise ValueError("Invalid layer structure")
        for row_id, layers in enumerate(links):
            for level, neighbors in enumerate(layers):
                if (not isinstance(neighbors, (list, tuple))
                        or len(neighbors) > degree * (2 if level == 0 else 1)):
                    raise ValueError("Invalid graph links")
                visited = set()
                for neighbor in neighbors:
                    if (not _integer(neighbor, 0, count - 1) or neighbor == row_id
                            or neighbor in visited or len(links[neighbor]) <= level):
                        raise ValueError("Invalid graph links")
                    visited.add(neighbor)
        if len(links[entry]) != max_level + 1:
            raise ValueError("Invalid entry level")

        if isinstance(vectors, (bytes, bytearray, memoryview)):
            matrix = _float32(vectors)
        else:
            try:
                values = list(vectors)
                if values and not isinstance(values[0], Real):
                    if len(values) != count:
                        raise ValueError("Matrix shape mismatch")
                    rows = [_float32(row) for row in values]
                    if any(len(row) != dimensions for row in rows):
                        raise ValueError("Matrix shape mismatch")
                    matrix = array("f", (value for row in rows for value in row))
                else:
                    matrix = _float32(values)
            except TypeError as error:
                raise ValueError("Matrix shape mismatch") from error
        if len(matrix) != count * dimensions:
            raise ValueError("Matrix shape mismatch")
        # Keep an immutable packed float32 snapshot instead of allocating one
        # Python float object per component. Iteration still yields the same
        # exact binary32 values in the same order for binary64 accumulation.
        packed = memoryview(matrix.tobytes()).cast('f')
        rows = tuple(packed[offset:offset + dimensions] for offset in range(0, len(matrix), dimensions))
        for row in rows:
            assert_unit(row, dimensions)
        self.dimensions = int(dimensions)
        self.size = int(count)
        self.space_id = self.index_space_id = space_id
        self.entry = int(entry)
        self.max_level = int(max_level)
        self.vectors = rows
        self.links = tuple(tuple(tuple(int(neighbor) for neighbor in layer) for layer in layers) for layers in links)

    @classmethod
    def load(cls, graph, vectors):
        return cls(graph, vectors)

    def _query(self, query):
        query = _float32(query)
        assert_unit(query, self.dimensions)
        return query

    def search(self, query, k=8, ef=32, trace=True, trace_limit=2048, cancelled=lambda: False):
        query = self._query(query)
        if (not _integer(k, 1, self.size) or not _integer(ef, k, self.size)
                or not _integer(trace_limit, 0, 10_000)):
            raise ValueError("Invalid search budget")
        if not callable(cancelled):
            raise ValueError("cancelled must be callable")
        ctx = {"cache": {}, "distanceEvaluations": 0, "expansions": 0,
               "events": [], "truncated": False}

        def check_cancelled():
            if cancelled():
                raise SearchCancelled("Search cancelled")

        def distance(row_id):
            if row_id not in ctx["cache"]:
                ctx["cache"][row_id] = cosine_distance(query, self.vectors[row_id])
                ctx["distanceEvaluations"] += 1
            return ctx["cache"][row_id]

        def record(event):
            if not trace:
                return
            if len(ctx["events"]) < trace_limit:
                ctx["events"].append({"sequence": len(ctx["events"]), **event,
                                      "distanceEvaluations": ctx["distanceEvaluations"]})
            else:
                ctx["truncated"] = True

        entries = [self.entry]
        for level in range(self.max_level, -1, -1):
            layer_ef = ef if level == 0 else 1
            candidates = _Heap(lambda a, b: _key(a) < _key(b))
            best = _Heap(lambda a, b: _key(a) > _key(b))
            visited = set()
            for row_id in entries:
                item = {"id": row_id, "distance": distance(row_id)}
                visited.add(row_id)
                candidates.push(item)
                best.push(item)
            record({"type": "enter", "level": level, "entryIds": list(entries), "ef": layer_ef})
            while candidates:
                check_cancelled()
                current = candidates.pop()
                if len(best) >= layer_ef and _key(current) > _key(best.peek()):
                    record({"type": "stop", "level": level, "id": current["id"],
                            "reason": "Frontier exceeds retained distance/ID ordering bound",
                            "bound": best.peek()["distance"]})
                    break
                considered = []
                ctx["expansions"] += 1
                for row_id in self.links[current["id"]][level]:
                    if row_id in visited:
                        continue
                    visited.add(row_id)
                    candidate = {"id": row_id, "distance": distance(row_id)}
                    accepted = len(best) < layer_ef or _key(candidate) < _key(best.peek())
                    evicted = None
                    if accepted:
                        candidates.push(candidate)
                        best.push(candidate)
                        if len(best) > layer_ef:
                            evicted = best.pop()["id"]
                    if trace:
                        considered.append({**candidate, "accepted": accepted, "evicted": evicted, "via": current["id"]})
                if trace:
                    record({"type": "expand", "level": level, "id": current["id"],
                            "distance": current["distance"], "considered": considered,
                            "frontier": sorted(candidates.items, key=_key)[:16], "frontierCount": len(candidates),
                            "retained": sorted(best.items, key=_key)[:16], "retainedCount": len(best),
                            "visitedOnLayer": len(visited), "snapshotsLimitedTo": 16})
            found = sorted(best.items, key=_key)
            check_cancelled()
            if level == 0:
                results = found[:k]
                record({"type": "results", "level": 0, "items": results})
                return {"spaceId": self.space_id, "results": results,
                        "stats": {"distanceEvaluations": ctx["distanceEvaluations"],
                                  "expansions": ctx["expansions"], "corpusCount": self.size, "ef": ef, "k": k},
                        "trace": {"schemaVersion": 1, "algorithm": ALGORITHM, "events": ctx["events"],
                                  "truncated": ctx["truncated"], "limit": trace_limit, "finalResults": results}}
            entries = [found[0]["id"]]
            record({"type": "descend", "level": level, "nextLevel": level - 1, "entryId": entries[0]})

    def exact_search(self, query, k=8, exclude_id=None, allowed=None):
        """Exhaustive top-k oracle with the reference's numeric row-ID ties. allowed, when given, is the set
        of rows a result may be (the serving list's served rows); every other row is skipped like exclude_id."""
        query = self._query(query)
        if not _integer(k, 1, self.size):
            raise ValueError("Invalid search budget")
        if exclude_id is not None and not _integer(exclude_id, 0, self.size - 1):
            raise ValueError("Invalid excluded row ID")
        return sorted(({"id": row_id, "distance": cosine_distance(query, vector)}
                       for row_id, vector in enumerate(self.vectors)
                       if row_id != exclude_id and (allowed is None or row_id in allowed)), key=_key)[:k]
