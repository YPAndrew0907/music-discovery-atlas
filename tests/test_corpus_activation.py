"""Explicit corpus activation, immutable graph consumption, and build inventory."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from active_corpus import selected_corpus
from corpus_release import ReleaseError, sha256
from test_corpus_release import Candidate, encoded


def configure(root, candidate=None, **changes):
    config = {'schemaVersion': 1, 'enabled': False} if candidate is None else {
        'schemaVersion': 1, 'enabled': True, 'directory': 'corpus-releases/test',
        'manifestSha256': candidate.expected, 'maxTracks': 500,
        'coreByteBudget': 16_000_000, 'evidenceByteBudget': 8_000_000, 'audioArchive': None}
    config.update(changes)
    data = encoded(config)
    (root / 'active-corpus.json').write_bytes(data)
    return {'files': [{'path': 'active-corpus.json', 'bytes': len(data), 'sha256': sha256(data)}]}


class ActivationTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        directory = root / 'corpus-releases/test'
        directory.mkdir(parents=True)
        candidate = Candidate(directory, count=32)
        return root, candidate

    def test_missing_and_explicit_disabled_selection_keep_legacy(self):
        root, _ = self.fixture()
        self.assertIsNone(selected_corpus(root, {'files': []}))
        self.assertIsNone(selected_corpus(root, configure(root)))
        with self.assertRaises(ReleaseError):
            selected_corpus(root, configure(root, directory='corpus-releases/test'))

    def test_enabled_selection_requires_package_pin_and_reviewed_release_hash(self):
        root, candidate = self.fixture()
        package = configure(root, candidate)
        self.assertEqual(selected_corpus(root, package).release.count, 32)
        with self.assertRaises(ReleaseError):
            selected_corpus(root, {'files': []})
        with self.assertRaises(ReleaseError):
            selected_corpus(root, configure(root, candidate, manifestSha256='0' * 64))

    def test_selection_tampering_and_directory_symlinks_are_rejected(self):
        root, candidate = self.fixture()
        package = configure(root, candidate)
        (root / 'active-corpus.json').write_bytes(b'{}')
        with self.assertRaises(ReleaseError):
            selected_corpus(root, package)
        package = configure(root, candidate)
        real = root / 'corpus-releases/real'
        candidate.root.rename(real)
        candidate.root.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ReleaseError):
            selected_corpus(root, package)

    def test_archive_pin_requires_same_existing_repo_https_versioned_asset(self):
        root, candidate = self.fixture()
        url = 'https://github.com/YPAndrew0907/music-discovery-atlas/releases/download/audio-v2/music-audio-500.zip'
        archive = {'url': url, 'bytes': 1000, 'sha256': 'a' * 64}
        self.assertEqual(selected_corpus(root, configure(root, candidate, audioArchive=archive)).audio_archive, archive)
        for bad in [url + '?token=x', url.replace('https:', 'http:'), url.replace('YPAndrew0907', 'other'),
                    url.replace('/download/', '/latest/'), url.replace('/audio-v2/', '/../')]:
            with self.subTest(url=bad), self.assertRaises(ReleaseError):
                selected_corpus(root, configure(root, candidate, audioArchive={**archive, 'url': bad}))

    def test_active_graph_uses_validated_snapshot_after_source_files_change(self):
        import numpy as np
        from api import load_graph
        root, candidate = self.fixture()
        selected = selected_corpus(root, configure(root, candidate))
        release = selected.release
        profile = candidate.manifest['allowedQueryProfiles'][0]
        encoder = SimpleNamespace(validated_release=release, release_directory=selected.directory,
            catalog_version=release.catalog_id, catalog_sha=sha256(release.assets['catalog']),
            ids=list(release.ordered_ids), vectors=np.frombuffer(release.assets['vectors'], dtype='<f4').reshape((release.count, 512)),
            audio_receipt={'vectorsSha256': sha256(release.assets['vectors'])},
            engine=profile['id'], query_profile=deepcopy(profile['identity']))
        for name in ['index.json', 'manifest.json', 'vectors.f32', 'ids.json']:
            (candidate.root / name).write_bytes(b'not the approved snapshot')
        graph, binding = load_graph(encoder, selected.directory)
        self.assertEqual(graph.size, 32)
        self.assertEqual(binding['indexSha256'], sha256(release.assets['index']))
        self.assertEqual(binding['corpusReleaseSha256'], candidate.expected)
        with self.assertRaises(RuntimeError):
            load_graph(encoder, root)
        encoder.vectors = encoder.vectors.copy()
        encoder.vectors[0, 0] = 0
        with self.assertRaisesRegex(RuntimeError, 'snapshot'):
            load_graph(encoder, selected.directory)

    def test_legacy_model_and_encoder_pins_remain_exact(self):
        self.assertEqual(sha256((ROOT / 'server/encoder.py').read_bytes()),
                         '234584ed0cfc521404c299ead8528cae47a8e5d4f448e96dc56241640a63a531')
        self.assertEqual(sha256((ROOT / 'music-search-studio/data/vectors.f32').read_bytes()),
                         'ec8e5c9996f229028cf9859dbfb11306a604af42be1e6465f61d9aa50b4d6fd8')
        self.assertEqual(sha256((ROOT / 'music-search-studio/data/catalog.json').read_bytes()),
                         'e7cfd8347b77929c5c593afdaec9e390509acaed725cacadc448fc6bcae1c013')

    def test_runtime_inventory_only_requires_files_copied_or_generated_by_docker(self):
        docker = (ROOT / 'Dockerfile').read_text()
        copied = []
        for line in docker.splitlines():
            if not line.startswith('COPY '):
                continue
            parts = shlex.split(line)
            if parts and parts[0] == 'COPY':
                copied.extend(parts[1:-1])
        package = json.loads((ROOT / 'package-manifest.json').read_bytes())
        for row in package['files']:
            name = row['path']
            with self.subTest(path=name):
                self.assertFalse(name.startswith('tests/'), 'Runtime inventory must not require tests absent from image')
                self.assertTrue(any(name == source or (source.endswith('/') and name.startswith(source)) for source in copied),
                                'Runtime asset is not copied into the Docker image')
        git = set((ROOT / '.gitignore').read_text().splitlines())
        ignore = set((ROOT / '.dockerignore').read_text().splitlines())
        for name in ['server/active_corpus.py', 'server/corpus_release.py', 'active-corpus.json',
                     'scripts/install_corpus_audio_release.py', 'corpus-releases/README.md']:
            self.assertIn('!/' + name, git)
            self.assertIn('!' + name, ignore)


if __name__ == '__main__':
    unittest.main()
