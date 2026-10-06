#!/usr/bin/env python3
"""The reviewed rights quarantine list and the checks every corpus build applies with it.

corpus-releases/quarantine.json names recordings that no corpus build may include: "remove" rows
whose evidence rules them out and "hold" rows kept out pending a later reviewed change. The release
builder drops listed rows from its approved inputs and compares its frozen parent without them; the
credits, pins and hydration-plan steps refuse a release that still holds one. A listed recording's
audio bytes under any other ID stop a build rather than being dropped silently. The server never
reads this list; docs/RIGHTS_QUARANTINE.md has the procedure.

Run directly to validate the list and print a summary.
"""
from datetime import date
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from corpus_release import TRACK_ID, ReleaseError, nonempty, require, sha256, strict_json, valid_sha  # noqa: E402

LIST = 'corpus-releases/quarantine.json'
KIND = 'music-corpus-rights-quarantine'
ACTIONS = ('remove', 'hold')
HEADER = ('schemaVersion', 'kind', 'description', 'procedure', 'evidenceRoot', 'entries')
FIELDS = ('id', 'action', 'reason', 'evidence', 'date', 'reviewer', 'catalogRow', 'audioSha256')
DATE = re.compile(r'\d{4}-\d{2}-\d{2}\Z')


class Quarantine:
    """A validated list: entries in list order, looked up by recording ID and by audio SHA-256."""

    def __init__(self, entries=(), sha=None):
        self.entries = tuple(entries)
        self.sha256 = sha
        self.by_id = {entry['id']: entry for entry in self.entries}
        self.by_audio = {entry['audioSha256']: entry['id'] for entry in self.entries}

    def __contains__(self, ident):
        return ident in self.by_id

    def __len__(self):
        return len(self.entries)


EMPTY = Quarantine()


def parse(data):
    value = strict_json(data, 'rights quarantine list', 1_000_000)
    require(isinstance(value, dict) and tuple(value) == HEADER and value['schemaVersion'] == 1
            and type(value['schemaVersion']) is int and value['kind'] == KIND
            and all(nonempty(value[key], 2048) for key in ('description', 'procedure', 'evidenceRoot'))
            and isinstance(value['entries'], list), 'Unknown rights quarantine list format')
    for entry in value['entries']:
        require(isinstance(entry, dict) and tuple(entry) == FIELDS, 'Quarantine entries need exactly ' + ', '.join(FIELDS))
        require(isinstance(entry['id'], str) and TRACK_ID.fullmatch(entry['id']) is not None, 'Invalid quarantined recording ID')
        require(entry['action'] in ACTIONS, 'Quarantine action must be remove or hold: ' + entry['id'])
        require(nonempty(entry['reason'], 2048) and nonempty(entry['evidence'], 2048) and nonempty(entry['reviewer'], 200)
                and nonempty(entry['catalogRow'], 512), 'Quarantine entry lacks its reason, evidence, reviewer or row: ' + entry['id'])
        require(isinstance(entry['date'], str) and DATE.fullmatch(entry['date']) is not None, 'Quarantine date must be YYYY-MM-DD')
        try:
            date.fromisoformat(entry['date'])
        except ValueError as error:
            raise ReleaseError('Invalid quarantine date: ' + entry['id']) from error
        require(valid_sha(entry['audioSha256']), 'Quarantine entry needs the audio SHA-256 it applies to: ' + entry['id'])
    entries = value['entries']
    require(len({entry['id'] for entry in entries}) == len(entries), 'A recording is listed twice in the quarantine list')
    require(len({entry['audioSha256'] for entry in entries}) == len(entries), 'An audio hash is listed twice in the quarantine list')
    return Quarantine(entries, sha256(data))


def load(path=None):
    """The repository's list (or an explicit path). A missing list is an error, never an empty list."""
    path = Path(path) if path is not None else ROOT / LIST
    require(path.is_file() and not path.is_symlink(), 'The rights quarantine list is missing: ' + str(path))
    return parse(path.read_bytes())


def apply(ids, tracks, rights_rows, quarantine):
    """(kept ids, kept tracks, kept rights rows, excluded entries), all in input order.

    A listed recording's audio bytes under an unlisted ID stop the build: that row needs a review
    and its own entry, not a silent drop."""
    require(len(ids) == len(tracks) == len(rights_rows), 'Quarantine inputs are not aligned')
    for ident, track in zip(ids, tracks):
        twin = quarantine.by_audio.get(track.get('audioSha256'))
        require(twin is None or twin == ident,
                f'Audio bytes of quarantined {twin} appear under {ident}; review that row and list it')
    keep = [ident not in quarantine for ident in ids]
    excluded = [quarantine.by_id[ident] for ident in ids if ident in quarantine]
    def kept(rows):
        return [row for row, wanted in zip(rows, keep) if wanted]
    return kept(ids), kept(tracks), kept(rights_rows), excluded


def require_clear(ids, audio_hashes, quarantine, what):
    """Fail if a release, or anything built from one, holds a listed recording or its audio bytes."""
    held = {ident for ident in ids if ident in quarantine}
    held |= {quarantine.by_audio[digest] for digest in audio_hashes if digest in quarantine.by_audio}
    require(not held, what + ' holds quarantined recordings: ' + ', '.join(sorted(held)))


def receipt(quarantine, excluded):
    """What a build records about the list it applied (kept with the release's build receipt)."""
    return {'list': LIST, 'listSha256': quarantine.sha256, 'listedEntries': len(quarantine),
            'excluded': [{key: entry[key] for key in ('id', 'action', 'reason', 'date', 'reviewer')} for entry in excluded]}


def main():
    quarantine = load(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({'list': LIST, 'sha256': quarantine.sha256, 'entries': len(quarantine),
                      **{action: [entry['id'] for entry in quarantine.entries if entry['action'] == action]
                         for action in ACTIONS}}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ReleaseError, OSError) as error:
        raise SystemExit(str(error)) from None
