"""Release installer for format v2: bundled verification and the content-addressed object-store
install, against the converted fma2000 fixture and an in-memory origin. No network."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_fixtures import ROOT, V1_COUNT, V1_EVIDENCE_BYTES, converted_fma2000  # noqa: E402
sys.path.insert(0, str(ROOT / 'scripts'))
import install_release_v2 as installer  # noqa: E402
from corpus_release import ReleaseError, sha256  # noqa: E402
from release_v2 import install_source, selection_config_v2  # noqa: E402

NAME = 'fma2000-v2'
ORIGIN, PREFIX = 'https://objects.example', '/music-atlas/v2/'


class Raw(io.BytesIO):
    """A response body that can drop the connection after `cut` bytes."""

    def __init__(self, body, cut=None):
        super().__init__(body)
        self.cut, self.decode_content = cut, True

    def read(self, size=-1):
        if self.cut is not None and self.tell() >= self.cut:
            from urllib3.exceptions import ProtocolError
            raise ProtocolError('Connection broken: fixture cut')
        if self.cut is not None and size is not None and size >= 0:
            size = min(size, self.cut - self.tell())
        return super().read(size)


class Response:
    def __init__(self, status, body=b'', headers=None, cut=None):
        self.status_code, self.headers, self.raw = status, dict(headers or {}), Raw(body, cut)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False


class Store:
    """An in-memory content-addressed origin. faults[url] is a list of callables consumed one per
    request; each gets (data, headers) and returns a Response or None (normal service)."""

    def __init__(self, objects):
        self.objects, self.calls, self.faults = dict(objects), [], {}

    def respond(self, url, headers):
        data = self.objects.get(url)
        queue = self.faults.get(url)
        if queue:
            response = queue.pop(0)(data, headers)
            if response is not None:
                return response
        if data is None:
            return Response(404)
        if 'Range' in headers:
            start, end = (int(v) for v in headers['Range'].removeprefix('bytes=').split('-'))
            body = data[start:end + 1]
            return Response(206, body, {'Content-Range': f'bytes {start}-{end}/{len(data)}', 'Content-Length': str(len(body))})
        return Response(200, data, {'Content-Length': str(len(data))})

    def session(self):
        store = self

        class Session:
            def get(self, url, *, headers, stream, allow_redirects, timeout):
                assert stream is True and allow_redirects is False and timeout
                store.calls.append((url, dict(headers)))
                return store.respond(url, headers)

            def close(self):
                pass
        return Session()


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src, cls.sha = converted_fma2000()
        cls.blobs = {name: (cls.src / name).read_bytes() for name in os.listdir(cls.src)}
        cls.manifest = json.loads(cls.blobs['release.json'])
        cls.by_path = {spec['path']: spec for spec in cls.manifest['assets'].values()}

    def temp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name).resolve()

    def root(self, source=None, *, bundle=True, tamper=None, release=None, **changes):
        src, sha = release or (self.src, self.sha)
        root = self.temp()
        (root / 'corpus-releases').mkdir()
        if bundle:
            # Hard links keep fixture copies cheap; tampered files are replaced, never edited in place.
            shutil.copytree(src, root / 'corpus-releases' / NAME, copy_function=os.link)
            for name, change in (tamper or {}).items():
                path = root / 'corpus-releases' / NAME / name
                data = path.read_bytes()
                path.unlink()
                path.write_bytes(change(data))
        config = {'schemaVersion': 2, 'enabled': True, 'format': 'music-corpus-release-v2',
                  'directory': 'corpus-releases/' + NAME, 'manifestSha256': sha,
                  **({'source': source} if source else {}), **changes}
        data = (json.dumps(config, indent=2) + '\n').encode()
        (root / 'active-corpus.json').write_bytes(data)
        (root / 'package-manifest.json').write_text(json.dumps(
            {'files': [{'path': 'active-corpus.json', 'bytes': len(data), 'sha256': sha256(data)}]}))
        return root

    def store(self):
        objects = {ORIGIN + PREFIX + self.sha: self.blobs['release.json']}
        for spec in self.manifest['assets'].values():
            objects[ORIGIN + PREFIX + spec['sha256']] = self.blobs[spec['path']]
        return Store(objects)

    def url(self, path):
        return ORIGIN + PREFIX + self.by_path[path]['sha256']

    def object_root(self):
        return self.root({'kind': 'object-store', 'origin': ORIGIN, 'pathPrefix': PREFIX}, bundle=False)

    def install(self, root, store, **options):
        return installer.install(root, installer.read_selection(root), session_factory=store.session,
                                 supervised=False, **options)

    # ---- selection --------------------------------------------------------------------------------
    def test_the_repository_selection_makes_the_build_step_a_no_op(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(installer.main([]), 0)
        if installer.read_selection(ROOT) is None:
            self.assertFalse(json.loads(output.getvalue())['installed'])

    def test_the_image_build_runs_the_installer_before_any_download(self):
        docker = (ROOT / 'Dockerfile').read_text().splitlines()
        install = docker.index('RUN python scripts/install_release_v2.py')
        self.assertLess(install, next(i for i, line in enumerate(docker) if 'fetch_model.py --download-model' in line))
        self.assertLess(docker.index('COPY scripts/install_release_v2.py /app/scripts/install_release_v2.py'), install)
        self.assertIn('!scripts/install_release_v2.py', (ROOT / '.dockerignore').read_text().splitlines())
        self.assertIn('!/scripts/install_release_v2.py', (ROOT / '.gitignore').read_text().splitlines())

    def test_source_block_is_exact_and_the_stage_can_never_be_selected(self):
        self.assertEqual(dict(install_source(None)), {'kind': 'bundled'})
        good = {'kind': 'object-store', 'origin': ORIGIN, 'pathPrefix': PREFIX}
        self.assertEqual(dict(install_source(good)), good)
        self.assertEqual(install_source({**good, 'pathPrefix': '/'})['pathPrefix'], '/')
        for bad in [{'kind': 'bundled', 'origin': ORIGIN}, {'kind': 'mirror'}, {'kind': 'object-store', 'origin': ORIGIN},
                    {**good, 'origin': 'http://objects.example'}, {**good, 'origin': ORIGIN + '/path'},
                    {**good, 'origin': 'https://user@objects.example'}, {**good, 'origin': 'https://*.example'},
                    {**good, 'pathPrefix': 'music/'}, {**good, 'pathPrefix': '/music'}, {**good, 'pathPrefix': '/../'},
                    {**good, 'pathPrefix': '/a/b/c/d/e/'}, {**good, 'pathPrefix': '/a b/'}, {**good, 'extra': 1}, 'bundled']:
            with self.subTest(source=bad), self.assertRaisesRegex(ReleaseError, 'Invalid v2 release source'):
                install_source(bad)
        root = self.root(bundle=False)
        parsed = installer.read_selection(root)
        self.assertEqual(installer.stage_directory(parsed).name, '.v2-install-' + NAME)
        for change in [{'directory': 'corpus-releases/.v2-install-' + NAME}, {'source': {'kind': 'bundled', 'x': 1}}]:
            with self.subTest(change=change), self.assertRaises(ReleaseError):
                installer.read_selection(self.root(bundle=False, **change))

    # ---- bundled ----------------------------------------------------------------------------------
    def test_bundled_release_is_verified_in_place_with_every_row(self):
        rows = {'rows': V1_COUNT, 'evidenceFiles': V1_COUNT, 'evidenceBytes': V1_EVIDENCE_BYTES}
        # Release format 2.1 (the converter's default) also proves its lookup index; 2.0 (no index) is the
        # format the fma2000 deploy pins (279cd21b...).
        for (src, sha), expected in [((self.src, self.sha), {**rows, 'lookupIndex': 'fts5-trigram-v1'}),
                                     (converted_fma2000(lookup_index=False), {**rows, 'lookupIndex': None})]:
            with self.subTest(release=sha):
                root = self.root(release=(src, sha))
                parsed = installer.read_selection(root)
                summary = installer.verify_directory(installer.checked_directory(parsed), parsed)
                self.assertEqual((summary['count'], summary['assets'], summary['releaseSha256']), (V1_COUNT, 7, sha))
                self.assertEqual(summary['rowValidation'], expected)
                self.assertEqual(sorted(os.listdir(root / 'corpus-releases' / NAME)), sorted(self.blobs))

    def test_bundled_tampering_inventory_and_symlinks_fail_closed(self):
        flip = lambda data: data[:-1] + bytes([data[-1] ^ 1])  # noqa: E731
        cases = [({'examples.json': flip}, 'integrity mismatch: examples.json'),  # lazily read by the server
                 ({'evidence.sqlite': flip}, 'integrity mismatch: evidence.sqlite'),  # lazily read by the server
                 ({'graph.bin': flip}, 'integrity mismatch: graph.bin'),
                 ({'release.json': lambda d: d + b' '}, 'Unreviewed v2 release manifest digest')]
        for tamper, message in cases:
            root = self.root(tamper=tamper)
            parsed = installer.read_selection(root)
            with self.subTest(tamper=list(tamper)), self.assertRaisesRegex(ReleaseError, message):
                installer.verify_directory(installer.checked_directory(parsed), parsed)
        root = self.root()
        parsed = installer.read_selection(root)
        (root / 'corpus-releases' / NAME / 'notes.txt').write_text('extra')
        with self.assertRaisesRegex(ReleaseError, 'inventory differs'):
            installer.verify_directory(installer.checked_directory(parsed), parsed)
        root = self.root()
        directory = root / 'corpus-releases' / NAME
        (directory / 'layout.f32').rename(root / 'layout.f32')
        (directory / 'layout.f32').symlink_to(root / 'layout.f32')
        with self.assertRaisesRegex(ReleaseError, 'non-file entry: layout.f32'):
            installer.verify_directory(directory, installer.read_selection(root))
        root = self.root()
        (root / 'corpus-releases' / NAME).rename(root / 'elsewhere')
        (root / 'corpus-releases' / NAME).symlink_to(root / 'elsewhere', target_is_directory=True)
        with self.assertRaisesRegex(ReleaseError, 'symlinks'):
            installer.checked_directory(installer.read_selection(root))
        with self.assertRaisesRegex(ReleaseError, 'symlinks'):
            installer.checked_directory(installer.read_selection(self.root(bundle=False)))

    # ---- object store -----------------------------------------------------------------------------
    def test_object_store_install_fetches_by_digest_verifies_and_publishes_once(self):
        root, store = self.object_root(), self.store()
        result = self.install(root, store)
        self.assertEqual((result['action'], result['count'], result['rowValidation']['rows']), ('fetched', V1_COUNT, V1_COUNT))
        final = root / 'corpus-releases' / NAME
        self.assertEqual({name: (final / name).read_bytes() for name in os.listdir(final)}, self.blobs)
        self.assertFalse((root / 'corpus-releases' / ('.v2-install-' + NAME)).exists())
        self.assertEqual(sorted(url for url, _ in store.calls), sorted(store.objects))
        self.assertTrue(all('Range' not in headers and headers['Accept-Encoding'] == 'identity' for _, headers in store.calls))
        self.assertEqual(result['transfer']['attempts'], 8)
        # Installed means verified, never replaced: a second run fetches nothing.
        again = self.install(root, store)
        self.assertEqual((again['action'], len(store.calls)), ('verified-existing', 8))

    def test_an_interrupted_object_resumes_with_an_exact_range(self):
        root, store = self.object_root(), self.store()
        url, size = self.url('catalog.sqlite'), self.by_path['catalog.sqlite']['bytes']
        store.faults[url] = [lambda data, headers: Response(200, data, {'Content-Length': str(len(data))}, cut=1_000_000)]
        with mock.patch.object(installer.time, 'sleep'):
            result = self.install(root, store)
        requests = [headers for called, headers in store.calls if called == url]
        self.assertEqual([h.get('Range') for h in requests], [None, f'bytes=1000000-{size - 1}'])
        self.assertEqual(result['transfer']['assets']['catalog.sqlite'], 'fetched')
        self.assertEqual((root / 'corpus-releases' / NAME / 'catalog.sqlite').read_bytes(), self.blobs['catalog.sqlite'])

    def test_a_stopped_run_resumes_from_its_private_stage(self):
        root, store = self.object_root(), self.store()
        url, size = self.url('evidence.sqlite'), self.by_path['evidence.sqlite']['bytes']
        refuse = lambda data, headers: Response(403)  # noqa: E731
        store.faults[url] = [lambda data, headers: Response(200, data, {'Content-Length': str(len(data))}, cut=2_000_000), refuse]
        with mock.patch.object(installer.time, 'sleep'), self.assertRaisesRegex(ReleaseError, 'HTTP 403'):
            self.install(root, store)
        stage = root / 'corpus-releases' / ('.v2-install-' + NAME)
        self.assertFalse((root / 'corpus-releases' / NAME).exists())
        self.assertEqual((stage / 'objects' / (self.by_path['evidence.sqlite']['sha256'] + '.part')).stat().st_size, 2_000_000)
        before = len(store.calls)
        self.install(root, store)
        resumed = [(called, headers) for called, headers in store.calls[before:]]
        self.assertEqual([h.get('Range') for called, h in resumed if called == url], [f'bytes=2000000-{size - 1}'])
        # Objects completed by the first run are not fetched again.
        self.assertNotIn(ORIGIN + PREFIX + self.sha, [called for called, _ in resumed])
        self.assertTrue((root / 'corpus-releases' / NAME / 'evidence.sqlite').is_file())

    def test_a_server_that_ignores_range_restarts_the_object_within_budget(self):
        root, store = self.object_root(), self.store()
        url = self.url('vectors.f32')
        whole = lambda data, headers: Response(200, data, {'Content-Length': str(len(data))})  # noqa: E731
        store.faults[url] = [lambda data, headers: Response(200, data, {'Content-Length': str(len(data))}, cut=100_000), whole]
        with mock.patch.object(installer.time, 'sleep'):
            result = self.install(root, store)
        self.assertEqual((root / 'corpus-releases' / NAME / 'vectors.f32').read_bytes(), self.blobs['vectors.f32'])
        self.assertGreaterEqual(result['transfer']['reservedBytesIncludingRetries'], self.manifest['count'] * 2048 + 100_000)

    def test_wrong_bytes_redirects_refusals_and_bad_headers_install_nothing(self):
        def corrupt(data, headers):
            return Response(200, data[:-1] + bytes([data[-1] ^ 1]), {'Content-Length': str(len(data))})
        cases = [('graph.bin', corrupt, 'differs from its pinned SHA-256'),
                 ('graph.bin', lambda d, h: Response(302, b'', {'Location': 'https://elsewhere.example/x'}), 'HTTP 302'),
                 ('graph.bin', lambda d, h: Response(404), 'HTTP 404'),
                 ('graph.bin', lambda d, h: Response(200, d, {'Content-Length': str(len(d) + 1)}), 'length differs'),
                 ('graph.bin', lambda d, h: Response(200, d, {'Content-Length': str(len(d)), 'Content-Encoding': 'gzip'}), 'Encoded'),
                 ('graph.bin', lambda d, h: Response(200, d + b'x', {'Content-Length': str(len(d))}), 'exceeded its pinned length'),
                 ('release.json', lambda d, h: Response(200, d + b' ', {'Content-Length': str(len(d) + 1)}), 'reviewed digest')]
        for path, fault, message in cases:
            root, store = self.object_root(), self.store()
            url = ORIGIN + PREFIX + self.sha if path == 'release.json' else self.url(path)
            store.faults[url] = [fault]
            with self.subTest(message=message), self.assertRaisesRegex(ReleaseError, message):
                self.install(root, store)
            self.assertFalse((root / 'corpus-releases' / NAME).exists())
            objects = root / 'corpus-releases' / ('.v2-install-' + NAME) / 'objects'
            digest = self.sha if path == 'release.json' else self.by_path[path]['sha256']
            self.assertEqual([p.name for p in objects.glob(digest + '*')], [])
        root, store = self.object_root(), self.store()
        store.faults[self.url('layout.f32')] = [lambda d, h: Response(503)] * 3
        with mock.patch.object(installer.time, 'sleep'), self.assertRaisesRegex(ReleaseError, 'bounded retries'):
            self.install(root, store)

    def test_an_existing_directory_is_verified_and_never_replaced(self):
        root = self.root({'kind': 'object-store', 'origin': ORIGIN, 'pathPrefix': PREFIX},
                         tamper={'layout.f32': lambda data: data[:-1] + bytes([data[-1] ^ 1])})
        store = self.store()
        with self.assertRaisesRegex(ReleaseError, 'integrity mismatch: layout.f32'):
            self.install(root, store)
        self.assertEqual(store.calls, [])

    def test_budget_and_deadline_stop_transfers_before_they_start(self):
        budget = installer.Budget(10, 60)
        budget.reserve(10)
        with self.assertRaisesRegex(ReleaseError, 'budget exceeded'):
            budget.reserve(1)
        root, store = self.object_root(), self.store()
        parsed = installer.read_selection(root)
        stage = installer.stage_directory(parsed)
        stage.mkdir()
        with self.assertRaisesRegex(ReleaseError, 'deadline expired'):
            installer.fetch_release(stage, parsed, store.session(), seconds=-1)
        self.assertEqual(store.calls, [])

    def test_absolute_supervisor_kills_a_stalled_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'should-never-exist'
            code = 'import time,pathlib;time.sleep(5);pathlib.Path(' + repr(str(marker)) + ').write_text("bad")'
            started = time.monotonic()
            with self.assertRaisesRegex(ReleaseError, 'worker terminated'):
                installer.run_bounded_worker([sys.executable, '-c', code], timeout=.05)
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
