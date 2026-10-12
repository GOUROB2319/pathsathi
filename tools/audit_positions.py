# -*- coding: utf-8 -*-
"""
Definitive audit: re-read the exact source cell of every emitted book record.

The builder records each book's (file, table-row, table-column) position, so the
audit re-decodes that one cell and checks that the stored pdfUrl is exactly the
best usable link there, and that no usable link was ever dropped.
"""
import sys, os, json, importlib.util, collections
sys.stdout.reconfigure(encoding='utf-8')
sys.path = [p for p in sys.path if 'pathsathi' not in p.lower()]
spec = importlib.util.spec_from_file_location("ex", r"C:\pathsathi\tools\extract_books.py")
ex = importlib.util.module_from_spec(spec); spec.loader.exec_module(ex)

D = r"C:\pathsathi\assets\data"
build = json.load(open(r"C:\pathsathi\tools\_build_report.json", encoding='utf-8'))

# Re-derive every book with its source position, exactly as build_json does.
sys.argv = ['build_json']
spec2 = importlib.util.spec_from_file_location("bj", r"C:\pathsathi\tools\build_json.py")
bj = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(bj)
raw = json.load(open(r"C:\pathsathi\tools\_extract_raw.json", encoding='utf-8'))
seen = set(); kept = []
for e in raw['rows']:
    if e['digest'] in seen:
        continue
    seen.add(e['digest']); kept.append(e)

cache = {}
def tabs_of(f):
    if f not in cache:
        payload, t, err = ex.decode_document(os.path.join(ex.ROOT, f))
        cache[f] = ex.split_tables(payload)
    return cache[f]

POS = []
for entry in kept:
    year, ay = bj.parse_year(entry['title'], entry['folder_year'])
    level, cid, _ = ex.resolve_level(entry['title'])
    if level is None:
        level, cid = 'unknown', 'unknown'
    for row in entry['rows']:
        title = ex.fold_chars(ex.norm_ws(row['title']))
        if not title:
            continue
        POS.append((year, title, row['language'], level, cid, row['pdfUrl'],
                    entry['file'], row['row'], row['col']))

stats = collections.Counter()
problems = []
shown = collections.defaultdict(list)

for (year, title, lang, level, cid, pdf, f, ri, ci) in POS:
    cell_html = None
    ftitle = ex.fold_chars(title)
    for th, clipped in tabs_of(f):
        rows = ex.split_rows(th)
        if ri >= len(rows):
            continue
        cells = ex.split_cells(rows[ri])
        if ci >= len(cells):
            continue
        # the row must really carry this book's title somewhere
        texts = [ex.fold_chars(ex.cell_text(c)) for c in cells]
        if not any(ftitle and ftitle in t for t in texts):
            continue
        cell_html = cells[ci]
        break
    if cell_html is None:
        stats['position not found'] += 1
        problems.append(('POS_MISSING', f, title, ri, ci))
        continue
    links = ex.cell_links(cell_html)
    usable = [u for u in links if ex.is_pdf_link(u)]
    best = ex.pick_pdf(links)
    if pdf:
        if pdf not in links:
            stats['stored pdfUrl absent from its source cell'] += 1
            problems.append(('NOT_IN_CELL', f, title, pdf, links[:2]))
        elif pdf != best:
            stats['stored pdfUrl present but not the preferred link'] += 1
        else:
            stats['OK stored link is the best usable link in the cell'] += 1
    else:
        if usable:
            stats['BUG empty pdfUrl although the cell has a usable link'] += 1
            problems.append(('EMPTY_HAS_LINK', f, title, ri, ci, usable[:2]))
        elif not links:
            stats['OK empty: cell has no href at all'] += 1
            shown['no_href'].append((f, title, year, cid, ri, ci))
        else:
            stats['OK empty: cell href is #/internal/unusable'] += 1
            shown['unusable'].append((f, title, year, cid, ri, ci, links[:2]))

print('records audited:', len(POS))
print()
for k, v in stats.most_common():
    print(f'  [{v:5d}] {k}')
print()
print('PROBLEMS:', len(problems))
for p in problems[:25]:
    print('   !', p)
print()
for k, v in shown.items():
    print('---', k, len(v))
    for s in v[:4]:
        print('    ', s)
json.dump({'stats': dict(stats), 'problems': problems},
          open(r"C:\pathsathi\tools\_position_audit.json", 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
