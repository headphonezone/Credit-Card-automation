"""Party -> head of account. Reads the Excel party sheet live (so edits in the sheet flow into the app)
and adds whatever the user assigned inside the app (stored in the database)."""
import re, difflib, os
import openpyxl
from . import db

BUILTIN_ALIAS = [   # (text found in statement description, party name in the sheet)
    ('ADOBE', 'Adobe Systems Software Ireland Ltd'), ('GOOGLE CLOUD', 'Google Cloud India Private Limited'),
    ('ANTHROPIC', 'Claude'), ('CLAUDE', 'Claude'), ('SHOPIFY', 'Shopify Commerce Singapore Pt Ltd'),
    ('TATA AIG', 'Tata AIG General Insurance Company Limited'), ('DELHIVERY', 'Delhivery Limited'),
    ('EXOTEL', 'EXOTEL TECHCOM PRIVATE LIMITED'), ('KLAVIYO', 'Klaviyo Inc'), ('SLACK', 'Slack Technologies Limited'),
    ('SQSP', 'Squarespace Ireland Limited'), ('SQUARESPACE', 'Squarespace Ireland Limited'), ('PADDLE', 'Paddle.com Market Ltd'),
    ('WEBCENTRIC', 'Webcentric'), ('ZAPIER', 'Zapier Inc'), ('ZOHO', 'ZOHO Corporation Private Limited'), ('FIGMA', 'Figma Inc'),
    ('CANVA', 'Canva'), ('PIXELCUT', 'Pixelcut'), ('INDEED', 'Indeed India Operations Private Limited'),
    ('WALKOVER', 'Walkover Web Solutions Private Limited'), ('NTRP', 'NTRP, WPC & Licensing Fee'),
    ('AIRTEL', 'Bharti Airtel Limted'), ('BSNL', 'BSNL'), ('RELIANCE', 'Reliance Retail Limited'),
    ('UBER', 'Uber India Systems Private Limited'), ('AMAZON WEB', 'AMAZON WEB SERVICES PRIVATE LIMITED'),
    ('MERCY ELECTRONICS', 'MERCY ELECTRONICS'), ('JIBBLE', 'JIBBLE'), ('GAMMA', 'Gamma AI'), ('NOTION', 'Notion Labs, Inc.'),
    ('FCY MARKUP', 'Credit Card Foreign Currency, Mark up fee'), ('MARKUP', 'Credit Card Foreign Currency, Mark up fee'),
    ('DCC FEE', 'Credit Card Foreign Currency, Mark up fee'), ('ANNUAL FEE', 'Credit Crad Subscription & Renew'),
    ('JOINING FEE', 'Credit Crad Subscription & Renew'), ('MAKEMYTRIP', 'Makemytrip (India) P Ltd'),
    ('AIR INDIA', 'AIR INDIA LIMITED'), ('INDIGO', 'INTERGLOBE AVIATION LIMITED'), ('LINKEDIN', 'LinkedIn Singapore Pte Ltd'),
    ('ACTARTLY', 'Actartly Technology Private Limited'), ('CIRCLECO', 'Circleco Inc'), ('JOTFORM', 'Jotform'),
    ('FRESHWORK', 'Freshwork'), ('MOCKINGBIRD', 'Mockingbird INC'), ('BOOSTR', 'Boostr, LLC'),
]
NA = (None, '', 'N/A', 'None', 'NONE', 'n/a')
PAYMENT_WORDS = ('PAYMENT RECEIVED', 'PAYMENT RECD', 'PYMT RECD', 'NEFT PAYMENT', 'BBPS', 'RTGS CREDIT', 'FUND TRANSFER',
                 'MB/IB PAYMENT', 'PAYMENT/', '_PAYMENT_', 'CREDIT CARD PAYMENT', 'AUTOPAY', 'THANK YOU')


def norm_name(x):
    x = str(x or '').lower().replace('&', ' and ')
    x = re.sub(r'\b(pvt|private|ltd|limited|inc|llc|llp|pt|pte|co|corporation|the|p|india|systems)\b', ' ', x)
    return re.sub(r'[^a-z0-9]+', '', x)


def alias_key(desc):
    """Reduce a statement description to a stable key (drop digits/refs/city) for the in-app assignments."""
    u = re.sub(r'(RAZ|RAZORPAY|PAYTM|PAYU|ZAAKPAY|BILLDESK|PAY|RSP|EMI|ING|PNB|MSW|ESPY|PYU|CT|WWW)\*', '', desc.upper())
    u = u.split(',')[0]
    u = re.sub(r'[^A-Z ]', ' ', u)
    return ' '.join(u.split()[:3])


def is_payment(desc):
    u = desc.upper()
    return any(w in u for w in PAYMENT_WORDS)


class HeadBook:
    def __init__(self, company):
        self.company = company
        self.entries = []          # from sheet
        self.error = None
        self._load_sheet()
        self._index = [(norm_name(e['party']), e) for e in self.entries]

    # -- sheet ---------------------------------------------------------------------------
    def _raw_rows(self):
        """Rows of the party tab: from the Google Sheet when configured (falls back to the Excel file)."""
        sheet = db.get('sheet_HPZ')
        gid = db.get('gsheet_party_id')
        gerr = None
        if gid:
            try:
                from . import gsheet
                if gsheet.configured():
                    self.source = 'Google Sheet'
                    return gsheet.values(gid, sheet)
                gerr = 'Google key file not found: ' + db.get('google_key')
            except Exception as e:
                gerr = str(e)
        path = db.get('party_sheet')
        if os.path.exists(path):
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            if sheet not in wb.sheetnames:
                raise RuntimeError("Tab '%s' not found (tabs: %s)." % (sheet, ', '.join(wb.sheetnames)))
            self.source = 'Excel file' + (' (Google unavailable: %s)' % gerr if gerr else '')
            return [tuple(r) for r in wb[sheet].iter_rows(values_only=True)]
        raise RuntimeError(gerr or ('Party sheet not found: ' + path))

    def _load_sheet(self):
        self.source = ''
        try:
            rows = [tuple(r) + (None,) * 14 for r in self._raw_rows()]
            hdr = next((i for i, r in enumerate(rows) if any(str(c).strip().lower() == 'party name' for c in r if c)), None)
            if hdr is None:
                self.error = "No 'Party Name' header in tab " + db.get('sheet_HPZ'); return
            h = [str(c or '').strip().lower() for c in rows[hdr]]
            ix = lambda name: h.index(name) if name in h else None
            ip, ih, isub, itds = ix('party name'), ix('head'), ix('sub head'), ix('tds applicability')
            for r in rows[hdr + 1:]:
                if r[ip] and str(r[ip]).strip():
                    self.entries.append(dict(party=str(r[ip]).strip(), head=str(r[ih] or '').strip(), sub=(str(r[isub]).strip() if r[isub] not in NA else None),
                                             tds=str(r[itds] or '').strip() if itds is not None else '', style=None, vendor_ledger=None, source='sheet'))
        except Exception as e:
            self.error = 'Could not read the party sheet for %s: %s' % (self.company, e)
        # parties added from the app (tab 'App additions'); the main tab wins if the same party is in both
        try:
            from . import gsheet
            have = {norm_name(e['party']) for e in self.entries}
            for r in gsheet.additions_rows():
                if norm_name(r['party']) not in have:
                    self.entries.append(dict(party=r['party'], head=r['head'], sub=(r['sub'] if r['sub'] not in NA else None), tds=r['tds'],
                                             style=None, vendor_ledger=None, source='App additions'))
        except Exception:
            pass

    # -- matching ------------------------------------------------------------------------
    def _user_rows(self):
        c = db.conn()
        rows = [dict(r) for r in c.execute('SELECT * FROM party_map')]
        c.close()
        return rows

    def find(self, desc):
        """Return (entry or None, how)."""
        key = alias_key(desc)
        for r in self._user_rows():
            if r['alias'] == key:
                return dict(party=r['party'], head=r['head'], sub=r['sub'], tds=r['tds'] or '', style=r['style'], vendor_ledger=r['vendor_ledger'], source='assigned in app'), 'assigned in app'
        u = desc.upper()
        for k, pn in BUILTIN_ALIAS:
            if k in u:
                for nk, e in self._index:
                    if nk == norm_name(pn):
                        return e, 'name rule'
        n = norm_name(re.split(r',', desc)[0])
        if n:
            for nk, e in self._index:
                if nk and (nk == n or (len(nk) >= 6 and (nk in n or n in nk))):
                    return e, 'name match'
            best = max(self._index, key=lambda ke: difflib.SequenceMatcher(None, ke[0], n).ratio(), default=None)
            if best and difflib.SequenceMatcher(None, best[0], n).ratio() >= 0.86:
                return best[1], 'fuzzy match'
        return None, ''

    @staticmethod
    def ledger_of(e):
        if not e: return None
        return e['head'] if e['sub'] in NA else e['sub']

    @staticmethod
    def booking_style(e):
        """vendor ledger (payment + expense entry) when TDS applies / sheet says so; otherwise direct expense entry."""
        if not e: return None
        if e.get('style') in ('vendor', 'direct'): return e['style']
        return 'vendor' if str(e.get('tds', '')).strip().lower() == 'yes' else 'direct'


def assign(company, desc, party, head, sub, tds='', style='', vendor_ledger=''):
    c = db.conn()
    c.execute('INSERT INTO party_map(company,alias,party,head,sub,tds,style,vendor_ledger) VALUES(?,?,?,?,?,?,?,?) '
              'ON CONFLICT(company,alias) DO UPDATE SET party=excluded.party,head=excluded.head,sub=excluded.sub,tds=excluded.tds,style=excluded.style,vendor_ledger=excluded.vendor_ledger',
              ('*', alias_key(desc), party, head, sub, tds, style, vendor_ledger))
    c.commit(); c.close()
