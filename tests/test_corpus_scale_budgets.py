"""Budget knobs added for the 5,777-track candidate; defaults keep every earlier release unchanged."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from active_corpus import selected_corpus
from corpus_release import ReleaseError, ReleaseLimits
from test_corpus_activation import configure
from test_corpus_release import Candidate
from web_gateway import STATIC_FILE_LIMIT, STATIC_TOTAL_LIMIT, STATIC_TOTAL_MAXIMUM, WebGateway, static_limits


class ReleaseBudgetTests(unittest.TestCase):
    def test_evidence_and_json_maxima_are_explicit_and_bounded(self):
        self.assertEqual(ReleaseLimits(evidence_bytes=32_000_000).evidence_bytes, 32_000_000)
        self.assertEqual(ReleaseLimits(json_bytes=16_000_000).json_bytes, 16_000_000)
        for changes in [{'evidence_bytes': 32_000_001}, {'json_bytes': 16_000_001}, {'evidence_bytes': True},
                        {'max_tracks': 10_001}, {'core_bytes': 64_000_001}]:
            with self.subTest(changes=changes), self.assertRaises(ReleaseError):
                ReleaseLimits(**changes)
        self.assertEqual(ReleaseLimits(), ReleaseLimits(max_tracks=500, core_bytes=16_000_000,
                                                        evidence_bytes=8_000_000, json_bytes=8_000_000))


class ActivationJsonBudgetTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        directory = root / 'corpus-releases/test'
        directory.mkdir(parents=True)
        return root, Candidate(directory, count=32)

    def test_optional_json_budget_is_applied_and_absent_keeps_the_default(self):
        root, candidate = self.fixture()
        self.assertEqual(selected_corpus(root, configure(root, candidate)).release.count, 32)
        self.assertEqual(selected_corpus(root, configure(root, candidate, jsonByteBudget=16_000_000)).release.count, 32)
        with self.assertRaisesRegex(ReleaseError, 'JSON budget'):
            selected_corpus(root, configure(root, candidate, jsonByteBudget=1000))

    def test_invalid_json_budget_or_unknown_keys_are_rejected(self):
        root, candidate = self.fixture()
        for changes in [{'jsonByteBudget': 0}, {'jsonByteBudget': True}, {'jsonByteBudget': '8000000'},
                        {'jsonByteBudget': 16_000_001}, {'unexpected': 1}]:
            with self.subTest(changes=changes), self.assertRaises(ReleaseError):
                selected_corpus(root, configure(root, candidate, **changes))


class StaticBudgetTests(unittest.TestCase):
    def gateway(self, limits=None, padding=0):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        web = root / 'web'
        (web / 'search-studio').mkdir(parents=True)
        files = {'search-studio/index.html': b'<!doctype html><title>Test fixture</title>',
                 'search-studio/data/padding.txt': b'x' * padding}
        rows = []
        for relative, data in files.items():
            path = web / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            rows.append({'path': relative, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        manifest = {'schemaVersion': 1, 'kind': 'music-public-web-assets', 'files': rows}
        if limits is not None:
            manifest['limits'] = limits
        (root / 'web-manifest.json').write_text(json.dumps(manifest))
        catalog = root / 'catalog.json'
        catalog.write_text(json.dumps({'id': 'synthetic-fixture-only:0', 'tracks': []}))
        return WebGateway(object(), web_root=web, web_manifest=root / 'web-manifest.json', catalog_path=catalog)

    def test_absent_limits_keep_the_original_static_caps(self):
        self.assertEqual(static_limits({'files': []}), (20_000_000, 30_000_000))
        self.assertEqual((STATIC_FILE_LIMIT, STATIC_TOTAL_LIMIT, STATIC_TOTAL_MAXIMUM), (20_000_000, 30_000_000, 64_000_000))
        self.assertIn('search-studio/index.html', self.gateway().assets)

    def test_pinned_manifest_budget_is_enforced_within_hard_maxima(self):
        self.assertIn('search-studio/data/padding.txt',
                      self.gateway({'maxFileBytes': 1000, 'maxTotalBytes': 2000}, padding=900).assets)
        with self.assertRaisesRegex(ValueError, 'Unexpected public web package'):
            self.gateway({'maxFileBytes': 1000, 'maxTotalBytes': 900}, padding=900)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.gateway({'maxFileBytes': 100, 'maxTotalBytes': 2000}, padding=900)
        for limits in [{'maxFileBytes': 20_000_001, 'maxTotalBytes': 30_000_000},
                       {'maxFileBytes': 20_000_000, 'maxTotalBytes': 64_000_001},
                       {'maxFileBytes': True, 'maxTotalBytes': 30_000_000},
                       {'maxFileBytes': 0, 'maxTotalBytes': 30_000_000},
                       {'maxTotalBytes': 30_000_000}, {'maxFileBytes': 1, 'maxTotalBytes': 1, 'extra': 1}, []]:
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, 'budget'):
                static_limits({'limits': limits})


if __name__ == '__main__':
    unittest.main()
