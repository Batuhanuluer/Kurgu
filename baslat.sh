#!/usr/bin/env bash
# Kurgu - macOS / Linux başlatıcı. İlk çalıştırmada sanal ortam kurar.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "İlk kurulum yapılıyor, birkaç dakika sürebilir..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m kurgu "$@"
