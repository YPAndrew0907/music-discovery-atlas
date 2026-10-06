#!/usr/bin/env python3
"""Derive the build-time audio plan (audio-hydration.json) for a release rebuilt from the reviewed one.

Nothing about a recording's official source is recomputed: each entry (archive, ZIP member path, local
header offset, compressed size, CRC-32, range length, audio and evidence pins) is copied unchanged from
the reviewed plan, in the new release's order. Only the corpus identity and the bindings (catalog, ids,
graph manifest, rights, release.json) are rebound to the new release. The result must pass
hydrate_corpus_audio.validate_plan, the check the image build runs, before anything is written, and the
release must not hold a quarantined recording.

The reviewed plan is read through its pin (PLAN_SHA in hydrate_corpus_audio.py). After a rebuild the
three reviewed constants there (PLAN_SHA, RELEASE_SHA, RELEASE_COUNT) are updated by hand to the values
this prints; docs/RIGHTS_QUARANTINE.md lists the steps.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import hydrate_corpus_audio as hydration  # noqa: E402
import rights_quarantine  # noqa: E402
from corpus_release import ReleaseLimits, object_sha, require, strict_json, validate_release  # noqa: E402


def encode(plan):
    return (json.dumps(plan, separators=(',', ':'), sort_keys=True) + '\n').encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def derive(reviewed_plan, release_dir, manifest_sha256, *, count, quarantine, limits=None):
    """(plan bytes, validated entries) for the release, from the reviewed plan's bytes."""
    require(sha256(reviewed_plan) == hydration.PLAN_SHA, 'The source plan is not the reviewed plan (PLAN_SHA)')
    source = strict_json(reviewed_plan, 'hydration plan', 1_500_000)
    release_dir = Path(release_dir)
    release = validate_release(release_dir, expected_manifest_sha256=manifest_sha256,
                               limits=limits or ReleaseLimits(max_tracks=count))
    require(release.count == count, 'The release does not hold --count recordings')
    tracks = json.loads(release.assets['catalog'])['tracks']
    rights_quarantine.require_clear(release.ordered_ids, [track['audioSha256'] for track in tracks], quarantine,
                                    'The release to hydrate')
    entries = {entry['id']: entry for entry in source['entries']}
    require(all(ident in entries for ident in release.ordered_ids),
            'The release holds a recording the reviewed plan does not; its source range needs its own review')
    def pin(name, data):
        return {'path': name, 'bytes': len(data), 'sha256': sha256(data)}
    plan = {**source, 'catalogId': release.catalog_id, 'count': release.count,
            'orderedIdsSha256': object_sha(list(release.ordered_ids)),
            'bindings': {'catalog.json': pin('catalog.json', release.assets['catalog']),
                         'ids.json': pin('ids.json', release.assets['ids']),
                         'manifest.json': pin('manifest.json', release.assets['graphManifest']),
                         'rights.json': pin('rights.json', release.assets['rights']),
                         'release.json': pin('release.json', (release_dir / 'release.json').read_bytes())},
            'entries': [entries[ident] for ident in release.ordered_ids]}
    data = encode(plan)
    validated = hydration.validate_plan(strict_json(data, 'hydration plan', 1_500_000),
                                        SimpleNamespace(release=release, directory=release_dir))
    return data, validated


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--release-dir', type=Path, default=ROOT / 'corpus-releases/fma2000')
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--reviewed-plan', type=Path, default=ROOT / hydration.PLAN_NAME)
    parser.add_argument('--output', type=Path, default=ROOT / hydration.PLAN_NAME)
    parser.add_argument('--quarantine', type=Path, default=ROOT / rights_quarantine.LIST)
    args = parser.parse_args()
    data, entries = derive(args.reviewed_plan.read_bytes(), args.release_dir, args.expected_manifest_sha256,
                           count=args.count, quarantine=rights_quarantine.load(args.quarantine))
    args.output.write_bytes(data)
    print(json.dumps({'output': str(args.output), 'entries': len(entries),
                      'rangeBytes': sum(entry.range_bytes for entry in entries),
                      'pinsForHydrateCorpusAudio': {'PLAN_SHA': sha256(data), 'RELEASE_SHA': args.expected_manifest_sha256,
                                                    'RELEASE_COUNT': len(entries)}}, indent=2))


if __name__ == '__main__':
    main()
