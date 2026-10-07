"""Derive corpus-releases/serving.json, the request-time serving list, from a rights decision's receipts.

The list partitions every recording of one release into served and held rows (server/serving.py reads it).
A row is served only when all three hold:
1. **The decision's bucket.** The row is in the keep bucket of the decision's row lists.
2. **The review's triggers.** It is not one of the review's hold-trigger rows.
3. **The embedded tags.** Its MP3 carries no embedded licence notice naming NC, ND or SA. Such a notice is
   held until a dated Wayback or Internet Archive check clears it.

Every held row records why, with codes the file defines. The inputs live in the project workspace, not in
this repository; their SHA-256 digests go into the receipt.

Usage (the 2026-10-06 decision):
  python scripts/derive_serving_list.py --release-dir corpus-releases/fma2000 \\
    --buckets $W/rights/adjudication/live2000_buckets_2026-10-06.json \\
    --tags $W/corpus200k/ingest/receipts/fma5777-embedded-tag-scan.json \\
    --review-holds $W/validation/v2-integrate/serving/review_holds_2026-10-06.json \\
    --output corpus-releases/serving.json --receipt <receipt.json>
--check compares the output with the file already there instead of writing it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import ReleaseError, require  # noqa: E402
from serving import KIND, ServingList  # noqa: E402

KEEP = 'A.16'
DECISION = ('Rights decision of 2026-10-06 (rights/RIGHTS_DECISION_2026-10-06.md, section 7.1 and appendix A) as '
            'applied by the cautious-counsel review of the same day (rights/SKEPTIC_REVIEW_2026-10-06.md, findings F2 '
            'and F3 and its count audit): serve a recording only if the decision keeps it, the review names no hold '
            'trigger for it, and its MP3 carries no embedded licence notice naming NC, ND or SA')
BUCKETS = {
    'A.1': 'Decision A.1: remove now; audio bound to the wrong row, or bytes identical to a row under another licence',
    'A.2': 'Decision A.2: created before 2013-11-25 but labelled CC BY 4.0, with no 2011-13 evidence of BY',
    'A.3': "Decision A.3: no longer listed in FMA's own sitemap",
    'A.4': "Decision A.4: the Internet Archive's copy of the album records BY-NC or the FMA licence; hand check",
    'A.5': "Decision A.5: the licensor's FMA page shows CC BY-NC today",
    'A.6': 'Decision A.6: CC0 applied by a radio-station curator to third-party records; disabled',
    'A.7': 'Decision A.7: contest entry on a pre-existing composition',
    'A.8': 'Decision A.8: titles that name songs which may be third-party compositions',
    'A.9': "Decision A.9: titles that indicate a cover, remix, feature or 'vs'; manual check",
    'A.10': 'Decision A.10: a WFMU-recorded live session; manual check',
    'A.11': 'Decision A.11: a third-party publisher or another composer named in the supplied notices',
    'A.12': "Decision A.12: CC0 uploaded through a curator folder rather than the artist's own account; authority check",
    'A.13': "Decision A.13: CC0 in the ccCommunity and no_curator folders, whose artist-upload status no note establishes",
    'A.14': 'Decision A.14: supplied notes describe samples or fragments of other material; manual check',
    'A.15': 'Decision A.15: a featured performer or a third-party edit in the title or artist field; manual check',
}
TAGS = {'NC': 'TAG.NC', 'ND': 'TAG.ND', 'SA': 'TAG.SA'}
TAG_TEXT = {
    'TAG.NC': 'The MP3 carries an FMA-written licence notice naming NonCommercial terms; held until a dated Wayback or Internet Archive check clears it (review F2)',
    'TAG.ND': 'The MP3 carries an FMA-written licence notice naming NoDerivatives terms; held until a dated Wayback or Internet Archive check clears it (review F2)',
    'TAG.SA': 'The MP3 carries an FMA-written licence notice naming ShareAlike terms; held until a dated Wayback or Internet Archive check clears it (review F2)',
}
SERVE = ("In the decision's keep set (A.16), clear of the review's hold triggers, and the MP3 carries no embedded "
         'licence notice naming NC, ND or SA')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fma(value):
    return value if isinstance(value, str) else 'fma:%d' % value


def derive(catalog, buckets, tags, review):
    ids = [track['id'] for track in catalog['tracks']]
    licence = {track['id']: track['license'] for track in catalog['tracks']}
    bucket_of = {}
    for code, members in buckets['buckets'].items():
        for member in members:
            ident = fma(member)
            require(ident not in bucket_of, 'A row is in two decision buckets: ' + ident)
            bucket_of[ident] = code
    require(all(ident in bucket_of for ident in ids), 'A catalog row is in no decision bucket')
    require(set(bucket_of[ident] for ident in ids) <= set(BUCKETS) | {KEEP}, 'Unknown decision bucket')
    notices = {row['id']: row['noticeKinds'] for row in tags['rows']}
    review_of = {}
    for code, category in review['categories'].items():
        require(code.startswith('R.'), 'Review codes start with R.')
        for member in category['ids']:
            review_of.setdefault(fma(member), []).append(code)
    for ident in review_of:
        require(bucket_of.get(ident) == KEEP, 'A review hold is not in the keep set: ' + ident)
    reasons = {'SERVE': SERVE, **BUCKETS, **{code: category['text'] + ' (' + category['rule'] + ')'
                                             for code, category in review['categories'].items()}, **TAG_TEXT}
    rows = []
    for ident in ids:
        bucket = bucket_of[ident]
        if bucket != KEEP:
            rows.append({'id': ident, 'serve': False, 'reasons': [bucket]})
            continue
        held = list(review_of.get(ident, []))
        kinds = notices.get(ident, [])
        require(all(kind in ('NC', 'ND', 'SA') or set(kind.split('+')) <= {'NC', 'ND', 'SA'} for kind in kinds),
                'Unknown embedded notice kind for ' + ident)
        for part in sorted({p for kind in kinds for p in kind.split('+')}):
            held.append(TAGS[part])
        rows.append({'id': ident, 'serve': not held, 'reasons': held or ['SERVE']})
    document = {'schemaVersion': 1, 'kind': KIND, 'catalogId': catalog['id'], 'decision': DECISION,
                'reasons': reasons, 'rows': rows}
    served = [row['id'] for row in rows if row['serve']]
    by_licence = {}
    for ident in served:
        by_licence[licence[ident]] = by_licence.get(licence[ident], 0) + 1
    held_by = {}
    for row in rows:
        if not row['serve']:
            for code in row['reasons']:
                held_by[code] = held_by.get(code, 0) + 1
    counts = {'rows': len(rows), 'served': len(served), 'held': len(rows) - len(served),
              'servedByLicence': dict(sorted(by_licence.items())), 'heldRowsByReason': dict(sorted(held_by.items())),
              'keepSet': sum(1 for ident in ids if bucket_of[ident] == KEEP),
              'keepSetHeldByReview': sum(1 for ident in ids if bucket_of[ident] == KEEP and ident in review_of),
              'keepSetHeldByTags': sum(1 for ident in ids if bucket_of[ident] == KEEP and notices.get(ident)),
              'keepSetHeldByBoth': sum(1 for ident in ids if bucket_of[ident] == KEEP and ident in review_of
                                       and notices.get(ident))}
    return document, counts


def encode(document):
    """One row per line, so a reviewed edit of one row is a one-line diff."""
    head = {key: document[key] for key in ('schemaVersion', 'kind', 'catalogId', 'decision', 'reasons')}
    lines = json.dumps(head, indent=2, ensure_ascii=False)[:-2] + ',\n  "rows": [\n'
    lines += ',\n'.join('    ' + json.dumps(row, ensure_ascii=False, separators=(', ', ': ')) for row in document['rows'])
    return (lines + '\n  ]\n}\n').encode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--release-dir', required=True)
    parser.add_argument('--buckets', required=True)
    parser.add_argument('--tags', required=True)
    parser.add_argument('--review-holds', required=True)
    parser.add_argument('--output', default=str(ROOT / 'corpus-releases/serving.json'))
    parser.add_argument('--receipt')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    release = Path(args.release_dir)
    catalog = json.loads((release / 'catalog.json').read_bytes())
    document, counts = derive(catalog, json.loads(Path(args.buckets).read_bytes()),
                              json.loads(Path(args.tags).read_bytes()), json.loads(Path(args.review_holds).read_bytes()))
    data = encode(document)
    ServingList(data, catalog_id=catalog['id'], ordered_ids=[t['id'] for t in catalog['tracks']])  # the server's own check
    output = Path(args.output)
    receipt = {'catalogId': catalog['id'], 'releaseSha256': digest(release / 'release.json'), 'output': str(output),
               'outputSha256': hashlib.sha256(data).hexdigest(), 'counts': counts,
               'inputs': {name: {'path': str(Path(value).resolve()), 'sha256': digest(value)}
                          for name, value in (('buckets', args.buckets), ('tags', args.tags),
                                              ('reviewHolds', args.review_holds))}}
    if args.check:
        require(output.is_file() and output.read_bytes() == data, 'The serving list differs from its derivation')
        receipt['check'] = 'identical'
    else:
        output.write_bytes(data)
    if args.receipt:
        Path(args.receipt).write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'sha256': receipt['outputSha256'], **counts}))


if __name__ == '__main__':
    try:
        main()
    except ReleaseError as error:
        raise SystemExit(str(error)) from None
