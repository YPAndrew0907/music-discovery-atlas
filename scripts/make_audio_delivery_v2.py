#!/usr/bin/env python3
"""Write a v2 audio delivery manifest bound to one verified v2 release (no network).

local:  every listed file in --audio-dir is hashed here, at build/publish time, against the
        catalog pins; the server then checks only inventory and sizes at startup and hashes
        each file again on its first request.
remote: rows point at content-addressed objects (<origin><prefix><sha256>.mp3). The operator's
        publish step must have uploaded exactly these bytes; --audio-dir, when given, hashes the
        local copy that was uploaded. Fetching the public URLs is a separate acceptance check.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from collection_v2 import DELIVERY_KIND, exact_https_origin, local_route  # noqa: E402
from corpus_release import require  # noqa: E402
from release_v2 import LimitsV2, load_release_v2  # noqa: E402


def build(release, *, mode, audio_dir=None, origin=None, prefix=None, subset=None):
    pins = release.audio_pins()
    wanted = None if subset is None else set(subset)
    rows, total, hashed = [], 0, 0
    for row, ident, size, digest in pins:
        if wanted is not None and ident not in wanted:
            continue
        if audio_dir is not None:
            path = Path(audio_dir) / local_route(ident).rsplit('/', 1)[1]
            require(path.is_file() and not path.is_symlink() and path.stat().st_size == size, 'Missing audio: ' + ident)
            with path.open('rb') as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == digest, 'Audio hash mismatch: ' + ident)
            hashed += 1
        url = local_route(ident) if mode == 'local' else origin + prefix + digest + '.mp3'
        rows.append({'id': ident, 'available': True, 'url': url, 'bytes': size, 'sha256': digest})
        total += size
    if mode == 'local':
        require(audio_dir is not None and hashed == len(rows), 'Local delivery requires hashing every listed file')
    return {'schemaVersion': 2, 'kind': DELIVERY_KIND, 'catalogId': release.catalog_id,
            'catalogSha256': release.catalog_sha256, 'releaseSha256': release.manifest_sha256,
            'enabled': True, 'publicDeliveryVerified': True, 'mode': mode,
            **({'origin': origin, 'pathPrefix': prefix} if mode == 'remote' else {}),
            'verification': {'method': 'sha256 of every listed file at build time' if hashed else
                             'content-addressed keys; operator publish-time verifier required',
                             'filesHashed': hashed, 'bytes': total,
                             'verifiedAt': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
            'tracks': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--mode', choices=('local', 'remote'), required=True)
    parser.add_argument('--audio-dir', type=Path)
    parser.add_argument('--origin')
    parser.add_argument('--prefix', default='/')
    parser.add_argument('--limits', type=json.loads, default=None)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    release = load_release_v2(args.release_dir, expected_manifest_sha256=args.expected_manifest_sha256,
                              limits=LimitsV2.from_config(args.limits))
    origin = exact_https_origin(args.origin) if args.mode == 'remote' else None
    record = build(release, mode=args.mode, audio_dir=args.audio_dir, origin=origin, prefix=args.prefix)
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'tracks': len(record['tracks']), **record['verification']}))


if __name__ == '__main__':
    main()
