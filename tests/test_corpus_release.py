"""Temporary synthetic contract fixtures only; no new music or rights clearance."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import (ASSETS, PAIR_ID, ReleaseError, ReleaseLimits, object_sha,
                            read_confined, sha256, strict_json, validate_release)


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


class Candidate:
    """Never use this fixture generator to construct a real corpus."""
    def __init__(self, directory, count=3, legacy=False):
        self.root = Path(directory)
        original = ROOT / 'music-search-studio/data'
        self.manifest = json.loads((original / 'manifest.json').read_bytes())
        self.ids = [f'synthetic-test:{i}' for i in range(count)]
        row = {'title': 'Synthetic unit-test fixture, not music', 'artist': 'Unit test',
               'license': 'CC0-1.0', 'licenseUrl': 'https://creativecommons.org/publicdomain/zero/1.0/',
               'sourceUrl': 'https://example.invalid/synthetic-fixture',
               'audioBytes': 1, 'audioSha256': sha256(b'x'), 'attribution': 'Synthetic fixture only',
               'modifications': 'No recording or derived music embedding exists', 'suppliedNotices': {}}
        self.catalog = {'schemaVersion': 1, 'id': 'synthetic-fixture-only:' + str(count), 'dimensions': 512,
                        'tracks': [{**row, 'id': ident} for ident in self.ids]}
        unit = struct.pack('<512f', 1., *([0.] * 511))
        self.vectors = unit * count
        self.graph = {'schemaVersion': 1, 'algorithm': 'hnsw-static-cosine-v1', 'dimensions': 512,
                      'M': 2, 'efConstruction': 4, 'seed': 43, 'entry': 0, 'maxLevel': 0,
                      'count': count, 'links': [[[(i+1) % count]] if count > 1 else [[]] for i in range(count)]}
        if legacy:
            self.catalog = json.loads((original / 'catalog.json').read_bytes())
            self.ids = json.loads((original / 'ids.json').read_bytes())
            self.vectors = (original / 'vectors.f32').read_bytes()
            self.graph = json.loads((original / 'index.json').read_bytes())
        self.evidence_data = b'SYNTHETIC TEST EVIDENCE ONLY. This file grants no music rights.\n'
        evidence = self.root / 'evidence/test.txt'
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(self.evidence_data)
        evidence_spec = {'path': 'evidence/test.txt', 'bytes': len(self.evidence_data), 'sha256': sha256(self.evidence_data)}
        self.rights = {'schemaVersion': 1, 'kind': 'music-corpus-rights', 'catalogId': self.catalog['id'],
                      'scope': 'searchable-audio-embeddings', 'tracks': []}
        for track in self.catalog['tracks']:
            self.rights['tracks'].append({**{key: track[key] for key in ('id', 'sourceUrl', 'license', 'licenseUrl', 'audioBytes', 'audioSha256')},
                'decision': 'approved', 'use': 'searchable-audio-embeddings', 'reviewedBy': 'Synthetic test fixture',
                'reviewedAt': '2026-10-04T00:00:00Z', 'basis': 'Test assertion only, not real clearance',
                'evidence': {'asset': deepcopy(evidence_spec), 'sourceUrl': 'https://example.invalid/test-evidence',
                             'locator': 'Synthetic test row ' + track['id']}})
        self.release = {'schemaVersion': 1, 'kind': 'music-corpus-release', 'catalogId': self.catalog['id'],
                        'count': len(self.ids), 'dimensions': 512, 'pairId': PAIR_ID}
        self.save()

    def save(self):
        raw = {'catalog': encoded(self.catalog), 'ids': encoded(self.ids), 'vectors': self.vectors}
        count = len(self.ids)
        identity = self.manifest['graphIdentity']
        identity.update(count=count, vectorsSha256=sha256(self.vectors), orderedIdsSha256=object_sha(self.ids),
                        construction={key: self.graph[key] for key in ('M', 'efConstruction', 'seed')})
        graph_id = 'experimental-clap-audio-graph:' + object_sha(identity)
        self.graph['spaceId'] = graph_id
        raw['index'] = encoded(self.graph)
        self.manifest.update(graphId=graph_id, catalogId=self.catalog['id'], count=count, orderedIds=self.ids,
                             orderedIdsSha256=object_sha(self.ids), vectorsSha256=sha256(self.vectors),
                             indexSha256=sha256(raw['index']))
        self.manifest['assets'] = {ASSETS[key]: {'path': ASSETS[key], 'bytes': len(value), 'sha256': sha256(value)}
                                   for key, value in raw.items()}
        raw['graphManifest'] = encoded(self.manifest)
        self.rights['catalogSha256'] = sha256(raw['catalog'])
        raw['rights'] = encoded(self.rights)
        self.release['assets'] = {key: {'path': ASSETS[key], 'bytes': len(value), 'sha256': sha256(value)}
                                  for key, value in raw.items()}
        for key, value in raw.items():
            (self.root / ASSETS[key]).write_bytes(value)
        (self.root / 'release.json').write_bytes(encoded(self.release))
        self.expected = sha256(encoded(self.release))

    def validate(self, **kwargs):
        return validate_release(self.root, expected_manifest_sha256=self.expected, **kwargs)


class ReleaseTests(unittest.TestCase):
    def candidate(self, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Candidate(directory.name, **kwargs)

    def test_real_108_dimensions_survive_additive_contract_without_activation(self):
        candidate = self.candidate(legacy=True)
        result = candidate.validate()
        self.assertEqual(result.count, 108)
        self.assertEqual(result.dimensions, 512)
        self.assertEqual(result.assets['vectors'], (ROOT / 'music-search-studio/data/vectors.f32').read_bytes())
        self.assertFalse(result.summary()['runtimeActivated'])
        self.assertFalse(result.summary()['playbackEnabled'])
        self.assertEqual(result.evidence_count, 1)
        # Its review ledger is synthetic, so this is a shape/identity check only.

    def test_500_synthetic_contract_rows_pass_without_becoming_music(self):
        result = self.candidate(count=500).validate()
        self.assertEqual(result.count, 500)
        self.assertEqual(len(result.assets['vectors']), 1_024_000)
        self.assertTrue(all(i.startswith('synthetic-test:') for i in result.ordered_ids))
        with self.assertRaises(TypeError):
            result.assets['vectors'] = b''

    def test_1000_synthetic_rows_need_the_explicit_local_candidate_budget(self):
        candidate = self.candidate(count=1000)
        with self.assertRaisesRegex(ReleaseError, 'Track count'):
            candidate.validate()
        result = candidate.validate(limits=ReleaseLimits(max_tracks=1000))
        self.assertEqual(result.count, 1000)
        self.assertEqual(len(result.assets['vectors']), 2_048_000)
        self.assertFalse(result.summary()['runtimeActivated'])
        self.assertTrue(all(ident.startswith('synthetic-test:') for ident in result.ordered_ids))

    def test_manifest_needs_exact_out_of_band_digest(self):
        candidate = self.candidate()
        for expected in [None, '', 'not-a-hash', '0' * 64]:
            with self.subTest(expected=expected), self.assertRaises(ReleaseError):
                validate_release(candidate.root, expected_manifest_sha256=expected)

    def test_tampered_each_core_asset_fails_before_use(self):
        candidate = self.candidate()
        for path in ASSETS.values():
            with self.subTest(path=path):
                target = candidate.root / path
                original = target.read_bytes()
                target.write_bytes(original + b'!')
                with self.assertRaises(ReleaseError):
                    candidate.validate()
                target.write_bytes(original)

    def test_count_budget_requires_explicit_review_and_has_hard_ceiling(self):
        candidate = self.candidate(count=501)
        with self.assertRaisesRegex(ReleaseError, 'Track count'):
            candidate.validate()
        self.assertEqual(candidate.validate(limits=ReleaseLimits(max_tracks=501)).count, 501)
        for changes in [{'max_tracks': 10001}, {'max_tracks': True}, {'core_bytes': 64_000_001}]:
            with self.subTest(changes=changes), self.assertRaises(ReleaseError):
                ReleaseLimits(**changes)

    def test_aggregate_core_and_evidence_budgets_fail_closed(self):
        candidate = self.candidate()
        with self.assertRaises(ReleaseError):
            candidate.validate(limits=ReleaseLimits(core_bytes=1000))
        with self.assertRaises(ReleaseError):
            candidate.validate(limits=ReleaseLimits(evidence_bytes=1))
        self.assertEqual(candidate.validate().evidence_bytes, len(candidate.evidence_data))

    def test_bad_dimensions_pair_and_boolean_count_are_rejected(self):
        for key, value in [('dimensions', 256), ('count', True), ('pairId', 'unreviewed')]:
            candidate = self.candidate()
            candidate.release[key] = value
            candidate.save()
            with self.subTest(key=key), self.assertRaises(ReleaseError):
                candidate.validate()

    def test_renormalized_manifest_cannot_hide_bad_vector_shape_or_values(self):
        for vectors in [b'x', struct.pack('<512f', float('nan'), *([0.] * 511)) * 3,
                        struct.pack('<512f', 2., *([0.] * 511)) * 3]:
            candidate = self.candidate()
            candidate.vectors = vectors
            candidate.save()
            with self.subTest(length=len(vectors)), self.assertRaises(ReleaseError):
                candidate.validate()

    def test_duplicate_and_reordered_ids_are_rejected_even_with_rehashed_files(self):
        for mutate in [lambda ids: [ids[0], ids[0], ids[2]], lambda ids: list(reversed(ids))]:
            candidate = self.candidate()
            candidate.ids = mutate(candidate.ids)
            candidate.save()
            with self.assertRaises(ReleaseError):
                candidate.validate()

    def test_audio_preprocessing_and_encoder_identity_cannot_silently_change(self):
        for mutate in [lambda manifest: manifest['graphIdentity']['audio'].update(audioModelSha256='0' * 64),
                       lambda manifest: manifest['allowedQueryProfiles'][0]['identity'].update(dimensions=256)]:
            candidate = self.candidate()
            mutate(candidate.manifest)
            candidate.save()
            with self.assertRaises(ReleaseError):
                candidate.validate()

    def test_graph_topology_count_and_degree_are_validated(self):
        for mutate in [lambda graph: graph.update(count=4), lambda graph: graph['links'][0][0].append(99),
                       lambda graph: graph['links'][0][0].append(1), lambda graph: graph.update(M=100)]:
            candidate = self.candidate()
            mutate(candidate.graph)
            candidate.save()
            with self.assertRaises(ReleaseError):
                candidate.validate()

    def test_rights_coverage_review_license_and_scope_are_required(self):
        mutations = [lambda rights: rights['tracks'].pop(),
                     lambda rights: rights['tracks'][0].update(decision='pending'),
                     lambda rights: rights['tracks'][0].update(license='CC-BY-NC-4.0'),
                     lambda rights: rights.update(scope='public-audio-distribution'),
                     lambda rights: rights['tracks'][0].update(audioSha256='0' * 64),
                     lambda rights: rights['tracks'][0].update(reviewedBy=''),
                     lambda rights: rights['tracks'][0].update(reviewedAt='yesterday')]
        for mutate in mutations:
            candidate = self.candidate()
            mutate(candidate.rights)
            candidate.save()
            with self.assertRaises(ReleaseError):
                candidate.validate()

    def test_missing_or_tampered_local_rights_evidence_fails(self):
        candidate = self.candidate()
        evidence = candidate.root / 'evidence/test.txt'
        evidence.write_bytes(b'changed')
        with self.assertRaises(ReleaseError):
            candidate.validate()
        evidence.unlink()
        with self.assertRaises(ReleaseError):
            candidate.validate()

    def test_conflicting_shared_evidence_pins_fail(self):
        candidate = self.candidate()
        candidate.rights['tracks'][1]['evidence']['asset']['sha256'] = '0' * 64
        candidate.save()
        with self.assertRaisesRegex(ReleaseError, 'Conflicting'):
            candidate.validate()

    def test_asset_traversal_and_symlinked_components_are_rejected(self):
        candidate = self.candidate()
        candidate.release['assets']['catalog']['path'] = '../catalog.json'
        raw = encoded(candidate.release)
        (candidate.root / 'release.json').write_bytes(raw)
        with self.assertRaises(ReleaseError):
            validate_release(candidate.root, expected_manifest_sha256=sha256(raw))
        candidate.save()
        path = candidate.root / 'evidence/test.txt'
        path.unlink()
        path.symlink_to(ROOT / 'README.md')
        with self.assertRaises(ReleaseError):
            candidate.validate()
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(ROOT)
        with self.assertRaises(ReleaseError):
            candidate.validate()

    def test_special_file_does_not_block_the_validator(self):
        candidate = self.candidate()
        path = candidate.root / 'fifo'
        os.mkfifo(path)
        code = 'import sys;sys.path.insert(0,sys.argv[1]);from corpus_release import read_confined;read_confined(sys.argv[2],"fifo",10)'
        result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'server'), str(candidate.root)],
                                capture_output=True, text=True, timeout=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ReleaseError', result.stderr)

    def test_strict_json_rejects_duplicates_nonfinite_overflow_and_wrong_encoding(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'\xff']:
            with self.subTest(raw=raw), self.assertRaises(ReleaseError):
                strict_json(raw, 'test', 100)

    def test_reviewed_graph_cannot_supply_a_different_runtime_space_alias(self):
        candidate = self.candidate()
        candidate.graph['indexSpaceId'] = 'different-space:unreviewed'
        candidate.save()
        with self.assertRaisesRegex(ReleaseError, 'space alias'):
            candidate.validate()

    def test_float_dimensions_and_disconnected_base_layer_are_rejected(self):
        for mutate in [lambda c: c.graph.update(dimensions=512.0), lambda c: c.graph.update(count=3.0),
                       lambda c: c.catalog.update(dimensions=512.0),
                       lambda c: c.graph.update(links=[[[]], [[]], [[]]])]:
            candidate = self.candidate()
            mutate(candidate)
            candidate.save()
            with self.assertRaises(ReleaseError):
                candidate.validate()

    def test_audio_identity_compares_canonical_json_not_python_numeric_equality(self):
        candidate = self.candidate()
        candidate.manifest['graphIdentity']['audio']['preprocessing']['sampleRateHz'] = 48000.0
        candidate.save()
        with self.assertRaisesRegex(ReleaseError, 'Audio model'):
            candidate.validate()

    def test_malformed_shapes_and_naive_review_date_are_structured_errors(self):
        mutations = [lambda c: c.manifest['allowedQueryProfiles'][0].update(source=[]),
                     lambda c: c.rights['tracks'][0].update(license=[]),
                     lambda c: c.rights['tracks'][0].update(reviewedAt='2026-10-04Z')]
        for mutate in mutations:
            candidate = self.candidate()
            mutate(candidate)
            candidate.save()
            with self.assertRaises(ReleaseError):
                candidate.validate()

    def test_unpaired_surrogate_strings_and_keys_are_rejected(self):
        for value in [{'title': chr(0xd800)}, {chr(0xdfff): 1}]:
            with self.assertRaises(ReleaseError):
                strict_json(json.dumps(value).encode(), 'test', 1000)

    def test_cli_returns_bounded_read_only_summary_and_nonzero_for_unreviewed_manifest(self):
        candidate = self.candidate()
        command = [sys.executable, str(ROOT / 'scripts/verify_corpus_release.py'), '--release-dir', str(candidate.root),
                   '--expected-manifest-sha256', candidate.expected]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertTrue(summary['ok'])
        self.assertFalse(summary['runtimeActivated'])
        self.assertFalse(summary['playbackEnabled'])
        result = subprocess.run(command[:-1] + ['0' * 64], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stderr)['ok'])


if __name__ == '__main__':
    unittest.main()
