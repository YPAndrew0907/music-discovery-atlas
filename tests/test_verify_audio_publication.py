"""Audio publication verifier for remote mode, against the converted fma2000 fixture and an
in-memory origin serving synthetic bytes (the real MP3s are not in the repository). No network."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_fixtures import ROOT, V1_COUNT, converted_fma2000  # noqa: E402
sys.path.insert(0, str(ROOT / 'scripts'))
import verify_audio_publication as verifier  # noqa: E402
from collection_v2 import AudioDeliveryV2  # noqa: E402
from corpus_release import ReleaseError, sha256  # noqa: E402
from release_v2 import load_release_v2  # noqa: E402

ORIGIN, PREFIX = 'https://audio.example', '/fma2000/'


class Published:
    """The fixture release with synthetic audio bytes in place of the real MP3s, pinned consistently
    in the catalog and rights rows; `overrides` edits individual records."""

    def __init__(self, release, objects):
        self._release, self.overrides = release, {}
        self._pins = [(row, ident, len(objects[row]), sha256(objects[row])) for row, ident in enumerate(release.ordered_ids)]

    def audio_pins(self):
        return list(self._pins)

    def track_record(self, row):
        track, rights = self._release.track_record(row)
        _, _, size, digest = self._pins[row]
        track_change, rights_change = self.overrides.get(row, ({}, {}))
        return ({**track, 'audioBytes': size, 'audioSha256': digest, **track_change},
                {**rights, 'audioBytes': size, 'audioSha256': digest, **rights_change})

    def __getattr__(self, name):
        return getattr(self._release, name)


class Response:
    def __init__(self, status, body=b'', headers=None):
        self.status_code, self.headers, self.raw = status, dict(headers or {}), io.BytesIO(body)
        self.raw.decode_content = True

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False


class Origin:
    """URL -> bytes, with faults[url] = callable(data, headers) -> Response or None (normal service)."""

    def __init__(self, objects):
        self.objects, self.faults, self.calls = objects, {}, []

    def session(self):
        origin = self

        class Session:
            def get(self, url, *, headers, stream, allow_redirects, timeout):
                assert stream is True and allow_redirects is False and timeout
                origin.calls.append((url, dict(headers)))
                data = origin.objects.get(url)
                fault = origin.faults.get(url)
                if fault:
                    response = fault(data, headers)
                    if isinstance(response, BaseException):
                        raise response
                    if response is not None:
                        return response
                if data is None:
                    return Response(404, b'not found', {'Content-Type': 'text/html'})
                if 'Range' in headers:
                    start, end = (int(v) for v in headers['Range'].removeprefix('bytes=').split('-'))
                    part = data[start:end + 1]
                    return Response(206, part, {'Content-Type': 'audio/mpeg', 'Content-Length': str(len(part)),
                                                'Content-Range': f'bytes {start}-{end}/{len(data)}'})
                return Response(200, data, {'Content-Type': 'audio/mpeg', 'Content-Length': str(len(data)),
                                            'Cache-Control': 'public, max-age=31536000, immutable'})

            def close(self):
                pass
        return Session()


class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory, sha = converted_fma2000()
        cls.release = load_release_v2(directory, expected_manifest_sha256=sha)
        # Row 0 is larger than the Range window so the sampled window moves inside the object.
        cls.objects = {row: (b'ID3 SYNTHETIC PUBLICATION TEST, NOT MUSIC %d ' % row) * (3000 if row == 0 else 1 + row % 7)
                       for row in range(cls.release.count)}

    def setUp(self):
        self.published = Published(self.release, self.objects)
        self.origin = Origin({verifier.object_url(ORIGIN, PREFIX, digest): self.objects[row]
                              for row, _, _, digest in self.published.audio_pins()})

    def temp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def run_verifier(self, **options):
        return verifier.verify(self.published, origin=ORIGIN, prefix=PREFIX, session_factory=self.origin.session, **options)

    def url(self, row):
        return verifier.object_url(ORIGIN, PREFIX, self.published.audio_pins()[row][3])

    def test_the_anonymous_session_never_stores_or_sends_a_cookie(self):
        # A real HTTP exchange (the fake origin above never sets a cookie): the server sets one on every reply.
        import http.server
        import threading
        import requests
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Cookie'))
                self.send_response(200)
                self.send_header('Set-Cookie', 'tracker=1; Path=/')
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_address[1]}/a.mp3'
        for factory, expected in [(requests.Session, 'tracker=1'), (verifier.anonymous_session, None)]:
            seen.clear()
            with self.subTest(factory=factory.__name__), factory() as session:
                for _ in range(2):
                    session.get(url, timeout=10).close()
                # A plain session sends the cookie back on the second request; the verifier's never does.
                self.assertEqual(seen, [None, expected])
                self.assertEqual(len(session.cookies), 0 if expected is None else 1)

    def test_the_real_fixture_rights_rows_all_meet_the_publication_rule(self):
        problems = [verifier.rights_problems(self.release, *pin) for pin in self.release.audio_pins()]
        self.assertEqual([p for p in problems if p], [])

    def test_every_object_and_rights_row_is_checked_before_the_manifest_is_written(self):
        record, report = self.run_verifier(range_every=50)
        self.assertIsNotNone(record, report['failures'][:3])
        self.assertEqual((report['listed'], report['passed'], report['failed'], report['rangeChecks']), (V1_COUNT, V1_COUNT, 0, 41))
        self.assertTrue(all('Authorization' not in h and 'Cookie' not in h for _, h in self.origin.calls))
        self.assertEqual(sum('Range' in h for _, h in self.origin.calls), 41)
        output = verifier.publish(record, self.published, self.temp() / 'audio-delivery.remote.json')
        written = json.loads(output.read_text())
        self.assertEqual((written['mode'], written['origin'], written['pathPrefix'], written['publicDeliveryVerified']),
                         ('remote', ORIGIN, PREFIX, True))
        self.assertEqual(written['verification']['filesFetched'], V1_COUNT)
        delivery = AudioDeliveryV2(output, self.published, enabled=True)
        self.assertEqual((delivery.manifest['mode'], delivery.manifest['available'], delivery.paths), ('remote', V1_COUNT, {}))
        with self.assertRaisesRegex(ReleaseError, 'exists'):
            verifier.publish(record, self.published, output)

    def test_each_publication_fault_blocks_the_manifest(self):
        flip = lambda d: d[:-1] + bytes([d[-1] ^ 1])  # noqa: E731
        ok = lambda d, length=None, kind='audio/mpeg', **h: {'Content-Type': kind, 'Content-Length': str(len(d) if length is None else length), **h}  # noqa: E731
        whole_only = lambda d, h: Response(200, d, ok(d)) if 'Range' in h else None  # noqa: E731
        cases = [(7, lambda d, h: Response(404), 'HTTP 404'),
                 (7, lambda d, h: Response(302, b'', {'Location': 'https://elsewhere.example/a.mp3'}), 'redirects are not allowed'),
                 (7, lambda d, h: Response(200, d, ok(d, kind='text/html')), 'Content-Type is text/html'),
                 (7, lambda d, h: Response(200, d, ok(d, length=len(d) + 1)), 'Content-Length'),
                 (7, lambda d, h: Response(200, flip(d), ok(d)), 'SHA-256 differs'),
                 (7, lambda d, h: Response(200, d[:-3], ok(d)), 'body has'),
                 (7, lambda d, h: Response(200, d + b'x', ok(d)), 'longer than the pinned length'),
                 (7, lambda d, h: Response(200, d, ok(d, **{'Content-Encoding': 'gzip'})), 'encoded response'),
                 (7, lambda d, h: ConnectionError('reset'), 'transfer failed'),
                 (50, whole_only, 'not 206'),
                 (50, lambda d, h: Response(206, d, {**ok(d), 'Content-Range': 'bytes 0-0/1'}) if 'Range' in h else None, 'wrong Content-Range'),
                 (0, lambda d, h: Response(206, b'x' * 65536, {**ok(d), 'Content-Range': h['Range'].replace('=', ' ') + f'/{len(d)}'})
                  if 'Range' in h else None, 'bytes differ')]
        for row, fault, message in cases:
            self.setUp()
            self.origin.faults[self.url(row)] = fault
            record, report = self.run_verifier(range_every=50)
            with self.subTest(message=message):
                self.assertIsNone(record)
                self.assertEqual([f['row'] for f in report['failures']], [row])
                self.assertIn(message, ' '.join(report['failures'][0]['problems']))

    def test_rights_rows_gate_publication_without_fetching(self):
        self.published.overrides = {3: ({}, {'publicPlaybackDecision': 'withheld'}),
                                    4: ({'license': 'CC-BY-NC-4.0'}, {}),
                                    5: ({}, {'decision': 'pending'}),
                                    6: ({}, {'licenseUrl': 'http://creativecommons.org/licenses/by/4.0/'})}
        record, report = self.run_verifier()
        self.assertIsNone(record)
        problems = {f['row']: ' '.join(f['problems']) for f in report['failures']}
        self.assertEqual(sorted(problems), [3, 4, 5, 6])
        self.assertIn('public playback is not approved', problems[3])
        self.assertIn('admitted CC class', problems[4])
        self.assertIn('review is not approved', problems[5])
        self.assertIn('rights licence differs', problems[6])
        fetched = {url for url, _ in self.origin.calls}
        self.assertFalse(any(self.url(row) in fetched for row in (3, 4, 5, 6)))

    def test_a_broken_origin_stops_early_and_a_subset_publishes_only_its_rows(self):
        self.origin.objects = {}
        record, report = self.run_verifier(max_failures=5, workers=1)
        self.assertIsNone(record)
        self.assertTrue(report['stoppedEarly'])
        self.assertEqual(report['failed'], 5)
        self.setUp()
        subset = list(self.release.ordered_ids[100:110])
        record, report = self.run_verifier(subset=subset)
        self.assertEqual([row['id'] for row in record['tracks']], subset)
        output = verifier.publish(record, self.published, self.temp() / 'subset.json')
        self.assertEqual(AudioDeliveryV2(output, self.published, enabled=True).manifest['available'], 10)
        for origin, prefix in [('http://audio.example', PREFIX), (ORIGIN + '/x', PREFIX), (ORIGIN, 'fma/'), (ORIGIN, '/../')]:
            with self.subTest(origin=origin, prefix=prefix), self.assertRaises(ReleaseError):
                verifier.verify(self.published, origin=origin, prefix=prefix, session_factory=self.origin.session)


if __name__ == '__main__':
    unittest.main()
