"""Download pinned public Tesseract models; MuPDF performs OCR without a CLI."""
import hashlib
import os
from pathlib import Path
import urllib.request

DATA=Path(os.getenv('OCR_DATA_DIR',str(Path(__file__).parent/'assets'/'tessdata')))
MODELS={'eng':'7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
        'ara':'e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729'}


def install():
    DATA.mkdir(parents=True,exist_ok=True)
    for language,sha in MODELS.items():
        path=DATA/(language+'.traineddata')
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==sha:continue
        raw=urllib.request.urlopen(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{language}.traineddata',timeout=60).read(8_000_000)
        if hashlib.sha256(raw).hexdigest()!=sha:raise RuntimeError('OCR model checksum mismatch: '+language)
        temporary=path.with_suffix('.pending');temporary.write_bytes(raw);temporary.replace(path)
        print('Installed OCR model: '+language,flush=True)


if __name__=='__main__':install()
