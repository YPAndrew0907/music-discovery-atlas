"""Explicit package-pinned corpus selection; absence/disabled keeps legacy108."""
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit
from types import MappingProxyType

from corpus_release import (ReleaseError, ReleaseLimits, integer, require, strict_json,
                            validate_release, valid_sha, verify_spec)


@dataclass(frozen=True)
class ActiveCorpus:
    directory: Path
    release: object
    audio_archive: object


def selected_corpus(root, package):
    root = Path(root)
    config_path = root / 'active-corpus.json'
    if not config_path.exists() and not config_path.is_symlink():
        return None
    specs = [row for row in package.get('files', []) if row.get('path') == 'active-corpus.json']
    require(len(specs) == 1, 'Corpus selection is not pinned by the reviewed package')
    data = verify_spec(root, specs[0], 16_384, 'active-corpus.json')
    config = strict_json(data, 'active corpus selection', 16_384)
    require(isinstance(config, dict) and integer(config.get('schemaVersion'), 1, 1)
            and type(config.get('enabled')) is bool, 'Invalid active corpus selection')
    if not config['enabled']:
        require(set(config) == {'schemaVersion', 'enabled'}, 'Disabled selection cannot contain activation settings')
        return None
    required = {'schemaVersion', 'enabled', 'directory', 'manifestSha256',
                'maxTracks', 'coreByteBudget', 'evidenceByteBudget', 'audioArchive'}
    # jsonByteBudget is optional; absent keeps the original 8 MB per-JSON-asset budget.
    require(set(config) in (required, required | {'jsonByteBudget'}), 'Incomplete active corpus selection')
    relative = config['directory']
    require(isinstance(relative, str) and re.fullmatch(r'corpus-releases/[A-Za-z0-9][A-Za-z0-9_-]{0,63}', relative),
            'Invalid corpus release directory')
    directory = root
    for part in relative.split('/'):
        directory = directory / part
        require(not directory.is_symlink() and directory.is_dir(), 'Corpus directory cannot contain symlinks')
    limits = ReleaseLimits(max_tracks=config['maxTracks'], core_bytes=config['coreByteBudget'],
                           evidence_bytes=config['evidenceByteBudget'],
                           json_bytes=config.get('jsonByteBudget', ReleaseLimits.json_bytes))
    release = validate_release(directory, expected_manifest_sha256=config['manifestSha256'], limits=limits)
    require(release.count >= 32, 'Active corpus must support the existing32-candidate search budget')
    audio = config['audioArchive']
    if audio is not None:
        require(isinstance(audio, dict) and set(audio) == {'url', 'bytes', 'sha256'}
                and isinstance(audio['url'], str) and integer(audio['bytes'], 1, 650_000_000)
                and valid_sha(audio['sha256']), 'Invalid approved audio archive pin')
        try:
            parsed = urlsplit(audio['url'])
        except ValueError as error:
            raise ReleaseError('Invalid approved audio archive URL') from error
        require(parsed.scheme == 'https' and parsed.netloc == 'github.com' and not parsed.query and not parsed.fragment
                and re.fullmatch(r'/YPAndrew0907/music-discovery-atlas/releases/download/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', parsed.path),
                'Audio archive must be a versioned asset of the existing repository')
    return ActiveCorpus(directory, release, MappingProxyType(audio) if audio is not None else None)
