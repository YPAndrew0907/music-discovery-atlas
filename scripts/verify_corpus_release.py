#!/usr/bin/env python3
"""Read-only validation. No model/audio downloads, publishing, or activation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from corpus_release import ReleaseError, ReleaseLimits, validate_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True,
                        help='Digest from a separately reviewed handoff, not automatic candidate approval')
    parser.add_argument('--max-tracks', type=int, default=500)
    parser.add_argument('--core-byte-budget', type=int, default=16_000_000)
    parser.add_argument('--evidence-byte-budget', type=int, default=8_000_000)
    parser.add_argument('--json-byte-budget', type=int, default=8_000_000)
    args = parser.parse_args()
    try:
        release = validate_release(args.release_dir, expected_manifest_sha256=args.expected_manifest_sha256,
            limits=ReleaseLimits(max_tracks=args.max_tracks, core_bytes=args.core_byte_budget,
                                 evidence_bytes=args.evidence_byte_budget, json_bytes=args.json_byte_budget))
    except ReleaseError as error:
        print(json.dumps({'ok': False, 'error': str(error), 'runtimeActivated': False}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, **release.summary()}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
