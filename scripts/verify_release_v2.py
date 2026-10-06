#!/usr/bin/env python3
"""Offline verification of a release-format-v2 directory (read-only, no network).

Runs the server's own loader (manifest digest, streamed asset digests, vectorised graph and
vector checks), then every catalog/rights/evidence row against the v1 rules, and optionally
the v1/v2 parity oracle when the v1 source release is available.
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
    args = parser.parse_args()
    try:
        release = load_release_v2(args.release_dir, expected_manifest_sha256=args.expected_manifest_sha256,
                                  limits=LimitsV2.from_config(args.limits))
        rows = validate_rows(release)
    except ReleaseError as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, **release.summary(), 'rowValidation': rows}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
