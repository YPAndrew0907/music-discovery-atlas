"""Offline adversarial transport fixtures plus exact approved-plan checks. No downloads."""
import bz2
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import hydrate_corpus_audio as hydration
from active_corpus import selected_corpus
from corpus_release import ReleaseError, sha256


def fixture(audio=b'SYNTHETIC TRANSPORT TEST, NOT MUSIC', *, encoded_audio=None):
    compressed = bz2.compress(audio if encoded_audio is None else encoded_audio)
    filename = 'fma_small/000/000001.mp3'
    entry = hydration.Entry('synthetic:transport', 'audio/000001.mp3', len(audio), sha256(audio),
        hydration.ARCHIVES['fma_small'][0], 7679594875, hydration.ARCHIVES['fma_small'][2],
        filename, 1000, len(compressed), zlib.crc32(audio), len(compressed) + 512)
    header = struct.pack('<4s5H3I2H', b'PK\x03\x04', 46, 0, 12, 0, 0, entry.crc32,
                         len(compressed), len(audio), len(filename), 0) + filename.encode()
    body = header + compressed + b'\x00' * (512 - len(header))
    return entry, body


class Raw(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.read_calls = 0
    def read(self, size=-1):
        self.read_calls += 1
        return super().read(size)


class Response:
    def __init__(self, entry, body, *, status=206, headers=None):
        self.status_code, self.raw = status, Raw(body)
        self.headers = {'Content-Range': f'bytes {entry.offset}-{entry.offset + entry.range_bytes - 1}/{entry.archive_bytes}',
                        'Content-Length': str(entry.range_bytes), 'ETag': entry.etag}
        self.headers.update(headers or {})
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        return False


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def get(self, url, **options):
        self.calls.append((url, options))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response
    def close(self):
        pass


class HydrationTransportTests(unittest.TestCase):
    def test_absolute_supervisor_kills_a_worker_that_ignores_cooperative_deadlines(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'should-never-publish'
            code = 'import time,pathlib;time.sleep(5);pathlib.Path(' + repr(str(marker)) + ').write_text("bad")'
            started = time.monotonic()
            with self.assertRaisesRegex(hydration.HydrationStopped, 'worker terminated'):
                hydration.run_bounded_worker([sys.executable, '-c', code], timeout=.05)
            self.assertLess(time.monotonic() - started, 2)
            self.assertFalse(marker.exists())

    def test_exact206_extracts_only_expected_bzip2_member_and_hash(self):
        entry, body = fixture()
        response, budget = Response(entry, body), hydration.Budget()
        session = Session([response])
        self.assertEqual(hydration.fetch_member(entry, budget, session), b'SYNTHETIC TRANSPORT TEST, NOT MUSIC')
        self.assertEqual(budget.reserved, entry.range_bytes)
        options = session.calls[0][1]
        self.assertTrue(options['stream'])
        self.assertFalse(options['allow_redirects'])
        self.assertEqual(options['headers']['If-Match'], entry.etag)

    def test_ignored_range_and_access_denials_never_read_full_response_or_retry(self):
        entry, body = fixture()
        for status in [200, 301, 401, 403, 404, 412, 416, 451]:
            response = Response(entry, body, status=status)
            session = Session([response])
            with self.subTest(status=status), self.assertRaises(hydration.HydrationStopped):
                hydration.fetch_member(entry, hydration.Budget(), session)
            self.assertEqual(response.raw.read_calls, 0)
            self.assertEqual(len(session.calls), 1)

    def test_wrong_range_length_etag_and_http_encoding_fail_before_body_read(self):
        entry, body = fixture()
        for headers in [{'Content-Range': 'bytes 0-1/2'}, {'Content-Length': '0'},
                        {'ETag': 'changed'}, {'Content-Encoding': 'gzip'}]:
            response = Response(entry, body, headers=headers)
            with self.subTest(headers=headers), self.assertRaises(hydration.HydrationStopped):
                hydration.fetch_member(entry, hydration.Budget(), Session([response]))
            self.assertEqual(response.raw.read_calls, 0)

    def test_short_and_oversized_bodies_fail_closed(self):
        entry, body = fixture()
        for content in [body[:-1], body + b'x']:
            with self.subTest(bytes=len(content)), self.assertRaises(hydration.HydrationStopped):
                hydration.fetch_member(entry, hydration.Budget(), Session([Response(entry, content)]))

    def test_headers_crc_path_method_and_bounded_decompression_are_enforced(self):
        entry, body = fixture()
        changes = [bytearray(body) for _ in range(3)]
        changes[0][6] = 1
        changes[1][8] = 8
        changes[2][30] = ord('x')
        for changed in changes:
            with self.assertRaises(ReleaseError):
                hydration.decode_member(bytes(changed), entry)
        with self.assertRaises(ReleaseError):
            hydration.decode_member(body, replace(entry, crc32=0))
        with self.assertRaises(ReleaseError):
            hydration.decode_member(body, replace(entry, audio_sha='0' * 64))
        bomb, bomb_body = fixture(b'xxx', encoded_audio=b'x' * 1_000_000)
        with self.assertRaises(ReleaseError):
            hydration.decode_member(bomb_body, bomb)

    def test_retry_reservations_count_against_one_global_byte_budget(self):
        entry, body = fixture()
        session = Session([Response(entry, body, status=503), Response(entry, body)])
        budget = hydration.Budget(byte_limit=entry.range_bytes * 2)
        with patch.object(hydration.time, 'sleep'):
            hydration.fetch_member(entry, budget, session)
        self.assertEqual((budget.attempts, budget.reserved), (2, entry.range_bytes * 2))
        session = Session([Response(entry, body, status=503), Response(entry, body)])
        budget = hydration.Budget(byte_limit=entry.range_bytes)
        with patch.object(hydration.time, 'sleep'), self.assertRaises(hydration.HydrationStopped):
            hydration.fetch_member(entry, budget, session)
        self.assertEqual(len(session.calls), 1)

    def test_expired_global_deadline_sends_nothing(self):
        entry, body = fixture()
        session = Session([Response(entry, body)])
        with self.assertRaises(hydration.HydrationStopped):
            hydration.fetch_member(entry, hydration.Budget(seconds=-1), session)
        self.assertFalse(session.calls)


class ApprovedHydrationPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = json.loads((ROOT / 'package-manifest.json').read_bytes())
        # This checked-in candidate is selected only for local/source acceptance.
        from corpus_release import ReleaseLimits, validate_release
        release = validate_release(ROOT / 'corpus-releases/fma2000',
            expected_manifest_sha256=hydration.RELEASE_SHA, limits=ReleaseLimits(max_tracks=2000))
        cls.selected = SimpleNamespace(release=release, directory=ROOT / 'corpus-releases/fma2000')
        cls.plan = json.loads((ROOT / 'audio-hydration.json').read_bytes())
        cls.entries = hydration.load_plan(ROOT, package, cls.selected)

    def test_actual_plan_has_exact2000_order_source_pins_and_original_mp3_hashes(self):
        self.assertEqual(len(self.entries), 2000)
        self.assertEqual(sum(entry.range_bytes for entry in self.entries), 1_951_856_486)
        self.assertEqual(sum(entry.audio_bytes for entry in self.entries), 2_034_768_876)
        self.assertEqual(tuple(entry.id for entry in self.entries), self.selected.release.ordered_ids)
        self.assertNotIn('fma:30702', [entry.id for entry in self.entries])
        self.assertEqual(len({entry.audio_sha for entry in self.entries}), 2000)

    def test_changed_policy_order_source_or_member_is_rejected(self):
        mutations = [lambda p: p['rangePolicy'].update(fullArchiveFallbackAllowed=True),
                     lambda p: p['entries'].reverse(),
                     lambda p: p['archives']['fma_small'].update(url='https://example.invalid/archive.zip'),
                     lambda p: p['entries'][0].update(offset=p['entries'][0]['offset'] + 1),
                     lambda p: p['entries'][0].update(audioSha256='0' * 64)]
        for mutate in mutations:
            plan = deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(ReleaseError):
                hydration.validate_plan(plan, self.selected)

    def test_unready_track_never_publishes_any_partial_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'empty-cache'
            cache.mkdir()
            with self.assertRaises((ReleaseError, OSError)):
                hydration.hydrate(root, self.selected, self.entries, source_cache=cache)
            for name in ['audio-preview', 'audio-release-credits', 'audio-delivery.verified.json']:
                self.assertFalse((root / name).exists())

    def test_existing_installation_and_low_disk_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'audio-preview').mkdir()
            with self.assertRaisesRegex(ReleaseError, 'overwrite'):
                hydration.hydrate(root, self.selected, self.entries, source_cache=root)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(hydration.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
                with self.assertRaisesRegex(ReleaseError, 'Insufficient disk'):
                    hydration.hydrate(Path(directory), self.selected, self.entries, source_cache=directory)


if __name__ == '__main__':
    unittest.main()
