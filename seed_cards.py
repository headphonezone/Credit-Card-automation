"""One-time: pre-load the card list with the Tally ledger names found in HPZ and Ferrari Video.
Passwords are NOT included - add them in the app (Cards tab). Safe to run again."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import db

CARDS = [  # name, bank, last4, company, tally ledger
 ('Amex 82008 (old 81000)', 'AMEX', '82008', 'HPZ', 'Amex 81000/82008'), ('Axis 4417', 'AXIS', '4417', 'HPZ', 'Axis CC 4417'),
 ('Axis 3912', 'AXIS', '3912', 'HPZ', 'AXIS CREDIT CARD 0486 ( 3912 )'), ('HDFC 1187', 'HDFC', '1187', 'HPZ', 'Credit Card HDFC 1187'),
 ('HDFC 8557', 'HDFC', '8557', 'HPZ', 'Credit Card HDFC 8557'), ('Indusind 8095', 'INDUSIND', '8095', 'HPZ', 'Credit Card - Indusland 8095'),
 ('HDFC 3639', 'HDFC', '3639', 'HPZ', 'HDFC3639 Credit Card'), ('HDFC 3319', 'HDFC', '3319', 'HPZ', 'HDFC Bank Credit Card No : 3319'),
 ('HDFC 5428', 'HDFC', '5428', 'HPZ', 'HDFC Bank Credit Card No : 5428'), ('HDFC 9305', 'HDFC', '9305', 'HPZ', 'HDFC Bank Credit Card No : 9305'),
 ('ICICI 0038', 'ICICI', '0038', 'HPZ', 'ICICI Bank Credit Card No : 0038'), ('ICICI 5004', 'ICICI', '5004', 'HPZ', 'ICICI Bank Credit Card No : 5004'),
 ('IDFC 2592', 'IDFC', '2592', 'HPZ', 'IDFC Bank Credit Card - 2592'), ('IDFC 1063', 'IDFC', '1063', 'HPZ', 'IDFC Credit Card No 1063'),
 ('IDFC 3066', 'IDFC', '3066', 'HPZ', 'IDFC Credit Card No 3066'), ('Kotak 4946', 'KOTAK', '4946', 'HPZ', 'Kotak Indigo Credit Card -4946'),
 ('Yes 4805', 'YES', '4805', 'HPZ', 'Yes Bank Credit Card 4805'), ('Yes 8835', 'YES', '8835', 'HPZ', 'Yes Bank Credit Card No : 8835'),
 ('Amex 81008', 'AMEX', '81008', 'FV', 'AMEX Credit Card No : 81008'), ('Axis 4417', 'AXIS', '4417', 'FV', 'Axis CC 4417'),
 ('Axis 3802', 'AXIS', '3802', 'FV', 'Axis Credit Card 3802'), ('Yes 4805', 'YES', '4805', 'FV', 'Credit Card Yes Bank 4805'),
 ('HDFC 1187', 'HDFC', '1187', 'FV', 'HDFC Bank Credit Card No : 1187'), ('HDFC 5428', 'HDFC', '5428', 'FV', 'HDFC Bank Credit Card No : 5428'),
 ('HDFC 6521', 'HDFC', '6521', 'FV', 'HDFC Bank Credit Card No : 6521'), ('HDFC 8557', 'HDFC', '8557', 'FV', 'HDFC Credit Card 8557'),
 ('ICICI 0038', 'ICICI', '0038', 'FV', 'ICICI Bank Credit Card No : 0038'), ('IDFC 2592', 'IDFC', '2592', 'FV', 'IDFC Bank Credit Card - 2592'),
 ('IDFC 3066', 'IDFC', '3066', 'FV', 'IDFC Credit Card No 3066'), ('SBI 2574', 'SBI', '2574', 'FV', 'SBI Bank Credit Card No : 2574'),
 ('SBI 2760', 'SBI', '2760', 'FV', 'SBI Bank Credit Card No : 2760'), ('Yes 0292', 'YES', '0292', 'FV', 'YES Bank Credit Card No : 0292'),
]
c = db.conn(); n = 0
for name, bank, last4, comp, led in CARDS:
    if not c.execute('SELECT 1 FROM cards WHERE bank=? AND last4=? AND company=?', (bank, last4, comp)).fetchone():
        c.execute('INSERT INTO cards(name,bank,last4,company,ledger,passwords) VALUES(?,?,?,?,?,?)', (name, bank, last4, comp, led, db.enc_list([]))); n += 1
c.commit(); c.close()
print('Added %d cards (%d total in list).' % (n, len(CARDS)))
