"""Open a statement PDF (with password if needed) and return its text."""
import io
import pdfplumber


class WrongPassword(Exception):
    pass


def read_text(data: bytes, passwords=()):
    """Try no password, then each password. Returns the text of all pages."""
    tries = [None] + [p for p in passwords if p]
    last = None
    for pw in tries:
        try:
            with pdfplumber.open(io.BytesIO(data), password=pw) as pdf:
                return '\n'.join((pg.extract_text() or '') + '\n<<PAGE>>' for pg in pdf.pages)
        except Exception as e:                      # pdfminer raises PDFPasswordIncorrect
            last = e
            if 'assword' not in repr(e):
                raise
    raise WrongPassword('PDF is password protected and none of the saved passwords worked') from last
