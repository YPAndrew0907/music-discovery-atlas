"""Release-format-v2 build hydration: the v2 dispatch, the plan bound through the v2 database,
v1-identical credits, worker publication and the parent's re-verification. Uses the converted
fma2000 fixture and synthetic audio bytes; no downloads and no real audio."""
import contextlib
from copy import deepcopy
import dataclasses
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_fixtures import ROOT, V1_DIR, V1_SHA, converted_fma2000  # noqa: E402
sys.path.insert(0, str(ROOT / 'scripts'))
import hydrate_corpus_audio as hydration  # noqa: E402
import hydrate_release_v2 as hydration_v2  # noqa: E402
from collection_v2 import AudioDeliveryV2  # noqa: E402
from corpus_release import ReleaseError, ReleaseLimits, sha256, validate_release  # noqa: E402
from release_v2 import load_release_v2  # noqa: E402


class SyntheticAudio:
    """The fixture release with synthetic audio pins: the real MP3s are not in the repository."""

    def __init__(self, release, pins):
        self._release, self._pins = release, pins

    def audio_pins(self):
        return list(self._pins)

    def __getattr__(self, name):
        return getattr(self._release, name)


class V2HydrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir, cls.sha = converted_fma2000()
        cls.release = load_release_v2(cls.dir, expected_manifest_sha256=cls.sha)
        cls.package = json.loads((ROOT / 'package-manifest.json').read_bytes())
        cls.plan = json.loads((ROOT / 'audio-hydration.json').read_bytes())
        cls.entries, cls.sources = hydration_v2.load_plan_v2(ROOT, cls.package, cls.release)
        v1_release = validate_release(V1_DIR, expected_manifest_sha256=V1_SHA, limits=ReleaseLimits(max_tracks=2000))
        cls.v1_entries = hydration.load_plan(ROOT, cls.package, SimpleNamespace(release=v1_release, directory=V1_DIR))

    def temp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name).resolve()

    def synthetic(self):
        """Entries, a matching local cache and a release view whose pins describe those bytes."""
        cache = self.temp() / 'cache'
        cache.mkdir()
        entries, pins = [], []
        for row, entry in enumerate(self.entries):
            data = b'SYNTHETIC V2 HYDRATION TEST, NOT MUSIC %d' % row
            (cache / Path(entry.audio).name).write_bytes(data)
            entries.append(dataclasses.replace(entry, audio_bytes=len(data), audio_sha=sha256(data)))
            pins.append((row, entry.id, len(data), sha256(data)))
        return tuple(entries), cache, SyntheticAudio(self.release, pins)

    def test_the_v2_plan_binds_exactly_the_v1_entries_and_sources(self):
        self.assertEqual(self.entries, self.v1_entries)
        self.assertEqual(sum(entry.range_bytes for entry in self.entries), 1_951_856_486)
        for name in ('catalog.json', 'ids.json', 'rights.json', 'manifest.json'):
            self.assertEqual(self.sources[name], (V1_DIR / name).read_bytes(), name)

    def test_changed_plan_binding_or_release_source_is_rejected(self):
        mutations = [lambda p: p['rangePolicy'].update(fullArchiveFallbackAllowed=True),
                     lambda p: p['entries'].reverse(),
                     lambda p: p['archives']['fma_small'].update(url='https://example.invalid/archive.zip'),
                     lambda p: p['entries'][0].update(offset=p['entries'][0]['offset'] + 1),
                     lambda p: p['entries'][0].update(audioSha256='0' * 64),
                     lambda p: p['entries'][5]['evidence'].update(sha256='0' * 64),
                     lambda p: p['bindings']['rights.json'].update(sha256='0' * 64),
                     lambda p: p['bindings']['release.json'].update(sha256='0' * 64)]
        for mutate in mutations:
            plan = deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(ReleaseError):
                hydration_v2.validate_plan_v2(plan, self.release, self.sources)
        other = SimpleNamespace(manifest={'source': {'manifestSha256': '0' * 64}}, count=2000)
        self.assertRegex(hydration_v2.plan_applies(other), 'not a conversion of the reviewed fma2000 release')
        with self.assertRaisesRegex(ReleaseError, 'not a conversion'):
            hydration_v2.load_plan_v2(ROOT, self.package, other)
        changed = dict(self.sources, **{'rights.json': self.sources['rights.json'] + b' '})
        with self.assertRaisesRegex(ReleaseError, 'binding mismatch'):
            hydration_v2.validate_plan_v2(self.plan, self.release, changed)

    def test_a_v2_release_without_a_plan_hydrates_nothing(self):
        other = SimpleNamespace(release=SimpleNamespace(manifest={'source': {'manifestSha256': '1' * 64}}, count=5777))
        output = io.StringIO()
        with mock.patch.object(hydration_v2, 'selected_release_v2', return_value=other), contextlib.redirect_stdout(output):
            self.assertEqual(hydration_v2.run(self.package), 0)
        self.assertEqual(json.loads(output.getvalue())['tracks'], 0)
        with mock.patch.object(hydration_v2, 'selected_release_v2', return_value=other), self.assertRaises(ReleaseError):
            hydration_v2.run(self.package, verify_plan=True)

    def test_only_a_pinned_v2_selection_takes_the_v2_path(self):
        selection = json.loads((ROOT / 'active-corpus.json').read_bytes())
        self.assertEqual(hydration.selects_release_v2(ROOT, self.package), selection.get('schemaVersion') == 2)
        root = self.temp()

        def pinned(config, *, pin=True, raw=None):
            data = raw if raw is not None else (json.dumps(config) + '\n').encode()
            (root / 'active-corpus.json').write_bytes(data)
            return {'files': [{'path': 'active-corpus.json', 'bytes': len(data), 'sha256': sha256(data)}] if pin else []}
        v2 = {'schemaVersion': 2, 'enabled': True}
        self.assertTrue(hydration.selects_release_v2(root, pinned(v2)))
        self.assertFalse(hydration.selects_release_v2(root, pinned(v2, pin=False)))
        self.assertFalse(hydration.selects_release_v2(root, pinned({'schemaVersion': 1, 'enabled': False})))
        self.assertFalse(hydration.selects_release_v2(root, pinned(None, raw=b'{"schemaVersion": 2,')))
        package = pinned(v2)
        (root / 'active-corpus.json').write_bytes(b'{"schemaVersion": 2, "enabled": false}\n')
        self.assertFalse(hydration.selects_release_v2(root, package))  # pin mismatch: the v1 path reports it

    def test_worker_output_is_reverified_by_the_parent_and_published_whole(self):
        entries, cache, release = self.synthetic()
        root, stage = self.temp(), self.temp()
        result = hydration_v2.hydrate_v2(stage, release, entries, self.sources, source_cache=cache)
        self.assertTrue(result['localCacheTest'])
        (stage / 'hydration-result.json').write_text(json.dumps(result))
        with self.assertRaisesRegex(ReleaseError, 'identity mismatch'):  # production never publishes a cache test
            hydration_v2.verify_stage(stage, release, self.sources, deadline=time.monotonic() + 60)
        final, _ = hydration_v2.verify_stage(stage, release, self.sources, deadline=time.monotonic() + 60,
                                             local_cache_allowed=True)
        hydration_v2.publish(stage, root, deadline=time.monotonic() + 60)
        self.assertEqual(sorted(p.name for p in root.iterdir()), sorted(hydration_v2.FINAL_NAMES))
        delivery = json.loads((root / 'audio-delivery.verified.json').read_bytes())
        self.assertEqual((delivery['schemaVersion'], delivery['kind'], delivery['mode']), (2, 'music-audio-delivery-v2', 'local'))
        self.assertEqual((delivery['catalogSha256'], delivery['releaseSha256']), (self.release.catalog_sha256, self.sha))
        self.assertEqual(delivery['hydration']['sourceReleaseSha256'], V1_SHA)
        self.assertEqual(len(list((root / 'audio-preview').iterdir())), 2000)
        accepted = AudioDeliveryV2(root / 'audio-delivery.verified.json', release, enabled=True, directory=root / 'audio-preview')
        self.assertEqual((accepted.manifest['available'], accepted.manifest['mode']), (2000, 'local'))
        credits = root / 'audio-release-credits'
        for name in ('catalog.json', 'rights.json'):
            self.assertEqual((credits / name).read_bytes(), (V1_DIR / name).read_bytes())
        evidence = sorted(p.relative_to(credits).as_posix() for p in (credits / 'evidence').iterdir())
        self.assertEqual(len(evidence), 2000)
        for name in evidence[::397]:
            self.assertEqual((credits / name).read_bytes(), (V1_DIR / name).read_bytes())
        self.assertEqual(final['tracks'], delivery['tracks'])

    def test_the_build_chain_runs_the_worker_reverifies_and_publishes(self):
        """run() -> hydrate_supervised_v2 -> the worker's CLI path -> verify_stage (no cache allowance) ->
        publish, as the image build runs it. Only the network transport is replaced: fetch_member serves
        the synthetic cache and reserves its range like a real attempt; the worker runs in-process."""
        entries, cache, release = self.synthetic()
        root = self.temp()

        def fetch_member(entry, budget, session):
            budget.reserve(entry.range_bytes)
            return (cache / Path(entry.audio).name).read_bytes()

        def run_bounded_worker(command, *, timeout):
            self.assertEqual(command[1:3], [str(root / 'scripts/hydrate_release_v2.py'), '--worker-dir'])
            self.assertGreater(timeout, 0)
            return hydration_v2.run(self.package, worker_dir=Path(command[3]))
        output = io.StringIO()
        with mock.patch.object(hydration_v2, 'ROOT', root), \
                mock.patch.object(hydration_v2, 'selected_release_v2', return_value=SimpleNamespace(release=release)), \
                mock.patch.object(hydration_v2, 'load_plan_v2', return_value=(entries, self.sources)), \
                mock.patch.object(hydration_v2.v1, 'fetch_member', fetch_member), \
                mock.patch.object(hydration_v2.v1, 'run_bounded_worker', run_bounded_worker), \
                contextlib.redirect_stdout(output):
            self.assertEqual(hydration_v2.run(self.package), 0)
        summary = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((summary['tracks'], summary['available'], summary['localCacheTest'], summary['releaseFormat']),
                         (2000, 2000, False, 2))
        self.assertEqual((summary['networkAttempts'], summary['rangeBytesReservedIncludingRetries']),
                         (2000, sum(entry.range_bytes for entry in entries)))
        self.assertEqual(sorted(p.name for p in root.iterdir()), sorted(hydration_v2.FINAL_NAMES))
        delivery = AudioDeliveryV2(root / 'audio-delivery.verified.json', release, enabled=True, directory=root / 'audio-preview')
        self.assertEqual((delivery.manifest['mode'], delivery.manifest['available']), ('local', 2000))
        self.assertEqual(json.loads((root / 'audio-delivery.verified.json').read_bytes())['hydration']['networkAttempts'], 2000)

    def test_a_changed_file_in_the_stage_publishes_nothing(self):
        entries, cache, release = self.synthetic()
        root, stage = self.temp(), self.temp()
        result = hydration_v2.hydrate_v2(stage, release, entries, self.sources, source_cache=cache)
        (stage / 'hydration-result.json').write_text(json.dumps(result))
        victim = next((stage / 'audio-preview').iterdir())
        victim.write_bytes(victim.read_bytes()[:-1] + b'X')
        with self.assertRaisesRegex(ReleaseError, 'Audio hash mismatch'):
            hydration_v2.verify_stage(stage, release, self.sources, deadline=time.monotonic() + 60, local_cache_allowed=True)
        (stage / 'audio-preview' / 'extra.mp3').write_bytes(b'x')
        with self.assertRaisesRegex(ReleaseError, 'inventory mismatch'):
            hydration_v2.verify_stage(stage, release, self.sources, deadline=time.monotonic() + 60, local_cache_allowed=True)
        self.assertEqual(list(root.iterdir()), [])

    def test_unready_track_never_publishes_any_partial_corpus(self):
        root = self.temp()
        cache = root / 'empty-cache'
        cache.mkdir()
        with self.assertRaises((ReleaseError, OSError)):
            hydration_v2.hydrate_v2(root, self.release, self.entries, self.sources, source_cache=cache)
        for name in hydration_v2.FINAL_NAMES:
            self.assertFalse((root / name).exists())

    def test_existing_installation_and_low_disk_are_never_overwritten(self):
        root = self.temp()
        (root / 'audio-preview').mkdir()
        with self.assertRaisesRegex(ReleaseError, 'overwrite'):
            hydration_v2.hydrate_v2(root, self.release, self.entries, self.sources, source_cache=root)
        with self.assertRaisesRegex(ReleaseError, 'overwrite'):
            hydration_v2.hydrate_supervised_v2(root, self.release, self.sources)
        root = self.temp()
        with mock.patch.object(hydration_v2.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ReleaseError, 'Insufficient disk'):
                hydration_v2.hydrate_v2(root, self.release, self.entries, self.sources, source_cache=root)

    def test_the_image_copies_every_v2_build_script(self):
        docker = (ROOT / 'Dockerfile').read_text().splitlines()
        for name in ('hydrate_release_v2.py', 'make_audio_delivery_v2.py'):
            self.assertIn(f'COPY scripts/{name} /app/scripts/{name}', docker)
            self.assertIn('!scripts/' + name, (ROOT / '.dockerignore').read_text().splitlines())
            self.assertIn('!/scripts/' + name, (ROOT / '.gitignore').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
