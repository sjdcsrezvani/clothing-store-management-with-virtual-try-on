@echo off
REM RaiKids POS launcher (Windows)
cd /d "%~dp0"

if not exist .env (
    echo Warning: No .env found - copying from .env.example. Edit it and set ADMIN_PASSWORD first.
    copy .env.example .env >nul
)

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
    .venv\Scripts\pip install --quiet --upgrade pip
    .venv\Scripts\pip install --quiet -r requirements.txt
)

echo Starting RaiKids POS at http://127.0.0.1:8000
start "" http://127.0.0.1:8000
.venv\Scripts\uvicorn main:app --host 127.0.0.1 --port 8000
