"""Shared release-format-v2 fixtures built from the checked-in fma2000 release.

The conversion runs the real converter (with a small oracle sample) and is cached under the
system temporary directory, keyed by the source release and the v2 code, so repeated test
runs reuse it. Nothing here touches the repository's own files.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'scripts'))

V1_DIR = ROOT / 'corpus-releases/fma2000'
V1_SHA = 'af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283'
CODE = ['server/release_v2.py', 'server/search_v2.py', 'server/corpus_release.py', 'server/hnsw_trace.py',
        'scripts/convert_release_v1_to_v2.py', 'scripts/upgrade_release_v2.py', 'scripts/build_web_v2.py']


def code_key():
    digest = hashlib.sha256(V1_SHA.encode())
    for name in CODE:
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()[:20]


def cache_root():
    root = Path(tempfile.gettempdir()).resolve() / 'music-v2-test-cache' / code_key()
    root.mkdir(parents=True, exist_ok=True)
    return root


def converted_fma2000(*, lookup_index=True):
    """(directory, manifest sha) of fma2000 converted by the real converter: release format 2.1 by
    default, 2.0 (no lookup index) with lookup_index=False."""
    from convert_release_v1_to_v2 import convert
    from corpus_release import ReleaseLimits
    output = cache_root() / ('fma2000-v2' if lookup_index else 'fma2000-v2.0')
    if not (output / 'release.json').is_file():
        if output.exists():
            shutil.rmtree(output)
        convert(V1_DIR, V1_SHA, output, v1_limits=ReleaseLimits(max_tracks=2000), samples=8, lookup_index=lookup_index)
    return output, hashlib.sha256((output / 'release.json').read_bytes()).hexdigest()


def web_manifest_for(web_root):
    files = []
    for path in sorted(p for p in Path(web_root).rglob('*') if p.is_file() and not p.name.startswith('.')):
        data = path.read_bytes()
        files.append({'path': path.relative_to(web_root).as_posix(), 'bytes': len(data),
                      'sha256': hashlib.sha256(data).hexdigest()})
    return {'schemaVersion': 1, 'kind': 'music-public-web-assets', 'files': files}


def v2_web_root(destination, release_dir, manifest_sha, **options):
    """A copy of web/ whose collection data is rebuilt for the v2 release, plus its manifest."""
    from build_web_v2 import build
    destination = Path(destination)
    shutil.copytree(ROOT / 'web', destination / 'web')
    receipt = build(release_dir, manifest_sha, destination / 'web', **options)
    manifest = destination / 'web-manifest.json'
    manifest.write_text(json.dumps(web_manifest_for(destination / 'web'), indent=2) + '\n')
    return destination / 'web', manifest, receipt


class FixtureEncoder:
    """Deterministic stand-in for NativeEncoder over a v2 release; encode() maps known texts to
    stored vectors so responses can be compared with the v1 server exactly."""

    def __init__(self, release, directory, vectors_by_text=None):
        from encoder import NativeEncoder
        self._manifest = NativeEncoder.manifest
        profile = release.graph_manifest['allowedQueryProfiles'][0]
        self.query_profile, self.engine = profile['identity'], profile['id']
        self.pair = json.loads((ROOT / 'model/model-space-q8.json').read_text())
        self.release_v2, self.release_directory = release, directory
        self.catalog_version, self.catalog_sha = release.catalog_id, release.catalog_sha256
        self.ids, self.vectors = list(release.ordered_ids), release.vectors
        self.audio_receipt = {'vectorsSha256': release.vectors_sha256,
                              'executionProfileSha256': release.graph_manifest['graphIdentity']['audio']['executionProfileSha256']}
        self.vectors_by_text = vectors_by_text or {}

    def manifest(self):
        return self._manifest(self)

    def encode(self, text, control):
        control.check()
        return self.vectors_by_text.get(text, self.vectors[0]), {'originalTokens': 3, 'usedTokens': 3, 'truncated': False,
            'transform': 'test fixture only'}, {'tokenization': 0.0, 'inferenceAndNormalization': 0.0}
