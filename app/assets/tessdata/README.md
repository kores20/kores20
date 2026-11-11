# OCR language models

`python -m app.ocr_setup` installs English and Arabic `tessdata_fast` models
from the pinned upstream release 4.1.0 and verifies SHA-256 checksums. Models
contain no user data. They use the Apache-2.0 license:
https://github.com/tesseract-ocr/tessdata_fast/blob/4.1.0/LICENSE

MuPDF's bundled OCR engine consumes these models directly; a system Tesseract
executable or an external OCR service is not needed. Private PDFs stay on the
application server. Model files are build assets and are not versioned here.
