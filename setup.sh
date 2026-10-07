#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install --no-cache-dir 'vosk==0.3.45'
if ! .venv/bin/python -c 'import PySide6' 2>/dev/null; then
    .venv/bin/python -m pip install PySide6
fi
.venv/bin/python download_model.py
echo 'Ready. Run ./run.sh to open Mochi.'
