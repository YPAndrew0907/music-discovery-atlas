#!/usr/bin/env python3
"""Pin a rebuilt v1 release in this tree: the "pins" phase after the release, credits and web builds.

Writes, fail-closed and only where something changed:
  active-corpus.json           the v1 selection: directory, reviewed manifest digest, maxTracks and budgets
  web/search-studio/index.html the static count texts (#about-count, [data-catalog-count], #collection-summary)
  web-manifest.json            every listed web file re-hashed
  package-manifest.json        every listed file re-hashed in place; rows of files deleted from the
                               rebuilt release directory dropped, rows of new release files appended
  .gitignore / .dockerignore   the release's per-file evidence allowances follow the files on disk

The release is validated with its reviewed digest and must not hold a quarantined recording. It never
downloads, pushes or deploys. --check writes nothing and exits 1 when the tree is not pinned to the
release (the pre-commit gate). The model weight fetched during the image build keeps its row as is.
This is the generalised prepare-local2000.py pins phase (see corpus20k/build_web.py), now in the repo.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
import rights_quarantine  # noqa: E402
from corpus_release import ReleaseLimits, require, validate_release  # noqa: E402

PAGE = 'web/search-studio/index.html'
EVIDENCE_LINE = re.compile(r'!/?corpus-releases/(?P<name>[A-Za-z0-9_-]+)/evidence/(?P<file>[^/]+\.json)\Z')


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def page_counts(page, count, artists, genres):
    """The three static count texts of the search page, each required to occur exactly once."""
    for pattern, text in [
            (r'<strong id="about-count">[\d,]+ recordings</strong>', f'<strong id="about-count">{count:,} recordings</strong>'),
            (r'<span data-catalog-count>[\d,]+</span>', f'<span data-catalog-count>{count:,}</span>'),
            (r'<p id="collection-summary" class="fine">[\d,]+ FMA excerpts · [\d,]+ source artist IDs · \d+ source genres</p>',
             f'<p id="collection-summary" class="fine">{count:,} FMA excerpts · {artists:,} source artist IDs · {genres} source genres</p>')]:
        page, found = re.subn(pattern, text, page)
        require(found == 1, 'The search page count text is not where the pins step expects it: ' + pattern)
    return page


def repinned_rows(root, rows, base='', deletable=()):
    """Rows re-hashed in place; a row may only disappear when its file is a deleted release file."""
    weight = json.loads((root / 'runtime-assets.json').read_text())['weights'][0]['path']
    result = []
    for row in rows:
        path = root / base / row['path']
        if not path.exists() and row['path'] in deletable:
            continue
        if base == '' and row['path'] == weight and not path.exists():  # fetched during the image build
            result.append(row)
            continue
        require(path.is_file() and not path.is_symlink(), 'A pinned file is missing: ' + row['path'])
        data = path.read_bytes()
        result.append({**row, 'bytes': len(data), 'sha256': sha256(data)})
    return result


def allowlist(text, name, evidence_files):
    """The release's per-file evidence allowances, matching the evidence files on disk."""
    lines = text.splitlines()
    styles = {match.group(0).split('corpus-releases/')[0] for line in lines if (match := EVIDENCE_LINE.match(line))
              and match.group('name') == name}
    require(len(styles) == 1, 'Expected one existing evidence allowance style for ' + name)
    prefix = styles.pop() + 'corpus-releases/' + name + '/evidence/'
    kept = [line for line in lines if not ((match := EVIDENCE_LINE.match(line)) and match.group('name') == name
                                           and match.group('file') not in evidence_files)]
    present = {line[len(prefix):] for line in kept if line.startswith(prefix)}
    return '\n'.join(kept + [prefix + file for file in sorted(evidence_files - present)]) + '\n'


def pin(root, name, manifest_sha256, *, count, core_bytes=16_000_000, evidence_bytes=8_000_000, quarantine=None, write=True):
    """{relative path: new bytes} for every file that changes; written unless write is False."""
    root = Path(root)
    quarantine = rights_quarantine.load() if quarantine is None else quarantine
    release_dir = root / 'corpus-releases' / name
    release = validate_release(release_dir, expected_manifest_sha256=manifest_sha256,
                               limits=ReleaseLimits(max_tracks=count, core_bytes=core_bytes, evidence_bytes=evidence_bytes))
    require(release.count == count, 'The release does not hold --count recordings')
    tracks = json.loads(release.assets['catalog'])['tracks']
    rights_quarantine.require_clear(release.ordered_ids, [track['audioSha256'] for track in tracks], quarantine, 'The pinned release')
    receipt = json.loads((release_dir / 'build-receipt.json').read_bytes())
    require(receipt.get('releaseManifestSha256') == manifest_sha256 and receipt.get('count') == count,
            'build-receipt.json belongs to another build')
    changes = {}

    def stage(relative, data):
        if (root / relative).read_bytes() != data:
            changes[relative] = data

    config = json.loads((root / 'active-corpus.json').read_bytes())
    require(config.get('schemaVersion') == 1 and config.get('audioArchive') is None,
            'Only a v1 selection without an audio archive is pinned here')
    config.update(enabled=True, directory='corpus-releases/' + name, manifestSha256=manifest_sha256, maxTracks=count,
                  coreByteBudget=core_bytes, evidenceByteBudget=evidence_bytes)
    stage('active-corpus.json', encode(config))
    page = (root / PAGE).read_text()
    stage(PAGE, page_counts(page, count, receipt['artists'], len(receipt['genres'])).encode())
    # The manifests below hash staged bytes where a file changes, so --check and a write agree.
    web = json.loads((root / 'web-manifest.json').read_bytes())
    staged_web = {relative.removeprefix('web/'): data for relative, data in changes.items() if relative.startswith('web/')}
    rows = []
    for row in repinned_rows(root, web['files'], 'web'):
        data = staged_web.get(row['path'])
        rows.append(row if data is None else {**row, 'bytes': len(data), 'sha256': sha256(data)})
    stage('web-manifest.json', encode({**web, 'files': rows}))
    package = json.loads((root / 'package-manifest.json').read_bytes())
    prefix = f'corpus-releases/{name}/'
    on_disk = {path.relative_to(root).as_posix() for path in release_dir.rglob('*') if path.is_file()}
    listed = {row['path'] for row in package['files']}
    deleted = {path for path in listed if path.startswith(prefix) and path not in on_disk}
    rows = []
    for row in repinned_rows(root, package['files'], deletable=deleted):
        data = changes.get(row['path'])
        rows.append(row if data is None else {**row, 'bytes': len(data), 'sha256': sha256(data)})
    for path in sorted(on_disk - listed):
        data = (root / path).read_bytes()
        rows.append({'path': path, 'bytes': len(data), 'sha256': sha256(data)})
    stage('package-manifest.json', encode({**package, 'files': rows}))
    evidence_files = {path.name for path in (release_dir / 'evidence').iterdir() if path.is_file()}
    for ignore in ('.gitignore', '.dockerignore'):
        stage(ignore, allowlist((root / ignore).read_text(), name, evidence_files).encode())
    if write:
        for relative, data in changes.items():
            (root / relative).write_bytes(data)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--name', default='fma2000', help='release directory name under corpus-releases/')
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--core-byte-budget', type=int, default=16_000_000)
    parser.add_argument('--evidence-byte-budget', type=int, default=8_000_000)
    parser.add_argument('--quarantine', type=Path, default=ROOT / rights_quarantine.LIST)
    parser.add_argument('--check', action='store_true', help='write nothing; exit 1 if anything would change')
    args = parser.parse_args()
    changes = pin(ROOT, args.name, args.expected_manifest_sha256, count=args.count, core_bytes=args.core_byte_budget,
                  evidence_bytes=args.evidence_byte_budget, quarantine=rights_quarantine.load(args.quarantine),
                  write=not args.check)
    print(json.dumps({'check': args.check, 'changed': sorted(changes)}, indent=2))
    return 1 if args.check and changes else 0


if __name__ == '__main__':
    raise SystemExit(main())
