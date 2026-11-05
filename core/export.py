"""Excel for Tally import. Every sheet shows the company (FV / HPZ Chennai); each company has its own import sheet so
it can be imported into the right Tally company."""
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from . import tally, db

HF = PatternFill('solid', fgColor='1F3864')
RED = PatternFill('solid', fgColor='FFC7CE')
AMB = PatternFill('solid', fgColor='FFEB9C')
CO_FILL = {'HPZ': PatternFill('solid', fgColor='DDEBF7'), 'FV': PatternFill('solid', fgColor='E2EFDA')}
LBL = db.COMPANY_LABEL
IMPORT_HDR = ['Company', 'Date', 'Voucher Type', 'Voucher No', 'Ledger Name', 'Dr/Cr', 'Amount', 'Narration', 'Reference', 'Card', 'Note']


def _style(ws):
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF'); c.fill = HF; c.alignment = Alignment(wrap_text=True, vertical='center')
    for i, col in enumerate(ws.columns, 1):
        m = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(max(11, m + 2), 55)
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions


def _card_txt(t):
    return '%s xx%s' % (t.get('card_name') or '', t.get('last4') or '')


def build(txns, only=None):
    """txns: rows from db.statement_txns (each has company 'FV'/'HPZ'/'', bank, last4, card_name).
    only: 'FV' or 'HPZ' to make a workbook for one company. Returns (xlsx bytes, counts)."""
    wb = Workbook()
    companies = [only] if only else ['HPZ', 'FV']
    sheets = {}
    first = True
    for co in companies:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = 'Tally Import - %s' % LBL[co]
        ws.append(IMPORT_HDR); sheets[co] = ws
    wsall = wb.create_sheet('All lines')
    wsall.append(['Post to', 'Date', 'Card', 'Description', 'Dr/Cr', 'Amount', 'Status', 'Party', 'Expense ledger', 'Booking style', 'Note'])
    wsnh = wb.create_sheet('No Head - assign')
    wsnh.append(['Post to', 'Date', 'Card', 'Description', 'Amount', 'Dr/Cr', 'Head (fill in)', 'Sub head / ledger (fill in)'])
    wsnc = wb.create_sheet('No Company - choose')
    wsnc.append(['Post to (fill FV / HPZ Chennai)', 'Date', 'Card', 'Description', 'Amount', 'Dr/Cr', 'Party', 'Expense ledger'])
    wss = wb.create_sheet('Summary')
    wss.append(['Item', 'Count'])
    cnt = dict(ready={'HPZ': 0, 'FV': 0}, nohead=0, nocompany=0, payment=0, noledger=0, posted=0)
    for t in txns:
        co = t.get('company') or ''
        if only and co and co != only:
            continue
        st = t['status']
        if st == 'payment':
            cnt['payment'] += 1
            wsall.append([LBL[co], t['date'], _card_txt(t), t['desc'], t['drcr'], t['amount'], 'Payment to card (enter via bank)', '', '', '', '']); continue
        if st == 'posted':
            cnt['posted'] += 1
            wsall.append([LBL[co], t['date'], _card_txt(t), t['desc'], t['drcr'], t['amount'], 'Already posted to Tally', t.get('party'), t.get('ledger'), t.get('style'), '']); continue
        if st == 'no_head' or not t.get('ledger'):
            cnt['nohead'] += 1
            wsnh.append([LBL[co], t['date'], _card_txt(t), t['desc'], t['amount'], t['drcr'], '', ''])
            for c in wsnh[wsnh.max_row]: c.fill = RED
            wsall.append([LBL[co], t['date'], _card_txt(t), t['desc'], t['drcr'], t['amount'], 'No head', '', '', '', '']); continue
        if not co:
            cnt['nocompany'] += 1
            wsnc.append(['', t['date'], _card_txt(t), t['desc'], t['amount'], t['drcr'], t.get('party'), t.get('ledger')])
            for c in wsnc[wsnc.max_row]: c.fill = AMB
            wsall.append(['', t['date'], _card_txt(t), t['desc'], t['drcr'], t['amount'], 'Company not chosen', t.get('party'), t.get('ledger'), t.get('style'), '']); continue
        if only and co != only:
            continue
        prof = db.card_profile(t['bank'], t['last4'], co)
        if not prof or not prof.get('ledger'):
            cnt['noledger'] += 1
            wsall.append([LBL[co], t['date'], _card_txt(t), t['desc'], t['drcr'], t['amount'], 'NO CARD LEDGER for %s' % LBL[co], t.get('party'), t.get('ledger'), t.get('style'), 'add this card for %s in seed_cards.py' % LBL[co]])
            for c in wsall[wsall.max_row]: c.fill = RED
            continue
        for v in tally.build_vouchers(t, prof):
            for led, side, amount in v['lines']:
                sheets[co].append([LBL[co], v['date'], v['vtype'], '', led, side, amount, v['narration'], v['reference'], _card_txt(t),
                                   ('review GST/TDS; ' if v['reference'].endswith('E') else '') + ('dated before 1-Apr-26: previous financial year?' if v['date'] < '2026-04-01' else '')])
        cnt['ready'][co] += 1
        wsall.append([LBL[co], t['date'], _card_txt(t), t['desc'], t['drcr'], t['amount'], 'Ready', t.get('party'), t.get('ledger'), t.get('style'), ''])
        for c in wsall[wsall.max_row]: c.fill = CO_FILL[co]
    for co in companies:
        wss.append(['Ready for %s' % LBL[co], cnt['ready'][co]])
    wss.append(['No head (see sheet)', cnt['nohead']]); wss.append(['Company not chosen (see sheet)', cnt['nocompany']])
    wss.append(['Card has no ledger for the chosen company', cnt['noledger']]); wss.append(['Payments to the card (not exported)', cnt['payment']]); wss.append(['Already posted', cnt['posted']])
    for w in list(sheets.values()) + [wsall, wsnh, wsnc, wss]:
        _style(w)
    bio = io.BytesIO(); wb.save(bio)
    return bio.getvalue(), cnt
