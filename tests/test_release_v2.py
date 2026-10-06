"""Release format v2: conversion proofs, tamper detection, limits, selection, search parity."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_fixtures import ROOT, V1_DIR, V1_SHA, converted_fma2000  # noqa: E402
from active_corpus import selected_corpus  # noqa: E402
from corpus_release import ReleaseError, ReleaseLimits, sha256, validate_release, validate_rights  # noqa: E402
from hnsw_trace import HNSW, cosine_distance  # noqa: E402
from release_v2 import (LOOKUP_INDEX, LimitsV2, check_csr, load_release_v2, page_query, reconstruct_sources,  # noqa: E402
                        selected_release_v2, validate_rows)
import search_v2  # noqa: E402
from search_v2 import GraphV2, exact_rescore  # noqa: E402


def copy_release(source, target):
    shutil.copytree(source, target)
    return target


class V2Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir, cls.sha = converted_fma2000()
        cls.release = load_release_v2(cls.dir, expected_manifest_sha256=cls.sha)
        cls.v1 = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=2000))
        cls.v1_index = json.loads(cls.v1.assets['index'])
        cls.reference = HNSW(cls.v1_index, cls.v1.assets['vectors'])
        cls.graph = GraphV2(cls.release)
        cls.examples = json.loads((V1_DIR / 'examples.json').read_bytes())['examples']

    def temp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name).resolve()


class ConversionTests(V2Fixture):
    def test_logical_identities_are_kept_and_artifact_digests_describe_v2_files(self):
        r, v1 = self.release, self.v1
        self.assertEqual((r.catalog_id, r.graph_id, r.count, r.dimensions), (v1.catalog_id, v1.graph_id, 2000, 512))
        self.assertEqual(r.vectors_sha256, sha256(v1.assets['vectors']))
        self.assertEqual(r.ordered_ids, v1.ordered_ids)
        # catalogSha256 and indexSha256 now name the files the server actually verified.
        self.assertEqual(r.catalog_sha256, sha256((self.dir / 'catalog.sqlite').read_bytes()))
        self.assertEqual(r.graph_sha256, sha256((self.dir / 'graph.bin').read_bytes()))
        self.assertNotEqual(r.catalog_sha256, sha256(v1.assets['catalog']))
        self.assertNotEqual(r.graph_sha256, sha256(v1.assets['index']))
        self.assertEqual(r.graph_manifest_sha256, sha256(v1.assets['graphManifest']))
        source = r.manifest['source']
        self.assertEqual((source['manifestSha256'], source['catalogSha256'], source['indexSha256'], source['rightsSha256']),
                         (V1_SHA, sha256(v1.assets['catalog']), sha256(v1.assets['index']), sha256(v1.assets['rights'])))
        self.assertEqual((self.dir / 'vectors.f32').read_bytes(), v1.assets['vectors'])

    def test_database_rebuilds_the_validated_v1_objects_and_evidence_bytes(self):
        catalog, rights, artists = reconstruct_sources(self.release)
        self.assertEqual((json.dumps(catalog, indent=2, ensure_ascii=False) + '\n').encode(), self.v1.assets['catalog'])
        self.assertEqual(rights, json.loads(self.v1.assets['rights']))
        self.assertEqual(artists, json.loads((V1_DIR / 'artist-records.json').read_bytes()))
        for row in (0, 1, 999, 1999):
            pin = rights['tracks'][row]['evidence']['asset']
            self.assertEqual(self.release.evidence_for_row(row), (V1_DIR / pin['path']).read_bytes())
        self.assertEqual(validate_rows(self.release), {'rows': 2000, 'evidenceFiles': 2000, 'evidenceBytes': 7_019_915,
                                                       'lookupIndex': LOOKUP_INDEX})

    def test_csr_graph_and_layout_decode_to_the_v1_index_and_positions(self):
        from convert_release_v1_to_v2 import decode_links
        self.assertEqual(decode_links(self.release), self.v1_index['links'])
        layout = json.loads((V1_DIR / 'layout.json').read_bytes())
        self.assertTrue(np.array_equal(np.asarray(self.release.layout, dtype=np.float64), np.asarray(layout['positions'])))
        self.assertEqual((self.release.graph['entry'], self.release.graph['maxLevel']),
                         (self.v1_index['entry'], self.v1_index['maxLevel']))

    def test_conversion_is_deterministic(self):
        from convert_release_v1_to_v2 import convert
        output = self.temp() / 'again'
        convert(V1_DIR, V1_SHA, output, v1_limits=ReleaseLimits(max_tracks=2000), samples=2)
        for name in ['release.json', 'catalog.sqlite', 'evidence.sqlite', 'vectors.f32', 'graph.bin', 'layout.f32',
                     'graph-manifest.json', 'examples.json']:
            with self.subTest(name=name):
                self.assertEqual((output / name).read_bytes(), (self.dir / name).read_bytes())

    def test_converter_refuses_an_unreviewed_source_or_an_existing_output(self):
        from convert_release_v1_to_v2 import convert
        with self.assertRaisesRegex(ReleaseError, 'Unreviewed release manifest digest'):
            convert(V1_DIR, '0' * 64, self.temp() / 'x', v1_limits=ReleaseLimits(max_tracks=2000))
        with self.assertRaisesRegex(ReleaseError, 'overwrite'):
            convert(V1_DIR, V1_SHA, self.dir, v1_limits=ReleaseLimits(max_tracks=2000))


class TamperTests(V2Fixture):
    def tampered(self, name, mutate):
        target = copy_release(self.dir, self.temp() / 'release')
        path = target / name
        data = bytearray(path.read_bytes())
        mutate(data)
        path.write_bytes(bytes(data))
        return target

    def test_every_core_asset_is_digest_checked_at_startup(self):
        for name in ['catalog.sqlite', 'vectors.f32', 'graph.bin', 'layout.f32', 'graph-manifest.json']:
            with self.subTest(name=name):
                target = self.tampered(name, lambda data: data.__setitem__(len(data) // 2, data[len(data) // 2] ^ 1))
                with self.assertRaisesRegex(ReleaseError, 'integrity mismatch'):
                    load_release_v2(target, expected_manifest_sha256=self.sha)

    def test_manifest_digest_limits_and_symlinks(self):
        with self.assertRaisesRegex(ReleaseError, 'Unreviewed v2 release manifest digest'):
            load_release_v2(self.dir, expected_manifest_sha256='f' * 64)
        with self.assertRaisesRegex(ReleaseError, 'exceeds the configured v2 limit'):
            load_release_v2(self.dir, expected_manifest_sha256=self.sha, limits=LimitsV2(max_tracks=1999))
        with self.assertRaisesRegex(ReleaseError, 'Invalid v2 asset pin: catalog'):
            load_release_v2(self.dir, expected_manifest_sha256=self.sha, limits=LimitsV2(catalog_bytes=1_000_000))
        with self.assertRaisesRegex(ReleaseError, 'Invalid v2 release limit'):
            LimitsV2(max_tracks=1_000_001)
        with self.assertRaisesRegex(ReleaseError, 'Unknown v2 release limit'):
            LimitsV2.from_config({'maxTracks': 10, 'everything': 1})
        target = copy_release(self.dir, self.temp() / 'linked')
        (target / 'layout.f32').unlink()
        os.symlink(self.dir / 'layout.f32', target / 'layout.f32')
        with self.assertRaisesRegex(ReleaseError, 'symlinked'):
            load_release_v2(target, expected_manifest_sha256=self.sha)

    def test_evidence_is_verified_per_row_on_read_not_at_startup(self):
        target = copy_release(self.dir, self.temp() / 'evidence')
        connection = sqlite3.connect(target / 'evidence.sqlite')
        path, data = connection.execute('SELECT path, data FROM evidence ORDER BY path LIMIT 1').fetchone()
        forged = bytes(data[:-1]) + bytes([data[-1] ^ 1])
        connection.execute('UPDATE evidence SET data = ? WHERE path = ?', (forged, path))
        connection.commit()
        connection.close()
        self.assertEqual((target / 'evidence.sqlite').stat().st_size, (self.dir / 'evidence.sqlite').stat().st_size)
        release = load_release_v2(target, expected_manifest_sha256=self.sha)  # startup does not hash evidence
        row = next(i for i, (_, right) in enumerate(release.connection().execute('SELECT track_json, rights_json FROM records ORDER BY row'))
                   if json.loads(right)['evidence']['asset']['path'] == path)
        with self.assertRaisesRegex(ReleaseError, 'Evidence integrity mismatch'):
            release.evidence_for_row(row)
        with self.assertRaisesRegex(ReleaseError, 'Evidence integrity mismatch'):
            validate_rows(release)

    def test_csr_structure_checks(self):
        r = self.release
        args = dict(count=r.count, degree=r.graph['M'], entry=r.graph['entry'], max_level=r.graph['maxLevel'])
        check_csr(r.node_layers, r.layer_offsets, r.neighbors, **args)
        cases = {'Invalid CSR neighbor': lambda n: n.__setitem__(0, r.count),
                 'CSR self link': lambda n: n.__setitem__(0, 0),
                 'Duplicate CSR neighbor': lambda n: n.__setitem__(1, n[0])}
        for message, mutate in cases.items():
            with self.subTest(message=message):
                neighbors = np.array(r.neighbors)
                mutate(neighbors)
                with self.assertRaisesRegex(ReleaseError, message):
                    check_csr(r.node_layers, r.layer_offsets, neighbors, **args)
        with self.assertRaisesRegex(ReleaseError, 'CSR entry level mismatch'):
            check_csr(r.node_layers, r.layer_offsets, r.neighbors, **{**args, 'entry': 0})


class RowRuleEquivalenceTests(V2Fixture):
    """The v2 row validator must accept and reject exactly what corpus_release.validate_rights does."""

    MUTATIONS = {
        'license URL': (lambda t, r: r.update(licenseUrl='https://creativecommons.org/licenses/by-nc/4.0/'), 'Unreviewed license class or URL'),
        'review decision': (lambda t, r: r.update(decision='rejected'), 'Missing explicit per-track review'),
        # v1 re-raises its inner timestamp error as 'Invalid review timestamp'; v2 must do the same.
        'review time': (lambda t, r: r.update(reviewedAt='2026-10-04 18:28:00'), 'Invalid review timestamp'),
        'source URL': (lambda t, r: (t.update(sourceUrl='ftp://example.org/x'), r.update(sourceUrl='ftp://example.org/x')),
                       'Missing per-track attribution/provenance'),
        'audio pin': (lambda t, r: r.update(audioBytes=r['audioBytes'] + 1), 'Audio provenance byte/hash mismatch'),
        'evidence path': (lambda t, r: r['evidence']['asset'].update(path='../evidence/x.json'), 'Evidence must use a confined evidence path'),
        'attribution': (lambda t, r: t.update(attribution=''), 'Missing per-track attribution/provenance'),
    }

    def test_tampered_rows_get_the_same_verdict_from_v1_and_v2(self):
        catalog, rights = json.loads(self.v1.assets['catalog']), json.loads(self.v1.assets['rights'])
        limits = ReleaseLimits(max_tracks=2000)
        self.assertEqual(validate_rights(rights, catalog, sha256(self.v1.assets['catalog']), V1_DIR, limits)[0], 2000)
        for name, (mutate, message) in self.MUTATIONS.items():
            with self.subTest(mutation=name):
                track, right = json.loads(json.dumps(catalog['tracks'][7])), json.loads(json.dumps(rights['tracks'][7]))
                mutate(track, right)
                v1_catalog = {**catalog, 'tracks': catalog['tracks'][:7] + [track] + catalog['tracks'][8:]}
                v1_rights = {**rights, 'tracks': rights['tracks'][:7] + [right] + rights['tracks'][8:]}
                with self.assertRaisesRegex(ReleaseError, message):
                    validate_rights(v1_rights, v1_catalog, sha256(self.v1.assets['catalog']), V1_DIR, limits)
                # A builder that pins bad rows: the digests are consistent, the content is not.
                target = copy_release(self.dir, self.temp() / 'rows')
                connection = sqlite3.connect(target / 'catalog.sqlite')
                connection.execute('UPDATE records SET track_json = ?, rights_json = ? WHERE row = 7',
                                   (json.dumps(track, ensure_ascii=False, separators=(',', ':')),
                                    json.dumps(right, ensure_ascii=False, separators=(',', ':'))))
                connection.commit()
                connection.close()
                manifest = json.loads((target / 'release.json').read_bytes())
                data = (target / 'catalog.sqlite').read_bytes()
                manifest['assets']['catalog'].update(bytes=len(data), sha256=sha256(data))
                payload = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()
                (target / 'release.json').write_bytes(payload)
                release = load_release_v2(target, expected_manifest_sha256=sha256(payload))
                with self.assertRaisesRegex(ReleaseError, message):
                    validate_rows(release)


class SelectionTests(V2Fixture):
    def root_with(self, config, link=True):
        root = self.temp()
        (root / 'corpus-releases').mkdir()
        if link:
            shutil.copytree(self.dir, root / 'corpus-releases' / 'fma2000-v2', copy_function=shutil.copy2)
        data = (json.dumps(config) + '\n').encode()
        (root / 'active-corpus.json').write_bytes(data)
        return root, {'files': [{'path': 'active-corpus.json', 'bytes': len(data), 'sha256': sha256(data)}]}

    def test_v2_selection_is_package_pinned_and_v1_rules_reject_it(self):
        config = {'schemaVersion': 2, 'enabled': True, 'format': 'music-corpus-release-v2',
                  'directory': 'corpus-releases/fma2000-v2', 'manifestSha256': self.sha}
        root, package = self.root_with(config)
        selected = selected_release_v2(root, package)
        self.assertEqual((selected.release.count, selected.release.manifest_sha256), (2000, self.sha))
        self.assertIs(selected_release_v2(root, package).release, selected.release)  # verified once per process
        with self.assertRaisesRegex(ReleaseError, 'Invalid active corpus selection'):
            selected_corpus(root, package)
        with self.assertRaisesRegex(ReleaseError, 'not pinned'):
            selected_release_v2(root, {'files': []})
        for change, message in [({'enabled': False}, 'Incomplete v2 corpus selection'),
                                ({'format': 'music-corpus-release'}, 'Incomplete v2 corpus selection'),
                                ({'extra': 1}, 'Incomplete v2 corpus selection'),
                                ({'directory': '../fma2000-v2'}, 'Invalid corpus release directory'),
                                ({'limits': {'maxTracks': 100}}, 'exceeds the configured v2 limit')]:
            with self.subTest(change=change):
                root, package = self.root_with({**config, **change}, link=False)
                if 'directory' not in change:
                    shutil.copytree(self.dir, root / 'corpus-releases/fma2000-v2', copy_function=shutil.copy2)
                with self.assertRaisesRegex(ReleaseError, message):
                    selected_release_v2(root, package)

    def test_v1_selection_is_left_to_the_v1_loader(self):
        root, package = self.root_with({'schemaVersion': 1, 'enabled': False}, link=False)
        self.assertIsNone(selected_release_v2(root, package))
        self.assertIsNone(selected_corpus(root, package))


class LookupIndexTests(V2Fixture):
    """Release format 2.1: the FTS5 trigram prefilter changes how fast a lookup is, never what it returns."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dir20, cls.sha20 = converted_fma2000(lookup_index=False)
        cls.release20 = load_release_v2(cls.dir20, expected_manifest_sha256=cls.sha20)

    def cases(self):
        texts = [t for (t,) in self.release.connection().execute('SELECT fold_text FROM tracks ORDER BY row')]
        rng = np.random.default_rng(31)
        cases = [(q, '', '', None) for q in ('love', 'lady love', 'the', 'the night', 'a', 'of', 'Électro', 'électro', 'ü',
                                              'straße', '"', 'a"b', "o'b", '-', '...', '(live)', 'zzzz not here', '  ', '́')]
        cases += [('', t, '', None) for t in ('piano', 'love', 'ü', 'xx yy', '"quoted"')]
        cases += [('a', 'e', 'Electronic', None), ('', '', 'Folk', None), ('love', '', '', lambda row: row % 3 == 0),
                  ('the', 'night', '', lambda row: row % 2 == 0)]
        for row in sorted(rng.choice(2000, size=40, replace=False).tolist()):
            words = texts[row].split()
            longest = max(words, key=len)
            start = int(rng.integers(max(1, len(longest) - 3)))
            cases += [(longest, '', '', None), (longest[start:start + 3], '', '', None), (longest[start:start + 4], '', '', None),
                      (' '.join(words[:2]), '', '', None), ('', longest, '', None), (words[0], longest[-3:], '', None)]
        return cases

    def page(self, release, case, **extra):
        query, text, genre, rows_filter = case
        return page_query(release, query=query, text=text, genre=genre, rows_filter=rows_filter, limit=48, facets=True, **extra)

    def test_format_2_1_declares_loads_and_reports_the_lookup_index(self):
        manifest = json.loads((self.dir / 'release.json').read_bytes())
        self.assertEqual(manifest['minorVersion'], 1)
        self.assertEqual((self.release.minor_version, self.release.lookup_index), (1, LOOKUP_INDEX))
        self.assertEqual(self.release.catalog_meta['lookupIndex'], LOOKUP_INDEX)
        self.assertEqual((self.release.summary()['formatVersion'], self.release.summary()['lookupIndex']), ('2.1', LOOKUP_INDEX))
        # A 2.0 release has no minorVersion and no index, and is read exactly as before.
        self.assertNotIn('minorVersion', json.loads((self.dir20 / 'release.json').read_bytes()))
        self.assertEqual((self.release20.minor_version, self.release20.lookup_index), (0, None))
        self.assertEqual(self.release20.summary()['formatVersion'], '2.0')
        self.assertEqual(validate_rows(self.release20)['lookupIndex'], None)
        for name in ('evidence.sqlite', 'vectors.f32', 'graph.bin', 'layout.f32', 'graph-manifest.json', 'examples.json'):
            self.assertEqual((self.dir / name).read_bytes(), (self.dir20 / name).read_bytes(), name)

    def test_index_and_scan_return_identical_pages_and_the_index_serves_rare_words(self):
        stats = self.release.lookup_stats
        before = dict(stats)
        for case in self.cases():
            with self.subTest(case=case[:3]):
                for offset in (0, 48):
                    indexed = self.page(self.release, case, offset=offset)
                    self.assertEqual(indexed, self.page(self.release, case, offset=offset, use_index=False))
                    self.assertEqual(indexed, self.page(self.release20, case, offset=offset))
        self.assertGreater(stats['indexed'] - before['indexed'], 100)  # rare words and fragments use the index
        self.assertGreater(stats['scanned'] - before['scanned'], 10)   # short and very common words scan
        self.assertEqual(self.release20.lookup_stats['indexed'], 0)

    def test_upgrading_2_0_gives_the_same_catalog_bytes_as_converting_to_2_1(self):
        from upgrade_release_v2 import upgrade
        output = self.temp() / 'upgraded'
        receipt = upgrade(self.dir20, self.sha20, output, samples=4)
        self.assertEqual((output / 'catalog.sqlite').read_bytes(), (self.dir / 'catalog.sqlite').read_bytes())
        for name in ('evidence.sqlite', 'vectors.f32', 'graph.bin', 'layout.f32', 'graph-manifest.json', 'examples.json'):
            self.assertEqual((output / name).read_bytes(), (self.dir20 / name).read_bytes(), name)
        self.assertEqual(receipt['proofs']['rowsUnchanged'], {'tracks': 2000, 'records': 2000})
        upgraded = load_release_v2(output, expected_manifest_sha256=receipt['releaseSha256'])
        self.assertEqual((upgraded.minor_version, upgraded.lookup_index), (1, LOOKUP_INDEX))
        with self.assertRaisesRegex(ReleaseError, 'Only a release-format 2.0 directory can be upgraded'):
            upgrade(self.dir, self.sha, self.temp() / 'again', samples=1)

    def repinned(self, target, change=None):
        manifest = json.loads((target / 'release.json').read_bytes())
        data = (target / 'catalog.sqlite').read_bytes()
        manifest['assets']['catalog'].update(bytes=len(data), sha256=sha256(data))
        if change:
            change(manifest)
        payload = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()
        (target / 'release.json').write_bytes(payload)
        return sha256(payload)

    def test_unknown_minor_versions_and_a_missing_index_are_refused(self):
        for value in (2, True, '1'):
            with self.subTest(minorVersion=value):
                target = copy_release(self.dir, self.temp() / 'minor')
                digest = self.repinned(target, lambda m: m.update(minorVersion=value))
                with self.assertRaisesRegex(ReleaseError, 'Unknown v2 release minor version'):
                    load_release_v2(target, expected_manifest_sha256=digest)
        target = copy_release(self.dir, self.temp() / 'no-index')
        shutil.copyfile(self.dir20 / 'catalog.sqlite', target / 'catalog.sqlite')
        digest = self.repinned(target)
        with self.assertRaisesRegex(ReleaseError, 'lacks the release 2.1 lookup index'):
            load_release_v2(target, expected_manifest_sha256=digest)

    def test_the_verifier_proves_the_index_against_every_row(self):
        texts = [t for (t,) in self.release.connection().execute('SELECT fold_text FROM tracks ORDER BY row')]
        row, title = 7, self.release.connection().execute('SELECT fold_title_artist FROM tracks WHERE row = 7').fetchone()[0]
        unique = next(title[i:i + n] for n in range(3, 12) for i in range(len(title) - n + 1)
                      if ' ' not in title[i:i + n] and sum(title[i:i + n] in t for t in texts) == 1)
        target = copy_release(self.dir, self.temp() / 'drift')
        connection = sqlite3.connect(target / 'catalog.sqlite')
        connection.execute("INSERT INTO tracks_fts(tracks_fts, rowid, fold_text) VALUES('delete', ?, ?)", (row, texts[row]))
        connection.commit()
        connection.close()
        release = load_release_v2(target, expected_manifest_sha256=self.repinned(target))  # the declaration is intact
        found = page_query(release, query=unique)
        self.assertEqual([t['row'] for t in page_query(release, query=unique, use_index=False)['rows']], [row])
        self.assertEqual(found['rows'], [], 'an index that drifted from its rows would hide them')
        with self.assertRaisesRegex(ReleaseError, 'Lookup index does not match the catalog'):
            validate_rows(release)


class SearchParityTests(V2Fixture):
    def queries(self, rows=60, random=20, seed=7):
        rng = np.random.default_rng(seed)
        out = [(np.asarray(item['queryVector'], dtype=np.float32), None) for item in self.examples]
        for row in rng.choice(2000, size=rows, replace=False).tolist():
            out.append((np.array(self.release.vectors[row]), row))
        for _ in range(random):
            vector = rng.standard_normal(512)
            out.append(((vector / np.linalg.norm(vector)).astype(np.float32), None))
        return out

    def test_exact_ranking_is_bit_identical_to_the_v1_server(self):
        for query, exclude in self.queries():
            for k in (1, 8, 16, 20):
                self.assertEqual(self.graph.exact_search(query, k=k, exclude_id=exclude),
                                 self.reference.exact_search(query, k=k, exclude_id=exclude))
        self.assertEqual(self.graph.exact_search(self.examples[0]['queryVector'], k=2000),
                         self.reference.exact_search(self.examples[0]['queryVector'], k=2000))

    def test_full_rescore_fallback_is_also_identical(self):
        graph = GraphV2(self.release)
        with mock.patch.object(search_v2, 'FLOAT32_DOT_ERROR', 10.0):
            for query, exclude in self.queries(rows=5, random=3):
                self.assertEqual(graph.exact_search(query, k=16, exclude_id=exclude),
                                 self.reference.exact_search(query, k=16, exclude_id=exclude))
        self.assertGreater(graph.stats['fullRescores'], 0)

    def test_sequential_numpy_accumulation_matches_the_python_loop_bit_for_bit(self):
        query = np.asarray(self.examples[2]['queryVector'], dtype=np.float32)
        rows = list(range(2000))
        expected = [cosine_distance(query, self.reference.vectors[row]) for row in rows]
        self.assertEqual(exact_rescore(self.release.vectors, query, rows), expected)

    def test_trace_is_identical_to_the_v1_reference(self):
        for query, exclude in self.queries(rows=12, random=4, seed=11):
            for k, ef, limit in ((8, 32, 2048), (17, 32, 128), (20, 108, 256)):
                self.assertEqual(self.graph.search(query, k=k, ef=ef, trace=True, trace_limit=limit),
                                 self.reference.search(query, k=k, ef=ef, trace=True, trace_limit=limit))


if __name__ == '__main__':
    unittest.main()
