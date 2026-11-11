#!/usr/bin/env sh
# Start Pitchdeck Title Tuner with the project's own virtual environment (macOS/Linux).
set -e
cd "$(dirname "$0")"   # so .streamlit/config.toml and .env are picked up
if [ ! -x .venv/bin/python ]; then
  echo "Virtual environment not found. Create it first:"
  echo "  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi
exec .venv/bin/python -m streamlit run app.py "$@"
