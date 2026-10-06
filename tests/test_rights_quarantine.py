"""The reviewed rights quarantine list (corpus-releases/quarantine.json) and its build-time checks."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import rights_quarantine  # noqa: E402
from corpus_release import ReleaseError  # noqa: E402

REMOVE = ['fma:93518', 'fma:93519', 'fma:93520', 'fma:93521', 'fma:98077', 'fma:154569']
HOLD = ['fma:1382', 'fma:125279']


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


class QuarantineListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT / rights_quarantine.LIST).read_bytes()
        cls.value = json.loads(cls.raw)

    def test_committed_list_names_the_reviewed_removals_and_holds(self):
        quarantine = rights_quarantine.load()
        self.assertEqual([entry['id'] for entry in quarantine.entries], REMOVE + HOLD)
        self.assertEqual([entry['id'] for entry in quarantine.entries if entry['action'] == 'remove'], REMOVE)
        self.assertEqual([entry['id'] for entry in quarantine.entries if entry['action'] == 'hold'], HOLD)
        for entry in quarantine.entries:
            self.assertEqual((entry['date'], entry['reviewer']), ('2026-10-06', 'rights research 2026-10-06'))
            self.assertIn('rights/research/fma-dataset-and-automated-screen.md', entry['evidence'])
        self.assertEqual(self.value['procedure'], 'docs/RIGHTS_QUARANTINE.md')
        self.assertEqual(self.raw, encode(self.value), 'The list keeps the repository JSON encoding')

    def test_malformed_lists_are_refused(self):
        def broken(change):
            value = copy.deepcopy(self.value)
            change(value)
            with self.assertRaises(ReleaseError):
                rights_quarantine.parse(encode(value))
        broken(lambda v: v['entries'].append(dict(v['entries'][0])))                           # listed twice
        broken(lambda v: v['entries'][0].update(action='ignore'))
        broken(lambda v: v['entries'][0].update(reason=' '))
        broken(lambda v: v['entries'][0].update(evidence=''))
        broken(lambda v: v['entries'][0].update(date='2026-13-01'))
        broken(lambda v: v['entries'][0].update(audioSha256='0' * 63))
        broken(lambda v: v['entries'][0].update(id='fma:1 382'))
        broken(lambda v: v['entries'][0].update(extra=True))
        broken(lambda v: v['entries'][0].pop('reviewer'))
        broken(lambda v: v['entries'][1].update(audioSha256=v['entries'][0]['audioSha256']))  # one audio, two IDs
        broken(lambda v: v.update(kind='something-else'))
        broken(lambda v: v.update(schemaVersion=2))
        with self.assertRaises(ReleaseError):
            rights_quarantine.load(ROOT / 'corpus-releases/missing-quarantine.json')

    def test_apply_drops_listed_rows_in_order_and_refuses_listed_audio_elsewhere(self):
        listed = {'id': 'x:2', 'action': 'hold', 'reason': 'r', 'evidence': 'e', 'date': '2026-10-06',
                  'reviewer': 'test', 'catalogRow': 'row', 'audioSha256': 'b' * 64}
        value = dict(self.value, entries=[listed])
        quarantine = rights_quarantine.parse(encode(value))
        ids = ['x:1', 'x:2', 'x:3']
        tracks = [{'id': i, 'audioSha256': c * 64} for i, c in zip(ids, 'abc')]
        rights = [{'id': i} for i in ids]
        kept_ids, kept_tracks, kept_rights, excluded = rights_quarantine.apply(ids, tracks, rights, quarantine)
        self.assertEqual((kept_ids, [t['id'] for t in kept_tracks], [r['id'] for r in kept_rights]),
                         (['x:1', 'x:3'], ['x:1', 'x:3'], ['x:1', 'x:3']))
        self.assertEqual([entry['id'] for entry in excluded], ['x:2'])
        receipt = rights_quarantine.receipt(quarantine, excluded)
        self.assertEqual(receipt['excluded'], [{'id': 'x:2', 'action': 'hold', 'reason': 'r', 'date': '2026-10-06', 'reviewer': 'test'}])
        self.assertEqual((receipt['list'], receipt['listedEntries'], receipt['listSha256']), (rights_quarantine.LIST, 1, quarantine.sha256))
        self.assertEqual(rights_quarantine.apply(ids, tracks, rights, rights_quarantine.EMPTY)[:3], (ids, tracks, rights))
        twin = [dict(tracks[0]), dict(tracks[1], id='x:9'), dict(tracks[2])]
        with self.assertRaisesRegex(ReleaseError, 'Audio bytes of quarantined x:2 appear under x:9'):
            rights_quarantine.apply(['x:1', 'x:9', 'x:3'], twin, rights, quarantine)
        with self.assertRaisesRegex(ReleaseError, 'holds quarantined recordings: x:2'):
            rights_quarantine.require_clear(['x:1', 'x:2'], [], quarantine, 'test release')
        with self.assertRaisesRegex(ReleaseError, 'holds quarantined recordings: x:2'):
            rights_quarantine.require_clear(['x:1'], ['b' * 64], quarantine, 'test release')
        rights_quarantine.require_clear(['x:1', 'x:3'], ['a' * 64, 'c' * 64], quarantine, 'test release')


if __name__ == '__main__':
    unittest.main()
