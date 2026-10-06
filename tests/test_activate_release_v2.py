"""The deploy's activation step (scripts/activate_release_v2.py) on a temporary copy of this tree,
with the converted fma2000 fixture release. Nothing in the repository itself changes."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_fixtures import ROOT, converted_fma2000  # noqa: E402
sys.path.insert(0, str(ROOT / 'scripts'))
import activate_release_v2 as activation  # noqa: E402
from corpus_release import ReleaseError  # noqa: E402
from release_v2 import selected_release_v2  # noqa: E402

# Files the activation rewrites are copied; everything else is hard-linked read-only input.
WRITTEN = {'active-corpus.json', 'web-manifest.json', 'package-manifest.json', '.gitignore', '.dockerignore'}


def tree_copy(destination):
    package = json.loads((ROOT / 'package-manifest.json').read_bytes())
    web = json.loads((ROOT / 'web-manifest.json').read_bytes())
    paths = {row['path'] for row in package['files']} | {'web/' + row['path'] for row in web['files']}
    paths |= WRITTEN | {'runtime-assets.json'}
    for relative in sorted(paths):
        source, target = ROOT / relative, destination / relative
        if not source.is_file():
            continue  # the model is fetched during the image build
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in WRITTEN or relative.startswith('web/search-studio/'):
            shutil.copyfile(source, target)
        else:
            try:
                os.link(source, target)
            except OSError:  # another volume: copy instead
                shutil.copyfile(source, target)
    return destination


class ActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir, cls.sha = converted_fma2000()

    def root(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return tree_copy(Path(directory.name).resolve())

    def test_bundled_activation_is_complete_checked_and_idempotent(self):
        root = self.root()
        result = activation.activate(root, self.dir, self.sha, name='fma2000-v2', source={'kind': 'bundled'})
        selection = json.loads((root / 'active-corpus.json').read_bytes())
        self.assertEqual(selection, {'schemaVersion': 2, 'enabled': True, 'format': 'music-corpus-release-v2',
                                     'directory': 'corpus-releases/fma2000-v2', 'manifestSha256': self.sha,
                                     'source': {'kind': 'bundled'}})
        self.assertEqual(sorted(os.listdir(root / 'corpus-releases/fma2000-v2')), sorted(os.listdir(self.dir)))
        data = root / 'web/search-studio/data'
        self.assertEqual(sorted(os.listdir(data)), ['examples.json', 'layout.json', 'manifest.json'])
        self.assertEqual(json.loads((data / 'manifest.json').read_bytes())['audioPolicy']['mode'], 'local')
        for line in ['!/corpus-releases/fma2000-v2/', '!/corpus-releases/fma2000-v2/catalog.sqlite']:
            self.assertIn(line, (root / '.gitignore').read_text().splitlines())
            self.assertIn(line, (root / '.dockerignore').read_text().splitlines())
        if (ROOT / 'web/search-studio/data/catalog.json').exists():  # this tree serves the v1 page
            self.assertIn('web/search-studio/data/catalog.json (removed)', result['changed'])
        checked = activation.check(root)
        self.assertEqual((checked['ok'], checked['release']['count']), (True, 2000))
        package = json.loads((root / 'package-manifest.json').read_bytes())
        self.assertFalse(any(row['path'].startswith('web/search-studio/data/catalog') for row in package['files']))
        self.assertFalse(any(row['path'].startswith('corpus-releases/fma2000-v2') for row in package['files']))
        # The activated tree is what hosting.build_application serves: the v2 gateway over this release.
        self.assertEqual(selected_release_v2(root, package).release.manifest_sha256, self.sha)
        again = activation.activate(root, self.dir, self.sha, name='fma2000-v2', source={'kind': 'bundled'})
        self.assertEqual(again['changed'], [])

    def test_object_store_activation_commits_no_release_files(self):
        root = self.root()
        source = {'kind': 'object-store', 'origin': 'https://objects.example', 'pathPrefix': '/music-atlas/v2/'}
        ignored = {name: (root / name).read_bytes() for name in ('.gitignore', '.dockerignore')}
        activation.activate(root, self.dir, self.sha, name='fma2000-v2', source=source)
        self.assertFalse((root / 'corpus-releases/fma2000-v2').exists())
        self.assertEqual(json.loads((root / 'active-corpus.json').read_bytes())['source'], source)
        self.assertEqual({name: (root / name).read_bytes() for name in ignored}, ignored)  # nothing to allowlist
        self.assertTrue(activation.check(root, self.dir)['ok'])
        with self.assertRaisesRegex(ReleaseError, 'local copy'):
            activation.check(root)

    def test_a_different_release_under_the_name_and_bad_inputs_are_refused(self):
        root = self.root()
        target = root / 'corpus-releases/fma2000-v2'
        shutil.copytree(self.dir, target)
        (target / 'layout.f32').write_bytes(b'\0' * 16)
        with self.assertRaisesRegex(ReleaseError, 'refusing to overwrite'):
            activation.activate(root, self.dir, self.sha, name='fma2000-v2', source={'kind': 'bundled'})
        for name, sha, message in [('../escape', self.sha, 'Invalid release name'), ('ok', '0' * 64, 'Unreviewed')]:
            with self.subTest(name=name), self.assertRaisesRegex(ReleaseError, message):
                activation.activate(self.root(), self.dir, sha, name=name, source={'kind': 'bundled'})
        root = self.root()
        disabled = b'{\n  "schemaVersion": 1,\n  "enabled": false\n}\n'
        (root / 'active-corpus.json').write_bytes(disabled)
        activation.repin_package(root, set())
        with self.assertRaisesRegex(ReleaseError, 'does not select a v2 release'):
            activation.check(root)


if __name__ == '__main__':
    unittest.main()
