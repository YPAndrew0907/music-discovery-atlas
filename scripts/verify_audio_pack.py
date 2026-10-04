"""Verify an operator-supplied local audio pack. Never downloads, copies or uploads."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_SHA = 'e7cfd8347b77929c5c593afdaec9e390509acaed725cacadc448fc6bcae1c013'


def verify(directory, selected=None):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Expected a regular local audio directory')
    catalog_path = ROOT / 'music-search-studio/data/catalog.json'
    if hashlib.sha256(catalog_path.read_bytes()).hexdigest() != CATALOG_SHA:
        raise ValueError('Catalog pin mismatch')
    catalog = json.loads(catalog_path.read_text())
    tracks = {t['id']: t for t in catalog['tracks']}
    chosen = set(tracks) if selected is None else set(selected)
    if not chosen or chosen - tracks.keys() or (selected is not None and len(chosen) != len(selected)):
        raise ValueError('Unknown, empty or duplicate selected IDs')
    expected = {Path(tracks[ident]['audio']).name: tracks[ident] for ident in chosen}
    actual = list(directory.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in actual) or {p.name for p in actual} != set(expected):
        raise ValueError('Pack contains missing, extra, nested or symlinked files')
    inventory = []
    for name, track in sorted(expected.items()):
        path = directory / name
        if path.stat().st_size != track['audioBytes']:
            raise ValueError('Audio length mismatch')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != track['audioSha256']:
            raise ValueError('Audio hash mismatch')
        inventory.append({'id': track['id'], 'filename': name, 'bytes': track['audioBytes'],
            'sha256': digest, 'available': False, 'url': None})
    return {'schemaVersion': 1, 'catalogId': catalog['id'], 'catalogSha256': CATALOG_SHA,
        'source': 'operator-supplied verified local pack', 'count': len(inventory),
        'totalAudioBytes': sum(x['bytes'] for x in inventory),
        'publicDeliveryVerified': False, 'tracks': inventory}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio-dir', required=True,
        help='Directory containing only the selected NNNNNN.mp3 files')
    parser.add_argument('--subset-ids', help='Optional JSON array of catalog IDs for a smaller preview pack')
    args = parser.parse_args()
    try:
        selected = json.loads(Path(args.subset_ids).read_text()) if args.subset_ids else None
        if selected is not None and (not isinstance(selected, list) or any(not isinstance(x, str) for x in selected)):
            raise ValueError('Expected a JSON array of IDs')
        print(json.dumps(verify(args.audio_dir, selected), indent=2))
    except (OSError, ValueError, TypeError):
        raise SystemExit('Audio pack verification failed; no files transferred or changed') from None


if __name__ == '__main__':
    main()
