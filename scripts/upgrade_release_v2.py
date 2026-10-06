#!/usr/bin/env python3
"""Upgrade a verified release-format-2.0 directory to 2.1 (docs/PLATFORM_V2.md).

2.1 differs from 2.0 only in catalog.sqlite, which gains the FTS5 trigram lookup index, and in
release.json, which names minorVersion 1, pins the new catalog and records the upgrade. Every other
asset is copied byte for byte (or hard-linked with --link). Before publishing, the upgrader proves
the result: the server's loader accepts it, every other asset still matches its pin, the tracks,
records and meta rows equal the source's (plus the one lookupIndex meta row), FTS5's integrity check
passes against every row, and sampled lookups and refinements return the same pages with and without
the index. For a release converted from v1, the upgraded catalog.sqlite is byte-identical to the one
the converter writes for 2.1 directly. The source directory is never modified; nothing is downloaded.
"""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'scripts'))
from corpus_release import ReleaseError, require  # noqa: E402
from release_v2 import ASSET_PATHS, LOOKUP_INDEX, LimitsV2, load_release_v2  # noqa: E402
from convert_release_v1_to_v2 import add_lookup_index, lookup_oracle  # noqa: E402


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def same_rows(source, target, table, *, key='row', chunk=2048):
    """Stream both tables in key order and require identical rows; returns the row count."""
    a = sqlite3.connect('file:' + str(source) + '?mode=ro&immutable=1', uri=True)
    b = sqlite3.connect('file:' + str(target) + '?mode=ro&immutable=1', uri=True)
    try:
        left = a.execute(f'SELECT * FROM {table} ORDER BY {key}')
        right = b.execute(f'SELECT * FROM {table} ORDER BY {key}')
        count = 0
        while True:
            x, y = left.fetchmany(chunk), right.fetchmany(chunk)
            require(x == y, f'Upgraded {table} rows differ from the source')
            if not x:
                return count
            count += len(x)
    finally:
        a.close()
        b.close()


def upgrade(source, expected_manifest_sha256, output, *, limits=None, link=False, samples=48, seed=20261006):
    started = time.perf_counter()
    source, output = Path(source).resolve(), Path(output)
    require(not output.exists() and not output.is_symlink(), 'Refusing to overwrite an existing output directory')
    limits = limits or LimitsV2()
    release = load_release_v2(source, expected_manifest_sha256=expected_manifest_sha256, limits=limits)
    require(release.minor_version == 0, 'Only a release-format 2.0 directory can be upgraded')
    manifest = json.loads(json.dumps(release.manifest))
    source_meta = dict(release.catalog_meta)
    del release
    gc.collect()  # drop the source's memory maps before the upgraded release is mapped
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.v2-upgrade-', dir=output.parent) as temporary:
        stage = Path(temporary) / output.name
        stage.mkdir()
        for name, spec in manifest['assets'].items():
            origin, target = source / spec['path'], stage / spec['path']
            require(not origin.is_symlink() and origin.is_file(), 'Missing source asset: ' + spec['path'])
            if link and name != 'catalog':
                os.link(origin, target)
            else:
                shutil.copyfile(origin, target)
        catalog = stage / ASSET_PATHS['catalog']
        connection = sqlite3.connect(catalog)
        try:
            add_lookup_index(connection)
        finally:
            connection.close()
        upgraded = {key: value for key, value in manifest.items() if key not in ('schemaVersion', 'kind')}
        upgraded['assets'] = {**manifest['assets'], 'catalog': {'path': ASSET_PATHS['catalog'], 'bytes': catalog.stat().st_size,
                                                               'sha256': file_sha256(catalog)}}
        upgraded['build'] = {**manifest['build'], 'upgrade': {
            'tool': 'scripts/upgrade_release_v2.py', 'from': '2.0', 'to': '2.1', 'sourceReleaseSha256': expected_manifest_sha256,
            'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version}}
        upgraded = {'schemaVersion': 2, 'kind': manifest['kind'], 'minorVersion': 1, **upgraded}
        payload = (json.dumps(upgraded, indent=2, ensure_ascii=False) + '\n').encode()
        (stage / 'release.json').write_bytes(payload)
        manifest_sha = hashlib.sha256(payload).hexdigest()
        # ---- proofs, against the staged files, before anything is published ----
        for name, spec in manifest['assets'].items():
            if name != 'catalog':
                require(file_sha256(stage / spec['path']) == spec['sha256'], 'Copied asset differs from its pin: ' + spec['path'])
        rows = {table: same_rows(source / ASSET_PATHS['catalog'], catalog, table) for table in ('tracks', 'records')}
        release = load_release_v2(stage, expected_manifest_sha256=manifest_sha, limits=limits)
        try:
            require(dict(release.catalog_meta) == {**source_meta, 'lookupIndex': LOOKUP_INDEX}, 'Upgraded meta rows differ')
            require(release.lookup_index == LOOKUP_INDEX, 'Lookup index was not loaded')
            lookups = lookup_oracle(release, samples=samples, seed=seed)
            require(not lookups['mismatches'], 'Lookup index oracle failed: ' + json.dumps(lookups['mismatches'][:3]))
            summary = release.summary()
        finally:
            release.close()
        os.replace(stage, output)
    return {'schemaVersion': 1, 'kind': 'music-corpus-release-v2-upgrade', 'output': str(output), 'from': '2.0', 'to': '2.1',
            'sourceReleaseSha256': expected_manifest_sha256, 'releaseSha256': manifest_sha,
            'catalog': upgraded['assets']['catalog'], 'sourceCatalog': manifest['assets']['catalog'],
            'proofs': {'otherAssetsUnchanged': sorted(n for n in manifest['assets'] if n != 'catalog'),
                       'rowsUnchanged': rows, 'metaUnchangedPlusLookupIndex': True, 'fts5IntegrityCheck': 'passed',
                       'lookupOracle': lookups},
            'summary': summary, 'linkedAssets': link, 'elapsedSeconds': round(time.perf_counter() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source-dir', type=Path, required=True, help='verified release-format-2.0 directory')
    parser.add_argument('--expected-manifest-sha256', required=True, help='reviewed 2.0 release.json digest')
    parser.add_argument('--output-dir', type=Path, required=True, help='new 2.1 release directory (must not exist)')
    parser.add_argument('--limits', type=json.loads, default=None, help='JSON object of v2 limits, as in the selection')
    parser.add_argument('--link', action='store_true', help='hard-link the unchanged assets instead of copying them')
    parser.add_argument('--oracle-samples', type=int, default=48)
    parser.add_argument('--receipt', type=Path, help='write the upgrade receipt JSON here as well')
    args = parser.parse_args()
    try:
        receipt = upgrade(args.source_dir, args.expected_manifest_sha256, args.output_dir,
                          limits=LimitsV2.from_config(args.limits), link=args.link, samples=args.oracle_samples)
    except ReleaseError as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        return 1
    text = json.dumps(receipt, indent=2, ensure_ascii=False) + '\n'
    if args.receipt:
        args.receipt.write_text(text)
    print(text, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
