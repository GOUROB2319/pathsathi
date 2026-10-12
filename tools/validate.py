# -*- coding: utf-8 -*-
"""Independently validate every generated assets/data/<year>.json file."""
import os, re, json, sys, collections, urllib.parse

OUT_DIR = r"C:\pathsathi\assets\data"
VALID_LEVELS = {'pre_primary', 'primary', 'secondary', 'higher_secondary',
                'ebtedayi', 'dakhil', 'technical', 'ethnic_minority'}
VALID_LANGS = {'bn', 'en', 'unknown'} | {'ccp', 'mhx', 'grt', 'sdr', 'tpe'}
REQUIRED = ['id', 'year', 'level', 'classId', 'title', 'language', 'pdfUrl']

problems = []          # hard failures
notes = collections.defaultdict(list)


def bad(where, msg):
    problems.append('%s: %s' % (where, msg))


files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith('.json'))
print('JSON files found:', len(files))

all_ids = {}
summary = []
for fn in files:
    path = os.path.join(OUT_DIR, fn)
    where = fn
    try:
        with open(path, encoding='utf-8') as fh:
            doc = json.load(fh)
    except Exception as exc:
        bad(where, 'invalid JSON: %s' % exc)
        continue

    m = re.fullmatch(r'(\d{4})\.json', fn)
    if not m:
        bad(where, 'filename is not <year>.json')
    file_year = int(m.group(1)) if m else None

    if not isinstance(doc, dict):
        bad(where, 'top level is not an object'); continue
    if set(doc.keys()) != {'year', 'books'}:
        bad(where, 'unexpected top-level keys: %s' % sorted(doc.keys()))
    if not isinstance(doc.get('year'), int):
        bad(where, 'year is not an integer')
    if doc.get('year') != file_year:
        bad(where, 'top-level year %r != filename year %r' % (doc.get('year'), file_year))
    if not isinstance(doc.get('books'), list):
        bad(where, 'books is not an array'); continue

    n = len(doc['books'])
    no_pdf = unknown_lang = 0
    per_level = collections.Counter()
    per_lang = collections.Counter()

    for i, b in enumerate(doc['books']):
        loc = '%s[%d]' % (where, i)
        if not isinstance(b, dict):
            bad(loc, 'entry is not an object'); continue
        missing = [k for k in REQUIRED if k not in b]
        if missing:
            bad(loc, 'missing fields: %s' % missing); continue
        extra = sorted(set(b) - set(REQUIRED) - {'academicYear'})
        if extra:
            bad(loc, 'unexpected fields: %s' % extra)

        for k in ('id', 'level', 'classId', 'title', 'language', 'pdfUrl'):
            if not isinstance(b[k], str):
                bad(loc, '%s is not a string' % k)
        if not isinstance(b['year'], int):
            bad(loc, 'year is not an integer')
        if b['year'] != doc['year']:
            bad(loc, 'book year %r != file year %r' % (b['year'], doc['year']))

        if not b['id']:
            bad(loc, 'empty id')
        if b['id'] in all_ids:
            bad(loc, 'duplicate id %r (also in %s)' % (b['id'], all_ids[b['id']]))
        else:
            all_ids[b['id']] = where
        if not re.fullmatch(r'[a-z0-9_\-]+', b['id']):
            notes['id_not_slug_safe'].append('%s -> %r' % (loc, b['id']))

        if b['level'] not in VALID_LEVELS:
            bad(loc, 'invalid level %r' % b['level'])
        if b['language'] not in VALID_LANGS:
            bad(loc, 'invalid language %r' % b['language'])
        if not b['classId']:
            bad(loc, 'empty classId')
        if not re.fullmatch(r'(class_([1-9]|1[0-2])|pre_primary|ebtedayi|dakhil|technical|ethnic_minority|unknown)',
                            b['classId']):
            notes['classId_unexpected'].append('%s -> %r' % (loc, b['classId']))
        if not b['title'].strip():
            bad(loc, 'empty title')
        if b['title'] != b['title'].strip():
            notes['title_padding'].append(loc)

        if b['pdfUrl']:
            u = b['pdfUrl']
            p = urllib.parse.urlparse(u)
            if p.scheme not in ('http', 'https'):
                bad(loc, 'pdfUrl scheme %r' % p.scheme)
            if not p.netloc:
                bad(loc, 'pdfUrl has no host: %r' % u)
            if ' ' in u:
                bad(loc, 'pdfUrl contains a space: %r' % u)
            if p.netloc not in ('drive.google.com', 'drive.egovcloud.gov.bd'):
                notes['pdf_host_unexpected'].append('%s -> %s' % (loc, p.netloc))
            if 'drive.google.com/open?id=' not in u and '/file/d/' not in u \
                    and 'egovcloud' not in u:
                notes['pdf_not_file_form'].append('%s -> %s' % (loc, u))
        else:
            no_pdf += 1
            notes['missing_pdf'].append('%s %s/%s %r' % (where, b['classId'], b['language'], b['title']))
        if b['language'] == 'unknown':
            unknown_lang += 1
            notes['unknown_language'].append('%s %r' % (where, b['title']))

        per_level[b['level']] += 1
        per_lang[b['language']] += 1

    # duplicate detection inside a file
    key = collections.Counter(
        (b.get('title'), b.get('language'), b.get('classId'), b.get('pdfUrl'))
        for b in doc['books'] if isinstance(b, dict))
    for k, c in key.items():
        if c > 1:
            notes['duplicate_rows'].append('%s %r x%d' % (where, k[0], c))

    summary.append({'file': fn, 'year': doc.get('year'), 'books': n,
                    'no_pdf': no_pdf, 'unknown_lang': unknown_lang,
                    'levels': dict(per_level), 'languages': dict(per_lang)})

print()
print('=== PER-FILE SUMMARY ===')
for s in summary:
    print('%-10s books=%-4d missing_pdf=%-4d unknown_lang=%-3d levels=%s langs=%s'
          % (s['file'], s['books'], s['no_pdf'], s['unknown_lang'],
             s['levels'], s['languages']))

print()
print('total books     :', sum(s['books'] for s in summary))
print('unique ids      :', len(all_ids))
print()
print('=== HARD PROBLEMS ===', len(problems))
for p in problems[:60]:
    print('  !', p)
print()
print('=== NOTES ===')
for k in sorted(notes):
    print('  %-22s %d' % (k, len(notes[k])))
    for v in notes[k][:5]:
        print('        ', v)

with open(r"C:\pathsathi\tools\_validation.json", 'w', encoding='utf-8') as fh:
    json.dump({'problems': problems, 'notes': {k: v for k, v in notes.items()},
               'summary': summary}, fh, ensure_ascii=False, indent=1)
