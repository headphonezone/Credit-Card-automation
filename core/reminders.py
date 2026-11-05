"""Due-date reminders. A reminder is sent only while a statement is still unpaid.
'Paid' is NOT taken from Tally: it comes from (1) the I-have-paid link / button, (2) the next statement showing the
payment, or (3) marking it in the app."""
import hmac, hashlib, smtplib, datetime, html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from . import db


def _secret():
    db.conn().close()
    return open(db.KEY_PATH, 'rb').read()


def token(statement_id):
    return hmac.new(_secret(), str(statement_id).encode(), hashlib.sha256).hexdigest()[:16]


def paid_link(statement_id):
    return '%s/?paid=%d.%s' % (db.get('base_url').rstrip('/'), statement_id, token(statement_id))


def verify(param):
    try:
        sid, tok = param.split('.')
        return int(sid) if hmac.compare_digest(tok, token(int(sid))) else None
    except Exception:
        return None


def mark_paid(statement_id, how, amount=None):
    c = db.conn()
    s = c.execute('SELECT * FROM statements WHERE id=?', (statement_id,)).fetchone()
    if not s:
        c.close(); return None
    need_min = db.get('min_counts_as_paid') == '1'
    target = (s['min_due'] if need_min and s['min_due'] else s['total_due']) or 0
    amount = amount if amount is not None else (s['total_due'] or 0)
    status = 'paid' if amount >= target - 1 else 'partial'
    c.execute('UPDATE statements SET status=?, paid_on=?, paid_amount=?, paid_how=? WHERE id=?', (status, db.now()[:10], amount, how, statement_id))
    c.commit(); c.close()
    return status


def unmark(statement_id):
    c = db.conn(); c.execute("UPDATE statements SET status='pending', paid_on=NULL, paid_amount=NULL, paid_how=NULL WHERE id=?", (statement_id,)); c.commit(); c.close()


def rows(today=None):
    """All statements with a due amount and their reminder state."""
    today = today or datetime.date.today()
    c = db.conn()
    out = []
    latest = {r['card_id']: r['id'] for r in c.execute('SELECT card_id, id FROM statements WHERE due_date IS NOT NULL ORDER BY due_date, id')}
    for s in c.execute('SELECT s.*, c.name, c.last4, c.company, c.bank FROM statements s JOIN cards c ON c.id=s.card_id '
                       'WHERE s.due_date IS NOT NULL ORDER BY s.due_date').fetchall():
        d = dict(s)
        due = datetime.date.fromisoformat(s['due_date'])
        d['days_left'] = (due - today).days
        d['latest'] = (s['id'] == latest.get(s['card_id']))
        if s['no_due'] or not (s['total_due'] or 0) > 0: d['state'] = 'No payment due'
        elif s['status'] == 'paid': d['state'] = 'Paid'
        elif s['status'] == 'partial': d['state'] = 'Partly paid' if d['days_left'] >= 0 else 'Overdue (part paid)'
        elif not d['latest'] and s['status'] == 'pending': d['state'] = 'Superseded by newer statement'
        elif d['days_left'] < 0: d['state'] = 'Overdue'
        elif d['days_left'] == 0: d['state'] = 'Due today'
        else: d['state'] = 'Upcoming'
        out.append(d)
    c.close()
    return out


def pending(today=None):
    return [r for r in rows(today) if r['state'] in ('Upcoming', 'Due today', 'Overdue', 'Partly paid', 'Overdue (part paid)')]


def should_send(r, today=None):
    """Which reminders go out today for this unpaid statement (de-duplicated by the log)."""
    today = today or datetime.date.today()
    days = [int(x) for x in db.get('remind_days').replace(' ', '').split(',') if x != '']
    kind = None
    if r['days_left'] in days: kind = 'D%d' % r['days_left']
    elif r['days_left'] < 0 and db.get('overdue_daily') == '1': kind = 'OVERDUE'
    if not kind: return None
    c = db.conn()
    done = c.execute("SELECT 1 FROM reminders_log WHERE statement_id=? AND kind=? AND substr(sent_at,1,10)=? AND ok=1", (r['id'], kind, today.isoformat())).fetchone()
    c.close()
    return None if done else kind


def build_mail(r):
    amt_total = '{:,.2f}'.format(r['total_due'] or 0); amt_min = '{:,.2f}'.format(r['min_due'] or 0)
    when = 'is due in %d day(s)' % r['days_left'] if r['days_left'] > 0 else ('is due TODAY' if r['days_left'] == 0 else 'is OVERDUE by %d day(s)' % -r['days_left'])
    subj = '[Credit card] %s xx%s %s - Rs %s' % (r['name'], r['last4'], when, amt_total)
    link = paid_link(r['id'])
    body = ('<p>Payment for <b>%s xx%s</b> (%s) %s.</p><table border="0" cellpadding="4">'
            '<tr><td>Due date</td><td><b>%s</b></td></tr><tr><td>Total due</td><td><b>Rs %s</b></td></tr><tr><td>Minimum due</td><td>Rs %s</td></tr></table>'
            '<p>Already paid? <a href="%s">Click here - I have paid</a> and the reminders stop.</p>'
            % (html.escape(r['name']), r['last4'], r['company'], when, r['due_date'], amt_total, amt_min, link))
    return subj, body


def send_mail(to_addr, subj, body_html):
    host, port = db.get('smtp_host'), int(db.get('smtp_port') or 587)
    user, pw, frm = db.get('smtp_user'), db.get('smtp_pass'), db.get('mail_from') or db.get('smtp_user')
    if not (user and pw and to_addr):
        raise RuntimeError('Email settings are incomplete (Settings tab).')
    msg = MIMEMultipart('alternative'); msg['Subject'] = subj; msg['From'] = frm; msg['To'] = to_addr
    msg.attach(MIMEText(body_html, 'html'))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(); s.login(user, pw); s.sendmail(frm, [a.strip() for a in to_addr.split(',')], msg.as_string())


def run_once(send=True, today=None):
    """Called daily (Task Scheduler). Returns a list of what was sent/skipped."""
    today = today or datetime.date.today()
    log = []
    c = db.conn()
    for r in pending(today):
        kind = should_send(r, today)
        if not kind: continue
        subj, body = build_mail(r)
        to = db.get('mail_to')
        try:
            from . import gsheet
            extra = gsheet.alert_mail(r['bank'], r['last4'])
            if extra and extra.lower() not in to.lower():
                to = (to + ',' + extra).strip(',')
        except Exception:
            pass
        if not send:
            log.append((r['name'], r['last4'], r['due_date'], kind, 'would send')); continue
        try:
            send_mail(to, subj, body); ok, err = 1, None
        except Exception as e:
            ok, err = 0, repr(e)
        c.execute('INSERT INTO reminders_log(statement_id,sent_at,kind,to_addr,ok,error) VALUES(?,?,?,?,?,?)', (r['id'], db.now(), kind, to, ok, err))
        c.commit()
        log.append((r['name'], r['last4'], r['due_date'], kind, 'sent' if ok else 'FAILED ' + str(err)))
    c.close()
    return log
