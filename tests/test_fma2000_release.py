"""Acceptance bindings for the actual local 2,000-track candidate."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ReleaseLimits, validate_release


class ActualFMA2000Tests(unittest.TestCase):
    def test_frozen1000_catalog_rights_ids_and_vectors_are_exact_prefixes(self):
        old, new = ROOT / 'corpus-releases/fma1000', ROOT / 'corpus-releases/fma2000'
        for name in ['catalog.json', 'rights.json']:
            baseline = json.loads((old / name).read_bytes())['tracks']
            current = json.loads((new / name).read_bytes())['tracks']
            self.assertEqual(len(current), 2000)
            self.assertEqual(current[:1000], baseline)
        baseline_ids = json.loads((old / 'ids.json').read_bytes())
        ids = json.loads((new / 'ids.json').read_bytes())
        self.assertEqual(ids[:1000], baseline_ids)
        self.assertEqual(len(set(ids)), 2000)
        self.assertNotIn('fma:30702', ids)
        old_vectors, new_vectors = (old / 'vectors.f32').read_bytes(), (new / 'vectors.f32').read_bytes()
        self.assertEqual(new_vectors[:len(old_vectors)], old_vectors)
        self.assertEqual(len(new_vectors), 2000 * 512 * 4)
        self.assertEqual(hashlib.sha256(new_vectors).hexdigest(), '12c1dfc0c23f27c14d0530799a284df92bb3a740ef4a22749aab53d00af2ae8d')

    def test_real2000_contract_rights_evidence_and_dimensions_pass(self):
        release = validate_release(ROOT / 'corpus-releases/fma2000',
            expected_manifest_sha256='af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283',
            limits=ReleaseLimits(max_tracks=2000))
        self.assertEqual(release.count, 2000)
        self.assertEqual(release.dimensions, 512)
        self.assertEqual(release.evidence_count, 2000)
        catalog = json.loads(release.assets['catalog'])
        self.assertEqual(len({track['audioSha256'] for track in catalog['tracks']}), 2000)
        rights = json.loads(release.assets['rights'])
        self.assertTrue(all(row['decision'] == 'approved' and row['publicPlaybackDecision'] == 'approved-with-attribution' for row in rights['tracks']))

    def test_static_caps_and_display_bind_to_real2000_release(self):
        manifest = json.loads((ROOT / 'web-manifest.json').read_bytes())
        self.assertLessEqual(sum(row['bytes'] for row in manifest['files']), 30_000_000)
        self.assertTrue(all(row['bytes'] <= 20_000_000 for row in manifest['files']))
        release_id = json.loads((ROOT / 'corpus-releases/fma2000/catalog.json').read_bytes())['id']
        page = json.loads((ROOT / 'web/search-studio/data/manifest.json').read_bytes())
        if page.get('format') == 2:
            # A release-format-v2 page carries the identity and count; the catalog stays on the server.
            self.assertEqual((page['count'], page['catalogId']), (2000, release_id))
            return
        catalog = json.loads((ROOT / 'web/search-studio/data/catalog.json').read_bytes())
        self.assertEqual(len(catalog['tracks']), 2000)
        self.assertEqual(catalog['id'], release_id)


if __name__ == '__main__':
    unittest.main()
