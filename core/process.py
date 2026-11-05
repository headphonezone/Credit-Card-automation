"""Ingest one statement: decrypt -> parse -> store -> find heads."""
import datetime
from . import db, pdfio, parsers
from .heads import HeadBook, is_payment

_books = {}


def book(company=None, fresh=False):
    """One head list (the party sheet) serves both FV and HPZ."""
    if fresh or 'ALL' not in _books:
        _books['ALL'] = HeadBook('ALL')
    return _books['ALL']


def apply_heads(t, card, hb):
    """Fill party/head/ledger/style on a txn dict (in place)."""
    if is_payment(t['desc']):
        t.update(status='payment', party=None, head=None, sub=None, ledger=None, style=None, how='payment to card')
        return
    e, how = hb.find(t['desc'])
    if e:
        t.update(party=e['party'], head=e['head'], sub=e['sub'], ledger=hb.ledger_of(e), style=hb.booking_style(e),
                 vendor_ledger=e.get('vendor_ledger') or e['party'], how=how, status=('ready' if t.get('company') else 'no_company'))
    else:
        t.update(party=None, head=None, sub=None, ledger=None, style=None, vendor_ledger=None, how='', status='no_head')


def ingest(card_id, file_name, data: bytes):
    """Returns dict(ok, message, statement_id, new, dup)"""
    c = db.conn()
    card = c.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
    if not card:
        return dict(ok=False, message='Card not found')
    fh = db.file_hash(data)
    if c.execute('SELECT id FROM statements WHERE file_hash=?', (fh,)).fetchone():
        return dict(ok=False, message='This exact file was already uploaded.')
    try:
        pws = list(db.dec_list(card['passwords']))
        try:
            from . import gsheet
            pws += [p for p in gsheet.card_passwords(card['bank'], card['last4'])[0] if p not in pws]
        except Exception:
            pass                                   # Google unreachable: fall back to passwords saved in the app
        text = pdfio.read_text(data, pws)
    except pdfio.WrongPassword as e:
        return dict(ok=False, message='%s. Add the password in Cards.' % e)
    txs, info = parsers.parse(card['bank'], text)
    if not txs and not info.get('due_date') and not info.get('no_due'):
        return dict(ok=False, message='Nothing could be read - is this a %s statement for card xx%s?' % (card['bank'], card['last4']))
    cur = c.execute('INSERT INTO statements(card_id,file_name,file_hash,statement_date,due_date,total_due,min_due,no_due,status,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (card_id, file_name, fh, info.get('statement_date'), info.get('due_date'), info.get('total_due'), info.get('min_due'),
                     1 if info.get('no_due') else 0, 'no_due' if info.get('no_due') else 'pending', db.now()))
    sid = cur.lastrowid
    hb = book(fresh=True)
    new = dup = 0
    credits = 0.0
    for t in txs:
        t['key'] = db.txn_key(card_id, t)
        if c.execute('SELECT 1 FROM txns WHERE key=?', (t['key'],)).fetchone():
            dup += 1; continue
        row = dict(t); row['amount'] = t['amount']
        apply_heads(row, card, hb)
        c.execute('INSERT INTO txns(statement_id,key,date,desc,amount,drcr,party,head,sub,ledger,style,vendor_ledger,how,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (sid, row['key'], row['date'], row['desc'], row['amount'], row['drcr'], row.get('party'), row.get('head'), row.get('sub'), row.get('ledger'),
                   row.get('style'), row.get('vendor_ledger'), row.get('how'), row['status']))
        new += 1
    c.commit()
    msg = 'Read %d lines (%d new, %d already in the app).' % (len(txs), new, dup)
    # this statement's payment lines can prove the PREVIOUS statement was paid
    paid_msg = auto_confirm_previous(c, card_id, sid, txs)
    c.close()
    return dict(ok=True, message=msg + (' ' + paid_msg if paid_msg else ''), statement_id=sid, new=new, dup=dup, info=info)


def auto_confirm_previous(c, card_id, sid, txs=None):
    """A statement can prove the one BEFORE it was paid: the payments shown in it (lines 'payment received')
    must cover the earlier statement's total due. Works whatever order the PDFs are uploaded in
    (statements of one card are lined up by due date). Only the immediately following statement is used."""
    sts = c.execute("SELECT * FROM statements WHERE card_id=? AND due_date IS NOT NULL ORDER BY due_date, id", (card_id,)).fetchall()
    msgs = []
    for a, b in zip(sts, sts[1:]):
        if a['status'] not in ('pending', 'partial') or not (a['total_due'] or 0) > 0:
            continue
        gap = (datetime.date.fromisoformat(b['due_date']) - datetime.date.fromisoformat(a['due_date'])).days
        if gap > 45 or gap < 20:      # a month is missing between them (or same cycle) - cannot prove anything
            continue
        paid = c.execute("SELECT COALESCE(SUM(amount),0) FROM txns WHERE statement_id=? AND drcr='CR' AND status='payment'", (b['id'],)).fetchone()[0]
        if paid and paid >= a['total_due'] - 1:
            c.execute("UPDATE statements SET status='paid', paid_on=?, paid_amount=?, paid_how='confirmed by next statement' WHERE id=?", (db.now()[:10], paid, a['id']))
            msgs.append('Statement due %s marked paid (the next statement shows %.2f received).' % (a['due_date'], paid))
    c.commit()
    return ' '.join(msgs)


def reapply_heads(statement_id=None):
    """Re-run head matching for lines not yet posted (after the party sheet or assignments changed)."""
    c = db.conn()
    q = "SELECT t.*, s.card_id FROM txns t JOIN statements s ON s.id=t.statement_id WHERE t.status IN ('new','no_head','no_company','ready')"
    args = ()
    if statement_id:
        q += ' AND t.statement_id=?'; args = (statement_id,)
    n = 0
    for r in c.execute(q, args).fetchall():
        card = c.execute('SELECT * FROM cards WHERE id=?', (r['card_id'],)).fetchone()
        t = dict(r)
        apply_heads(t, card, book(fresh=(n == 0)))
        c.execute('UPDATE txns SET party=?,head=?,sub=?,ledger=?,style=?,vendor_ledger=?,how=?,status=? WHERE id=?',
                  (t.get('party'), t.get('head'), t.get('sub'), t.get('ledger'), t.get('style'), t.get('vendor_ledger'), t.get('how'), t['status'], t['id']))
        n += 1
    c.commit(); c.close()
    return n


def set_company(txn_ids, company):
    """Mark lines as FV / HPZ ('' clears). Lines that already have a head become 'ready' (or 'no_company' when cleared)."""
    c = db.conn()
    for i in txn_ids:
        r = c.execute('SELECT status, ledger FROM txns WHERE id=?', (i,)).fetchone()
        if not r or r['status'] in ('posted', 'payment'):
            continue
        st = r['status']
        if r['ledger']:
            st = 'ready' if company else 'no_company'
        c.execute('UPDATE txns SET company=?, status=? WHERE id=?', (company or '', st, i))
    c.commit(); c.close()
