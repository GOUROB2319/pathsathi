# -*- coding: utf-8 -*-
"""
PathSathi (পাঠসাথী) — build assets/data/<year>.json from the raw extraction.

Reads tools/_extract_raw.json (produced by extract_books.py), resolves the
academic year, level and classId for every book, deduplicates, and writes one
JSON file per academic year. Nothing is invented: pdfUrl stays empty when the
HTML offered no usable link, and every drop is recorded for the report.
"""
import os, re, json, sys, collections, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ex", os.path.join(HERE, "extract_books.py"))
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)

OUT_DIR = r"C:\pathsathi\assets\data"
RAW = os.path.join(HERE, "_extract_raw.json")

BN = {'০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
      '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9'}


def bn2ascii(s):
    return ''.join(BN.get(c, c) for c in s)


def parse_year(title, folder_year):
    """Academic year from the document heading.

    A bi-annual heading such as ২০২০-২১ belongs to the year it starts in;
    that is recorded in `academicYear` while `year` carries the start year.
    """
    t = bn2ascii(ex.fold_chars(title))
    m = re.search(r'(\d{4})\s*[-–]\s*(\d{2,4})', t)
    if m:
        start = int(m.group(1))
        end_raw = m.group(2)
        end = int(end_raw) if len(end_raw) == 4 else int(str(start)[:2] + end_raw)
        return start, '%d-%d' % (start, end)
    m = re.search(r'(\d{4})', t)
    if m:
        return int(m.group(1)), None
    return int(folder_year), None


def main():
    raw = json.load(open(RAW, encoding='utf-8'))

    # ---------------------------------------------------------- dedupe files
    # A payload that is byte-identical under a different folder is the same
    # page stored twice, so only the first (alphabetical) copy is used.
    kept, dropped_dup = [], []
    seen_digest = {}
    for entry in raw['rows']:
        d = entry['digest']
        if d in seen_digest:
            dropped_dup.append({'file': entry['file'], 'same_as': seen_digest[d],
                                'title': entry['title'],
                                'books_skipped': len(entry['rows'])})
            continue
        seen_digest[d] = entry['file']
        kept.append(entry)

    books = []
    stats = collections.Counter()
    skipped_navigation = []
    no_link_rows = []
    non_pdf_rows = []

    for entry in kept:
        year, academic_year = parse_year(entry['title'], entry['folder_year'])
        level, class_id, classes = ex.resolve_level(entry['title'])
        if level is None:
            stats['unresolved_title'] += 1
            level, class_id = 'unknown', 'unknown'

        for row in entry['rows']:
            title = ex.fold_chars(ex.norm_ws(row['title']))
            if not title:
                stats['empty_title'] += 1
                continue
            if not row['pdfUrl']:
                # The only link-less "rows" in the corpus are the nav cells of
                # an index page, or a real book whose download cell is empty.
                if row['title'] == 'প্রাক-প্রাথমিক' or re.match(
                        r'^[\u09e6-\u09ef]+(?:ম|য়|য়)?\s*শ্রেণি$', title):
                    skipped_navigation.append(
                        {'file': entry['file'], 'cell': title,
                         'reason': 'navigation cell (links to an internal page, '
                                   'not to a book file)'})
                    continue
                no_link_rows.append({'file': entry['file'], 'title': title,
                                     'language': row['language'],
                                     'row': row['row'], 'col': row['col']})
            books.append({
                'title': title,
                'language': row['language'],
                'pdfUrl': row['pdfUrl'],
                'year': year,
                'academicYear': academic_year,
                'level': level,
                'classId': class_id,
                'file': entry['file'],
                'clipped': entry['clipped'],
                # exact position in the source table, used by the validation
                # audit to re-read the very same cell
                'row': row['row'],
                'col': row['col'],
            })

    # ------------------------------------------------------------- make ids
    # The same textbook is listed on more than one page of the same academic
    # year (for example the vocational page and the SSC-vocational page). Those
    # byte-identical records are one book, so they are collapsed; genuine
    # variants (different language, class or link) are all kept.
    seen_book = {}
    unique_books = []
    dropped_rows = []
    for b in books:
        key = (b['year'], b['title'], b['language'], b['classId'], b['pdfUrl'])
        if key in seen_book:
            dropped_rows.append({'title': b['title'], 'language': b['language'],
                                 'classId': b['classId'], 'year': b['year'],
                                 'pdfUrl': b['pdfUrl'], 'file': b['file'],
                                 'same_as': seen_book[key]})
            continue
        seen_book[key] = b['file']
        unique_books.append(b)
    books = unique_books

    per_year = collections.defaultdict(list)
    for b in books:
        per_year[b['year']].append(b)

    out_files = {}
    id_seen = set()
    for year in sorted(per_year):
        items = []
        for b in per_year[year]:
            base = '%d-%s-%s-%s' % (year, b['classId'],
                                    ex.slugify(b['title'])[:60], b['language'])
            base = re.sub(r'-+', '-', base).strip('-')
            book_id = base
            n = 2
            while book_id in id_seen:
                book_id = '%s-%d' % (base, n)
                n += 1
            id_seen.add(book_id)
            rec = {
                'id': book_id,
                'year': year,
                'level': b['level'],
                'classId': b['classId'],
                'title': b['title'],
                'language': b['language'],
                'pdfUrl': b['pdfUrl'],
            }
            if b['academicYear']:
                rec['academicYear'] = b['academicYear']
            items.append(rec)

        # stable order: by classId then title then language
        def sort_key(r):
            cid = r['classId']
            m = re.search(r'(\d+)$', cid)
            return (0, int(m.group(1))) if m else (1, cid)
        items.sort(key=lambda r: (sort_key(r), r['title'], r['language']))

        payload = {'year': year, 'books': items}
        out_files[year] = payload

    os.makedirs(OUT_DIR, exist_ok=True)
    written = []
    for year, payload in out_files.items():
        path = os.path.join(OUT_DIR, '%d.json' % year)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
        os.replace(tmp, path)
        written.append({'file': path, 'year': year, 'books': len(payload['books'])})

    # --------------------------------------------------------- build report
    report = {
        'total_html': raw['total_html'],
        'parsed_ok': len(raw['parsed_ok']),
        'parse_errors': raw['errors'],
        'files_with_books': len(raw['rows']),
        'files_kept_after_dedupe': len(kept),
        'duplicate_files': dropped_dup,
        'clipped_files': raw['clipped'],
        'books_total': len(books),
        'books_before_in_year_dedupe': len(books) + len(dropped_rows),
        'in_year_duplicates_dropped': dropped_rows,
        'written': written,
        'stats': dict(stats),
        'skipped_navigation': skipped_navigation,
        'no_link_rows': no_link_rows,
        'non_pdf_rows': non_pdf_rows,
    }
    with open(os.path.join(HERE, '_build_report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print('kept files        :', len(kept), ' dropped duplicates:', len(dropped_dup))
    print('books before dedupe:', len(books) + len(dropped_rows))
    print('in-year dups dropped:', len(dropped_rows))
    print('books total       :', len(books))
    print('written files     :', len(written))
    for w in written:
        print('   %s  books=%d' % (w['file'], w['books']))
    print('skipped navigation:', len(skipped_navigation))
    print('rows without link :', len(no_link_rows))
    print('stats             :', dict(stats))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
