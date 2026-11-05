"""SQLite storage + encrypted card passwords."""
import sqlite3, os, json, hashlib, datetime
from cryptography.fernet import Fernet

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get('CCAPP_DATA') or os.path.join(BASE, 'data')
os.makedirs(DATA, exist_ok=True)
DB_PATH = os.path.join(DATA, 'ccapp.db')
KEY_PATH = os.path.join(DATA, 'secret.key')

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY, name TEXT, bank TEXT, last4 TEXT, company TEXT, ledger TEXT, passwords TEXT DEFAULT '[]',
  UNIQUE(bank,last4,company));
CREATE TABLE IF NOT EXISTS statements(
  id INTEGER PRIMARY KEY, card_id INTEGER, file_name TEXT, file_hash TEXT UNIQUE, statement_date TEXT, due_date TEXT,
  total_due REAL, min_due REAL, no_due INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', paid_on TEXT, paid_amount REAL,
  paid_how TEXT, uploaded_at TEXT);
CREATE TABLE IF NOT EXISTS txns(
  id INTEGER PRIMARY KEY, statement_id INTEGER, key TEXT UNIQUE, date TEXT, desc TEXT, amount REAL, drcr TEXT,
  party TEXT, head TEXT, sub TEXT, ledger TEXT, style TEXT, vendor_ledger TEXT, how TEXT,
  status TEXT DEFAULT 'new', posted_at TEXT, note TEXT, company TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS party_map(
  id INTEGER PRIMARY KEY, company TEXT, alias TEXT, party TEXT, head TEXT, sub TEXT, tds TEXT, style TEXT, vendor_ledger TEXT,
  UNIQUE(company, alias));
CREATE TABLE IF NOT EXISTS reminders_log(
  id INTEGER PRIMARY KEY, statement_id INTEGER, sent_at TEXT, kind TEXT, to_addr TEXT, ok INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULTS = {
    'google_key': r'Z:\Vijay\hpz-picking-app-231c80e84a52.json',
    'gsheet_party_id': '1zHdNHT8mvzZ9f6InLYka7jI0f6QhO5aYIhIXWYgfZzo',
    'gsheet_pw_id': '1RMC7TA2El4TkBmUCWECWEJcI9QvBnnneLsPYLmMk2EY',
    'gsheet_additions_tab': 'App additions',
    'party_sheet': r'Z:\Vijay\HPZ (P) Ltd 25-26 expenses .xlsx',
    'sheet_HPZ': 'Party Wise Expenses Head',
    'sheet_FV': 'FV Party Wise Heads',
    'tally_url': 'http://localhost:9000',
    'tally_company_HPZ': 'Headphone Zone Pvt Ltd Chennai - (from 1-Apr-26)',
    'tally_company_FV': 'Ferrari Video Chennai - (from 1-Apr-26)',
    'smtp_host': 'smtp.gmail.com', 'smtp_port': '587', 'smtp_user': '', 'smtp_pass': '', 'mail_from': '',
    'mail_to': '', 'base_url': 'http://localhost:8501', 'remind_days': '7,3,1,0', 'overdue_daily': '1',
    'min_counts_as_paid': '0',
}


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    if 'company' not in [r[1] for r in c.execute('PRAGMA table_info(txns)')]:
        c.execute("ALTER TABLE txns ADD COLUMN company TEXT DEFAULT ''"); c.commit()
    return c


def _fernet():
    if not os.path.exists(KEY_PATH):
        with open(KEY_PATH, 'wb') as f:
            f.write(Fernet.generate_key())
    return Fernet(open(KEY_PATH, 'rb').read())


def enc_list(items):
    return _fernet().encrypt(json.dumps(list(items)).encode()).decode()


def dec_list(s):
    try:
        return json.loads(_fernet().decrypt(s.encode()).decode())
    except Exception:
        return []


def get(key, c=None):
    own = c is None
    c = c or conn()
    r = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    if own: c.close()
    return r['value'] if r else DEFAULTS.get(key, '')


def put(key, value):
    c = conn()
    c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
    c.commit(); c.close()


def file_hash(data: bytes):
    return hashlib.sha256(data).hexdigest()


def txn_key(card_id, t):
    """Stable id of one statement line: same line uploaded twice (or in 2 overlapping statements) = same key."""
    raw = '%s|%s|%s|%.2f|%s' % (card_id, t['date'], t['desc'].upper(), t['amount'], t['drcr'])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


COMPANY_LABEL = {'HPZ': 'HPZ Chennai', 'FV': 'FV', '': ''}
LABEL_COMPANY = {'HPZ Chennai': 'HPZ', 'FV': 'FV', '': '', None: ''}


def card_profile(bank, last4, company):
    """The card's profile (with its ledger name) for one company, or None."""
    c = conn()
    r = c.execute('SELECT * FROM cards WHERE bank=? AND last4=? AND company=?', (bank, last4, company)).fetchone()
    c.close()
    return dict(r) if r else None


def statement_txns(statement_id):
    c = conn()
    rows = [dict(r) for r in c.execute('SELECT t.*, k.bank AS bank, k.last4 AS last4, k.name AS card_name FROM txns t JOIN statements s ON s.id=t.statement_id '
                                       'JOIN cards k ON k.id=s.card_id WHERE t.statement_id=? ORDER BY t.date, t.id', (statement_id,))]
    c.close()
    return rows


def now():
    return datetime.datetime.now().isoformat(timespec='seconds')
