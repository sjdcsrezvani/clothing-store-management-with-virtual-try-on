#!/usr/bin/env bash
# RaiKids POS launcher (Linux/macOS)
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "⚠️  No .env found — copying from .env.example. Edit it and set ADMIN_PASSWORD first."
    cp .env.example .env
fi

if [ ! -d .venv ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet -r requirements.txt
fi

echo "🚀 Starting RaiKids POS at http://127.0.0.1:8000"
if command -v xdg-open >/dev/null 2>&1; then
    (sleep 2 && xdg-open "http://127.0.0.1:8000" >/dev/null 2>&1) &
elif command -v open >/dev/null 2>&1; then
    (sleep 2 && open "http://127.0.0.1:8000") &
fi
exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
