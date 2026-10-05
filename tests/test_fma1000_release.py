"""Checks on the actual local 1,000-track candidate, never synthetic music."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ReleaseLimits, validate_release


class ActualFMA1000Tests(unittest.TestCase):
    def test_exact_frozen500_catalog_rights_ids_and_vectors_are_preserved(self):
        old, new = ROOT / 'corpus-releases/fma500', ROOT / 'corpus-releases/fma1000'
        for name, key in [('catalog.json', 'tracks'), ('rights.json', 'tracks')]:
            baseline = json.loads((old / name).read_bytes())[key]
            current = json.loads((new / name).read_bytes())[key]
            self.assertEqual(len(current), 1000)
            self.assertEqual(current[:500], baseline)
        old_ids = json.loads((old / 'ids.json').read_bytes())
        new_ids = json.loads((new / 'ids.json').read_bytes())
        self.assertEqual(new_ids[:500], old_ids)
        self.assertEqual(len(set(new_ids)), 1000)
        self.assertNotIn('fma:30702', new_ids)
        self.assertTrue(all(ident.startswith('fma:') for ident in new_ids))
        old_vectors, new_vectors = (old / 'vectors.f32').read_bytes(), (new / 'vectors.f32').read_bytes()
        self.assertEqual(new_vectors[:len(old_vectors)], old_vectors)
        self.assertEqual(len(new_vectors), 1000 * 512 * 4)
        self.assertEqual(hashlib.sha256(new_vectors).hexdigest(), 'a010e04960804f652012763a84827e2e7d8d4380ba869febdc2b3d6b6efb7f6b')

    def test_real1000_manifest_rights_evidence_and_dimensions_pass_explicit_budget(self):
        release = validate_release(ROOT / 'corpus-releases/fma1000',
            expected_manifest_sha256='f278752268eb78ceeb74b66c7ea86ba80406c5850dd16d0e78f9a1720f1c31f5',
            limits=ReleaseLimits(max_tracks=1000))
        self.assertEqual(release.count, 1000)
        self.assertEqual(release.dimensions, 512)
        self.assertEqual(release.evidence_count, 1000)
        catalog = json.loads(release.assets['catalog'])
        self.assertEqual(len({track['audioSha256'] for track in catalog['tracks']}), 1000)
        rights = json.loads(release.assets['rights'])
        self.assertTrue(all(row['decision'] == 'approved' and row['publicPlaybackDecision'] == 'approved-with-attribution'
                            for row in rights['tracks']))

    def test_retained1000_layout_is_bound_to_the_unchanged_vectors(self):
        data = ROOT / 'corpus-releases/fma1000'
        layout = json.loads((data / 'layout.json').read_bytes())
        self.assertEqual(layout['count'], 1000)
        self.assertEqual(layout['vectorsSha256'], hashlib.sha256((data / 'vectors.f32').read_bytes()).hexdigest())
        self.assertEqual(layout['orderedIds'], json.loads((data / 'ids.json').read_bytes()))


if __name__ == '__main__':
    unittest.main()
