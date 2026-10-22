"""Server entry point for gunicorn. Settings come from environment variables (see deploy/install.sh).

Run with exactly ONE worker - the video analyzer lives inside the worker process:
  gunicorn --workers 1 --threads 16 --worker-class gthread --bind 127.0.0.1:8050 wsgi:app
"""
import os

from run import ROOT, build_app

env = os.environ.get
app = build_app(
    source=env("MENUWATCH_SOURCE", str(ROOT / "footage" / "shopfront_bradford.mp4")),
    camera_id=env("MENUWATCH_CAMERA_ID", "door-cam-1"),
    config_path=env("MENUWATCH_CONFIG", str(ROOT / "config.json")),
    db_path=env("MENUWATCH_DB", str(ROOT / "menu_watch.db")),
    loop=env("MENUWATCH_LOOP", "0") == "1",
    speed=float(env("MENUWATCH_SPEED", "1")),
    idle_pause_sec=float(env("MENUWATCH_IDLE_PAUSE_SEC")) if env("MENUWATCH_IDLE_PAUSE_SEC") else None,
    username=env("MENUWATCH_USER", "admin"),
    password=env("MENUWATCH_PASSWORD"),
)
