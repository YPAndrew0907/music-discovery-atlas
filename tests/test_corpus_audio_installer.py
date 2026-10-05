"""Synthetic tiny archive fixtures; no network or real music acquisition."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'server'))
import install_corpus_audio_release as installer
from corpus_release import ReleaseError, sha256
from test_corpus_release import Candidate, encoded


class CorpusAudioTests(unittest.TestCase):
    def fixture(self, mutate=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        data = root / 'release'
        data.mkdir()
        candidate = Candidate(data)
        for i, track in enumerate(candidate.catalog['tracks']):
            track['audio'] = f'audio/{i:06}.mp3'
        for row in candidate.rights['tracks']:
            row['publicPlaybackDecision'] = 'approved-with-attribution'
        candidate.save()
        release = candidate.validate()
        files = {track['audio']: b'x' for track in candidate.catalog['tracks']}
        files.update({'credits/catalog.json': release.assets['catalog'], 'credits/rights.json': release.assets['rights'],
                      'credits/evidence/test.txt': candidate.evidence_data,
                      'credits/track-attribution.html': b'<!doctype html><title>Synthetic test credits</title>'})
        pack = {'schemaVersion': 1, 'kind': 'music-corpus-audio-pack', 'catalogId': release.catalog_id,
                'catalogSha256': sha256(release.assets['catalog']), 'corpusReleaseSha256': release.manifest_sha256,
                'count': release.count, 'files': [{'path': name, 'bytes': len(value), 'sha256': sha256(value)}
                                                 for name, value in files.items()]}
        files['audio-pack.json'] = encoded(pack)
        if mutate:
            mutate(files)
        archive = root / 'audio.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as output:
            for name, value in files.items():
                output.writestr(name, value)
        selected = SimpleNamespace(release=release, audio_archive={'url': 'https://github.com/YPAndrew0907/music-discovery-atlas/releases/download/test/audio.zip',
                                  'bytes': archive.stat().st_size, 'sha256': sha256(archive.read_bytes())})
        return root, archive, selected

    def test_exact_verified_members_install_once_and_keep_credits(self):
        root, archive, selected = self.fixture()
        result = installer.install(archive, root, selected)
        self.assertEqual(result['tracks'], 3)
        self.assertFalse(result['runtimeEnabled'])
        self.assertFalse(result['hostedDeliveryTested'])
        manifest = json.loads((root / 'audio-delivery.verified.json').read_bytes())
        self.assertEqual(manifest['catalogId'], selected.release.catalog_id)
        self.assertEqual(len(manifest['tracks']), 3)
        self.assertEqual((root / 'audio-release-credits/evidence/test.txt').read_bytes(),
                         b'SYNTHETIC TEST EVIDENCE ONLY. This file grants no music rights.\n')
        with self.assertRaises(ReleaseError):
            installer.install(archive, root, selected)

    def test_wrong_archive_digest_or_extra_traversal_member_rejects_before_extraction(self):
        root, archive, selected = self.fixture()
        selected.audio_archive['sha256'] = '0' * 64
        with self.assertRaises(ReleaseError):
            installer.install(archive, root, selected)
        self.assertFalse((root / 'audio-preview').exists())
        root, archive, selected = self.fixture(lambda files: files.update({'../escape': b'bad'}))
        with self.assertRaises(ReleaseError):
            installer.install(archive, root, selected)
        self.assertFalse((root / 'audio-preview').exists())

    def test_member_hash_and_credits_mismatch_are_rejected(self):
        for mutate in [lambda files: files.update({'audio/000000.mp3': b'y'}),
                       lambda files: files.update({'credits/catalog.json': b'{}'}),
                       lambda files: files.update({'credits/evidence/test.txt': b'changed'})]:
            root, archive, selected = self.fixture(mutate)
            with self.assertRaises(ReleaseError):
                installer.inspect_archive(archive, selected)

    def test_swapped_rights_after_inspection_cannot_be_installed(self):
        root, archive, selected = self.fixture()
        inspect = installer.inspect_archive
        def swap(path, current):
            verified = inspect(path, current)
            with zipfile.ZipFile(path) as source:
                content = {info.filename: source.read(info.filename) for info in source.infolist()}
            original = content['credits/rights.json']
            content['credits/rights.json'] = b'x' * len(original)
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as target:
                for name, value in content.items():
                    target.writestr(name, value)
            return verified
        with patch.object(installer, 'inspect_archive', swap):
            with self.assertRaisesRegex(ReleaseError, 'integrity mismatch'):
                installer.install(archive, root, selected)
        self.assertFalse((root / 'audio-preview').exists())
        self.assertFalse((root / 'audio-release-credits').exists())
        self.assertFalse((root / 'audio-delivery.verified.json').exists())

    def test_duplicate_zip_member_is_rejected(self):
        root, archive, selected = self.fixture()
        with zipfile.ZipFile(archive, 'a') as output:
            output.writestr('audio/000000.mp3', b'x')
        selected.audio_archive.update(bytes=archive.stat().st_size, sha256=sha256(archive.read_bytes()))
        with self.assertRaises(ReleaseError):
            installer.inspect_archive(archive, selected)


if __name__ == '__main__':
    unittest.main()
