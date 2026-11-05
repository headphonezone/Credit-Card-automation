"""Read the two Google Sheets (party heads, card passwords) with a service account.

* the app only ever WRITES to one extra tab ('App additions') of the party sheet, never to your main tab or the password sheet;
* values are cached for a few minutes in memory; the last good copy is also kept on disk (the password copy is
  encrypted) so reminders still work if Google is unreachable;
* passwords are looked up when a PDF is opened - they are not copied into the app's own card table.
"""
import json, os, re, time
from . import db

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']      # read + write (new parties go to the additions tab)
TTL = 300
_mem = {}
_client = None

BANK_WORDS = {'INDUS': 'INDUSIND', 'INDUSIND': 'INDUSIND', 'INDUSLAND': 'INDUSIND', 'YES': 'YES', 'KOTAK': 'KOTAK', 'AXIS': 'AXIS', 'AMEX': 'AMEX',
              'HDFC': 'HDFC', 'ICICI': 'ICICI', 'IDFC': 'IDFC', 'SBI': 'SBI', 'UBI': 'UBI'}


class GoogleError(Exception):
    pass


def _secret_info():
    """Service-account key stored in Streamlit secrets ([gcp_service_account]) when the app is hosted."""
    try:
        import streamlit as st
        info = st.secrets.get('gcp_service_account')
        return dict(info) if info else None
    except Exception:
        return None


def configured():
    return (bool(db.get('google_key')) and os.path.exists(db.get('google_key'))) or bool(_secret_info())


def _gc():
    global _client
    if _client is None:
        import gspread
        try:
            if db.get('google_key') and os.path.exists(db.get('google_key')):
                _client = gspread.service_account(filename=db.get('google_key'), scopes=SCOPES)
            else:
                _client = gspread.service_account_from_dict(_secret_info(), scopes=SCOPES)
        except Exception as e:
            raise GoogleError('Cannot use the Google key file: %r' % e)
    return _client


def _cache_path(name):
    return os.path.join(db.DATA, 'gcache_%s.bin' % name)


def _save_cache(name, obj, secret):
    raw = json.dumps(obj).encode()
    if secret:
        raw = db._fernet().encrypt(raw)
    open(_cache_path(name), 'wb').write(raw)


def _load_cache(name, secret):
    try:
        raw = open(_cache_path(name), 'rb').read()
        return json.loads((db._fernet().decrypt(raw) if secret else raw).decode())
    except Exception:
        return None


def values(sheet_id, tab=None, secret=False, force=False):
    """All cell values (list of rows of strings) of one tab. Falls back to the last good copy."""
    key = (sheet_id, tab)
    if not force and key in _mem and time.time() - _mem[key][0] < TTL:
        return _mem[key][1]
    cname = re.sub(r'\W+', '_', '%s_%s' % (sheet_id[:8], tab or 'first'))
    try:
        sh = _gc().open_by_key(sheet_id)
        ws = sh.worksheet(tab) if tab else sh.sheet1
        vals = ws.get_all_values()
        _mem[key] = (time.time(), vals)
        _save_cache(cname, vals, secret)
        return vals
    except Exception as e:
        old = _load_cache(cname, secret)
        if old is not None:
            _mem[key] = (time.time() - TTL + 60, old)      # retry in a minute
            return old
        raise GoogleError('Could not read the Google Sheet (%s): %s' % (tab or 'first tab', str(e)[:200]))


def tabs(sheet_id):
    return [w.title for w in _gc().open_by_key(sheet_id).worksheets()]


# ----------------------------------------------------------------------------- card passwords
def password_rows(force=False):
    """List of dict(bank, last4, password, folder, alert_mail) from the password sheet."""
    sid = db.get('gsheet_pw_id')
    if not (sid and configured()):
        return []
    vals = values(sid, None, secret=True, force=force)
    out = []
    for r in vals[1:]:
        r = (r + [''] * 5)[:5]
        folder, pw = r[1].strip(), r[2].strip()
        m = re.search(r'([A-Za-z]+)\s*(\d{3,5})\s*$', folder)
        if not (m and pw):
            continue
        bank = BANK_WORDS.get(m[1].upper())
        out.append(dict(bank=bank, bank_word=m[1].upper(), last4=m[2], password=pw, folder=folder, alert_mail=r[4].strip()))
    return out


def card_passwords(bank, last4):
    """Passwords for this card: exact row first, then the other rows of the same bank as fallback."""
    rows = password_rows()
    exact = [r['password'] for r in rows if r['bank'] == bank.upper() and (r['last4'] == last4 or r['last4'].endswith(last4) or last4.endswith(r['last4']))]
    same_bank = [r['password'] for r in rows if r['bank'] == bank.upper()]
    seen, out = set(), []
    for p in exact + same_bank:
        if p not in seen:
            seen.add(p); out.append(p)
    return out, bool(exact)


def alert_mail(bank, last4):
    for r in password_rows():
        if r['bank'] == bank.upper() and r['last4'] == last4 and r['alert_mail']:
            return r['alert_mail']
    return ''


# ----------------------------------------------------------------------------- new parties -> 'App additions' tab
ADD_HDR = ['Party Name', 'Head', 'Sub head', 'TDS Applicability', 'Added on', 'Example description']


def additions_tab():
    return db.get('gsheet_additions_tab') or 'App additions'


def additions_rows(force=False):
    """Rows of the additions tab as dicts (empty list if the tab does not exist yet)."""
    sid = db.get('gsheet_party_id')
    if not (sid and configured()):
        return []
    try:
        vals = values(sid, additions_tab(), force=force)
    except GoogleError:
        return []
    out = []
    for r in vals[1:]:
        r = (r + [''] * 6)[:6]
        if r[0].strip():
            out.append(dict(party=r[0].strip(), head=r[1].strip(), sub=r[2].strip(), tds=r[3].strip()))
    return out


def add_party(party, head, sub, tds='No', example=''):
    """Add (or update) one party in the 'App additions' tab. Returns a message. Never touches the main tab."""
    sid = db.get('gsheet_party_id')
    if not (sid and configured()):
        raise GoogleError('Google is not configured.')
    sh = _gc().open_by_key(sid)
    try:
        ws = sh.worksheet(additions_tab())
    except Exception:
        ws = sh.add_worksheet(title=additions_tab(), rows=500, cols=len(ADD_HDR))
        ws.append_row(ADD_HDR, value_input_option='RAW')
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(ADD_HDR, value_input_option='RAW'); vals = [ADD_HDR]
    row = [party, head, sub or 'N/A', tds or 'No', db.now()[:10], example[:80]]
    norm = lambda x: re.sub(r'[^a-z0-9]+', '', str(x).lower())
    for i, r in enumerate(vals[1:], 2):
        if r and norm(r[0]) == norm(party):
            ws.update(range_name='A%d:F%d' % (i, i), values=[row], value_input_option='RAW')
            values(sid, additions_tab(), force=True)
            return 'Updated "%s" in the Google Sheet (tab %s).' % (party, additions_tab())
    ws.append_row(row, value_input_option='RAW')
    values(sid, additions_tab(), force=True)
    return 'Added "%s" to the Google Sheet (tab %s).' % (party, additions_tab())
