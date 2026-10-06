"""The tools that rebuild a release under the rights quarantine list, checked against the committed tree:
the release builder's row selection, the input reconstruction, the credits page, the pins and the
build-time audio plan. Each must reproduce exactly what is committed for the active release."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import build_corpus_credits  # noqa: E402
import build_corpus_release  # noqa: E402
import derive_hydration_plan  # noqa: E402
import pin_corpus_release  # noqa: E402
import reconstruct_release_inputs  # noqa: E402
import rights_quarantine  # noqa: E402
from corpus_release import ReleaseError, ReleaseLimits, sha256, validate_release  # noqa: E402

ACTIVE = json.loads((ROOT / 'active-corpus.json').read_bytes())
NAME = ACTIVE['directory'].removeprefix('corpus-releases/')
RELEASE_DIR, RELEASE_SHA, COUNT = ROOT / ACTIVE['directory'], ACTIVE['manifestSha256'], ACTIVE['maxTracks']
# The committed release was built before the quarantine list existed, so it is reproduced with an empty list.
COMMITTED_QUARANTINE = rights_quarantine.EMPTY


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def listing(*rows):
    """A parsed quarantine list holding (id, audio sha) rows."""
    entries = [{'id': ident, 'action': 'hold', 'reason': 'test', 'evidence': 'test', 'date': '2026-10-06',
                'reviewer': 'test', 'catalogRow': 'test', 'audioSha256': audio} for ident, audio in rows]
    value = json.loads((ROOT / rights_quarantine.LIST).read_bytes())
    return rights_quarantine.parse(encode(dict(value, entries=entries)))


class RebuildToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = validate_release(RELEASE_DIR, expected_manifest_sha256=RELEASE_SHA, limits=ReleaseLimits(max_tracks=COUNT))
        first = json.loads(cls.release.assets['catalog'])['tracks'][0]
        cls.first_listed = listing((first['id'], first['audioSha256']))

    def test_select_rows_drops_listed_rows_and_compares_the_parent_without_them(self):
        def rows(ids):
            return ([{'id': i, 'audioSha256': sha256(i.encode())} for i in ids],
                    [{'id': i, 'decision': 'approved', 'publicPlaybackDecision': 'approved-with-attribution'} for i in ids])
        quarantine = listing(('p:2', sha256(b'p:2')), ('n:2', sha256(b'n:2')))
        parent = ['p:1', 'p:2', 'p:3']
        inputs = parent + ['n:1', 'n:2']
        ids, tracks, rights, excluded, kept = build_corpus_release.select_rows(
            inputs, *rows(inputs), parent, *rows(parent), quarantine, 3)
        self.assertEqual((ids, [t['id'] for t in tracks], [r['id'] for r in rights]), (['p:1', 'p:3', 'n:1'],) * 3)
        self.assertEqual(([entry['id'] for entry in excluded], kept), (['p:2', 'n:2'], 2))
        with self.assertRaisesRegex(ReleaseError, 'Need exactly 4 approved distinct rows'):
            build_corpus_release.select_rows(inputs, *rows(inputs), parent, *rows(parent), quarantine, 4)
        moved = ['p:3', 'p:1', 'p:2', 'n:1', 'n:2']
        with self.assertRaisesRegex(ReleaseError, 'Frozen parent catalog/rights prefix changed'):
            build_corpus_release.select_rows(moved, *rows(moved), parent, *rows(parent), quarantine, 3)
        everything = listing(*[(i, sha256(i.encode())) for i in parent])
        with self.assertRaisesRegex(ReleaseError, 'must extend its frozen parent'):
            build_corpus_release.select_rows(inputs, *rows(inputs), parent, *rows(parent), everything, 2)
        unlisted = ['p:1', 'p:3', 'n:1']
        twin_tracks, twin_rights = rows(unlisted)
        twin_tracks[2]['audioSha256'] = sha256(b'n:2')
        with self.assertRaisesRegex(ReleaseError, 'Audio bytes of quarantined n:2 appear under n:1'):
            build_corpus_release.select_rows(unlisted, twin_tracks, twin_rights, parent, *rows(parent), quarantine, 3)

    def test_reconstructed_inputs_are_the_release_rows_and_name_their_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            (temporary / 'audio').mkdir()
            result = reconstruct_release_inputs.reconstruct(RELEASE_DIR, RELEASE_SHA, temporary / 'audio', temporary / 'inputs',
                                                            limits=ReleaseLimits(max_tracks=COUNT))
            ingestion, embeddings = temporary / 'inputs/ingestion', temporary / 'inputs/embeddings'
            ready = json.loads((ingestion / 'inputs-ready.json').read_bytes())
            self.assertEqual((ready['state'], ready['tracks']), ('local-inputs-ready', COUNT))
            self.assertEqual(ready['reconstructedFrom']['manifestSha256'], RELEASE_SHA)
            for name, digest in ready['hashes'].items():
                self.assertEqual(sha256((ingestion / name).read_bytes()), digest)
            self.assertEqual(json.loads((ingestion / 'approved-ids.json').read_bytes()), list(self.release.ordered_ids))
            self.assertEqual(json.loads((ingestion / 'approved-catalog-tracks.json').read_bytes()),
                             json.loads(self.release.assets['catalog'])['tracks'])
            self.assertEqual((embeddings / 'vectors.f32').read_bytes(), self.release.assets['vectors'])
            provenance = json.loads((embeddings / 'embedding-provenance.json').read_bytes())
            self.assertEqual((provenance['count'], provenance['newCount'], result['evidenceFiles']),
                             (COUNT, result['receiptRows'], COUNT))
            self.assertTrue((ingestion / 'approved/audio').is_symlink())
            with self.assertRaisesRegex(ReleaseError, 'must not exist yet'):
                reconstruct_release_inputs.reconstruct(RELEASE_DIR, RELEASE_SHA, temporary / 'audio', temporary / 'inputs',
                                                       limits=ReleaseLimits(max_tracks=COUNT))

    def test_credits_page_is_the_committed_page_and_refuses_a_listed_recording(self):
        page = build_corpus_credits.render(self.release, COMMITTED_QUARANTINE)
        self.assertEqual(page, (ROOT / 'notices/track-attribution.html').read_bytes())
        self.assertEqual(page, (ROOT / 'web/notices/track-attribution.html').read_bytes())
        with self.assertRaisesRegex(ReleaseError, 'holds quarantined recordings'):
            build_corpus_credits.render(self.release, self.first_listed)

    def test_pins_reproduce_the_committed_tree_and_refuse_a_listed_recording(self):
        self.assertEqual(pin_corpus_release.pin(ROOT, NAME, RELEASE_SHA, count=COUNT, quarantine=COMMITTED_QUARANTINE,
                                                write=False), {})
        with self.assertRaisesRegex(ReleaseError, 'holds quarantined recordings'):
            pin_corpus_release.pin(ROOT, NAME, RELEASE_SHA, count=COUNT, quarantine=self.first_listed, write=False)

    def test_hydration_plan_is_the_committed_plan_and_refuses_a_listed_recording(self):
        plan = (ROOT / 'audio-hydration.json').read_bytes()
        data, entries = derive_hydration_plan.derive(plan, RELEASE_DIR, RELEASE_SHA, count=COUNT, quarantine=COMMITTED_QUARANTINE)
        self.assertEqual((data, len(entries)), (plan, COUNT))
        with self.assertRaisesRegex(ReleaseError, 'holds quarantined recordings'):
            derive_hydration_plan.derive(plan, RELEASE_DIR, RELEASE_SHA, count=COUNT, quarantine=self.first_listed)
        with self.assertRaisesRegex(ReleaseError, 'not the reviewed plan'):
            derive_hydration_plan.derive(plan + b' ', RELEASE_DIR, RELEASE_SHA, count=COUNT, quarantine=COMMITTED_QUARANTINE)


if __name__ == '__main__':
    unittest.main()
