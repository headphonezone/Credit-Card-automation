"""Talk to TallyPrime over its HTTP/XML port (default 9000).

Safety rules learnt the hard way:
 * every ledger/company name is sent QUOTED - a name starting with 'Yes' is a reserved word in Tally's formula
   language and makes Tally pop up 'bad formula' and freeze;
 * every request has a timeout, so a stuck Tally never freezes the app;
 * nothing is posted unless the user presses the post button.
"""
import re, html, urllib.request
from xml.sax.saxutils import escape
from . import db

TIMEOUT = 40


def _post(xml: str, timeout=TIMEOUT):
    req = urllib.request.Request(db.get('tally_url'), xml.encode('utf-8'))
    return re.sub(r'&#\d+;', '', urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore'))


def companies():
    x = ('<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>LC</ID></HEADER><BODY><DESC>'
         '<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE>'
         '<COLLECTION NAME="LC" ISMODIFY="No"><TYPE>Company</TYPE><FETCH>Name</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>')
    return [html.unescape(n) for n in re.findall(r'<COMPANY NAME="([^"]*)"', _post(x))]


def ledger_parent(company, name):
    """Return the group of a ledger, or None if the ledger does not exist in that company."""
    nn = escape(name, {'"': '&quot;'})
    x = ('<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>LQ</ID></HEADER><BODY><DESC>'
         '<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT><SVCURRENTCOMPANY>%s</SVCURRENTCOMPANY></STATICVARIABLES><TDL><TDLMESSAGE>'
         '<COLLECTION NAME="LQ" ISMODIFY="No"><TYPE>Ledger</TYPE><FILTER>LQF</FILTER><FETCH>Name,Parent</FETCH></COLLECTION>'
         '<SYSTEM TYPE="Formulae" NAME="LQF">$$IsEqual:$Name:"%s"</SYSTEM></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>') % (escape(company), nn)
    r = _post(x)
    m = re.search(r'<PARENT[^>]*>([^<]*)<', r)
    return html.unescape(m.group(1)) if m else None


def _amt(v):
    return '%.2f' % v


def voucher_xml(v):
    """v = dict(date 'YYYY-MM-DD', vtype, narration, reference, lines=[(ledger,'Dr'|'Cr',amount)])"""
    d = v['date'].replace('-', '')
    out = ['<VOUCHER VCHTYPE="%s" ACTION="Create" OBJVIEW="Accounting Voucher View">' % escape(v['vtype']),
           '<DATE>%s</DATE><VOUCHERTYPENAME>%s</VOUCHERTYPENAME>' % (d, escape(v['vtype'])),
           '<NARRATION>%s</NARRATION>' % escape(v['narration'])]
    if v.get('reference'):
        out.append('<REFERENCE>%s</REFERENCE>' % escape(v['reference']))
    for led, side, amount in v['lines']:
        dr = side == 'Dr'
        out.append('<ALLLEDGERENTRIES.LIST><LEDGERNAME>%s</LEDGERNAME><ISDEEMEDPOSITIVE>%s</ISDEEMEDPOSITIVE><AMOUNT>%s</AMOUNT></ALLLEDGERENTRIES.LIST>'
                   % (escape(led), 'Yes' if dr else 'No', _amt(-amount if dr else amount)))
    out.append('</VOUCHER>')
    return ''.join(out)


def import_xml(company, vouchers):
    body = ''.join('<TALLYMESSAGE xmlns:UDF="TallyUDF">%s</TALLYMESSAGE>' % voucher_xml(v) for v in vouchers)
    return ('<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME>'
            '<STATICVARIABLES><SVCURRENTCOMPANY>%s</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA>%s</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>'
            % (escape(company), body))


def post_vouchers(company, vouchers):
    """Send vouchers to Tally. Returns dict(created, errors, raw)."""
    raw = _post(import_xml(company, vouchers), timeout=120)
    g = lambda t: int(re.search(r'<%s>(\d+)</%s>' % (t, t), raw).group(1)) if re.search(r'<%s>(\d+)</%s>' % (t, t), raw) else 0
    errs = re.findall(r'<LINEERROR>([^<]*)</LINEERROR>', raw)
    return dict(created=g('CREATED'), altered=g('ALTERED'), errors=g('ERRORS'), messages=[html.unescape(e) for e in errs], raw=raw)


# ------------------------------------------------------------------ building vouchers from statement lines
def build_vouchers(txn, card):
    """One statement line -> 1 voucher (direct) or 2 vouchers (vendor ledger: payment + expense).
    txn: dict with date, desc, amount, drcr, party, ledger, style, vendor_ledger, key. card: dict with name, last4, ledger."""
    ref = 'CC' + txn['key'][:10]
    narr = '%s | %s | %s xx%s' % (txn['desc'], txn.get('party') or '', card['name'], card['last4'])
    amt = float(txn['amount'])
    cr = txn['drcr'] == 'CR'          # refund / reversal
    cardled = card['ledger']
    exp = txn['ledger']
    if txn.get('style') == 'vendor':
        ven = txn.get('vendor_ledger') or txn.get('party')
        pay = dict(date=txn['date'], vtype='Journal', narration=narr + ' | payment', reference=ref,
                   lines=[(cardled, 'Dr', amt), (ven, 'Cr', amt)] if cr else [(ven, 'Dr', amt), (cardled, 'Cr', amt)])
        exv = dict(date=txn['date'], vtype='Journal', narration=narr + ' | expense (check GST/TDS)', reference=ref + 'E',
                   lines=[(ven, 'Dr', amt), (exp, 'Cr', amt)] if cr else [(exp, 'Dr', amt), (ven, 'Cr', amt)])
        return [pay, exv]
    return [dict(date=txn['date'], vtype='Journal', narration=narr, reference=ref,
                 lines=[(cardled, 'Dr', amt), (exp, 'Cr', amt)] if cr else [(exp, 'Dr', amt), (cardled, 'Cr', amt)])]
