"""Release format v2: one pinned manifest over SQLite metadata, memory-mapped vectors,
an int32 CSR graph and a float32 layout.

The v1 contract (corpus_release.py, active_corpus.py) is untouched: a schemaVersion 1
selection still validates exactly as before, and this module refuses it. A v2 release is
validated in two places:

* at build time (scripts/convert_release_v1_to_v2.py, scripts/verify_release_v2.py), every
  catalog, rights and evidence row is checked against the same rules as v1, the CSR graph is
  decoded and compared with the source index, and a sampled parity oracle compares exact and
  traced search with the v1 reference implementation;
* at startup (this module), the pinned manifest digest is checked, every core asset is
  streamed through SHA-256 once (constant memory), and the structural invariants that are
  cheap to vectorise are re-checked. Evidence blobs are verified lazily, row by row, against
  the pins held in the verified catalog database. Nothing parses the whole catalog as JSON.

Minor versions are additive and named in release.json ("minorVersion"; a 2.0 release has no such key and
is read exactly as before). 2.1 adds an FTS5 trigram index over the folded "title artist album" column of
catalog.sqlite. It is only a prefilter for name lookups and refinements: the instr() conditions stay the
final filter, so every page is the same with or without it (see page_query).
"""
from dataclasses import dataclass, fields
import functools
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import struct
import tempfile
import threading
from types import MappingProxyType
import unicodedata
from urllib.parse import quote, urlsplit

import numpy as np

from corpus_release import (HNSW_SOURCE_SHA, NATIVE_PROFILE_SHA, PAIR_ID, ReleaseError, TRACK_ID,
                            integer, object_sha, pinned_pair, require, strict_json, valid_sha, verify_spec)

FORMAT = 'music-corpus-release-v2'
CATALOG_KIND = 'music-corpus-catalog-v2'
EVIDENCE_KIND = 'music-corpus-evidence-v2'
GRAPH_MAGIC = b'MDACSR01'
GRAPH_ENCODING = 'csr-int32-v1'
GRAPH_HEADER = struct.Struct('<8s9I3Q')  # magic, version, count, dims, M, efC, seed, entry, maxLevel, pad; 3 lengths
ASSET_PATHS = MappingProxyType({
    'catalog': 'catalog.sqlite', 'evidence': 'evidence.sqlite', 'vectors': 'vectors.f32',
    'graph': 'graph.bin', 'layout': 'layout.f32', 'graphManifest': 'graph-manifest.json',
    'examples': 'examples.json'})
CORE_ASSETS = ('catalog', 'vectors', 'graph', 'layout', 'graphManifest')
LAZY_ASSETS = ('evidence', 'examples')  # pinned by size here; content verified when read
SELECTION_KEYS = {'schemaVersion', 'enabled', 'format', 'directory', 'manifestSha256'}
SELECTION_OPTIONAL = {'limits', 'source'}
SOURCE_KINDS = ('bundled', 'object-store')
# Object keys sit under at most four plain path segments: <origin>/<a>/<b>/<sha256>.
SOURCE_PREFIX = re.compile(r'/(?:[A-Za-z0-9_-]{1,64}/){0,4}\Z')
DIMENSIONS = 512
# Small, scan-friendly display/search columns in `tracks`; the exact source objects live in
# `records` so a lookup or browse never pages through multi-kilobyte JSON rows.
CATALOG_SCHEMA = '''
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE tracks (
  row INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, artist TEXT NOT NULL,
  album TEXT, genre TEXT, license TEXT NOT NULL, artist_id TEXT, audio_bytes INTEGER NOT NULL,
  audio_sha256 TEXT NOT NULL, fold_title TEXT NOT NULL, fold_title_artist TEXT NOT NULL, fold_text TEXT NOT NULL);
CREATE INDEX tracks_genre ON tracks (genre, row);
CREATE TABLE records (row INTEGER PRIMARY KEY, track_json TEXT NOT NULL, rights_json TEXT NOT NULL);
'''
# Release format 2.1: the lookup index. External content (no second copy of the text), case-sensitive
# trigrams over the already folded column, so a quoted phrase matches exactly the rows whose fold_text
# contains it as a substring: the same test instr() applies, for words of three or more characters.
LOOKUP_INDEX = 'fts5-trigram-v1'
LOOKUP_INDEX_SQL = ("CREATE VIRTUAL TABLE tracks_fts USING fts5(fold_text, content='tracks', content_rowid='row', "
                    "tokenize='trigram case_sensitive 1')")
MINOR_VERSIONS = MappingProxyType({1: LOOKUP_INDEX})  # minorVersion -> what it adds to 2.0
EVIDENCE_SCHEMA = '''
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE evidence (path TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, data BLOB NOT NULL);
'''
_cache_lock = threading.Lock()
_loaded = {}


@dataclass(frozen=True)
class LimitsV2:
    """Per-release validation caps. Defaults admit the existing 2,000 and 5,777 releases;
    anything larger must be configured explicitly in the reviewed selection, and nothing can
    exceed the ceilings below."""
    max_tracks: int = 10_000
    catalog_bytes: int = 64_000_000
    evidence_bytes: int = 64_000_000
    graph_bytes: int = 16_000_000
    graph_manifest_bytes: int = 16_000_000
    examples_bytes: int = 8_000_000

    CEILINGS = MappingProxyType({'max_tracks': 1_000_000, 'catalog_bytes': 8_000_000_000,
        'evidence_bytes': 16_000_000_000, 'graph_bytes': 512_000_000,
        'graph_manifest_bytes': 64_000_000, 'examples_bytes': 64_000_000})
    CONFIG_KEYS = MappingProxyType({'maxTracks': 'max_tracks', 'catalogBytes': 'catalog_bytes',
        'evidenceBytes': 'evidence_bytes', 'graphBytes': 'graph_bytes',
        'graphManifestBytes': 'graph_manifest_bytes', 'examplesBytes': 'examples_bytes'})

    def __post_init__(self):
        for item in fields(self):
            require(integer(getattr(self, item.name), 1, self.CEILINGS[item.name]),
                    'Invalid v2 release limit: ' + item.name)

    @classmethod
    def from_config(cls, config):
        if config is None:
            return cls()
        require(isinstance(config, dict) and set(config) <= set(cls.CONFIG_KEYS), 'Unknown v2 release limit')
        return cls(**{cls.CONFIG_KEYS[key]: value for key, value in config.items()})

    def as_config(self):
        return {key: getattr(self, name) for key, name in self.CONFIG_KEYS.items()}


def exact_https_origin(value):
    """An exact https://host[:port] origin: no path, query, fragment, credentials or wildcard."""
    parsed = urlsplit(value) if isinstance(value, str) else None
    require(parsed is not None and parsed.scheme == 'https' and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
            and value == f'https://{parsed.netloc}' and '*' not in value, 'Audio origin must be an exact HTTPS origin')
    parsed.port  # rejects malformed ports
    return value


def install_source(value):
    """The selection's optional "source": where the image build materialises the release from.

    Absent or {"kind": "bundled"}: the release directory ships in the image (COPY corpus-releases/).
    {"kind": "object-store", "origin": "https://host", "pathPrefix": "/a/b/"}: every file is fetched
    from that one origin under its content-addressed key <origin><pathPrefix><sha256>, and
    release.json is the object whose key is the selection's manifestSha256. The running server never
    reads this block; scripts/install_release_v2.py does."""
    if value is None:
        return MappingProxyType({'kind': 'bundled'})
    require(isinstance(value, dict) and value.get('kind') in SOURCE_KINDS, 'Invalid v2 release source')
    if value['kind'] == 'bundled':
        require(set(value) == {'kind'}, 'Invalid v2 release source')
    else:
        require(set(value) == {'kind', 'origin', 'pathPrefix'} and isinstance(value['pathPrefix'], str)
                and SOURCE_PREFIX.fullmatch(value['pathPrefix']) is not None, 'Invalid v2 release source')
        try:
            exact_https_origin(value['origin'])
        except (ReleaseError, ValueError) as error:
            raise ReleaseError('Invalid v2 release source origin') from error
    return MappingProxyType(dict(value))


def fold(value):
    """Python twin of the page's foldMetadata(): NFKD, drop combining marks, lowercase."""
    text = unicodedata.normalize('NFKD', '' if value is None else str(value))
    return ''.join(c for c in text if not unicodedata.category(c).startswith('M')).lower()


def create_lookup_index(connection):
    """Add the release-2.1 lookup index to a writable catalog connection (converter and upgrader)."""
    connection.execute(LOOKUP_INDEX_SQL)
    connection.execute("INSERT INTO tracks_fts(tracks_fts) VALUES('rebuild')")
    connection.execute('INSERT INTO meta (key, value) VALUES (?, ?)', ('lookupIndex', LOOKUP_INDEX))


def check_lookup_index(connection):
    """FTS5's own integrity check, with rank 1: the index structure and its agreement with every row of
    the tracks table. It needs a writable connection, so it runs at build time or on a private copy."""
    try:
        connection.execute("INSERT INTO tracks_fts(tracks_fts, rank) VALUES('integrity-check', 1)")
    except sqlite3.Error as error:
        raise ReleaseError('Lookup index does not match the catalog: ' + str(error)) from error


@functools.lru_cache(maxsize=1)
def lookup_index_supported():
    """Whether this SQLite build has FTS5 with the trigram tokenizer. Without it a 2.1 release still loads
    and every lookup scans the narrow table, with the same results."""
    try:
        probe = sqlite3.connect(':memory:')
        try:
            probe.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram case_sensitive 1')")
            probe.execute("INSERT INTO t (x) VALUES ('abcdef')")
            return probe.execute("""SELECT count(*) FROM t WHERE t MATCH '"bcd"'""").fetchone()[0] == 1
        finally:
            probe.close()
    except sqlite3.Error:
        return False


def sha256_file(handle, size, chunk=4 * 1024 * 1024):
    digest, remaining = hashlib.sha256(), size
    handle.seek(0)
    while remaining:
        block = handle.read(min(chunk, remaining))
        require(block, 'Asset shrank while it was verified')
        digest.update(block)
        remaining -= len(block)
    require(not handle.read(1), 'Asset grew while it was verified')
    return digest.hexdigest()


def open_confined(root, relative):
    """Open root/relative without following symlinks in any component; return a binary file."""
    require(isinstance(relative, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', relative) is not None,
            'Invalid v2 asset path')
    flags = os.O_RDONLY | os.O_NOFOLLOW
    directory = None
    try:
        directory = os.open(root, flags | os.O_DIRECTORY)
        descriptor = os.open(relative, flags | os.O_NONBLOCK, dir_fd=directory)
    except OSError as error:
        raise ReleaseError('Missing, unreadable or symlinked v2 asset: ' + relative) from error
    finally:
        if directory is not None:
            os.close(directory)
    handle = os.fdopen(descriptor, 'rb', buffering=0)
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode):
        handle.close()
        raise ReleaseError('v2 asset is not a regular file: ' + relative)
    return handle


def asset_pin(spec, name, max_bytes):
    require(isinstance(spec, dict) and set(spec) == {'path', 'bytes', 'sha256'}
            and spec['path'] == ASSET_PATHS[name] and integer(spec['bytes'], 1, max_bytes)
            and valid_sha(spec['sha256']), 'Invalid v2 asset pin: ' + name)
    return spec


def verified_handle(root, spec):
    """Open a pinned asset, check its length and SHA-256 by streaming; keep the open file."""
    handle = open_confined(root, spec['path'])
    try:
        info = os.fstat(handle.fileno())
        require(info.st_size == spec['bytes'], 'v2 asset size mismatch: ' + spec['path'])
        require(sha256_file(handle, spec['bytes']) == spec['sha256'], 'v2 asset integrity mismatch: ' + spec['path'])
        return handle, info
    except BaseException:
        handle.close()
        raise


def read_csr(data, header):
    """Decode graph.bin into int32 arrays (views into data) after checking its header."""
    magic, version, count, dims, degree, construction, seed, entry, max_level, pad, n_nodes, n_layers, n_links = header
    require(magic == GRAPH_MAGIC and version == 1 and pad == 0 and n_nodes == count + 1, 'Unknown CSR graph header')
    offset = GRAPH_HEADER.size
    require(len(data) == offset + 4 * (n_nodes + n_layers + n_links), 'CSR graph length mismatch')
    node_layers = np.frombuffer(data, dtype='<i4', count=n_nodes, offset=offset)
    offset += 4 * n_nodes
    layer_offsets = np.frombuffer(data, dtype='<i4', count=n_layers, offset=offset)
    offset += 4 * n_layers
    neighbors = np.frombuffer(data, dtype='<i4', count=n_links, offset=offset)
    return node_layers, layer_offsets, neighbors


def check_csr(node_layers, layer_offsets, neighbors, *, count, degree, entry, max_level, reachability=True, block=32768):
    """Vectorised version of corpus_release.validate_index for the CSR encoding. Works in blocks of
    nodes so its temporaries stay a few megabytes at any catalog size."""
    require(count >= 1 and node_layers.shape == (count + 1,) and int(node_layers[0]) == 0, 'CSR node table mismatch')
    layers_per_node = np.diff(node_layers)
    require(bool(np.all(layers_per_node >= 1)) and bool(np.all(layers_per_node <= max_level + 1)), 'Invalid CSR layers')
    total_layers = int(node_layers[-1])
    require(layer_offsets.shape == (total_layers + 1,) and int(layer_offsets[0]) == 0
            and int(layer_offsets[-1]) == len(neighbors), 'CSR layer table mismatch')
    require(0 <= entry < count and int(layers_per_node[entry]) == max_level + 1, 'CSR entry level mismatch')
    if len(neighbors):
        require(int(neighbors.min()) >= 0 and int(neighbors.max()) < count, 'Invalid CSR neighbor')
    for first in range(0, count, block):
        last = min(count, first + block)
        layer_start, layer_stop = int(node_layers[first]), int(node_layers[last])
        offsets = layer_offsets[layer_start:layer_stop + 1].astype(np.int64)
        degrees = np.diff(offsets)
        require(bool(np.all(degrees >= 0)), 'CSR layer offsets decrease')
        per_node = layers_per_node[first:last].astype(np.int64)
        owner_of_layer = np.repeat(np.arange(first, last, dtype=np.int64), per_node)
        level_of_layer = (np.arange(layer_start, layer_stop, dtype=np.int64)
                          - np.repeat(node_layers[first:last].astype(np.int64), per_node))
        require(bool(np.all(degrees <= np.where(level_of_layer == 0, 2 * degree, degree))), 'CSR degree budget mismatch')
        if not offsets[-1] - offsets[0]:
            continue
        targets = neighbors[offsets[0]:offsets[-1]].astype(np.int64)
        layer_of_link = np.repeat(np.arange(len(degrees), dtype=np.int64), degrees)
        require(not bool(np.any(targets == owner_of_layer[layer_of_link])), 'CSR self link')
        require(bool(np.all(layers_per_node[targets] > level_of_layer[layer_of_link])), 'CSR neighbor lacks the linked level')
        keys = layer_of_link * count + targets
        require(len(np.unique(keys)) == len(keys), 'Duplicate CSR neighbor')
    if reachability:
        base = node_layers[:-1]
        reached = np.zeros(count, dtype=bool)
        reached[entry] = True
        frontier = np.array([entry], dtype=np.int64)
        while len(frontier):
            starts, stops = layer_offsets[base[frontier]], layer_offsets[base[frontier] + 1]
            found = np.unique(np.concatenate([neighbors[a:b] for a, b in zip(starts.tolist(), stops.tolist())]))
            frontier = found[~reached[found]].astype(np.int64)
            reached[frontier] = True
        require(bool(reached.all()), 'CSR base layer does not reach every track')


def check_unit_rows(matrix, chunk=2048):
    for start in range(0, matrix.shape[0], chunk):
        block = np.asarray(matrix[start:start + chunk], dtype=np.float64)
        require(bool(np.isfinite(block).all()), 'Non-finite audio vector')
        norms = np.sqrt(np.einsum('ij,ij->i', block, block))
        require(bool(np.all(np.abs(norms - 1) <= 1e-5)), 'Audio vector is not L2 normalized')


def expected_audio_identity(pair):
    return {'family': pair['family'], 'repo': pair['repo'], 'revision': pair['revision'],
            'audioModelSha256': pair['audioModelSha256'], 'audioQuantization': pair['audioQuantization'],
            'preprocessorConfigSha256': pair['preprocessorConfigSha256'],
            'preprocessing': pair['audioPreprocessing'], 'preprocessingSha256': object_sha(pair['audioPreprocessing']),
            'executionProfile': pair['audioExecutionProfile'],
            'executionProfileSha256': object_sha(pair['audioExecutionProfile'])}


def check_graph_manifest(manifest, release, ids, graph):
    """The parts of corpus_release.validate_graph_manifest that do not need v1 files."""
    require(isinstance(manifest, dict) and isinstance(manifest.get('graphIdentity'), dict), 'Missing audio graph identity')
    identity = manifest['graphIdentity']
    graph_id = 'experimental-clap-audio-graph:' + object_sha(identity)
    count, ordered = len(ids), object_sha(ids)
    require(manifest.get('graphId') == graph_id == release['graphId'], 'Graph identity digest mismatch')
    require(object_sha(identity.get('audio')) == object_sha(expected_audio_identity(pinned_pair())),
            'Audio model/preprocessing identity mismatch')
    construction = {'M': graph['M'], 'efConstruction': graph['efConstruction'], 'seed': graph['seed']}
    require(integer(manifest.get('schemaVersion'), 1, 1) and integer(identity.get('schemaVersion'), 1, 1)
            and manifest.get('catalogId') == release['catalogId']
            and integer(manifest.get('count'), count, count) and integer(identity.get('count'), count, count)
            and integer(manifest.get('dimensions'), DIMENSIONS, DIMENSIONS)
            and integer(identity.get('dimensions'), DIMENSIONS, DIMENSIONS)
            and identity.get('metric') == 'cosine' and identity.get('algorithm') == graph['algorithm']
            and object_sha(identity.get('construction')) == object_sha(construction)
            and identity.get('hnswSourceSha256') == HNSW_SOURCE_SHA, 'Graph release contract mismatch')
    require(manifest.get('orderedIds') == ids and manifest.get('orderedIdsSha256') == ordered
            and identity.get('orderedIdsSha256') == ordered == release['orderedIdsSha256'], 'Graph row-order mismatch')
    require(manifest.get('vectorsSha256') == identity.get('vectorsSha256') == release['vectorsSha256'],
            'Graph vector digest mismatch')
    profiles = manifest.get('allowedQueryProfiles')
    require(isinstance(profiles, list) and len(profiles) == 1, 'Exactly one reviewed native query profile is required')
    profile = profiles[0]
    require(isinstance(profile, dict) and profile.get('kind') == 'server-live'
            and object_sha(profile.get('identity')) == NATIVE_PROFILE_SHA
            and profile.get('id') == 'experimental-native-q8:' + NATIVE_PROFILE_SHA
            and isinstance(profile.get('source'), dict)
            and profile['source'].get('queryProfileSha256') == NATIVE_PROFILE_SHA,
            'Unreviewed or mismatched native query profile')
    return graph_id


MANIFEST_KEYS = {'schemaVersion', 'kind', 'catalogId', 'graphId', 'count', 'dimensions', 'pairId',
                 'orderedIdsSha256', 'vectorsSha256', 'assets', 'graph', 'layout', 'source', 'build'}
GRAPH_KEYS = {'algorithm', 'encoding', 'M', 'efConstruction', 'seed', 'entry', 'maxLevel', 'layers', 'links'}


class ReleaseV2:
    """A verified, read-only v2 release. Vectors and graph are memory-mapped; SQLite
    connections are opened read-only and immutable, one per thread."""

    def __init__(self, directory, manifest_sha256, manifest, limits):
        self.directory, self.manifest_sha256, self.manifest, self.limits = Path(directory), manifest_sha256, manifest, limits
        self.catalog_id, self.graph_id, self.count = manifest['catalogId'], manifest['graphId'], manifest['count']
        self.dimensions = manifest['dimensions']
        self.minor_version = manifest.get('minorVersion', 0)
        self.lookup_index = None  # set by _load for a 2.1 release on a runtime with FTS5 trigrams
        self.lookup_stats = {'indexed': 0, 'scanned': 0}
        self.assets = MappingProxyType({key: dict(value) for key, value in manifest['assets'].items()})
        self.catalog_sha256 = self.assets['catalog']['sha256']
        self.graph_sha256 = self.assets['graph']['sha256']
        self.vectors_sha256 = manifest['vectorsSha256']
        self.graph_manifest_sha256 = self.assets['graphManifest']['sha256']
        self._local = threading.local()
        self._handles = []
        self._evidence_lock = threading.Lock()

    # Loading ---------------------------------------------------------------------------
    def _load(self):
        root, manifest, limits = self.directory, self.manifest, self.limits
        handles, infos = {}, {}
        try:
            for name in CORE_ASSETS:
                handles[name], infos[name] = verified_handle(root, self.assets[name])
        finally:
            self._handles = list(handles.values())
        # Lazy assets must exist with their pinned length; content is checked when read.
        for name in LAZY_ASSETS:
            if name in self.assets:
                with open_confined(root, self.assets[name]['path']) as lazy:
                    require(os.fstat(lazy.fileno()).st_size == self.assets[name]['bytes'],
                            'v2 asset size mismatch: ' + self.assets[name]['path'])
        count, dims = self.count, self.dimensions
        self._catalog_identity = infos['catalog']
        # Vectors: exact length, finite, unit rows; mapped read-only from the verified descriptor.
        require(self.assets['vectors']['bytes'] == count * dims * 4, 'Vector dimensions/length mismatch')
        self._vector_map = mmap.mmap(handles['vectors'].fileno(), 0, access=mmap.ACCESS_READ)
        self.vectors = np.frombuffer(self._vector_map, dtype='<f4').reshape((count, dims))
        check_unit_rows(self.vectors)
        # Layout: count x 2 finite float32.
        require(self.assets['layout']['bytes'] == count * 8, 'Layout length mismatch')
        self._layout_map = mmap.mmap(handles['layout'].fileno(), 0, access=mmap.ACCESS_READ)
        self.layout = np.frombuffer(self._layout_map, dtype='<f4').reshape((count, 2))
        require(bool(np.isfinite(self.layout).all()), 'Non-finite layout position')
        # Graph: header must agree with the manifest; structure re-checked vectorised.
        graph = manifest['graph']
        self._graph_map = mmap.mmap(handles['graph'].fileno(), 0, access=mmap.ACCESS_READ)
        header = GRAPH_HEADER.unpack_from(self._graph_map, 0)
        require(header[2:9] == (count, dims, graph['M'], graph['efConstruction'], graph['seed'], graph['entry'],
                                graph['maxLevel']) and header[11] == graph['layers'] + 1 and header[12] == graph['links'],
                'CSR graph header disagrees with the release manifest')
        self.node_layers, self.layer_offsets, self.neighbors = read_csr(self._graph_map, header)
        check_csr(self.node_layers, self.layer_offsets, self.neighbors, count=count, degree=graph['M'],
                  entry=graph['entry'], max_level=graph['maxLevel'])
        self.graph = MappingProxyType(dict(graph))
        # Catalog database: identity, contiguous rows, ordered IDs.
        connection = self.connection()
        meta = dict(connection.execute('SELECT key, value FROM meta'))
        require(meta.get('schemaVersion') == '2' and meta.get('kind') == CATALOG_KIND
                and meta.get('catalogId') == self.catalog_id and meta.get('count') == str(count)
                and meta.get('orderedIdsSha256') == manifest['orderedIdsSha256'], 'Catalog database identity mismatch')
        low, high, rows = connection.execute('SELECT min(row), max(row), count(*) FROM tracks').fetchone()
        require((low, high, rows) == (0, count - 1, count), 'Catalog database row range mismatch')
        ids = [row[0] for row in connection.execute('SELECT id FROM tracks ORDER BY row')]
        require(all(isinstance(i, str) and TRACK_ID.fullmatch(i) for i in ids) and len(set(ids)) == count
                and object_sha(ids) == manifest['orderedIdsSha256'], 'Catalog database ordered IDs mismatch')
        self.ordered_ids = tuple(ids)
        self.row_of = MappingProxyType({ident: row for row, ident in enumerate(ids)})
        self.catalog_meta = MappingProxyType(meta)
        # Graph manifest (graphIdentity, query profile), pinned above, parsed strictly.
        manifest_bytes = handles['graphManifest']
        manifest_bytes.seek(0)
        graph_manifest = strict_json(manifest_bytes.read(), 'graph manifest', limits.graph_manifest_bytes)
        check_graph_manifest(graph_manifest, manifest, ids, graph)
        self.graph_manifest = graph_manifest
        self.genres = tuple(genre_counts(connection.execute('SELECT genre, count(*) FROM tracks GROUP BY genre')))
        if self.minor_version >= 1:
            # Content is covered by catalogSha256 like every other table; this pins the declaration the
            # queries rely on. The index's agreement with the rows is proved at build time (validate_rows).
            declared = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'tracks_fts'").fetchone()
            require(meta.get('lookupIndex') == LOOKUP_INDEX and declared is not None and declared[0] == LOOKUP_INDEX_SQL,
                    'Catalog database lacks the release 2.1 lookup index')
            self.lookup_index = LOOKUP_INDEX if lookup_index_supported() else None
        self.artist_count = connection.execute('SELECT count(DISTINCT artist_id) FROM tracks').fetchone()[0]
        self.close()  # the maps hold their own descriptors; SQLite reopens the verified path by identity
        return self

    # SQLite ------------------------------------------------------------------------------
    def connection(self):
        connection = getattr(self._local, 'catalog', None)
        if connection is None:
            path = (self.directory / self.assets['catalog']['path']).resolve()
            info = os.stat(path, follow_symlinks=False)
            expected = self._catalog_identity
            require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                    == (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns),
                    'Catalog database changed after verification')
            connection = sqlite3.connect('file:' + quote(str(path)) + '?mode=ro&immutable=1', uri=True)
            connection.execute('PRAGMA query_only = 1')
            connection.execute('PRAGMA trusted_schema = 0')
            self._local.catalog = connection
        return connection

    def evidence_connection(self):
        connection = getattr(self._local, 'evidence', None)
        if connection is None:
            path = (self.directory / self.assets['evidence']['path']).resolve()
            require(not path.is_symlink() and path.is_file(), 'Missing evidence database')
            connection = sqlite3.connect('file:' + quote(str(path)) + '?mode=ro&immutable=1', uri=True)
            connection.execute('PRAGMA query_only = 1')
            connection.execute('PRAGMA trusted_schema = 0')
            self._local.evidence = connection
        return connection

    # Reads ---------------------------------------------------------------------------------
    DISPLAY = ('row', 'id', 'title', 'artist', 'album', 'genre', 'license', 'artist_id', 'audio_bytes', 'audio_sha256')

    def display_rows(self, rows):
        """Bounded display metadata for explicit rows, in the requested order."""
        rows = [int(row) for row in rows]
        require(all(0 <= row < self.count for row in rows), 'Row outside the catalog')
        if not rows:
            return []
        found = {}
        for start in range(0, len(rows), 500):
            part = sorted(set(rows[start:start + 500]))
            query = 'SELECT {} FROM tracks WHERE row IN ({})'.format(', '.join(self.DISPLAY), ','.join('?' * len(part)))
            for record in self.connection().execute(query, part):
                found[record[0]] = record
        return [self._display(found[row]) for row in rows]

    def _display(self, record):
        row, ident, title, artist, album, genre, license_id, artist_id, audio_bytes, audio_sha = record
        x, y = self.layout[row]
        return {'row': row, 'id': ident, 'title': title, 'artist': artist, 'album': album, 'genre': genre,
                'license': license_id, 'artistId': artist_id, 'audioBytes': audio_bytes, 'audioSha256': audio_sha,
                'position': [float(x), float(y)]}

    def positions(self, rows):
        rows = sorted({int(row) for row in rows})
        require(all(0 <= row < self.count for row in rows), 'Row outside the catalog')
        return {'rows': rows, 'xy': [float(v) for v in self.layout[rows].reshape(-1)]} if rows else {'rows': [], 'xy': []}

    def track_record(self, row):
        record = self.connection().execute('SELECT track_json, rights_json FROM records WHERE row = ?', (int(row),)).fetchone()
        require(record is not None, 'Unknown catalog row')
        return json.loads(record[0]), json.loads(record[1])

    def evidence_for_row(self, row):
        """Return a track's evidence blob after checking it against the pin in its verified rights row."""
        _, rights = self.track_record(row)
        pin = rights['evidence']['asset']
        path = pin['path']
        require(isinstance(path, str) and path.startswith('evidence/') and len(path) <= 256, 'Invalid evidence path')
        record = self.evidence_connection().execute('SELECT bytes, sha256, data FROM evidence WHERE path = ?', (path,)).fetchone()
        require(record is not None, 'Missing evidence row')
        size, digest, data = record
        require(isinstance(data, bytes) and size == len(data) == pin['bytes']
                and digest == hashlib.sha256(data).hexdigest() == pin['sha256'], 'Evidence integrity mismatch: ' + path)
        return data

    def audio_pins(self):
        """(row, id, audioBytes, audioSha256) for every row, in catalog order."""
        return self.connection().execute('SELECT row, id, audio_bytes, audio_sha256 FROM tracks ORDER BY row').fetchall()

    def summary(self):
        return {'schemaVersion': 2, 'releaseFormat': FORMAT, 'formatVersion': '2.%d' % self.minor_version,
                'lookupIndex': self.lookup_index, 'releaseId': 'corpus-release-v2:' + self.manifest_sha256,
                'catalogId': self.catalog_id, 'graphId': self.graph_id, 'count': self.count, 'dimensions': self.dimensions,
                'catalogSha256': self.catalog_sha256, 'graphSha256': self.graph_sha256, 'vectorsSha256': self.vectors_sha256,
                'source': self.manifest['source'], 'limits': self.limits.as_config()}

    def close(self):
        for handle in self._handles:
            handle.close()
        self._handles = []


def parse_manifest(data, limits):
    manifest = strict_json(data, 'v2 release manifest', 262_144)
    require(isinstance(manifest, dict) and set(manifest) in (MANIFEST_KEYS, MANIFEST_KEYS | {'minorVersion'})
            and integer(manifest['schemaVersion'], 2, 2) and manifest['kind'] == FORMAT, 'Unknown v2 release contract')
    require('minorVersion' not in manifest or (type(manifest['minorVersion']) is int
                                               and manifest['minorVersion'] in MINOR_VERSIONS),
            'Unknown v2 release minor version')
    count = manifest['count']
    require(integer(count, 32, limits.max_tracks), 'Track count exceeds the configured v2 limit')
    require(type(manifest['dimensions']) is int and manifest['dimensions'] == DIMENSIONS
            and manifest['pairId'] == PAIR_ID, 'Unreviewed embedding dimensions or pairing')
    require(isinstance(manifest['catalogId'], str) and 0 < len(manifest['catalogId']) <= 256
            and isinstance(manifest['graphId'], str) and manifest['graphId'].startswith('experimental-clap-audio-graph:')
            and valid_sha(manifest['orderedIdsSha256']) and valid_sha(manifest['vectorsSha256']), 'Invalid v2 identities')
    assets = manifest['assets']
    require(isinstance(assets, dict) and set(CORE_ASSETS) | {'evidence'} <= set(assets) <= set(ASSET_PATHS),
            'Incomplete or unexpected v2 assets')
    caps = {'catalog': limits.catalog_bytes, 'evidence': limits.evidence_bytes, 'vectors': count * DIMENSIONS * 4,
            'graph': limits.graph_bytes, 'layout': count * 8, 'graphManifest': limits.graph_manifest_bytes,
            'examples': limits.examples_bytes}
    for name, spec in assets.items():
        asset_pin(spec, name, caps[name])
    require(assets['vectors']['sha256'] == manifest['vectorsSha256'], 'Vector pin disagrees with the vector identity')
    graph = manifest['graph']
    require(isinstance(graph, dict) and set(graph) == GRAPH_KEYS and graph['algorithm'] == 'hnsw-static-cosine-v1'
            and graph['encoding'] == GRAPH_ENCODING and integer(graph['M'], 2, 32)
            and integer(graph['efConstruction'], graph['M'], 1000) and integer(graph['seed'], 0, 2**32 - 1)
            and integer(graph['maxLevel'], 0, 24) and integer(graph['entry'], 0, count - 1)
            and integer(graph['layers'], count, count * 25) and integer(graph['links'], 0, count * 2 * 32 * 25),
            'Invalid v2 graph description')
    require(isinstance(manifest['layout'], dict) and isinstance(manifest['source'], dict)
            and isinstance(manifest['build'], dict), 'Invalid v2 provenance blocks')
    return manifest


def load_release_v2(directory, *, expected_manifest_sha256, limits=None):
    """Verify and open a v2 release. The digest must come from a separately reviewed selection."""
    limits = limits or LimitsV2()
    require(isinstance(limits, LimitsV2), 'Invalid v2 limits')
    require(valid_sha(expected_manifest_sha256), 'A reviewed v2 release manifest digest is required')
    with open_confined(directory, 'release.json') as handle:
        size = os.fstat(handle.fileno()).st_size
        require(0 < size <= 262_144, 'v2 release manifest exceeds its budget')
        data = handle.read(size + 1)
    require(len(data) == size and hashlib.sha256(data).hexdigest() == expected_manifest_sha256,
            'Unreviewed v2 release manifest digest')
    release = ReleaseV2(directory, expected_manifest_sha256, parse_manifest(data, limits), limits)
    try:
        return release._load()
    except BaseException:
        release.close()
        raise


@dataclass(frozen=True)
class SelectionV2:
    directory: Path
    release: ReleaseV2
    config: object


@dataclass(frozen=True)
class SelectionConfigV2:
    """A parsed, package-pinned v2 selection. Nothing here has looked at the release directory."""
    root: Path
    relative: str
    directory: Path
    manifest_sha256: str
    limits: LimitsV2
    source: object
    config: object


def selection_config_v2(root, package):
    """Parse active-corpus.json as a v2 selection without opening the release. Returns None when the
    file is absent, disabled or a v1 (schemaVersion 1) selection. The file must be pinned by the
    reviewed package. The installer uses this before the release directory exists."""
    root = Path(root)
    config_path = root / 'active-corpus.json'
    if not config_path.exists() and not config_path.is_symlink():
        return None
    specs = [row for row in package.get('files', []) if row.get('path') == 'active-corpus.json']
    require(len(specs) == 1, 'Corpus selection is not pinned by the reviewed package')
    data = verify_spec(root, specs[0], 16_384, 'active-corpus.json')
    config = strict_json(data, 'active corpus selection', 16_384)
    require(isinstance(config, dict), 'Invalid active corpus selection')
    if config.get('schemaVersion') != 2 or type(config.get('schemaVersion')) is not int:
        return None
    # A v2 selection is always an activation; disabling uses the v1 form {"schemaVersion": 1, "enabled": false}.
    require(config.get('enabled') is True and SELECTION_KEYS <= set(config) <= SELECTION_KEYS | SELECTION_OPTIONAL
            and config['format'] == FORMAT and valid_sha(config['manifestSha256']), 'Incomplete v2 corpus selection')
    relative = config['directory']
    require(isinstance(relative, str) and re.fullmatch(r'corpus-releases/[A-Za-z0-9][A-Za-z0-9_-]{0,63}', relative),
            'Invalid corpus release directory')
    source = install_source(config.get('source'))
    return SelectionConfigV2(root, relative, root.joinpath(*relative.split('/')), config['manifestSha256'],
                             LimitsV2.from_config(config.get('limits')), source, MappingProxyType(config))


def selected_release_v2(root, package):
    """Return the verified v2 selection, or None when active-corpus.json is absent, disabled
    or a v1 (schemaVersion 1) selection. The file must be pinned by the reviewed package."""
    parsed = selection_config_v2(root, package)
    if parsed is None:
        return None
    directory = parsed.root
    for part in parsed.relative.split('/'):
        directory = directory / part
        require(not directory.is_symlink() and directory.is_dir(), 'Corpus directory cannot contain symlinks')
    key = (str(directory.resolve()), parsed.manifest_sha256, parsed.limits)
    with _cache_lock:
        release = _loaded.get(key)
        if release is None:
            release = load_release_v2(directory, expected_manifest_sha256=parsed.manifest_sha256, limits=parsed.limits)
            _loaded[key] = release
    return SelectionV2(directory, release, parsed.config)


LOOKUP_INDEX_SHARE = 10  # use the index while a phrase matches under 1/10 of the catalog; scan above that


def lookup_phrase(words):
    """The FTS5 query for a word list: one quoted phrase per word of three or more characters (a trigram
    index cannot narrow shorter words), with quotes doubled, so no word is read as query syntax. A word
    holding NUL is left out too: FTS5 reads NUL as the end of the phrase and refuses the query, and leaving
    a word out only widens the prefilter, so such a lookup returns the scan's page."""
    return ' '.join('"' + word.replace('"', '""') + '"' for word in words if len(word) >= 3 and '\x00' not in word)


def lookup_prefilter(release, connection, words, use_index=True):
    """(conditions, params) that narrow a lookup through the 2.1 index, or ([], []) to scan.

    The index matches every row whose fold_text contains each phrase. Lookup words are tested against
    fold_title_artist, a prefix of fold_text, and refinement words against fold_text itself, so the
    prefilter keeps every row the instr() conditions accept; those conditions still decide the result.
    A phrase that matches a tenth of the catalog or more scans faster than it filters, so a bounded probe
    picks the plan. Either plan returns the same rows in the same order."""
    phrase = lookup_phrase(words) if use_index and getattr(release, 'lookup_index', None) else ''
    if phrase:
        bound = max(64, release.count // LOOKUP_INDEX_SHARE)
        matched = connection.execute('SELECT count(*) FROM (SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH ? LIMIT ?)',
                                     (phrase, bound)).fetchone()[0]
        if matched < bound:
            release.lookup_stats['indexed'] += 1
            return ['row IN (SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH ?)'], [phrase]
    if words and hasattr(release, 'lookup_stats'):
        release.lookup_stats['scanned'] += 1
    return [], []


def page_query(release, *, offset=0, limit=12, query='', text='', genre='', rows_filter=None, facets=False, use_index=True):
    """Server-side twin of the page's browse/lookup + refinement rules (results-view.mjs and
    listen-lab retrieval.mjs): browse is catalog order; a lookup scores an exact folded title 100
    and every folded word inside "title artist" 10, ordered by score then row; refinement keeps
    rows whose folded "title artist album" contains every word and whose genre matches exactly.
    rows_filter, when given, is a callable row -> bool (verified-preview filter). On a release-2.1 catalog the
    lookup index narrows the rows first (lookup_prefilter; use_index=False forces the scan, for proofs)."""
    require(integer(offset, 0, 10_000_000) and integer(limit, 1, 48), 'Invalid page window')
    for value in (query, text, genre):
        require(isinstance(value, str) and len(value) <= 512, 'Invalid page filter')
    connection = release.connection()
    where, params, order = [], [], 'row'
    lookup = fold(query).strip()
    channel = 'lookup' if query.strip() else 'browse'
    words = lookup.split() if channel == 'lookup' else []
    for word in words:
        where.append('instr(fold_title_artist, ?) > 0')
        params.append(word)
    if channel == 'lookup':
        order = '(fold_title = ?) DESC, row'
    base_index, base_index_params = lookup_prefilter(release, connection, words, use_index)
    base_where, base_params = base_index + where, base_index_params + params
    refine = fold(text).strip().split()
    for word in refine:
        where.append('instr(fold_text, ?) > 0')
        params.append(word)
    index, index_params = ((base_index, base_index_params) if not refine else
                           lookup_prefilter(release, connection, words + refine, use_index))
    where, params = index + where, index_params + params
    if genre:
        where.append('genre = ?')
        params.append(genre)
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    base_clause = (' WHERE ' + ' AND '.join(base_where)) if base_where else ''
    order_params = [lookup] if channel == 'lookup' else []
    if rows_filter is None:
        total = connection.execute('SELECT count(*) FROM tracks' + clause, params).fetchone()[0]
        selected = [row for (row,) in connection.execute(
            'SELECT row FROM tracks' + clause + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?',
            params + order_params + [limit, offset])]
    else:
        ordered = [row for (row,) in connection.execute('SELECT row FROM tracks' + clause + ' ORDER BY ' + order,
                                                        params + order_params) if rows_filter(row)]
        total, selected = len(ordered), ordered[offset:offset + limit]
    base_total = (release.count if not base_where else
                  connection.execute('SELECT count(*) FROM tracks' + base_clause, base_params).fetchone()[0])
    result = {'channel': channel, 'total': total, 'baseTotal': base_total, 'offset': offset, 'limit': limit,
              'rows': release.display_rows(selected)}
    if facets:
        result['genres'] = [{'genre': g, 'count': c} for g, c in genre_counts(connection.execute(
            'SELECT genre, count(*) FROM tracks' + base_clause + ' GROUP BY genre', base_params))]
    return result


def genre_counts(records):
    """sourceGenres() keeps string genres that are non-empty after JS trim(); order is left to the page."""
    return [(genre, count) for genre, count in records if isinstance(genre, str) and genre.strip()]


def validate_lookup_index(release, *, scratch_dir=None):
    """Run check_lookup_index on a private copy of the verified catalog (the release stays read-only)."""
    spec = release.assets['catalog']
    with tempfile.TemporaryDirectory(prefix='.lookup-index-check-', dir=scratch_dir) as temporary:
        copy = Path(temporary) / 'catalog.sqlite'
        with open_confined(release.directory, spec['path']) as source, open(copy, 'wb') as target:
            shutil.copyfileobj(source, target, 4 * 1024 * 1024)
        with open(copy, 'rb') as handle:
            require(os.fstat(handle.fileno()).st_size == spec['bytes'] and sha256_file(handle, spec['bytes']) == spec['sha256'],
                    'Catalog database changed after verification')
        connection = sqlite3.connect(copy)
        try:
            check_lookup_index(connection)
        finally:
            connection.close()
    return LOOKUP_INDEX


def validate_rows(release, *, evidence_budget=None, scratch_dir=None):
    """Offline semantic validation of every catalog/rights/evidence row of a loaded release.

    The per-row rules are a line-by-line mirror of corpus_release.validate_rights (and the
    catalog row checks of validate_release); tests run both on the same rows, including
    tampered ones, and require identical verdicts. Startup does not run this: it relies on
    the pinned digests of the files this function accepted at build time. For a 2.1 release it
    also proves the lookup index (validate_lookup_index), on a copy made in scratch_dir."""
    from datetime import datetime
    from corpus_release import LICENSES, nonempty, public_source_url
    budget = evidence_budget or release.limits.evidence_bytes
    connection = release.connection()
    meta = release.catalog_meta
    catalog_header = json.loads(meta['catalogHeader'])
    rights_header = json.loads(meta['rightsHeader'])
    require(catalog_header.get('id') == release.catalog_id and integer(catalog_header.get('schemaVersion'), 1, 1)
            and integer(catalog_header.get('dimensions'), DIMENSIONS, DIMENSIONS), 'Catalog identity/count mismatch')
    require(isinstance(rights_header, dict) and integer(rights_header.get('schemaVersion'), 1, 1)
            and rights_header.get('kind') == 'music-corpus-rights'
            and rights_header.get('catalogId') == release.catalog_id
            and rights_header.get('catalogSha256') == release.manifest['source'].get('catalogSha256')
            and rights_header.get('scope') == 'searchable-audio-embeddings', 'Rights manifest binding/scope mismatch')
    evidence, evidence_bytes = {}, 0
    columns = connection.execute('SELECT t.row, t.id, t.title, t.artist, t.album, t.genre, t.license, t.audio_bytes, '
                                 't.audio_sha256, t.fold_title, t.fold_title_artist, t.fold_text, r.track_json, r.rights_json '
                                 'FROM tracks t JOIN records r ON r.row = t.row ORDER BY t.row')
    seen = 0
    for (row_number, ident, title, artist, album, genre, license_column, audio_bytes, audio_sha,
         fold_title, fold_title_artist, fold_text, track_json, rights_json) in columns:
        track, row = json.loads(track_json), json.loads(rights_json)
        require(row_number == seen and isinstance(track, dict) and track.get('id') == ident == release.ordered_ids[seen],
                'Catalog row-order mismatch')
        seen += 1
        require((title, artist, album, genre, license_column, audio_bytes, audio_sha)
                == (track.get('title'), track.get('artist'), track.get('album'), track.get('genre'), track.get('license'),
                    track.get('audioBytes'), track.get('audioSha256'))
                and fold_title == fold(track.get('title')) and fold_title_artist == fold(f"{track.get('title')} {track.get('artist')}")
                and fold_text == fold(f"{track.get('title')} {track.get('artist')} {'' if track.get('album') is None else track.get('album')}"),
                'Catalog display columns disagree with the source row')
        # The 2.1 lookup index covers fold_text only; lookups rely on fold_title_artist being its prefix.
        require(release.minor_version < 1 or fold_text.startswith(fold_title_artist), 'Lookup columns are not nested')
        # ---- mirror of corpus_release.validate_rights, per row ----
        require(isinstance(row, dict) and row.get('id') == track['id'], 'Rights row-order mismatch')
        license_id = track.get('license')
        require(isinstance(license_id, str) and license_id in LICENSES and track.get('licenseUrl') == LICENSES[license_id]
                and row.get('license') == license_id and row.get('licenseUrl') == LICENSES[license_id],
                'Unreviewed license class or URL')
        require(public_source_url(track.get('sourceUrl')) and row.get('sourceUrl') == track['sourceUrl']
                and nonempty(track.get('title')) and nonempty(track.get('artist'))
                and nonempty(track.get('attribution'), 8192) and nonempty(track.get('modifications'), 8192)
                and isinstance(track.get('suppliedNotices'), dict), 'Missing per-track attribution/provenance')
        require(integer(track.get('audioBytes'), 1, 20_000_000) and valid_sha(track.get('audioSha256'))
                and row.get('audioBytes') == track['audioBytes'] and row.get('audioSha256') == track['audioSha256'],
                'Audio provenance byte/hash mismatch')
        require(row.get('decision') == 'approved' and row.get('use') == 'searchable-audio-embeddings'
                and nonempty(row.get('reviewedBy'), 200) and nonempty(row.get('basis'), 4096),
                'Missing explicit per-track review')
        timestamp = row.get('reviewedAt')
        try:
            require(isinstance(timestamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', timestamp), 'Review timestamp must be UTC RFC3339')
            datetime.fromisoformat(timestamp[:-1] + '+00:00')
        except ValueError as error:
            raise ReleaseError('Invalid review timestamp') from error
        proof = row.get('evidence')
        require(isinstance(proof, dict) and set(proof) == {'asset', 'sourceUrl', 'locator'}
                and public_source_url(proof['sourceUrl']) and nonempty(proof['locator'], 2048), 'Missing rights evidence')
        spec = proof['asset']
        require(isinstance(spec, dict) and isinstance(spec.get('path'), str)
                and spec['path'].startswith('evidence/'), 'Evidence must use a confined evidence path')
        previous = evidence.get(spec['path'])
        if previous is not None:
            require(previous == spec, 'Conflicting pins for shared rights evidence')
        else:
            require(evidence_bytes < budget, 'Evidence aggregate budget exceeded')
            require(set(spec) == {'path', 'bytes', 'sha256'} and integer(spec['bytes'], 1, budget - evidence_bytes)
                    and valid_sha(spec['sha256']), 'Invalid asset byte/hash pin')
            data = release.evidence_for_row(row_number)
            require(len(data) == spec['bytes'], 'Asset integrity mismatch: ' + spec['path'])
            evidence[spec['path']] = spec
            evidence_bytes += len(data)
    require(seen == release.count, 'Incomplete rights coverage')
    stored = release.evidence_connection().execute('SELECT count(*), coalesce(sum(bytes), 0) FROM evidence').fetchone()
    require(stored == (len(evidence), evidence_bytes), 'Evidence database holds unpinned rows')
    lookup = validate_lookup_index(release, scratch_dir=scratch_dir) if release.minor_version >= 1 else None
    return {'rows': seen, 'evidenceFiles': len(evidence), 'evidenceBytes': evidence_bytes, 'lookupIndex': lookup}


def reconstruct_sources(release):
    """Rebuild the v1 catalog, rights and artist-record objects from the database (for proofs)."""
    meta = release.catalog_meta
    records = release.connection().execute('SELECT track_json, rights_json FROM records ORDER BY row').fetchall()
    catalog, rights = json.loads(meta['catalogHeader']), json.loads(meta['rightsHeader'])
    catalog['tracks'] = [json.loads(track) for track, _ in records]
    rights['tracks'] = [json.loads(right) for _, right in records]
    artists = json.loads(meta['artistHeader'])
    artists['rows'] = [{'trackId': ident, 'artistId': artist} for ident, artist in
                       release.connection().execute('SELECT id, artist_id FROM tracks ORDER BY row')]
    return catalog, rights, artists
