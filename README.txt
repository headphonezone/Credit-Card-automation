CREDIT CARD APP  -  how to use
================================

START
  Double-click run_app.bat. The app opens in the browser (http://localhost:8501).
  Keep Tally open on the same PC when you want to check ledgers or post.

FIRST TIME (10 minutes)
  Cards          : 32 cards are pre-loaded with their Tally ledger names (HPZ and FV); PDF passwords come from the Google password sheet.
                   To add another card, edit seed_cards.py and run it again.
  Tab 5 Settings : check the party sheet path (Z:\Vijay\HPZ (P) Ltd 25-26 expenses .xlsx), the Tally company names,
                   and the email settings for reminders (see "EMAIL" below).

EVERY MONTH
  1 Upload   : choose the card, drop the statement PDF. The app decrypts it, reads every line and the
               due date / total due / minimum due. Check those three values in the table below and correct if needed.
  2 Review   : each line gets its party and head from your party sheet. Then choose "Post to" for EVERY line: FV or HPZ Chennai
               (nothing is guessed; use the Mark ALL buttons for a whole statement, then change single lines, then Save).
               Lines without a company cannot be exported or posted. Merchants not in the sheet appear under
               "no head" - pick the party (or type a new head) once; the app remembers it for next time.
  3 Tally    : A) download the Excel. Every sheet shows the company; "Tally Import - HPZ Chennai" and "Tally Import - FV" are separate
               (or download one company only). Import each into its own Tally company. Other sheets: All lines, No Head, No Company.
               B) post straight to Tally, company by company: a company is posted only if it is open in Tally, and only its own lines go in.
               Lines of a company that is not open stay waiting. Use "Check the ledgers exist" first - it lists any ledger missing in that company.
  4 Reminders: see what is due. Mark paid here, or from the link in the reminder email.

HOW HEADS ARE DECIDED
  * The party sheet is read live each time, so editing the sheet changes the app (press "Reload sheet" in tab 5).
  * Booking style per party:  TDS = Yes in the sheet  ->  vendor ledger (payment entry + expense entry)
                              otherwise                ->  direct expense entry, party name in the narration.
    You can override the style when you assign a merchant.
  * The expense ledger used is the Sub head from the sheet (or the Head when Sub head is N/A).
  * A card used by both companies needs two profiles in seed_cards.py (one per company, each with that company's ledger name).
  * Payments to the card (bank payments) are not exported; enter them through the bank as usual.
  * FV and HPZ Chennai use the SAME party sheet / tab (Party Wise Expenses Head) - there is no separate FV sheet.

REMINDERS (no Tally needed)
  * A reminder is sent 7, 3, 1 days before the due date, on the due date, then every day while overdue.
  * It stops as soon as the statement is paid. Paid is set by:
       1) the "I have paid" link in the email,   2) "Mark paid" in tab 4,
       3) the NEXT month's statement showing payments that cover the earlier due amount.
  * Only the newest statement of a card is reminded; older unpaid ones are shown as "Superseded".
  * Daily sending: Windows Task Scheduler -> create a daily task (e.g. 9:00) that runs send_reminders.bat.
    Test first with:  python send_reminders.py --dry
EMAIL
  Gmail: use smtp.gmail.com, port 587, your address as user, and an "App password" (Google account > Security).
  "App address for the I-have-paid link" must be reachable from where you read the mail. http://localhost:8501 only
  works on this PC; use this PC's network name (e.g. http://OFFICE-PC:8501) to click it from a phone/other PC.
  The app must be running (run_app.bat) for the link to work.

SAFETY
  * Nothing is posted to Tally unless you tick the box and press the button.
  * Re-uploading the same PDF is refused; the same statement line is never added twice.
  * The app talks to Tally with timeouts and quoted names (a ledger name starting with "Yes" froze Tally earlier).
  * GST/TDS: the expense voucher for vendor-style lines is created at the full amount with no tax split; its Excel row
    says "review GST/TDS". Complete the GST/TDS details in Tally for those.
  * Lines dated before 1-Apr-26 (previous financial year) are flagged in the Excel note column.

FILES
  app.py (screens) | core\ (reading PDFs, heads, Tally, export, reminders) | data\ (database + key; back this folder up)
  seed_cards.py (card list) | send_reminders.py/.bat (daily reminder job)
