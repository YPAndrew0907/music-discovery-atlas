"""The serving list: which recordings of the selected catalog the public site may serve, read at request time.

corpus-releases/serving.json partitions every recording of the selected release into served and held
rows. Each row carries reason codes that the file itself defines. The server reads the list once at
start, binds it to the selected catalog and refuses to start when it does not match:
* the catalog ID must be equal;
* the list must name every recording of the catalog, exactly once;
* every reason code must be defined.

It then applies the list to every request. A held recording is absent from search results, lookups,
neighbours, collection pages and the credits, and its audio route answers 404.

Changing which recordings are served is an edit of this file and a deploy; the release is not rebuilt. A
held recording's data stays in the release, so it returns by setting its entry back to served. The
build-time quarantine (corpus-releases/quarantine.json) is the separate, slower path that takes a
recording out of the release itself.
"""
import hashlib
from pathlib import Path
import re

from corpus_release import TRACK_ID, ReleaseError, require, strict_json

KIND = 'music-serving-list'
SUMMARY_KIND = 'music-serving-summary'
PATH = 'corpus-releases/serving.json'
CODE = re.compile(r'[A-Z][A-Za-z0-9.:+_-]{0,47}\Z')
MAX_BYTES = 16_000_000


class ServingList:
    """A verified serving list bound to one catalog (its ID and its recordings in catalog order)."""

    def __init__(self, data, *, catalog_id, ordered_ids, label=PATH):
        data = bytes(data)
        self.sha256 = hashlib.sha256(data).hexdigest()
        document = strict_json(data, label, MAX_BYTES)
        require(isinstance(document, dict) and set(document) == {'schemaVersion', 'kind', 'catalogId', 'decision',
                                                                 'reasons', 'rows'}
                and document['schemaVersion'] == 1 and type(document['schemaVersion']) is int
                and document['kind'] == KIND, label + ' is not a serving list')
        require(document['catalogId'] == catalog_id, label + ' does not match the selected catalog')
        require(isinstance(document['decision'], str) and 0 < len(document['decision']) <= 4096,
                label + ' must name the decision it applies')
        reasons = document['reasons']
        require(isinstance(reasons, dict) and reasons and all(
            isinstance(code, str) and CODE.fullmatch(code) and isinstance(text, str) and 0 < len(text) <= 2048
            for code, text in reasons.items()), label + ' has an invalid reason table')
        ordered_ids = list(ordered_ids)
        row_of = {ident: row for row, ident in enumerate(ordered_ids)}
        require(len(row_of) == len(ordered_ids), 'The selected catalog repeats a recording ID')
        rows = document['rows']
        require(isinstance(rows, list) and len(rows) == len(ordered_ids),
                label + ' must list every recording of the selected catalog exactly once')
        served, held, seen = [], {}, set()
        for entry in rows:
            require(isinstance(entry, dict) and set(entry) == {'id', 'serve', 'reasons'}, label + ' has an invalid row')
            ident, serve, codes = entry['id'], entry['serve'], entry['reasons']
            require(isinstance(ident, str) and TRACK_ID.fullmatch(ident) and ident in row_of and ident not in seen,
                    label + ' names an unknown or repeated recording: ' + str(ident)[:128])
            seen.add(ident)
            require(type(serve) is bool, label + ' needs a boolean serve flag: ' + ident)
            require(isinstance(codes, list) and codes and len(codes) == len(set(codes))
                    and all(isinstance(code, str) and code in reasons for code in codes),
                    label + ' needs defined reason codes: ' + ident)
            if serve:
                served.append(row_of[ident])
            else:
                held[ident] = tuple(codes)
        require(served, label + ' serves no recording')
        self.catalog_id = catalog_id
        self.ordered_ids = tuple(ordered_ids)
        self.row_of = row_of
        self.served_rows = frozenset(served)
        self.held = held
        self.count, self.total = len(served), len(ordered_ids)

    def serves_row(self, row):
        return row in self.served_rows

    def serves_id(self, ident):
        row = self.row_of.get(ident)
        return row is not None and row in self.served_rows

    def rows_in_order(self):
        return sorted(self.served_rows)

    def mask(self):
        """A boolean list over catalog rows: True where the row is served."""
        return [row in self.served_rows for row in range(self.total)]

    def manifest_block(self):
        """The part of /v1/manifest that names the list in force."""
        return {'sha256': self.sha256, 'served': self.count, 'held': self.total - self.count}

    def public(self):
        """GET /serving.json: the page hides every row this does not list."""
        rows = self.rows_in_order()
        return {'schemaVersion': 1, 'kind': SUMMARY_KIND, 'catalogId': self.catalog_id, 'sha256': self.sha256,
                'served': self.count, 'held': self.total - self.count, 'total': self.total, 'rows': rows,
                'ids': [self.ordered_ids[row] for row in rows]}


def load_serving_list(root, *, catalog_id, ordered_ids):
    """Read and bind <root>/corpus-releases/serving.json; any problem refuses the start."""
    path = Path(root) / PATH
    require(path.is_file() and not path.is_symlink() and not path.parent.is_symlink(),
            'The serving list ' + PATH + ' is missing')
    require(path.stat().st_size <= MAX_BYTES, 'The serving list exceeds its budget')
    return ServingList(path.read_bytes(), catalog_id=catalog_id, ordered_ids=ordered_ids)


def serve_all(*, catalog_id, ordered_ids, decision='Every recording of this catalog is served (test fixture)'):
    """A list that serves every row, for fixtures that are not about the list."""
    import json
    document = {'schemaVersion': 1, 'kind': KIND, 'catalogId': catalog_id, 'decision': decision,
                'reasons': {'SERVE': 'Served'}, 'rows': [{'id': ident, 'serve': True, 'reasons': ['SERVE']}
                                                         for ident in ordered_ids]}
    return ServingList(json.dumps(document).encode(), catalog_id=catalog_id, ordered_ids=ordered_ids, label='fixture list')


__all__ = ['KIND', 'PATH', 'ReleaseError', 'ServingList', 'load_serving_list', 'serve_all']


# ---- the credits page --------------------------------------------------------------------------------------
HELD_SENTENCE = ('Recordings held under the serving list after the rights review of 2026-10-06 are not served '
                 'and are not listed here.')
ARTICLE = re.compile(r'<article id="(fma-[0-9]{1,6})">.*?</article>\n', re.S)


def served_credits_page(data, serving):
    """The v1 static credits page (scripts/build_corpus_credits.py) with only the served recordings: the held
    articles are dropped, the count in the title and the introduction becomes the served count, and the
    introduction says why. Any page that does not have exactly the expected shape refuses the start."""
    text = bytes(data).decode('utf-8')
    first = text.find('<article id="')
    require(first > 0, 'The credits page has no credit articles')
    head, body = text[:first], text[first:]
    articles = list(ARTICLE.finditer(body))
    require(articles and ''.join(match.group(0) for match in articles) == body,
            'The credits page has content outside its credit articles')
    ids = [match.group(1).replace('-', ':') for match in articles]
    require(ids == list(serving.ordered_ids), 'The credits page does not list the selected catalog in order')
    total, count = f'{serving.total:,}', f'{serving.count:,}'
    title, intro = f'<title>Music Discovery Atlas: {total} track credits</title>', f'<p>{total} screened FMA recordings.'
    require(head.count(title) == 1 and head.count(intro) == 1 and head.count('</p>') == 1,
            'The credits page heading has an unexpected shape')
    head = (head.replace(title, f'<title>Music Discovery Atlas: {count} track credits</title>')
                .replace(intro, f'<p>{count} screened FMA recordings.').replace('</p>', ' ' + HELD_SENTENCE + '</p>'))
    kept = [match.group(0) for match, ident in zip(articles, ids) if serving.serves_id(ident)]
    return (head + ''.join(kept)).encode('utf-8')


# ---- the takedown page -------------------------------------------------------------------------------------
CONTACT_PLACEHOLDER = '[RIGHTS CONTACT: set MUSIC_RIGHTS_CONTACT before this page is published]'
CONTACT = re.compile(r'[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\Z')


def rights_contact(value):
    """The rights contact from the service environment, or None when it is unset or not an e-mail address.
    The takedown page is served only with a contact: a page with a blank contact is worse than none."""
    if not isinstance(value, str) or len(value) > 254 or not CONTACT.fullmatch(value.strip()):
        return None
    return value.strip()


def takedown_page(template, contact):
    """The takedown page template with every contact placeholder replaced by a mailto link."""
    import html
    text = bytes(template).decode('utf-8')
    require(CONTACT_PLACEHOLDER in text, 'The takedown page template has no contact placeholder')
    address = html.escape(contact, quote=True)
    return text.replace(CONTACT_PLACEHOLDER, f'<a href="mailto:{address}">{address}</a>').encode('utf-8')
