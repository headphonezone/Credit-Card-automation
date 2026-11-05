"""Statement text -> transactions + statement info (due date, total due, minimum due).

One parser per bank. Input is the plain text of the whole PDF (pdfplumber extract_text).
Every parser returns the same shape so the rest of the app does not care about the bank.
"""
import re, datetime

MON = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
AMT = r'([\d,]*\d\.\d\d)'
BANKS = ['AMEX', 'AXIS', 'HDFC', 'ICICI', 'IDFC', 'INDUSIND', 'KOTAK', 'SBI', 'YES']
CATS = ['MISCELLANEOUS STORES', 'MISCELLANEOUS', 'RETAIL STORES', 'HOME FURNISHING', 'UTILITIES', 'ELECTRONICS', 'RESTAURANTS',
        'GROCERY & SUPERMARKETS', 'GROCERY', 'AUTOMOTIVE', 'HOTELS & RESORTS', 'TRAVEL', 'AIRLINES', 'ENTERTAINMENT', 'EDUCATION',
        'FUEL', 'INSURANCE', 'GOVERNMENT SERVICES', 'MEDICAL', 'APPAREL', 'DEPARTMENT STORES', 'BUSINESS SERVICES', 'JEWELLERY',
        'COMPUTER', 'TELECOM', 'SERVICES', 'HEALTH', 'CLOTHING', 'TRANSPORT', 'RAILWAYS', 'AIRLINE', 'BOOKS', 'FOOD', 'OTHERS',
        'RETAIL OUTLET SERVICES', 'RAILROADS']


def _amt(s):
    return float(s.replace(',', ''))


def _dmy(s, sep='/'):
    d, m, y = s.split(sep)
    return datetime.date(int(y), int(m), int(d))


def _mon(d, mon, y):
    y = int(y)
    y = y + 2000 if y < 100 else y
    return datetime.date(y, MON[mon[:3].lower()], int(d))


def _strip_cat(s):
    for c in sorted(CATS, key=len, reverse=True):
        if s.upper().endswith(' ' + c):
            return s[:-(len(c) + 1)]
    return s


def _txn(d, desc, amt, drcr):
    return dict(date=d.isoformat(), desc=re.sub(r'\s+', ' ', desc or '').strip(' ,-'), amount=amt, drcr=drcr)


def _date_any(s):
    """Parse the date formats banks print for due/statement dates."""
    s = (s or '').strip()
    for pat, fn in [
        (r'(\d{1,2})/(\d{1,2})/(\d{4})', lambda m: datetime.date(int(m[3]), int(m[2]), int(m[1]))),
        (r'(\d{1,2})[- /]([A-Za-z]{3})[a-z]*[-/, ]+(\d{4})', lambda m: _mon(m[1], m[2], m[3])),
        (r"(\d{1,2}) ([A-Za-z]{3}) '(\d{2})", lambda m: _mon(m[1], m[2], m[3])),
        (r'([A-Za-z]{3})[a-z]* ?(\d{1,2}), (\d{4})', lambda m: _mon(m[2], m[1], m[3])),
    ]:
        m = re.search(pat, s)
        if m:
            try:
                return fn(m)
            except Exception:
                pass
    return None


# ----------------------------------------------------------------------------- transactions
def _tx_hdfc(t):
    out = []
    for l in t:
        m = re.match(r'^(\d\d/\d\d/\d{4}) ?\| ?\d\d:\d\d (.*?)(?: \+ \d+)?(?: (\+))? ?C ?' + AMT + r' l$', l)
        if m:
            out.append(_txn(_dmy(m[1]), m[2], _amt(m[4]), 'CR' if m[3] else 'DR'))
    return out


def _tx_indus(t):
    out = []
    for l in t:
        m = re.match(r'^(\d\d/\d\d/\d{4}) (.+?) (\d[\d,]*) ' + AMT + r' (DR|CR)$', l)
        if m:
            out.append(_txn(_dmy(m[1]), _strip_cat(m[2]), _amt(m[4]), m[5]))
    return out


def _tx_kotak(t):
    out = []
    for l in t:
        m = re.match(r'^(\d\d-[A-Za-z]{3}-\d{4}) (.+?) ' + AMT + r'( Cr)?$', l)
        if m:
            d = datetime.datetime.strptime(m[1], '%d-%b-%Y').date()
            out.append(_txn(d, re.sub(r' (Airline|Railroads)$', '', m[2]), _amt(m[3]), 'CR' if m[4] else 'DR'))
    return out


def _tx_axis(t):
    out = []
    for l in t:
        m = re.match(r"^(\d\d \w{3}) '(\d\d) (.*?) ?₹ " + AMT + r' (Debit|Credit)$', l)
        if m:
            out.append(_txn(_mon(m[1][:2], m[1][3:], m[2]), m[3], _amt(m[4]), 'DR' if m[5] == 'Debit' else 'CR'))
            continue
        m = re.match(r'^(\d\d/\d\d/\d{4}) (.*?) ?' + AMT + r' (Dr|Cr)$', l)
        if m:
            out.append(_txn(_dmy(m[1]), _strip_cat(m[2]), _amt(m[3]), m[4].upper()))
    return out


def _tx_idfc(t):
    out = []
    bad = lambda s: (not s) or re.match(r'^(\d\d/|\d\d \w{3} \d\d|<<PAGE|Payments|Purchases|Card Number|Date|Transaction|YOUR)', s) or re.search(AMT, s)
    for i, l in enumerate(t):
        m = re.match(r'^(\d\d/\d\d/\d{4}|\d\d \w{3} \d\d) ?(.*?) ?(?:Convert )?(?:USD [\d.]+ )?' + AMT + r' (DR|CR)$', l)
        if not m:
            continue
        ds = m[1]
        d = _dmy(ds) if '/' in ds else _mon(ds[:2], ds[3:6], ds[7:])
        desc = re.sub(r'\s*Convert$', '', m[2]).strip()
        if desc.startswith('Convert'):
            desc = ''
        if not desc:
            prev = t[i - 1] if i > 0 else ''
            nxt = t[i + 1] if i + 1 < len(t) else ''
            p = '' if bad(prev) else prev
            n = '' if (bad(nxt) or len(nxt) > 26 or re.match(r'^(FT|Payment/)', nxt)) else nxt
            desc = (p + ' ' + n).strip()
        out.append(_txn(d, desc, _amt(m[3]), m[4]))
    return out


def _tx_sbi(t):
    out = []
    for l in t:
        m = re.match(r'^(\d\d \w{3} \d\d) (.*?) ' + AMT + r' (C|D)$', l)
        if m:
            out.append(_txn(_mon(m[1][:2], m[1][3:6], m[1][7:]), re.sub(r'\(Pay in EMIs\)|#', '', m[2]), _amt(m[3]), 'CR' if m[4] == 'C' else 'DR'))
    return out


def _tx_yes(t):
    out = []
    for i, l in enumerate(t):
        m = re.match(r'^(\d\d/\d\d/\d{4}) (.*?) ?' + AMT + r' (Dr|Cr)$', l)
        if not m:
            continue
        prev = t[i - 1] if i > 0 else ''
        desc = prev.rsplit(' - Ref No', 1)[0] if prev.rstrip().endswith('Ref No:') else re.split(r' - Ref No', m[2])[0]
        out.append(_txn(_dmy(m[1]), _strip_cat(desc), _amt(m[3]), m[4].upper()))
    return out


def _tx_icici(t):
    out = []
    for l in t:
        m = re.search(r'(\d\d/\d\d/\d{4}) (\d{9,}) (.*?) (-?\d+)(?: [\d.]+ [A-Z]{3})? ' + AMT + r'( CR)?$', l)
        if m:
            out.append(_txn(_dmy(m[1]), m[3], _amt(m[5]), 'CR' if m[6] else 'DR'))
    return out


def _tx_amex(t, year):
    out = []
    for i, l in enumerate(t):
        m = re.match(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{1,2}) (.*?) ' + AMT + r'$', l)
        if not m:
            continue
        cr = ('PAYMENT RECEIVED' in m[3].upper()) or any(x.strip() == 'CR' or x.strip().endswith(' CR') for x in t[i + 1:i + 3])
        mo = MON[m[1].lower()]
        y = year - 1 if (mo >= 11 and year and datetime.date.today().month < 3) else year
        out.append(_txn(datetime.date(y, mo, int(m[2])), m[3], _amt(m[4]), 'CR' if cr else 'DR'))
    return out


# ----------------------------------------------------------------------------- statement info
def _after(lines, label_re, n=4):
    for i, l in enumerate(lines):
        if re.search(label_re, l, re.I):
            return i, lines[i + 1:i + 1 + n]
    return None, []


def _first_amt(s):
    m = re.findall(AMT, s or '')
    return _amt(m[0]) if m else None


def meta(bank, text, lines):
    """Return dict(due_date, total_due, min_due, no_due) - any value may be None if not found."""
    r = dict(due_date=None, total_due=None, min_due=None, no_due=False, statement_date=None)
    head = '\n'.join(lines[:80])
    try:
        if bank == 'KOTAK':
            m = re.search(r'Total Amount Due \(TAD\)\s*₹?\s*' + AMT, text); r['total_due'] = _amt(m[1]) if m else None
            m = re.search(r'Minimum Amount Due \(MAD\)\s*₹?\s*' + AMT, text); r['min_due'] = _amt(m[1]) if m else None
            m = re.search(r'Due Date\s*(\d\d-[A-Za-z]{3}-\d{4})', text); r['due_date'] = _date_any(m[1]) if m else None
        elif bank == 'AXIS':
            m = re.search(r'Total Payment Due Minimum Payment Due Statement Period Payment Due Date Statement Generation Date\s*\n' + AMT + r' Dr ' + AMT + r' Dr [\d/]+ - [\d/]+ (\d\d/\d\d/\d{4}) (\d\d/\d\d/\d{4})', text)
            if m:
                r.update(total_due=_amt(m[1]), min_due=_amt(m[2]), due_date=_date_any(m[3]), statement_date=_date_any(m[4]))
            else:
                m = re.search(r"Total Payment Due Minimum Payment Due Payment Due Date\s*\n₹ ?-?" + AMT + r" ₹ ?-?" + AMT + r" (\d{1,2} \w{3} '\d\d)", text)
                if m:
                    r.update(total_due=_amt(m[1]), min_due=_amt(m[2]), due_date=_date_any(m[3]))
        elif bank == 'HDFC':
            m = re.search(r'C([\d,]+\.\d\d) (\d{1,2} [A-Za-z]{3}, \d{4})', head)
            if m:
                r['min_due'] = _amt(m[1]); r['due_date'] = _date_any(m[2])
            i, nxt = _after(lines, r'^Past Dues OVER LIMIT')
            if nxt:
                a = re.findall(r'C([\d,]+\.\d\d)', nxt[0])
                if len(a) >= 2:
                    r['total_due'] = _amt(a[-2]); r['min_due'] = r['min_due'] or _amt(a[-1])
        elif bank == 'IDFC':
            i, nxt = _after(lines, r'^(Minimum Amount Due Total Amount Due Payment Due Date|Total Amount Due Minimum Amount Due)', 2)
            if nxt:
                a = re.findall(r'r' + AMT, nxt[0]); d = re.search(r'(\d\d)/([A-Za-z]{3})/(\d{4})', nxt[0])
                if len(a) >= 2:
                    first = lines[i].startswith('Minimum')
                    r['min_due'], r['total_due'] = (_amt(a[0]), _amt(a[1])) if first else (_amt(a[1]), _amt(a[0]))
                if d:
                    r['due_date'] = _mon(d[1], d[2], d[3])
            else:
                m = re.search(r'Total Amount Due\s*\nr' + AMT, text)
                if m:
                    r['total_due'] = _amt(m[1])
                    m = re.search(r'Payment Due Date.*\n.*?(\d\d)/([A-Za-z]{3})/(\d{4})', text)
                    if m: r['due_date'] = _mon(m[1], m[2], m[3])
        elif bank == 'INDUSIND':
            m = re.search(r'Payment Due Date\s*\n(\d\d/\d\d/\d{4})', text); r['due_date'] = _date_any(m[1]) if m else None
            i, nxt = _after(lines, r'Total Amount Due\s*$', 3)
            for l in nxt:
                m = re.search(AMT + r' DR$', l)
                if m: r['total_due'] = _amt(m[1]); break
            i, nxt = _after(lines, r'^Minimum Amount Due\s*$', 3)
            for l in nxt:
                m = re.search(r'\s' + AMT + r'$', l)
                if m: r['min_due'] = _amt(m[1]); break
        elif bank == 'SBI':
            i, nxt = _after(lines, r'^\*Total Amount Due', 1); r['total_due'] = _first_amt(nxt[0]) if nxt else None
            i, nxt = _after(lines, r'^\*\*Minimum Amount Due', 3)
            for l in nxt:
                if re.search(AMT + r'$', l): r['min_due'] = _first_amt(l.split()[-1]); break
            i, nxt = _after(lines, r'Available Credit Limit .* Payment Due Date', 1)
            if nxt:
                if 'NO PAYMENT' in nxt[0].upper(): r['no_due'] = True
                else: r['due_date'] = _date_any(nxt[0])
        elif bank == 'YES':
            i, nxt = _after(lines, r'^Total Amount Due:', 1); r['total_due'] = _first_amt(nxt[0]) if nxt else None
            i, nxt = _after(lines, r'^Minimum Amount Due:', 1); r['min_due'] = _first_amt(nxt[0]) if nxt else None
            m = re.search(r'Payment Due Date:\s*(\d\d/\d\d/\d{4})', text)
            if m: r['due_date'] = _date_any(m[1])
            elif re.search(r'Payment Due Date:\s*NO PYMT', text): r['no_due'] = True
        elif bank == 'ICICI':
            m = re.search(r'P+A+Y+M+E+N+T+ +D+U+E+ +D+A+T+E+\s*\n([A-Za-z]+ \d{1,2}, \d{4})', text); r['due_date'] = _date_any(m[1]) if m else None
            m = re.search(r'S+T+A+T+E+M+E+N+T+ +D+A+T+E+\s*\n([A-Za-z]+ \d{1,2}, \d{4})', text); r['statement_date'] = _date_any(m[1]) if m else None
            i, nxt = _after(lines, r'^Total Amount due', 1); r['total_due'] = _first_amt(nxt[0].replace('`', '')) if nxt else None
            i, nxt = _after(lines, r'^Minimum Amount due', 1); r['min_due'] = _first_amt(nxt[0].replace('`', '')) if nxt else None
        elif bank == 'AMEX':
            m = re.search(r'Balance Due (\d\d-[A-Za-z]{3}-\d{4})', text)
            if m:
                r['due_date'] = _date_any(m[1])
                m2 = re.search(r'= ' + AMT + r' ' + AMT + r'\s*\n', text)
                if m2: r['total_due'] = _amt(m2[2])
            else:
                m = re.search(r'payment of Rs\.' + AMT + r' by\s*\n?(\d\d/\d\d/\d{4})', text)
                if m: r['min_due'] = _amt(m[1]); r['due_date'] = _date_any(m[2])
                m2 = re.search(r'= ' + AMT + r' ' + AMT + r'\s*\n', text)
                if m2: r['total_due'] = _amt(m2[1])
    except Exception:
        pass
    if r['total_due'] is not None and r['total_due'] <= 0:
        r['no_due'] = True
    return r


def _stmt_year(text, lines):
    m = re.search(r'Statement Date\s*[:\-]?\s*(\d\d)[-/ ]?([A-Za-z]{3}|\d\d)[-/ ]?(\d{4})', text)
    m2 = re.findall(r'\b(20\d\d)\b', text[:3000])
    return int(m2[0]) if m2 else datetime.date.today().year


def parse(bank, text):
    """Return (transactions, info)."""
    lines = text.split('\n')
    bank = bank.upper()
    if bank == 'HDFC': tx = _tx_hdfc(lines)
    elif bank == 'INDUSIND': tx = _tx_indus(lines)
    elif bank == 'KOTAK': tx = _tx_kotak(lines)
    elif bank == 'AXIS': tx = _tx_axis(lines)
    elif bank == 'IDFC': tx = _tx_idfc(lines)
    elif bank == 'SBI': tx = _tx_sbi(lines)
    elif bank == 'YES': tx = _tx_yes(lines)
    elif bank == 'ICICI': tx = _tx_icici(lines)
    elif bank == 'AMEX': tx = _tx_amex(lines, _stmt_year(text, lines))
    else: raise ValueError('Unknown bank ' + bank)
    info = meta(bank, text, lines)
    for k in ('due_date', 'statement_date'):
        if info.get(k): info[k] = info[k].isoformat()
    return tx, info
