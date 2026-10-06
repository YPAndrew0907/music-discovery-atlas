"""Acceptance bindings for the fma2000 release: the reviewed 2,000 recordings without the 8 on the
rights quarantine list of 2026-10-06, so 1,992 rows, rebuilt with the repository's own builders."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import rights_quarantine  # noqa: E402
from corpus_release import ReleaseLimits, validate_release  # noqa: E402

RELEASE_SHA = '32015637189671d9f2fa44429bd8baa56696439fa6b8f1967337c2d10bbfbe42'
SOURCE_RELEASE_SHA = 'af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283'  # the 2,000 rows it was rebuilt from
COUNT = 1992


class ActualFMA2000Tests(unittest.TestCase):
    def test_frozen1000_rows_without_the_quarantined_ones_are_exact_prefixes(self):
        quarantine = rights_quarantine.load()
        old, new = ROOT / 'corpus-releases/fma1000', ROOT / 'corpus-releases/fma2000'
        baseline_ids = json.loads((old / 'ids.json').read_bytes())
        keep = [ident not in quarantine for ident in baseline_ids]
        kept = sum(keep)
        self.assertEqual(kept, 994)
        for name in ['catalog.json', 'rights.json']:
            baseline = [row for row, wanted in zip(json.loads((old / name).read_bytes())['tracks'], keep) if wanted]
            current = json.loads((new / name).read_bytes())['tracks']
            self.assertEqual(len(current), COUNT)
            self.assertEqual(current[:kept], baseline)
        ids = json.loads((new / 'ids.json').read_bytes())
        self.assertEqual(ids[:kept], [ident for ident, wanted in zip(baseline_ids, keep) if wanted])
        self.assertEqual(len(set(ids)), COUNT)
        self.assertNotIn('fma:30702', ids)
        old_vectors, new_vectors = (old / 'vectors.f32').read_bytes(), (new / 'vectors.f32').read_bytes()
        self.assertEqual(new_vectors[:kept * 2048],
                         b''.join(old_vectors[row * 2048:(row + 1) * 2048] for row, wanted in enumerate(keep) if wanted))
        self.assertEqual(len(new_vectors), COUNT * 512 * 4)
        self.assertEqual(hashlib.sha256(new_vectors).hexdigest(), '5a11cc9be05d30553d61c5d798b8b6b8dba5268f30b081440f91e1d03bed49ba')

    def test_release_contract_rights_evidence_and_dimensions_pass(self):
        release = validate_release(ROOT / 'corpus-releases/fma2000', expected_manifest_sha256=RELEASE_SHA,
                                   limits=ReleaseLimits(max_tracks=COUNT))
        self.assertEqual(release.count, COUNT)
        self.assertEqual(release.dimensions, 512)
        self.assertEqual(release.evidence_count, COUNT)
        catalog = json.loads(release.assets['catalog'])
        self.assertEqual(len({track['audioSha256'] for track in catalog['tracks']}), COUNT)
        rights = json.loads(release.assets['rights'])
        self.assertTrue(all(row['decision'] == 'approved' and row['publicPlaybackDecision'] == 'approved-with-attribution' for row in rights['tracks']))

    def test_quarantined_recordings_are_out_of_everything_served_and_the_build_records_why(self):
        quarantine = rights_quarantine.load()
        listed = set(quarantine.by_id)
        release_dir = ROOT / 'corpus-releases/fma2000'
        tracks = json.loads((release_dir / 'catalog.json').read_bytes())['tracks']
        rights_quarantine.require_clear([t['id'] for t in tracks], [t['audioSha256'] for t in tracks], quarantine, 'fma2000')
        evidence = {path.name for path in (release_dir / 'evidence').iterdir()}
        self.assertEqual(len(evidence), COUNT)
        self.assertFalse({'fma-' + ident.removeprefix('fma:') + '.json' for ident in listed} & evidence)
        receipt = json.loads((release_dir / 'build-receipt.json').read_bytes())['rightsQuarantine']
        self.assertEqual({row['id'] for row in receipt['excluded']}, listed)
        self.assertEqual((receipt['inputRows'], receipt['listSha256'], receipt['inputSource']['manifestSha256']),
                         (2000, quarantine.sha256, SOURCE_RELEASE_SHA))
        provenance = json.loads((release_dir / 'embedding-provenance.json').read_bytes())
        self.assertEqual((set(provenance['quarantinedIds']), provenance['count']), (listed, COUNT))
        selection = json.loads((ROOT / 'active-corpus.json').read_bytes())
        if selection['schemaVersion'] == 1:
            self.assertEqual((selection['manifestSha256'], selection['maxTracks']), (RELEASE_SHA, COUNT))
        plan = json.loads((ROOT / 'audio-hydration.json').read_bytes())
        self.assertEqual(len(plan['entries']), COUNT)
        self.assertFalse({entry['id'] for entry in plan['entries']} & listed)
        self.assertFalse({entry['audioSha256'] for entry in plan['entries']} & set(quarantine.by_audio))
        for credits in ['notices/track-attribution.html', 'web/notices/track-attribution.html']:
            page = (ROOT / credits).read_text()
            if credits.startswith('web/') and selection['schemaVersion'] != 1:
                # An activated v2 tree serves the credits page by page from /collection/credits (a listed recording
                # has no page there; tests/test_api_v2.py); the web copy is the small page that links to them.
                self.assertEqual((page.count('<article id="'), page.count('href="/collection/credits"')), (0, 1))
                self.assertIn('and so are the recordings on the rights quarantine list', page)
                continue
            self.assertEqual(page.count('<article id="'), COUNT)
            for ident in listed:
                self.assertNotIn('<article id="' + ident.replace(':', '-') + '">', page)
            self.assertIn('and so are the recordings on the rights quarantine list', page)
        web_catalog = ROOT / 'web/search-studio/data/catalog.json'
        if web_catalog.exists():  # a v1 page; an activated v2 page has no catalog file
            self.assertFalse({t['id'] for t in json.loads(web_catalog.read_bytes())['tracks']} & listed)

    def test_static_caps_and_display_bind_to_the_release(self):
        manifest = json.loads((ROOT / 'web-manifest.json').read_bytes())
        self.assertLessEqual(sum(row['bytes'] for row in manifest['files']), 30_000_000)
        self.assertTrue(all(row['bytes'] <= 20_000_000 for row in manifest['files']))
        release_id = json.loads((ROOT / 'corpus-releases/fma2000/catalog.json').read_bytes())['id']
        page = json.loads((ROOT / 'web/search-studio/data/manifest.json').read_bytes())
        html = (ROOT / 'web/search-studio/index.html').read_text()
        self.assertIn('<strong id="about-count">1,992 recordings</strong>', html)
        self.assertIn('<p id="collection-summary" class="fine">1,992 FMA excerpts · 550 source artist IDs · 14 source genres</p>', html)
        if page.get('format') == 2:
            # A release-format-v2 page carries the identity and count; the catalog stays on the server.
            self.assertEqual((page['count'], page['catalogId']), (COUNT, release_id))
            return
        catalog = json.loads((ROOT / 'web/search-studio/data/catalog.json').read_bytes())
        self.assertEqual(len(catalog['tracks']), COUNT)
        self.assertEqual(catalog['id'], release_id)


if __name__ == '__main__':
    unittest.main()
