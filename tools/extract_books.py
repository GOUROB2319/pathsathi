# -*- coding: utf-8 -*-
"""
PathSathi (পাঠসাথী) — NCTB textbook metadata extractor.

Reads every HTML/HTM file under the "Book Link" folder, decodes the base64
`encoded-content` payload of the <rt-renderer> block, parses the book tables,
and writes one assets/data/<year>.json file per academic year.

No value is invented: every title, level, classId, language and pdfUrl is
derived from the HTML, and pdfUrl is left empty when no usable link exists.
"""
import os, re, sys, io, json, base64, hashlib, collections, datetime

if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT = r"C:\Users\GourobSaha\OneDrive\Desktop\Book-link"
OUT_DIR = r"C:\pathsathi\assets\data"
REPORT_DIR = r"C:\pathsathi\tools"

# ---------------------------------------------------------------- helpers ---
BN_DIGITS = {'০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
             '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9'}


def bn2ascii(s):
    return ''.join(BN_DIGITS.get(c, c) for c in s)


def norm_ws(s):
    return re.sub(r'\s+', ' ', s.replace('\xa0', ' ')).strip()


# Some source pages were authored with the archaic Unicode letters (য with
# nukta written as U+09DF, ড় as U+09DC, ঢ় as U+09DD, ৎ as U+09CE) instead of
# the modern precomposed forms. Folding them keeps titles stable and makes the
# keyword checks below match regardless of which form a file uses.
CHAR_FOLD = {
    '\u09df': '\u09af\u09bc',   # য়  -> য়
    '\u09dc': '\u09a1\u09bc',   # ড়  -> ড়
    '\u09dd': '\u09a2\u09bc',   # ঢ়  -> ঢ়
    '\u09ce': '\u09a4\u09cd',   # ৎ  -> ত্
}


def fold_chars(s):
    for a, b in CHAR_FOLD.items():
        s = s.replace(a, b)
    return s


def strip_tags(html):
    html = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html)
    html = re.sub(r'(?i)<br\s*/?>', '\n', html)
    html = re.sub(r'(?i)</(p|div|tr|td|li|h[1-6])>', '\n', html)
    txt = re.sub(r'(?s)<[^>]+>', '', html)
    txt = txt.replace('&nbsp;', ' ').replace('&amp;', '&')
    txt = txt.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    txt = txt.replace('&#39;', "'").replace('&ndash;', '-').replace('&mdash;', '-')
    return txt


def cell_text(cell_html):
    """Readable text of a cell (anchors + plain text), whitespace-normalised."""
    return norm_ws(strip_tags(cell_html))


def split_cells(row_html):
    return re.findall(r'(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>', row_html)


def split_rows(table_html):
    return re.findall(r'(?is)<tr\b[^>]*>(.*?)</tr>', table_html)


def split_tables(doc_html):
    """Complete <table>...</table> blocks, plus a trailing clipped fragment.

    Some source payloads were themselves cut off at a 10240-byte boundary, so
    the document can end inside a table. The fragment is returned with
    clipped=True instead of being silently dropped.
    """
    out = []
    pos = 0
    while True:
        m = re.search(r'(?is)<table\b.*?</table\s*>', doc_html[pos:])
        if not m:
            break
        out.append((m.group(0), False))
        pos += m.end()
    rest = doc_html[pos:]
    if re.search(r'(?i)<table\b', rest):
        out.append((rest[re.search(r'(?i)<table\b', rest).start():], True))
    return out


HREF_RE = re.compile(r'(?is)<a\b([^>]*)>')
HREF_ATTR_RE = re.compile(r'(?is)\bhref\s*=\s*"([^"]*)"')


def cell_links(cell_html):
    """Every well-formed anchor href in the cell, in document order."""
    out = []
    for m in HREF_RE.finditer(cell_html):
        hm = HREF_ATTR_RE.search(m.group(1))
        if not hm:
            continue                      # attribute missing -> not a link
        url = hm.group(1).strip()
        if not url:
            continue
        out.append(url)
    return out


def is_pdf_link(url):
    """True only for links that actually serve the textbook file."""
    if url.startswith('#'):
        return False
    if url.startswith('/') or url.startswith('./') or url.startswith('../'):
        return False                      # internal portal page, not a file
    low = url.lower()
    if low.startswith('http://') or low.startswith('https://'):
        return 'drive.google.com' in low or 'egovcloud' in low
    return False


def link_rank(url):
    """Prefer a Google Drive file link, then Google Drive, then mirrors."""
    low = url.lower()
    if 'drive.google.com/file/d/' in low:
        return 0
    if 'drive.google.com' in low:
        return 1
    if 'egovcloud' in low:
        return 2
    return 3


def pick_pdf(links):
    usable = [u for u in links if is_pdf_link(u)]
    if not usable:
        return ''
    return sorted(usable, key=link_rank)[0]


# ------------------------------------------------------------ vocabulary ---
SERIAL_RE = re.compile(r'^[\d\u09e6-\u09ef\s\.\)\-–]+$')

LANG_BY_HEADER = [
    ('ইংরেজি', 'en'), ('english', 'en'),
    ('বাংলা', 'bn'),
    ('চাকমা', 'ccp'), ('মারমা', 'mhx'), ('গারো', 'grt'),
    ('সাদরি', 'sdr'), ('ত্রিপুরা', 'tpe'),
]

LEVEL_BY_FOLDER_TEXT = {
    # order matters: the first hit wins, so the most specific wording goes first
    'প্রাক-প্রাথমিক': ('pre_primary', 'pre_primary'),
    'প্রাক প্রাথমিক': ('pre_primary', 'pre_primary'),
    'ইবতেদায়ি': ('ebtedayi', None),
    'ইবতেদায়ী': ('ebtedayi', None),
    'উচ্চ মাধ্যমিক': ('higher_secondary', None),
    'দাখিল': ('dakhil', None),
    'এসএসসি': ('technical', None),
    'ভোকেশনাল': ('technical', None),
    'কারিগরি': ('technical', None),
    'মাধ্যমিক': ('secondary', None),
    'ক্ষুদ্র নৃগোষ্ঠি': ('ethnic_minority', None),
    'ক্ষুদ্র নৃগোষ্ঠীর': ('ethnic_minority', None),
    'ক্ষুদ্র নৃগোষ্ঠির': ('ethnic_minority', None),
    'ক্ষুদ্র নৃ-গোষ্ঠী': ('ethnic_minority', None),
    'প্রাথমিক': ('primary', None),
}

ORDINAL_TOK = r'(?:ম|র্থ|ষ্ঠ|মে|শ|তম)'
ARABIC_ORD_RE = re.compile(r'(\d{1,2})\s*(?:st|nd|rd|th)\b', re.I)
WORD_NUM = {'প্রথম': 1, 'দ্বিতীয়': 2, 'দিতীয়': 2, 'তৃতীয়': 3, 'চতুর্থ': 4,
            'পঞ্চম': 5, 'ষষ্ঠ': 6, 'সপ্তম': 7, 'অষ্টম': 8, 'নবম': 9,
            'দশম': 10, 'একাদশ': 11, 'দ্বাদশ': 12}
WORD_NUM_RE = re.compile('|'.join(WORD_NUM))
CONNECTOR = r'(?:\s*(?:ও|,|-|–|থেকে)\s*)'


def detect_classes(text):
    """All class numbers the text mentions, most specific form first."""
    text = fold_chars(text)
    t = bn2ascii(text)
    found = collections.OrderedDict()

    def put(n):
        if 1 <= n <= 12 and n not in found:
            found[n] = True

    # explicit ranges: ৯-১০, ৯ ও ১০, ৯ম-১০ম
    for m in re.finditer(r'(\d{1,2})\s*' + ORDINAL_TOK + r'?\s*[-–]\s*'
                         r'(\d{1,2})\s*' + ORDINAL_TOK + r'?', t):
        put(int(m.group(1))); put(int(m.group(2)))
    # word ordinals joined by ও / comma: নবম ও দশম, একাদশ-দ্বাদশ
    word_pair_re = re.compile('(' + WORD_NUM_RE.pattern + ')' + CONNECTOR
                              + '(' + WORD_NUM_RE.pattern + ')')
    for m in word_pair_re.finditer(text):
        put(WORD_NUM[m.group(1)]); put(WORD_NUM[m.group(2)])
    # Bengali numeral ordinals: ৯ম, ১০ম, ১১শ
    for m in re.finditer(r'([\u09e6-\u09ef]{1,2})' + ORDINAL_TOK, text):
        put(int(bn2ascii(m.group(1))))
    for m in re.finditer(r'([\u09e6-\u09ef]{1,2})\s*(?:নং\s*)?শ্রেণি', text):
        put(int(bn2ascii(m.group(1))))
    for m in ARABIC_ORD_RE.finditer(t):
        put(int(m.group(1)))
    for m in re.finditer(r'(\d{1,2})\s*(?:নং\s*)?শ্রেণি', t):
        put(int(m.group(1)))
    for m in WORD_NUM_RE.finditer(text):
        put(WORD_NUM[m.group(0)])
    return list(found.keys())


def detect_level_from_text(text):
    """(level, classId-or-None) from the document heading/filename."""
    text = fold_chars(text)
    for key, val in LEVEL_BY_FOLDER_TEXT.items():
        if key in text:
            return val
    return (None, None)


def resolve_level(title):
    classes = detect_classes(title)
    level, class_id = detect_level_from_text(title)
    if level and level != 'ethnic_minority':
        if classes:
            return level, 'class_%d' % classes[0], classes
        if level in ('dakhil', 'ebtedayi', 'technical', 'pre_primary'):
            return level, level, classes
        return level, level, classes
    if level == 'ethnic_minority':
        if classes:
            # an ethnic-minority textbook that is taught in a numbered class
            return 'ethnic_minority', 'class_%d' % classes[0], classes
        return 'ethnic_minority', 'pre_primary', classes
    if classes:
        n = classes[0]
        if n <= 5:
            return 'primary', 'class_%d' % n, classes
        if n <= 10:
            return 'secondary', 'class_%d' % n, classes
        return 'higher_secondary', 'class_%d' % n, classes
    return None, None, classes


def detect_language(title, header_lang):
    """Column header wins; otherwise the title's own wording."""
    if header_lang:
        return header_lang
    low = title.lower()
    if 'english' in low or 'ইংরেজি' in title:
        return 'en'
    for name, code in (('চাকমা', 'ccp'), ('মারমা', 'mhx'), ('গারো', 'grt'),
                       ('সাদরি', 'sdr'), ('ত্রিপুরা', 'tpe')):
        if name in title:
            return code
    if re.search(r'[\u0980-\u09FF]', title):
        return 'bn'
    if re.search(r'[A-Za-z]', title):
        return 'en'
    return 'unknown'


# ------------------------------------------------------------- slugging ---
BN_TRANSLIT = {
    'অ': 'o', 'আ': 'a', 'ই': 'i', 'ঈ': 'i', 'উ': 'u', 'ঊ': 'u', 'ঋ': 'ri',
    'এ': 'e', 'ঐ': 'oi', 'ও': 'o', 'ঔ': 'ou',
    'ক': 'k', 'খ': 'kh', 'গ': 'g', 'ঘ': 'gh', 'ঙ': 'ng',
    'চ': 'ch', 'ছ': 'chh', 'জ': 'j', 'ঝ': 'jh', 'ঞ': 'n',
    'ট': 't', 'ঠ': 'th', 'ড': 'd', 'ঢ': 'dh', 'ণ': 'n',
    'ত': 't', 'থ': 'th', 'দ': 'd', 'ধ': 'dh', 'ন': 'n',
    'প': 'p', 'ফ': 'ph', 'ব': 'b', 'ভ': 'bh', 'ম': 'm',
    'য': 'j', 'র': 'r', 'ল': 'l', 'শ': 'sh', 'ষ': 'sh', 'স': 's', 'হ': 'h',
    'ড়': 'r', 'ঢ়': 'rh', 'য়': 'y', 'ৎ': 't', 'ং': 'ng', 'ঃ': 'h', 'ঁ': '',
    'া': 'a', 'ি': 'i', 'ী': 'i', 'ু': 'u', 'ূ': 'u', 'ৃ': 'ri', 'ে': 'e',
    'ৈ': 'oi', 'ো': 'o', 'ৌ': 'ou', '্': '', '়': '', '্য': 'y',
    '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
    '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9',
}


def slugify(text):
    out = []
    for ch in text:
        if ch in BN_TRANSLIT:
            out.append(BN_TRANSLIT[ch])
        elif ch.isalnum() and ord(ch) < 128:
            out.append(ch.lower())
        else:
            out.append('-')
    s = ''.join(out)
    s = re.sub(r'[^a-z0-9]+', '-', s.lower())
    return s.strip('-') or 'book'


# ------------------------------------------------------------- decoding ---
ENCODED_RE = re.compile(r'encoded-content="([^"]*)"')


def decode_document(path):
    """(decoded_payload, doc_title, error) for one HTML file."""
    raw = open(path, 'rb').read().decode('utf-8', 'replace')
    m = re.search(r'(?is)<h2>\s*(.*?)\s*</h2>', raw)
    title = norm_ws(strip_tags(m.group(1))) if m else ''
    em = ENCODED_RE.search(raw)
    if not em:
        return None, title, 'rt-renderer/encoded-content block not found'
    try:
        b64 = em.group(1)
        b64 += '=' * (-len(b64) % 4)          # payloads are stored unpadded
        payload = base64.b64decode(b64).decode('utf-8', 'replace')
    except Exception as exc:
        return None, title, 'base64/UTF-8 decode failed: %s' % exc
    return payload, title, None


# ------------------------------------------------------------ table parse --
def header_languages(header_cells):
    """{col_index: language} for language-specific *content* columns.

    A column whose header names the textbook (…নাম…) is a name column even when
    it also says (বাংলা ভার্সন), so it is never treated as a language column.
    """
    out = {}
    for i, cell in enumerate(header_cells):
        txt = cell_text(cell)
        if is_name_header(txt) or is_serial_header(txt):
            continue
        low = txt.lower()
        for needle, code in LANG_BY_HEADER:
            if needle in txt or needle in low:
                out[i] = code
                break
    return out


def is_serial_header(txt):
    t = norm_ws(txt)
    return t.replace('.', '').replace('।', '').strip() in (
        'ক্রমিক', 'ক্রমিক নং', 'ক্রমিক নম্বর', 'ক্রম', 'নং', 'নম্বর')


def is_download_header(txt):
    """A generic download/PDF column, as opposed to a named language column."""
    t = norm_ws(txt).lower()
    return ('ডাউনলোড' in t or 'ডাউনলোড' in t or 'download' in t
            or 'পিডিএফ' in t or 'pdf' in t)


def is_index_page(headers, rows):
    """True for a page that only navigates to other pages.

    Such a page has no download column, and every link it does contain points
    at an internal portal page rather than at a textbook file.
    """
    joined = ' '.join(norm_ws(h) for h in headers)
    if any(k in joined for k in ('ডাউনলোড', 'ডাউনলোড', 'পিডিএফ', 'PDF', 'pdf')):
        return False
    if any(is_name_header(h) for h in headers):
        return False
    for row in rows:
        for cell in split_cells(row):
            for u in cell_links(cell):
                if is_pdf_link(u):
                    return False
    return True


def is_name_header(txt):
    """Header cells that name the textbook column.

    Deliberately narrow: a real book title such as "আমার বই" or
    "গল্পের বই" must never be mistaken for a repeated header row.
    """
    t = norm_ws(txt)
    return 'নাম' in t or 'পুস্তক' in t or t in ('তালিকা', 'পাঠ্যপুস্তকের তালিকা')


def parse_table(table_html, clipped=False):
    """All (title, language, links) triples found in one HTML table.

    Returns (rows, headers, clipped_tail_ignored).
    """
    rows = split_rows(table_html)
    tail_dropped = False
    if clipped and rows:
        # the last <tr> of a clipped payload is usually itself incomplete
        last_open = table_html.rfind('<tr')
        last_close = table_html.rfind('</tr>')
        if last_open > last_close:
            tail_dropped = True
    if len(rows) < 2:
        return [], None, tail_dropped
    header_cells = split_cells(rows[0])
    if not header_cells:
        return [], None, None
    headers = [cell_text(c) for c in header_cells]

    serial_idx = next((i for i, h in enumerate(headers) if is_serial_header(h)), None)
    name_idx = next((i for i, h in enumerate(headers) if is_name_header(h)), None)
    if name_idx is None:
        name_idx = next((i for i, h in enumerate(headers)
                         if i != serial_idx), None)
    if name_idx is None:
        return [], None, None

    lang_cols = header_languages(header_cells)
    # the name column may itself carry "(বাংলা ভার্সন)" / "(ইংরেজি ভার্সন)"
    name_lang = lang_cols.pop(name_idx, None)
    lang_cols.pop(serial_idx, None)

    link_cols = sorted(i for i in lang_cols
                       if i != name_idx and i != serial_idx)
    if not link_cols:
        link_cols = [i for i in range(len(header_cells))
                     if i not in (serial_idx, name_idx)]
    else:
        extra = [i for i in range(len(header_cells))
                 if i not in (serial_idx, name_idx) and i not in link_cols]
        link_cols = sorted(link_cols + extra)

    out = []
    is_index = is_index_page(headers, rows[1:])
    for r_i, row in enumerate(rows[1:], start=1):
        cells = split_cells(row)
        if not cells or name_idx >= len(cells):
            continue
        title = cell_text(cells[name_idx])
        # guard: some tables repeat the header row
        if not title or is_name_header(title) or is_serial_header(title):
            continue
        if SERIAL_RE.match(title):
            continue
        produced = False
        for ci in link_cols:
            if ci >= len(cells):
                continue
            links = cell_links(cells[ci])
            hdr_lang = lang_cols.get(ci)
            names_language = ci in lang_cols
            # A named language/version column (বাংলা ভার্সন, ইংরেজি ভার্সন,
            # অডিও ভার্সন) that carries no link is simply a missing edition,
            # not a book entry of its own.
            if names_language and not links:
                continue
            lang = detect_language(title, hdr_lang)
            pdf = pick_pdf(links)
            out.append({'title': title, 'language': lang, 'pdfUrl': pdf,
                        'col': ci, 'row': r_i})
            produced = True
        if not produced and link_cols:
            # a download column with no link at all: record the book, leave
            # pdfUrl empty rather than dropping the book silently
            generic = [ci for ci in link_cols
                       if not (ci in lang_cols)]
            if not generic:
                continue
            out.append({'title': title,
                        'language': detect_language(title, name_lang),
                        'pdfUrl': pick_pdf(cell_links(cells[name_idx]))
                        if name_idx < len(cells) else '',
                        'col': name_idx, 'row': r_i})
    return out, headers, tail_dropped, is_index


# ------------------------------------------------------------ main driver --
def main():
    files = []
    for dp, dn, fn in os.walk(ROOT):
        for f in fn:
            if os.path.splitext(f)[1].lower() in ('.html', '.htm'):
                files.append(os.path.join(dp, f))
    files.sort()

    report = {
        'total_html': len(files),
        'parsed_ok': [], 'errors': [], 'index_only': [],
        'rows': [], 'duplicate_files': [], 'duplicate_rows': [],
        'clipped': [], 'warnings': [],
    }

    seen_payload = {}
    for path in files:
        rel = os.path.relpath(path, ROOT)
        payload, doc_title, err = decode_document(path)
        if err:
            report['errors'].append({'file': rel, 'reason': err})
            continue
        digest = hashlib.sha256(
            re.sub(r'\s+', ' ', payload).encode('utf-8')).hexdigest()
        folder_year = rel.split(os.sep)[0]
        tables = split_tables(payload)
        rows_here = []
        clip_notes = []
        index_tables = 0
        for t_html, is_clipped in tables:
            rows, headers, tail_dropped, is_index = parse_table(t_html, is_clipped)
            if is_index:
                index_tables += 1
                continue
            rows_here.extend(rows)
            if is_clipped:
                clip_notes.append({
                    'unclosed_table': True,
                    'incomplete_last_row_skipped': tail_dropped,
                    'payload_bytes': len(payload),
                    'payload_hits_10k_multiple': len(payload) % 10240 == 0,
                })
        if clip_notes:
            report['clipped'].append({'file': rel, 'title': doc_title,
                                      'notes': clip_notes,
                                      'rows_recovered': len(rows_here)})

        if not rows_here:
            report['index_only'].append({'file': rel, 'title': doc_title,
                                         'tables': len(tables),
                                         'index_tables': index_tables})
            report['parsed_ok'].append(rel)
            continue

        entry = {'file': rel, 'title': doc_title, 'folder_year': folder_year,
                 'digest': digest, 'rows': rows_here,
                 'tables': len(tables), 'clipped': bool(clip_notes),
                 'index_tables': index_tables}
        if digest in seen_payload:
            report['duplicate_files'].append(
                {'file': rel, 'same_as': seen_payload[digest],
                 'title': doc_title})
        else:
            seen_payload[digest] = rel
        report['rows'].append(entry)
        report['parsed_ok'].append(rel)

    json.dump(report, open(os.path.join(REPORT_DIR, '_extract_raw.json'), 'w',
                           encoding='utf-8'), ensure_ascii=False, indent=1)
    print('total html            :', report['total_html'])
    print('parsed ok             :', len(report['parsed_ok']))
    print('parse errors          :', len(report['errors']))
    print('files with book rows  :', len(report['rows']))
    print('total raw book rows   :',
          sum(len(e['rows']) for e in report['rows']))
    print('files clipped @ 10k   :', len(report['clipped']))
    print('duplicate files       :', len(report['duplicate_files']))
    print('index-only (no books) :', len(report['index_only']))
    for e in report['index_only']:
        print('  INDEX-ONLY:', e['file'])
    for e in report['errors']:
        print('  ERROR     :', e['file'], '->', e['reason'])


if __name__ == '__main__':
    main()
