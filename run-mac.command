#!/bin/bash
cd "$(dirname "$0")" || exit 1
echo "Namothaj — local forms website"
if ! python3 -c 'import sys;sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
  echo "Install Python 3.10 or newer from https://www.python.org/downloads/ then run this file again."
  open 'https://www.python.org/downloads/'
  read -r -p 'Press Enter to close.'
  exit 1
fi
if [ ! -x '.venv/bin/python' ]; then
  python3 -m venv .venv || exit 1
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt || { read -r -p 'Setup failed. Press Enter to close.'; exit 1; }
.venv/bin/python run_local.py
