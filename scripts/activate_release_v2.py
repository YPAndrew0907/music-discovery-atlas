#!/usr/bin/env python3
"""Activate a converted release-format-v2 release in this repository tree (the deploy's one data step).

The deploy plan (docs/DEPLOY_PLAN_V2.md) runs this once, on a clean tree, then reviews and commits
the result. It never pushes, deploys, downloads or touches a running server. Steps, all fail-closed:

1. Verify the converted release completely (scripts/install_release_v2.py verify_directory: exact
   inventory, manifest digest, every asset digest, the server loader, every catalog/rights/evidence row).
2. bundled: copy it to corpus-releases/<name>/ and re-verify the copy (an existing directory must
   already be identical). object-store: copy nothing; the image build fetches it by digest.
3. Write active-corpus.json as a schemaVersion 2 selection with an explicit "source" block.
4. Rebuild the page data for the release (scripts/build_web_v2.py): manifest.json, layout.json,
   examples.json and studio-release.mjs; the whole-catalog v1 page files are removed.
5. Re-pin web-manifest.json and package-manifest.json (changed rows refreshed, deleted rows dropped;
   the release files themselves are pinned by the selection, not the package manifest).
6. bundled: allowlist the release files in .gitignore and .dockerignore (both default-deny).

--check verifies an activated tree without changing it (the pre-push gate in the deploy plan).
Rollback is `git revert` of the commit that holds the result.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'server')]
from build_web_v2 import V1_DATA_FILES, build as build_web  # noqa: E402
from collection_v2 import check_web_release  # noqa: E402
from corpus_release import ReleaseError, require  # noqa: E402
from install_release_v2 import MANIFEST_NAME, checked_directory, verify_directory  # noqa: E402
from release_v2 import FORMAT, LimitsV2, install_source, load_release_v2, selection_config_v2  # noqa: E402

NAME = r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}'
WEB_DATA = 'web/search-studio/data/'
STUDIO = 'web/search-studio/src/studio-release.mjs'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def dump(value):
    return json.dumps(value, indent=2) + '\n'


def write_if_changed(path, text):
    path = Path(path)
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def selection_for(name, manifest_sha256, source, limits):
    return {'schemaVersion': 2, 'enabled': True, 'format': FORMAT, 'directory': 'corpus-releases/' + name,
            'manifestSha256': manifest_sha256, **({'limits': limits} if limits else {}), 'source': dict(source)}


def release_files(directory):
    manifest = json.loads((Path(directory) / MANIFEST_NAME).read_bytes())
    return [MANIFEST_NAME] + sorted(spec['path'] for spec in manifest['assets'].values())


def copy_release(source_dir, target):
    """Copy a verified release; an existing target must already hold exactly the same files."""
    names = release_files(source_dir)
    if target.exists() or target.is_symlink():
        require(target.is_dir() and not target.is_symlink()
                and sorted(p.name for p in target.iterdir()) == sorted(names)
                and all((target / n).read_bytes() == (Path(source_dir) / n).read_bytes() for n in names),
                'corpus-releases holds a different release under this name; refusing to overwrite it')
        return False
    target.mkdir()
    for name in names:
        shutil.copyfile(Path(source_dir) / name, target / name)
    return True


def repin_web(root, removed):
    """web-manifest.json rows: refresh every listed file, drop the removed v1 data files."""
    path = root / 'web-manifest.json'
    manifest = json.loads(path.read_text())
    rows = []
    for row in manifest['files']:
        file = root / 'web' / row['path']
        if ('web/' + row['path']) in removed:
            require(not file.exists(), 'A removed v1 page file is still present: ' + row['path'])
            continue
        data = file.read_bytes()
        rows.append({**row, 'bytes': len(data), 'sha256': sha256(data)})
    listed = {row['path'] for row in rows}
    for name in ('manifest.json', 'layout.json', 'examples.json'):
        require('search-studio/data/' + name in listed, 'The v2 page data is not in the web manifest: ' + name)
    manifest['files'] = rows
    return write_if_changed(path, dump(manifest))


def repin_package(root, removed):
    """package-manifest.json rows: refresh what changed, drop removed files, keep the order."""
    path = root / 'package-manifest.json'
    package = json.loads(path.read_text())
    weight = json.loads((root / 'runtime-assets.json').read_text())['weights'][0]
    rows, refreshed = [], []
    for row in package['files']:
        if row['path'] in removed:
            continue
        file = root / row['path']
        if row['path'] == weight['path'] and not file.exists():  # fetched during the image build
            rows.append(row)
            continue
        data = file.read_bytes()
        if (row['bytes'], row['sha256']) != (len(data), sha256(data)):
            refreshed.append(row['path'])
        rows.append({**row, 'bytes': len(data), 'sha256': sha256(data)})
    package['files'] = rows
    write_if_changed(path, dump(package))
    return refreshed


def allowlist(root, name, files):
    """Default-deny export lists: add the release directory and each file, once."""
    added = []
    for ignore, prefix in (('.gitignore', '!/corpus-releases/'), ('.dockerignore', '!/corpus-releases/')):
        path = root / ignore
        lines = path.read_text().splitlines()
        wanted = [f'{prefix}{name}/'] + [f'{prefix}{name}/{file}' for file in files]
        missing = [line for line in wanted if line not in lines]
        if missing:
            path.write_text('\n'.join(lines + missing) + '\n')
            added += [f'{ignore}: {line}' for line in missing]
    return added


def activate(root, release_dir, manifest_sha256, *, name, source, limits=None, audio_mode='local',
             audio_origin=None, audio_prefix=None):
    root, release_dir = Path(root), Path(release_dir)
    require(re.fullmatch(NAME, name) is not None, 'Invalid release name')
    source = install_source(source)
    limits_v2 = LimitsV2.from_config(limits)
    parsed = SimpleNamespace(manifest_sha256=manifest_sha256, limits=limits_v2)
    verified = verify_directory(release_dir, parsed)
    target = root / 'corpus-releases' / name
    changed = []
    if source['kind'] == 'bundled':
        if copy_release(release_dir, target):
            changed.append(f'corpus-releases/{name}/ (copied)')
        verify_directory(target, parsed)
        web_release = target
    else:
        require(not target.exists() and not target.is_symlink(),
                'An object-store release must not be committed under corpus-releases/')
        web_release = release_dir
    selection = selection_for(name, manifest_sha256, source, limits)
    if write_if_changed(root / 'active-corpus.json', dump(selection)):
        changed.append('active-corpus.json')
    before = {p: (root / p).read_bytes() for p in [WEB_DATA + n for n in ('manifest.json', 'layout.json', 'examples.json')] + [STUDIO]
              if (root / p).exists()}
    web = build_web(web_release, manifest_sha256, root / 'web', audio_mode=audio_mode, audio_origin=audio_origin,
                    audio_prefix=audio_prefix, limits=limits_v2)
    removed = {WEB_DATA + n for n in V1_DATA_FILES}
    for path in [WEB_DATA + n for n in ('manifest.json', 'layout.json', 'examples.json')] + [STUDIO]:
        if before.get(path) != (root / path).read_bytes():
            changed.append(path)
    changed += [WEB_DATA + n + ' (removed)' for n in web['removedV1DataFiles']]
    if repin_web(root, removed):
        changed.append('web-manifest.json')
    changed += ['package-manifest.json: ' + p for p in repin_package(root, removed)]
    if source['kind'] == 'bundled':
        changed += allowlist(root, name, release_files(target))
    return {'activated': True, 'release': {'name': name, **{k: verified[k] for k in ('releaseSha256', 'catalogId', 'count')}},
            'source': dict(source), 'selection': selection, 'webData': {k: web[k] for k in ('manifestSha256', 'webDataBytes', 'sampleCount')},
            'changed': changed}


def check(root, release_dir=None):
    """Verify an activated tree: pinned selection, release, web package and package pins agree."""
    root = Path(root)
    package = json.loads((root / 'package-manifest.json').read_bytes())
    parsed = selection_config_v2(root, package)
    require(parsed is not None, 'The tree does not select a v2 release')
    if parsed.source['kind'] == 'bundled':
        directory = checked_directory(parsed)
        summary = verify_directory(directory, parsed)
        names = release_files(directory)
        git = set((root / '.gitignore').read_text().splitlines())
        docker = set((root / '.dockerignore').read_text().splitlines())
        for file in [''] + names:
            line = f'!/{parsed.relative}/{file}'
            require(line in git and line in docker, 'Release file missing from an export allowlist: ' + line)
    else:
        require(release_dir is not None, 'An object-store activation is checked against a local copy: pass --release-dir')
        require(not parsed.directory.exists(), 'An object-store release must not be committed under corpus-releases/')
        directory = Path(release_dir)
        summary = verify_directory(directory, parsed)
    release = load_release_v2(directory, expected_manifest_sha256=parsed.manifest_sha256, limits=parsed.limits)
    web = json.loads((root / 'web-manifest.json').read_bytes())
    assets = {}
    for row in web['files']:
        data = (root / 'web' / row['path']).read_bytes()
        require((len(data), sha256(data)) == (row['bytes'], row['sha256']), 'Stale web pin: ' + row['path'])
        assets[row['path']] = root / 'web' / row['path']
    manifest = check_web_release(root / 'web', assets, release)
    studio = (root / STUDIO).read_text()
    require(f"MANIFEST_SHA='{sha256((root / WEB_DATA / 'manifest.json').read_bytes())}'" in studio,
            'studio-release.mjs does not pin the page manifest')
    weight = json.loads((root / 'runtime-assets.json').read_text())['weights'][0]
    for row in package['files']:
        file = root / row['path']
        if row['path'] == weight['path'] and not file.exists():
            continue
        data = file.read_bytes()
        require((len(data), sha256(data)) == (row['bytes'], row['sha256']), 'Stale package pin: ' + row['path'])
    return {'ok': True, 'selection': dict(parsed.config), 'release': summary, 'audioPolicy': manifest['audioPolicy']}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='verify an activated tree; change nothing')
    parser.add_argument('--release-dir', type=Path, help='the converted v2 release (from scripts/convert_release_v1_to_v2.py)')
    parser.add_argument('--expected-manifest-sha256')
    parser.add_argument('--name', help='directory name under corpus-releases/, e.g. fma2000-v2')
    parser.add_argument('--source', choices=('bundled', 'object-store'), default='bundled')
    parser.add_argument('--origin', help='object-store origin, e.g. https://objects.example.org')
    parser.add_argument('--prefix', help='object-store key prefix, e.g. /music-atlas/releases/')
    parser.add_argument('--limits', type=json.loads, default=None, help='JSON v2 limits, as in the selection')
    parser.add_argument('--audio-mode', choices=('local', 'remote', 'disabled'), default='local')
    parser.add_argument('--audio-origin')
    parser.add_argument('--audio-prefix')
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check(ROOT, args.release_dir), indent=2))
        return 0
    require(args.release_dir and args.expected_manifest_sha256 and args.name,
            '--release-dir, --expected-manifest-sha256 and --name are required')
    source = ({'kind': 'bundled'} if args.source == 'bundled' else
              {'kind': 'object-store', 'origin': args.origin, 'pathPrefix': args.prefix})
    print(json.dumps(activate(ROOT, args.release_dir, args.expected_manifest_sha256, name=args.name, source=source,
                              limits=args.limits, audio_mode=args.audio_mode, audio_origin=args.audio_origin,
                              audio_prefix=args.audio_prefix), indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
