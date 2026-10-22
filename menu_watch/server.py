"""Flask web app: live annotated video, daily statistics dashboard and zone editor."""
import hmac
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, render_template, request


def create_app(analyzer, db, config, config_path: Path, username="admin", password=None):
    app = Flask(__name__)

    @app.before_request
    def require_login():
        """HTTP Basic login, switched on when a password is configured (always do this on a public server)."""
        if not password:
            return None
        auth = request.authorization
        if (auth and hmac.compare_digest(auth.username or "", username)
                and hmac.compare_digest(auth.password or "", password)):
            return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Menu Watch"'})

    @app.get("/")
    def dashboard():
        return render_template("dashboard.html", camera_id=analyzer.camera_id)

    @app.get("/zones")
    def zones_page():
        return render_template("zones.html")

    @app.get("/video_feed")
    def video_feed():
        def gen():
            last, last_sent = None, 0.0
            while True:
                with analyzer.frame_cond:
                    analyzer.frame_cond.wait(timeout=2)
                    jpg = analyzer.latest_jpeg
                # when the video is paused/finished, resend the last frame every 5s so a closed
                # browser tab is noticed and its thread freed
                if jpg is None or (jpg is last and time.monotonic() - last_sent < 5):
                    continue
                last, last_sent = jpg, time.monotonic()
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/snapshot.jpg")
    def snapshot():
        """Clean (un-annotated) frame, used as the background of the zone editor."""
        with analyzer.frame_cond:
            raw = analyzer.latest_raw
        if raw is None:
            return "No frame yet", 503
        ok, jpg = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(jpg.tobytes(), mimetype="image/jpeg")

    @app.get("/api/status")
    def status():
        return jsonify(analyzer.status)

    @app.get("/api/stats")
    def stats():
        analyzer.last_viewer = time.monotonic()  # the open dashboard polls this every 2s
        day =request.args.get("day") or datetime.now().strftime("%Y-%m-%d")
        data = db.daily_stats(day, analyzer.camera_id)
        data["days"] = db.days(analyzer.camera_id)
        data["trend"] = db.trend(day, 14, analyzer.camera_id)
        data["status"] = analyzer.status
        data["min_read_sec"] = config.get("min_read_sec")
        return jsonify(data)

    @app.post("/api/clear")
    def clear_day():
        day = request.get_json(force=True).get("day") or datetime.now().strftime("%Y-%m-%d")
        deleted = db.delete_day(day, analyzer.camera_id)
        analyzer._today_cache = {}  # refresh the counter drawn on the video
        return jsonify(ok=True, day=day, deleted=deleted)

    @app.post("/api/speed")
    def set_speed():
        """Fast-forward control for video files (live cameras always run at 1x)."""
        speed = float(request.get_json(force=True).get("speed", 1))
        analyzer.speed = min(max(speed, 0.25), 16.0)
        analyzer.status["speed"] = analyzer.speed
        return jsonify(ok=True, speed=analyzer.speed)

    @app.get("/api/config")
    def get_config():
        return jsonify(config)

    @app.post("/api/config")
    def set_config():
        body = request.get_json(force=True)
        for key in ("menu_zone", "door_zone"):
            if key in body.get("zones", {}):
                config["zones"][key] = [[round(float(x), 4), round(float(y), 4)] for x, y in body["zones"][key]]
        for key in ("min_read_sec", "max_still_speed", "lost_timeout_sec"):
            if key in body:
                config[key] = float(body[key])
        config_path.write_text(json.dumps(config, indent=2))
        return jsonify(ok=True, config=config)

    @app.get("/api/export.csv")
    def export_csv():
        day = request.args.get("day") or datetime.now().strftime("%Y-%m-%d")
        rows = db.daily_stats(day, analyzer.camera_id)["hourly"]
        cols = ["hour", "passed_by", "read_left", "read_entered", "walked_in", "exited_shop"]
        csv = ",".join(cols) + "\n" + "\n".join(",".join(str(r[c]) for c in cols) for r in rows)
        return Response(csv, mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=menu-stats-{day}.csv"})

    return app
