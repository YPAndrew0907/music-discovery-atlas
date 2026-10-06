#!/usr/bin/env python3
"""Offline verification of a release-format-v2 directory (read-only, no network).

Runs the server's own loader (manifest digest, streamed asset digests, vectorised graph and
vector checks), then every catalog/rights/evidence row against the v1 rules. For release format
2.1 it also runs FTS5's integrity check of the lookup index against every tracks row, on a
private copy of catalog.sqlite made in --scratch-dir (the release itself stays read-only).
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ReleaseError  # noqa: E402
from release_v2 import LimitsV2, load_release_v2, validate_rows  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('release_dir', type=Path)
    parser.add_argument('expected_manifest_sha256')
    parser.add_argument('--limits', type=json.loads, default=None, help='JSON object of v2 limits, e.g. {"maxTracks": 200000}')
    parser.add_argument('--scratch-dir', type=Path, default=None,
                        help='where the 2.1 lookup-index check copies catalog.sqlite (default: the system temporary directory)')
    args = parser.parse_args()
    try:
        release = load_release_v2(args.release_dir, expected_manifest_sha256=args.expected_manifest_sha256,
                                  limits=LimitsV2.from_config(args.limits))
        rows = validate_rows(release, scratch_dir=args.scratch_dir)
    except ReleaseError as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, **release.summary(), 'rowValidation': rows}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
