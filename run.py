"""Menu Watch - count people who read the menu outside an F&B outlet and whether they walk in.

Examples:
  python run.py --source footage/shopfront_bradford.mp4 --loop
  python run.py --source rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101 --camera-id outlet-kl
  python run.py --source "https://www.youtube.com/watch?v=<live-id>"
  python run.py --source 0                       # USB webcam
Then open http://localhost:5000
"""
import argparse
import json
import logging
import os
from pathlib import Path

from menu_watch.analyzer import Analyzer
from menu_watch.db import Database
from menu_watch.server import create_app

ROOT = Path(__file__).parent


def build_app(source, camera_id="door-cam-1", config_path=ROOT / "config.json", db_path=ROOT / "menu_watch.db",
              loop=False, fast=False, speed=1.0, idle_pause_sec=None, username="admin", password=None):
    """Start the analyzer thread and return the Flask app. Used by run.py and by wsgi.py (gunicorn)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    config_path = Path(config_path)
    config = json.loads(config_path.read_text())
    db = Database(db_path)
    analyzer = Analyzer(config, db, source, camera_id, realtime=not fast, loop=loop, speed=speed,
                        idle_pause_sec=idle_pause_sec)
    analyzer.start()
    return create_app(analyzer, db, config, config_path, username=username, password=password)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, help="video file, RTSP/HTTP URL, YouTube URL or webcam index")
    p.add_argument("--camera-id", default="door-cam-1", help="name stored with every record")
    p.add_argument("--config", default=str(ROOT / "config.json"))
    p.add_argument("--db", default=str(ROOT / "menu_watch.db"))
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--loop", action="store_true", help="replay a video file forever (demo mode)")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed for video files, e.g. 4 = 4x fast-forward")
    p.add_argument("--fast", action="store_true", help="process every analysed frame as fast as the CPU allows (no pacing)")
    p.add_argument("--idle-pause", type=float, default=None, metavar="SEC",
                   help="pause a video file when no dashboard has been open for SEC seconds")
    args = p.parse_args()

    app = build_app(args.source, args.camera_id, args.config, args.db, loop=args.loop, fast=args.fast,
                    speed=args.speed, idle_pause_sec=args.idle_pause,
                    password=os.environ.get("MENUWATCH_PASSWORD"))
    print(f"\n  Dashboard:   http://localhost:{args.port}\n  Zone editor: http://localhost:{args.port}/zones\n")
    app.run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
