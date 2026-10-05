"""Offline acceptance contract for a future explicitly reviewed corpus release.

This module never selects a deployment, downloads audio, constructs embeddings,
or grants rights. The caller must supply a separately reviewed manifest digest.
The existing 108-track runtime does not import or activate this contract.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
from types import MappingProxyType
from urllib.parse import urlsplit


PAIR_SHA = 'af765397efb6230ae8bf88d3b863a3773a2f453a0d3cec52dd2645cd6786ed0c'
PAIR_ID = 'experimental-fma-q8:3d5a3ebe3ac4bd09fa96f05fdefc2df1ae841d2562dea913abb8a5990b377566'
NATIVE_PROFILE_SHA = 'ea6dd665180b9b3f2a2999941fe7cff736bbefafa597d26312fd520cc0e5ea31'
HNSW_SOURCE_SHA = 'da9d421f511df4dfbe5d21c4c4b748821d0b5f14b9e37a5f09c1b32c7ba0031d'
ASSETS = {'catalog': 'catalog.json', 'ids': 'ids.json', 'vectors': 'vectors.f32',
          'index': 'index.json', 'graphManifest': 'manifest.json', 'rights': 'rights.json'}
LICENSES = {'CC-BY-4.0': 'https://creativecommons.org/licenses/by/4.0/',
            'CC-BY-3.0': 'https://creativecommons.org/licenses/by/3.0/',
            'CC0-1.0': 'https://creativecommons.org/publicdomain/zero/1.0/'}
SHA = re.compile(r'[a-f0-9]{64}\Z')
TRACK_ID = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')


class ReleaseError(ValueError):
    """The candidate is incomplete, inconsistent, unreviewed, or over budget."""


@dataclass(frozen=True)
class ReleaseLimits:
    max_tracks: int = 500
    core_bytes: int = 16_000_000
    evidence_bytes: int = 8_000_000
    json_bytes: int = 8_000_000

    def __post_init__(self):
        for value, maximum in [(self.max_tracks, 10_000), (self.core_bytes, 64_000_000),
                               (self.evidence_bytes, 16_000_000), (self.json_bytes, 16_000_000)]:
            if not integer(value, 1, maximum):
                raise ReleaseError('Invalid explicit validation budget')


@dataclass(frozen=True)
class ValidatedRelease:
    manifest_sha256: str
    catalog_id: str
    graph_id: str
    count: int
    dimensions: int
    ordered_ids: tuple
    assets: object  # read-only name -> exact verified bytes; never reopened by the consumer
    evidence_count: int
    evidence_bytes: int

    def summary(self):
        vector_bytes = len(self.assets['vectors'])
        return {'schemaVersion': 1, 'releaseId': 'corpus-release:' + self.manifest_sha256,
                'catalogId': self.catalog_id, 'graphId': self.graph_id, 'count': self.count,
                'dimensions': self.dimensions, 'vectorBytes': vector_bytes,
                'coreBytes': sum(map(len, self.assets.values())),
                'uniqueEvidenceFiles': self.evidence_count, 'evidenceBytes': self.evidence_bytes,
                'rightsStatus': 'recorded reviews and local evidence verified; not an independent legal determination',
                'currentStaticVectorLimitSatisfied': vector_bytes <= 20_000_000,
                'runtimeActivated': False, 'playbackEnabled': False}


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def object_sha(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def nonempty(value, maximum=2048):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def strict_json(data, label, limit):
    require(len(data) <= limit, label + ' exceeds JSON budget')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, label + ' has duplicate JSON keys')
            result[key] = value
        return result
    def invalid_constant(_):
        raise ReleaseError(label + ' has a non-finite JSON number')
    def finite_float(text):
        value = float(text)
        require(math.isfinite(value), label + ' has an overflowing JSON number')
        return value
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=finite_float)
        def check_strings(item):
            if isinstance(item, str):
                item.encode('utf-8', errors='strict')
            elif isinstance(item, dict):
                for key, child in item.items():
                    check_strings(key)
                    check_strings(child)
            elif isinstance(item, list):
                for child in item:
                    check_strings(child)
        check_strings(value)
        return value
    except ReleaseError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ReleaseError(label + ' is not bounded UTF-8 JSON') from error


def read_confined(root, relative, max_bytes):
    """Open every path component without following symlinks; return bounded bytes."""
    require(nonempty(relative, 256) and not relative.startswith('/') and '\\' not in relative
            and '\x00' not in relative, 'Invalid release asset path')
    parts = relative.split('/')
    require(all(part and not part.startswith('.') for part in parts), 'Invalid release asset component')
    require(integer(max_bytes, 1, 64_000_000), 'Invalid asset read limit')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory = None
    try:
        directory = os.open(root, flags | os.O_DIRECTORY)
        for part in parts[:-1]:
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(parts[-1], flags, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_size <= max_bytes,
                    'Asset type or byte budget mismatch: ' + relative)
            data = stream.read(max_bytes + 1)
        require(len(data) <= max_bytes, 'Asset grew beyond byte budget: ' + relative)
        return data
    except OSError as error:
        raise ReleaseError('Missing, unreadable, or symlinked asset: ' + relative) from error
    finally:
        if directory is not None:
            os.close(directory)


def verify_spec(root, spec, max_bytes, expected_path=None):
    require(isinstance(spec, dict) and set(spec) == {'path', 'bytes', 'sha256'}, 'Invalid asset pin')
    require(integer(spec['bytes'], 1, max_bytes) and valid_sha(spec['sha256']), 'Invalid asset byte/hash pin')
    if expected_path is not None:
        require(spec['path'] == expected_path, 'Unexpected asset path')
    data = read_confined(root, spec['path'], spec['bytes'])
    require(len(data) == spec['bytes'] and sha256(data) == spec['sha256'], 'Asset integrity mismatch: ' + spec['path'])
    return data


def pinned_pair():
    root = Path(__file__).resolve().parents[1]
    data = read_confined(root, 'model/model-space-q8.json', 16_384)
    require(sha256(data) == PAIR_SHA, 'Built-in model pairing pin mismatch')
    pair = strict_json(data, 'model pair', 16_384)
    require(pair.get('id') == PAIR_ID and PAIR_ID == 'experimental-fma-q8:' + object_sha(pair['identity']),
            'Built-in model pairing identity mismatch')
    return pair['identity']


def validate_vectors(data, count, dimensions):
    require(len(data) == count * dimensions * 4, 'Vector dimensions/length mismatch')
    for row in struct.iter_unpack('<' + str(dimensions) + 'f', data):
        require(all(math.isfinite(value) for value in row), 'Non-finite audio vector')
        require(abs(math.sqrt(sum(value * value for value in row)) - 1) <= 1e-5,
                'Audio vector is not L2 normalized')


def validate_index(graph, count, dimensions):
    require(isinstance(graph, dict) and integer(graph.get('schemaVersion'), 1, 1)
            and graph.get('algorithm') == 'hnsw-static-cosine-v1'
            and integer(graph.get('count'), count, count) and integer(graph.get('dimensions'), dimensions, dimensions),
            'Index dimensions/count mismatch')
    degree, maximum, entry = graph.get('M'), graph.get('maxLevel'), graph.get('entry')
    require(integer(degree, 2, 32) and integer(graph.get('efConstruction'), degree, 1000)
            and integer(graph.get('seed'), 0, 2**32 - 1) and integer(maximum, 0, 24)
            and integer(entry, 0, count - 1), 'Index construction budget mismatch')
    links = graph.get('links')
    require(isinstance(links, list) and len(links) == count, 'Index row count mismatch')
    for layers in links:
        require(isinstance(layers, list) and 1 <= len(layers) <= maximum + 1, 'Invalid index layers')
    for row, layers in enumerate(links):
        for level, neighbors in enumerate(layers):
            require(isinstance(neighbors, list) and len(neighbors) <= degree * (2 if level == 0 else 1),
                    'Index degree budget mismatch')
            require(all(integer(i, 0, count - 1) and i != row and len(links[i]) > level for i in neighbors),
                    'Invalid index neighbor')
            require(len(set(neighbors)) == len(neighbors), 'Duplicate index neighbor')
    require(len(links[entry]) == maximum + 1, 'Index entry level mismatch')
    require('indexSpaceId' not in graph or graph['indexSpaceId'] == graph.get('spaceId'), 'Conflicting graph space alias')
    reached, pending = {entry}, [entry]
    while pending:
        for neighbor in links[pending.pop()][0]:
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    require(len(reached) == count, 'Index base layer does not reach every track')


def validate_graph_manifest(manifest, graph, catalog, ids, raw, pair):
    require(isinstance(manifest, dict), 'Invalid graph manifest')
    identity = manifest.get('graphIdentity')
    require(isinstance(identity, dict), 'Missing audio graph identity')
    graph_id = 'experimental-clap-audio-graph:' + object_sha(identity)
    require(manifest.get('graphId') == graph_id and graph.get('spaceId') == graph_id,
            'Graph identity digest mismatch')
    count = len(ids)
    expected_audio = {'family': pair['family'], 'repo': pair['repo'], 'revision': pair['revision'],
        'audioModelSha256': pair['audioModelSha256'], 'audioQuantization': pair['audioQuantization'],
        'preprocessorConfigSha256': pair['preprocessorConfigSha256'],
        'preprocessing': pair['audioPreprocessing'], 'preprocessingSha256': object_sha(pair['audioPreprocessing']),
        'executionProfile': pair['audioExecutionProfile'], 'executionProfileSha256': object_sha(pair['audioExecutionProfile'])}
    require(object_sha(identity.get('audio')) == object_sha(expected_audio), 'Audio model/preprocessing identity mismatch')
    require(integer(manifest.get('schemaVersion'), 1, 1) and integer(identity.get('schemaVersion'), 1, 1)
            and manifest.get('catalogId') == catalog['id']
            and integer(manifest.get('count'), count, count) and integer(identity.get('count'), count, count)
            and integer(manifest.get('dimensions'), 512, 512) and integer(identity.get('dimensions'), 512, 512)
            and identity.get('metric') == 'cosine' and identity.get('algorithm') == graph['algorithm']
            and object_sha(identity.get('construction')) == object_sha({key: graph[key] for key in ('M', 'efConstruction', 'seed')})
            and identity.get('hnswSourceSha256') == HNSW_SOURCE_SHA,
            'Graph release contract mismatch')
    require(manifest.get('orderedIds') == ids
            and manifest.get('orderedIdsSha256') == object_sha(ids)
            and identity.get('orderedIdsSha256') == object_sha(ids), 'Graph row-order mismatch')
    require(manifest.get('vectorsSha256') == sha256(raw['vectors'])
            and identity.get('vectorsSha256') == sha256(raw['vectors'])
            and manifest.get('indexSha256') == sha256(raw['index']), 'Graph vector/index digest mismatch')
    require(isinstance(manifest.get('assets'), dict), 'Invalid graph asset pins')
    for key in ('catalog', 'ids', 'vectors', 'index'):
        spec = manifest.get('assets', {}).get(ASSETS[key], {})
        require(spec == {'path': ASSETS[key], 'bytes': len(raw[key]), 'sha256': sha256(raw[key])},
                'Graph asset pin mismatch: ' + key)
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


def public_source_url(value):
    if not nonempty(value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def validate_rights(rights, catalog, catalog_sha, root, limits):
    require(isinstance(rights, dict) and integer(rights.get('schemaVersion'), 1, 1)
            and rights.get('kind') == 'music-corpus-rights'
            and rights.get('catalogId') == catalog['id'] and rights.get('catalogSha256') == catalog_sha
            and rights.get('scope') == 'searchable-audio-embeddings', 'Rights manifest binding/scope mismatch')
    rows, tracks = rights.get('tracks'), catalog['tracks']
    require(isinstance(rows, list) and len(rows) == len(tracks), 'Incomplete rights coverage')
    evidence, evidence_bytes = {}, 0
    for row, track in zip(rows, tracks):
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
            require(evidence_bytes < limits.evidence_bytes, 'Evidence aggregate budget exceeded')
            data = verify_spec(root, spec, limits.evidence_bytes - evidence_bytes)
            evidence[spec['path']] = spec
            evidence_bytes += len(data)
    return len(evidence), evidence_bytes


def validate_release(root, *, expected_manifest_sha256, limits=None):
    """Validate a candidate snapshot without altering the current deployment.

    The expected SHA must come from a separately reviewed handoff. Computing it
    from an untrusted candidate and immediately trusting it is not approval.
    """
    limits = limits or ReleaseLimits()
    require(isinstance(limits, ReleaseLimits), 'Invalid validation limits')
    require(valid_sha(expected_manifest_sha256), 'A reviewed release manifest digest is required')
    manifest_bytes = read_confined(root, 'release.json', 65_536)
    require(sha256(manifest_bytes) == expected_manifest_sha256, 'Unreviewed release manifest digest')
    release = strict_json(manifest_bytes, 'release manifest', 65_536)
    require(isinstance(release, dict) and set(release) == {'schemaVersion', 'kind', 'catalogId', 'count',
            'dimensions', 'pairId', 'assets'} and integer(release['schemaVersion'], 1, 1)
            and release['kind'] == 'music-corpus-release' and nonempty(release['catalogId'], 256),
            'Unknown release contract')
    count = release['count']
    require(integer(count, 1, limits.max_tracks), 'Track count exceeds explicit review budget')
    require(type(release['dimensions']) is int and release['dimensions'] == 512 and release['pairId'] == PAIR_ID,
            'Unreviewed embedding dimensions or pairing')
    specs = release['assets']
    require(isinstance(specs, dict) and set(specs) == set(ASSETS), 'Incomplete or unexpected release assets')
    raw, total = {}, 0
    for key, path in ASSETS.items():
        require(total < limits.core_bytes, 'Core aggregate byte budget exceeded')
        raw[key] = verify_spec(root, specs[key], limits.core_bytes - total, path)
        total += len(raw[key])
    catalog = strict_json(raw['catalog'], 'catalog', limits.json_bytes)
    ids = strict_json(raw['ids'], 'ordered IDs', limits.json_bytes)
    require(isinstance(catalog, dict) and catalog.get('id') == release['catalogId']
            and integer(catalog.get('schemaVersion'), 1, 1) and integer(catalog.get('dimensions'), 512, 512)
            and isinstance(catalog.get('tracks'), list) and len(catalog['tracks']) == count,
            'Catalog identity/count mismatch')
    require(isinstance(ids, list) and len(ids) == count
            and all(isinstance(i, str) and TRACK_ID.fullmatch(i) for i in ids) and len(set(ids)) == count,
            'Invalid or duplicate ordered track IDs')
    require(all(isinstance(track, dict) for track in catalog['tracks'])
            and [track.get('id') for track in catalog['tracks']] == ids, 'Catalog row-order mismatch')
    validate_vectors(raw['vectors'], count, 512)
    graph = strict_json(raw['index'], 'index', limits.json_bytes)
    validate_index(graph, count, 512)
    graph_manifest = strict_json(raw['graphManifest'], 'graph manifest', limits.json_bytes)
    graph_id = validate_graph_manifest(graph_manifest, graph, catalog, ids, raw, pinned_pair())
    rights = strict_json(raw['rights'], 'rights manifest', limits.json_bytes)
    evidence_count, evidence_bytes = validate_rights(rights, catalog, sha256(raw['catalog']), root, limits)
    return ValidatedRelease(expected_manifest_sha256, catalog['id'], graph_id, count, 512,
                            tuple(ids), MappingProxyType(raw), evidence_count, evidence_bytes)
