#!/usr/bin/env python3
"""Write the track credits page (/notices/track-attribution.html) for a validated release.

This is the credits phase that produced the committed page (prepare-local2000.py --phase credits,
generalised in corpus20k/build_web.py), now kept in the repository: one article per catalog row in
catalog order, with the source and licence links, the attribution line, the modification statement
and the supplied notices. With an empty quarantine list it reproduces the page committed for the
original 2,000-track release byte for byte. It refuses a release that still holds a quarantined
recording, and the page then says that quarantined recordings are excluded.

build_corpus_web.py --credits copies the written page to notices/ and web/notices/.
"""
import argparse
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import rights_quarantine  # noqa: E402
from corpus_release import ReleaseLimits, require, validate_release  # noqa: E402

LEGACY_EXCLUSION = 'Conflicted legacy recording 30702 is excluded.'
QUARANTINE_EXCLUSION = ('Conflicted legacy recording 30702 is excluded, and so are the recordings on the rights '
                        'quarantine list (removed or held after a rights review).')


def render(release, quarantine):
    """The page bytes for a validated release."""
    tracks = json.loads(release.assets['catalog'])['tracks']
    count = release.count
    rights_quarantine.require_clear([track['id'] for track in tracks], [track['audioSha256'] for track in tracks],
                                    quarantine, 'The credited release')
    exclusions = QUARANTINE_EXCLUSION if len(quarantine) else LEGACY_EXCLUSION
    esc = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    text = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
            f'<title>Music Discovery Atlas: {count:,} track credits</title><style>body{{max-width:75ch;margin:2rem auto;padding:0 1rem;font:16px/1.6 system-ui}}article{{border-top:1px solid #bbb;padding:1rem 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style>',
            f'<h1>Track credits and licenses</h1><p>{count:,} screened FMA recordings. Source metadata and license notices are reproduced for attribution. This collection does not imply endorsement. {exclusions}</p>']
    for track in tracks:
        text.append('<article id="' + esc(track['id'].replace(':', '-')) + '"><h2>' + esc(track['title']) + '</h2><p>' + esc(track['artist']) + '</p>')
        for label, value in [('Source', track['sourceUrl']), ('License', track['licenseUrl'])]:
            require(value.startswith('https://') or value.startswith('http://'), 'unexpected URL scheme')
            text.append('<p>' + label + ': <a href="' + esc(value) + '">' + esc(value) + '</a></p>')
        for label, value in [('Attribution', track['attribution']), ('Modifications', track['modifications']), ('Supplied notices', track['suppliedNotices'])]:
            rendered = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)
            text.append('<h3>' + label + '</h3><pre>' + esc(rendered) + '</pre>')
        text.append('</article>')
    return ('\n'.join(text) + '\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True, help='where to write the page (then build_corpus_web.py --credits)')
    parser.add_argument('--core-byte-budget', type=int, default=16_000_000)
    parser.add_argument('--evidence-byte-budget', type=int, default=8_000_000)
    parser.add_argument('--quarantine', type=Path, default=ROOT / rights_quarantine.LIST)
    args = parser.parse_args()
    release = validate_release(args.release_dir, expected_manifest_sha256=args.expected_manifest_sha256,
                               limits=ReleaseLimits(max_tracks=args.count, core_bytes=args.core_byte_budget,
                                                    evidence_bytes=args.evidence_byte_budget))
    require(release.count == args.count, 'The release does not hold --count recordings')
    data = render(release, rights_quarantine.load(args.quarantine))
    args.output.write_bytes(data)
    print(json.dumps({'count': release.count, 'bytes': len(data), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
