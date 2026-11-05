import datetime
import pandas as pd
import streamlit as st

from core import db, process, parsers, tally, export, reminders, gsheet
from core.heads import HeadBook, alias_key, assign

st.set_page_config(page_title='Credit Card App', page_icon='💳', layout='wide')
db.conn().close()

# ---------------------------------------------------------------- "I have paid" link from the reminder email
qp = st.query_params
if 'paid' in qp:
    sid = reminders.verify(qp['paid'])
    if sid:
        status = reminders.mark_paid(sid, 'I-have-paid link')
        st.success('Thank you - marked as %s. No more reminders for this statement.' % status)
    else:
        st.error('This link is not valid.')
    st.stop()

st.title('💳 Credit Card App')
if st.session_state.get('flash'):
    st.success(st.session_state.pop('flash'))
tabs = st.tabs(['1 Upload', '2 Review & heads', '3 Tally', '4 Reminders', '5 Settings'])


def cards_df():
    c = db.conn(); rows = [dict(r) for r in c.execute('SELECT * FROM cards ORDER BY company, name')]; c.close()
    return rows


def label(card):
    return '%s xx%s  [%s]' % (card['name'], card['last4'], card['company'])


def statements_for_select():
    c = db.conn()
    rows = [dict(r) for r in c.execute('SELECT s.*, c.name, c.last4, c.company FROM statements s JOIN cards c ON c.id=s.card_id ORDER BY s.id DESC')]
    c.close()
    return rows


def stmt_label(s):
    return '#%d  %s xx%s  due %s  (%s)' % (s['id'], s['name'], s['last4'], s['due_date'] or '-', s['file_name'][-30:])


# ================================================================= 1 UPLOAD
with tabs[0]:
    cards = cards_df()
    if not cards:
        st.info('No cards in the app yet. Run seed_cards.py in the CreditCardApp folder to load the card list.')
    else:
        phys = {}
        for cd in cards: phys.setdefault((cd['bank'], cd['last4']), cd)
        physical = sorted(phys.values(), key=lambda c: (c['bank'], c['last4']))
        pick = st.selectbox('Card', physical, format_func=lambda c: '%s xx%s (%s)' % (c['name'], c['last4'], c['bank']), key='up_card')
        files = st.file_uploader('Statement PDF(s) for this card', type=['pdf'], accept_multiple_files=True, key='up_files')
        if st.button('Read statements', type='primary', disabled=not files):
            for f in files:
                with st.spinner('Reading %s ...' % f.name):
                    r = process.ingest(pick['id'], f.name, f.getvalue())
                (st.success if r['ok'] else st.error)('%s: %s' % (f.name, r['message']))
                if r['ok']:
                    st.session_state['rv_stmt'] = r['statement_id']; st.session_state['ta_stmt'] = r['statement_id']
                    i = r['info']
                    st.caption('Due date %s | total due %s | minimum due %s %s' % (i.get('due_date'), i.get('total_due'), i.get('min_due'), '(no payment due)' if i.get('no_due') else ''))
    st.subheader('Statements in the app')
    st.caption('If the due date / amount was not read correctly, correct it here and press Save. Reminders use these values.')
    srows = statements_for_select()
    if srows:
        df = pd.DataFrame([{k: s[k] for k in ('id', 'name', 'last4', 'file_name', 'statement_date', 'due_date', 'total_due', 'min_due', 'status')} for s in srows])
        edited = st.data_editor(df, hide_index=True, use_container_width=True, disabled=['id', 'name', 'last4', 'file_name', 'status'], key='stmt_editor')
        if st.button('Save due-date changes'):
            c = db.conn()
            for _, r in edited.iterrows():
                c.execute('UPDATE statements SET statement_date=?, due_date=?, total_due=?, min_due=? WHERE id=?',
                          (r['statement_date'] or None, r['due_date'] or None, None if pd.isna(r['total_due']) else float(r['total_due']), None if pd.isna(r['min_due']) else float(r['min_due']), int(r['id'])))
            c.commit(); c.close(); st.success('Saved.')

# ================================================================= 2 REVIEW & HEADS
with tabs[1]:
    srows = statements_for_select()
    if not srows:
        st.info('Upload a statement first.')
    else:
        by_id = {r['id']: r for r in srows}
        if st.session_state.get('rv_stmt') not in by_id: st.session_state['rv_stmt'] = srows[0]['id']
        s = by_id[st.selectbox('Statement', list(by_id), format_func=lambda i, by_id=by_id: stmt_label(by_id[i]) if i in by_id else str(i), key='rv_stmt')]
        txs = db.statement_txns(s['id'])
        top = st.columns([1, 1, 1, 2])
        if top[0].button('Mark ALL lines: FV'):
            process.set_company([t['id'] for t in txs if t['status'] not in ('payment', 'posted')], 'FV'); st.rerun()
        if top[1].button('Mark ALL lines: HPZ Chennai'):
            process.set_company([t['id'] for t in txs if t['status'] not in ('payment', 'posted')], 'HPZ'); st.rerun()
        if top[2].button('Clear all'):
            process.set_company([t['id'] for t in txs if t['status'] not in ('payment', 'posted')], ''); st.rerun()
        if top[3].button('Re-check heads against the party sheet'):
            n = process.reapply_heads(s['id']); st.success('Re-checked %d lines.' % n); st.rerun()
        cnt = pd.Series([t['status'] for t in txs]).value_counts().to_dict() if txs else {}
        st.write('**%d lines** — ready to post: %d | company not chosen: %d | no head: %d | payments to card: %d | posted: %d'
                 % (len(txs), cnt.get('ready', 0), cnt.get('no_company', 0), cnt.get('no_head', 0), cnt.get('payment', 0), cnt.get('posted', 0)))
        st.caption('Choose **Post to** for every line (FV or HPZ Chennai) and press Save. The Excel and the Tally posting use this choice. Nothing is guessed.')
        only_open = st.checkbox('Show only lines where Post to is empty', key='rv_only_empty')
        if txs:
            LBL = db.COMPANY_LABEL
            rows = [t for t in txs if not (only_open and t['company'] and t['status'] != 'payment')]
            df = pd.DataFrame([{'id': t['id'], 'Post to': LBL.get(t['company'] or '', ''), 'date': t['date'], 'desc': t['desc'], 'amount': t['amount'], 'Dr/Cr': t['drcr'],
                                'party': t['party'], 'expense ledger': t['ledger'], 'style': t['style'], 'status': t['status']} for t in rows])
            ed = st.data_editor(df, hide_index=True, use_container_width=True, height=420, key='rv_ed_%d_%s' % (s['id'], only_open),
                                disabled=[c for c in df.columns if c != 'Post to'],
                                column_config={'Post to': st.column_config.SelectboxColumn('Post to', options=['', 'FV', 'HPZ Chennai'], required=False),
                                               'id': None})
            if st.button('Save "Post to" choices', type='primary'):
                changed = 0
                for (_, a), (_, b) in zip(df.iterrows(), ed.iterrows()):
                    if (a['Post to'] or '') != (b['Post to'] or '') and a['status'] not in ('payment', 'posted'):
                        process.set_company([int(a['id'])], db.LABEL_COMPANY.get(b['Post to'] or '', '')); changed += 1
                st.success('Saved %d change(s).' % changed); st.rerun()
        # ---- assign heads for merchants not in the list
        nh = [t for t in txs if t['status'] == 'no_head']
        if nh:
            st.subheader('Merchants with no head — assign once, the app remembers')
            hb = process.book()
            if hb.error: st.warning(hb.error)
            groups = {}
            for t in nh: groups.setdefault(alias_key(t['desc']), []).append(t)
            for k, l in groups.items():
                with st.expander('%s  —  %d line(s), Rs %s   e.g. "%s"' % (k or '(blank)', len(l), '{:,.2f}'.format(sum(t['amount'] for t in l)), l[0]['desc'][:50])):
                    parties = ['(new party)'] + [e['party'] for e in hb.entries]
                    ch = st.selectbox('Pick the party from your sheet, or add a new one', parties, key='p_' + k)
                    if ch == '(new party)':
                        a, b2 = st.columns(2)
                        party = a.text_input('Party name', value=k.title(), key='pn_' + k)
                        head = b2.text_input('Head (group)', key='h_' + k)
                        sub = a.text_input('Sub head / expense ledger in Tally', key='s_' + k)
                        tds = b2.selectbox('TDS applicable?', ['No', 'Yes'], key='t_' + k)
                    else:
                        e = [x for x in hb.entries if x['party'] == ch][0]
                        party, head, sub, tds = e['party'], e['head'], e['sub'] or '', e['tds']
                        st.caption('Head: %s  >  %s   (TDS: %s)' % (head, sub or 'N/A', tds))
                    style = st.radio('Booking style', ['auto (by TDS)', 'direct', 'vendor'], horizontal=True, key='st_' + k)
                    to_sheet = False
                    if ch == '(new party)':
                        to_sheet = st.checkbox('Also add this party to the Google Sheet (tab "%s")' % gsheet.additions_tab(), value=True, key='gs_' + k)
                    if st.button('Save for this merchant', key='sv_' + k):
                        assign('*', l[0]['desc'], party, head, sub, tds, '' if style.startswith('auto') else style)
                        msg = ''
                        if to_sheet:
                            try: msg = gsheet.add_party(party, head, sub, tds, l[0]['desc'])
                            except Exception as e: msg = 'Saved in the app, but could not write to the Google Sheet: %s' % e
                        process.book(fresh=True); process.reapply_heads(); st.session_state['flash'] = 'Saved. ' + msg; st.rerun()

# ================================================================= 3 TALLY
with tabs[2]:
    srows = statements_for_select()
    if not srows:
        st.info('Upload a statement first.')
    else:
        by_id = {r['id']: r for r in srows}
        if st.session_state.get('ta_stmt') not in by_id: st.session_state['ta_stmt'] = srows[0]['id']
        s = by_id[st.selectbox('Statement', list(by_id), format_func=lambda i, by_id=by_id: stmt_label(by_id[i]) if i in by_id else str(i), key='ta_stmt')]
        txs = db.statement_txns(s['id'])
        LBL = db.COMPANY_LABEL
        by = {co: [t for t in txs if t['status'] == 'ready' and t['company'] == co] for co in ('HPZ', 'FV')}
        st.write('Ready to post — **HPZ Chennai: %d**  |  **FV: %d**   ·   company not chosen: %d   ·   no head: %d   ·   already posted: %d'
                 % (len(by['HPZ']), len(by['FV']), sum(1 for t in txs if t['status'] == 'no_company'), sum(1 for t in txs if t['status'] == 'no_head'), sum(1 for t in txs if t['status'] == 'posted')))
        # ---------------------------------------------------------------- A. Excel
        st.subheader('A. Excel to import in Tally')
        st.caption('Every sheet shows the company. Each company has its own import sheet - import "Tally Import - HPZ Chennai" while HPZ is open and "Tally Import - FV" while FV is open.')
        c1, c2, c3 = st.columns(3)
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        stamp = '%s xx%s %s' % (s['name'], s['last4'], s['due_date'] or '')
        both, _ = export.build(txs)
        c1.download_button('Both companies (one Excel)', both, file_name='Tally import BOTH %s.xlsx' % stamp, mime=mime)
        hp, _ = export.build(txs, only='HPZ')
        c2.download_button('HPZ Chennai only', hp, file_name='Tally import HPZ Chennai %s.xlsx' % stamp, mime=mime)
        fv, _ = export.build(txs, only='FV')
        c3.download_button('FV only', fv, file_name='Tally import FV %s.xlsx' % stamp, mime=mime)
        # ---------------------------------------------------------------- B. Post
        st.subheader('B. Post straight to Tally')
        st.caption('Each company is posted separately and only if that company is open in Tally. Lines for a company that is not open stay waiting.')
        try:
            open_cos = tally.companies()
            tally_err = None
        except Exception as e:
            open_cos, tally_err = [], repr(e)
        if tally_err:
            st.warning('Could not talk to Tally (%s). Is Tally open with the port enabled?' % tally_err)
        else:
            st.write('Open in Tally now:', ', '.join(open_cos) or '(none)')
        for co in ('HPZ', 'FV'):
            tcname = db.get('tally_company_' + co)
            lines = by[co]
            is_open = tcname in open_cos
            with st.expander('%s — %d line(s) ready — Tally company %s' % (LBL[co], len(lines), ('OPEN' if is_open else 'NOT OPEN')), expanded=bool(lines)):
                if not lines:
                    st.caption('Nothing ready for %s.' % LBL[co]); continue
                missing_card = [t for t in lines if not (db.card_profile(t['bank'], t['last4'], co) or {}).get('ledger')]
                if missing_card:
                    st.error('This card has no ledger name for %s - this card has no profile for that company - add it to seed_cards.py and run it again.' % LBL[co])
                    continue
                prev = []
                for t in lines:
                    prof = db.card_profile(t['bank'], t['last4'], co)
                    for v in tally.build_vouchers(t, prof):
                        prev.append(dict(company=LBL[co], date=v['date'], type=v['vtype'], ref=v['reference'], lines=' | '.join('%s %s %.2f' % (l, sd, am) for l, sd, am in v['lines'])))
                st.dataframe(pd.DataFrame(prev), hide_index=True, use_container_width=True, height=220)
                if st.button('Check the ledgers exist in %s' % LBL[co], key='chk_' + co, disabled=not is_open):
                    try:
                        need = set()
                        for t in lines:
                            need.add(db.card_profile(t['bank'], t['last4'], co)['ledger']); need.add(t['ledger'])
                            if t['style'] == 'vendor': need.add(t['vendor_ledger'] or t['party'])
                        miss = [n for n in sorted(x for x in need if x) if tally.ledger_parent(tcname, n) is None]
                        st.success('All ledgers exist in %s.' % LBL[co]) if not miss else st.error('Missing in %s Tally - create these first: %s' % (LBL[co], '; '.join(miss)))
                    except Exception as e:
                        st.error('Could not check: %r' % e)
                if not is_open:
                    st.warning('%s is not open in Tally - open it, then reload this page. These lines stay waiting.' % tcname)
                ok = st.checkbox('I checked the preview. Post these %d %s lines into "%s"' % (len(lines), LBL[co], tcname), key='ok_' + co)
                if st.button('Post %s lines to Tally' % LBL[co], type='primary', key='post_' + co, disabled=not (ok and is_open)):
                    try:
                        if tcname not in tally.companies():
                            raise RuntimeError('"%s" is not open in Tally.' % tcname)
                        done = 0
                        for t in lines:
                            prof = db.card_profile(t['bank'], t['last4'], co)
                            res = tally.post_vouchers(tcname, tally.build_vouchers(t, prof))
                            if res['errors'] or not res['created']:
                                st.error('Stopped at %s "%s": Tally said %s' % (t['date'], t['desc'][:30], res['messages'] or res['raw'][:200])); break
                            c = db.conn(); c.execute("UPDATE txns SET status='posted', posted_at=? WHERE id=?", (db.now(), t['id'])); c.commit(); c.close(); done += 1
                        if done: st.success('Posted %d %s line(s) into "%s".' % (done, LBL[co], tcname))
                    except Exception as e:
                        st.error('Posting stopped: %r' % e)

# ================================================================= 4 REMINDERS
with tabs[3]:
    st.write('A reminder goes out only while a statement is **unpaid**. Paid = the "I have paid" link in the email, the button below, or the next statement showing the payment. Tally is not used.')
    rr = reminders.rows()
    if not rr:
        st.info('No statements with a due date yet.')
    else:
        df = pd.DataFrame([{k: r[k] for k in ('id', 'name', 'last4', 'company', 'due_date', 'days_left', 'total_due', 'min_due', 'state', 'paid_how')} for r in rr])
        st.dataframe(df[df['state'] != 'No payment due'], hide_index=True, use_container_width=True, height=330)
        pend = reminders.pending()
        c1, c2, c3 = st.columns(3)
        with c1:
            if pend:
                pick = st.selectbox('Mark as paid', pend, format_func=lambda r: '#%d %s xx%s due %s (Rs %s)' % (r['id'], r['name'], r['last4'], r['due_date'], r['total_due']))
                amt = st.number_input('Amount paid', value=float(pick['total_due'] or 0), key='paid_amt')
                if st.button('Mark paid'):
                    st.success('Marked ' + reminders.mark_paid(pick['id'], 'marked in app', amt)); st.rerun()
        with c2:
            paid = [r for r in rr if r['state'] == 'Paid']
            if paid:
                u = st.selectbox('Undo paid', paid, format_func=lambda r: '#%d %s xx%s due %s' % (r['id'], r['name'], r['last4'], r['due_date']))
                if st.button('Undo'): reminders.unmark(u['id']); st.rerun()
        with c3:
            st.write('Reminders due today: **%d**' % sum(1 for r in pend if reminders.should_send(r)))
            if st.button('Show what would be sent (dry run)'):
                st.write(reminders.run_once(send=False) or 'Nothing to send today.')
            if st.button('Send reminders now'):
                try: st.write(reminders.run_once(send=True) or 'Nothing to send today.')
                except Exception as e: st.error(repr(e))
    st.caption('Daily automatic sending: schedule send_reminders.bat in Windows Task Scheduler (once a day, e.g. 9 AM).')

# ================================================================= 5 SETTINGS
with tabs[4]:
    st.subheader('Google Sheets (service account, read-only)')
    for k, lbl in [('google_key', 'Service-account JSON key file'), ('gsheet_party_id', 'Party / head-of-accounts sheet ID (the long text between /d/ and /edit in its link)'),
                   ('gsheet_pw_id', 'Card-password sheet ID'), ('sheet_HPZ', 'Tab with the party-wise heads (used for FV and HPZ Chennai)')]:
        v = st.text_input(lbl, db.get(k), key='set_' + k)
        if v != db.get(k): db.put(k, v)
    if st.button('Test Google connection'):
        try:
            if not gsheet.configured(): raise RuntimeError('Key file not found: ' + db.get('google_key'))
            st.success('Party sheet tabs: ' + ', '.join(gsheet.tabs(db.get('gsheet_party_id'))))
            pr = gsheet.password_rows(force=True)
            st.success('Password sheet: %d cards found' % len(pr))
            hb = HeadBook('ALL')
            (st.warning if hb.error else st.success)('Heads: %s' % (hb.error or '%d parties read from %s' % (len(hb.entries), hb.source)))
        except Exception as e:
            st.error(str(e))
    st.caption('Share both sheets with the service-account email (Viewer). Fallback Excel file used only if Google is unreachable:')
    v = st.text_input('Fallback Excel file', db.get('party_sheet'), key='set_party_sheet')
    if v != db.get('party_sheet'): db.put('party_sheet', v)
    st.subheader('Tally')
    for k, lbl in [('tally_url', 'Tally address'), ('tally_company_HPZ', 'HPZ company name in Tally'), ('tally_company_FV', 'FV company name in Tally')]:
        v = st.text_input(lbl, db.get(k), key='set_' + k)
        if v != db.get(k): db.put(k, v)
    st.subheader('Email reminders')
    for k, lbl, pwd in [('smtp_host', 'SMTP server', False), ('smtp_port', 'SMTP port', False), ('smtp_user', 'SMTP user (email)', False), ('smtp_pass', 'SMTP password / app password', True),
                        ('mail_from', 'Send from', False), ('mail_to', 'Send reminders to (comma separated)', False), ('base_url', 'App address used in the "I have paid" link', False),
                        ('remind_days', 'Remind this many days before due date', False)]:
        v = st.text_input(lbl, db.get(k), type='password' if pwd else 'default', key='set_' + k)
        if v != db.get(k): db.put(k, v)
    o = st.checkbox('Keep reminding every day after the due date until paid', db.get('overdue_daily') == '1')
    db.put('overdue_daily', '1' if o else '0')
    m = st.checkbox('Paying only the minimum counts as paid', db.get('min_counts_as_paid') == '1')
    db.put('min_counts_as_paid', '1' if m else '0')
    if st.button('Send a test email'):
        try:
            reminders.send_mail(db.get('mail_to'), 'Credit Card App test', '<p>Test mail from the Credit Card App.</p>'); st.success('Sent.')
        except Exception as e: st.error(repr(e))
