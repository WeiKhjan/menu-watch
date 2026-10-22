@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
start "" http://localhost:5000
.venv\Scripts\python run.py --source footage\shopfront_bradford.mp4 --loop %*
