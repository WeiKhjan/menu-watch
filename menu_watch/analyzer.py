"""Live analysis: detect + track people, measure how long each one stands at the menu,
and decide whether they walked in or left. Every finished person is written to the database."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import cv2
import numpy as np
import torch

from .sources import LatestFrameReader, open_capture, resolve_source

log = logging.getLogger("menu_watch")

# BGR colours for drawing
GREY, YELLOW, ORANGE, GREEN, RED, WHITE = (170, 170, 170), (0, 215, 255), (0, 140, 255), (80, 200, 60), (60, 60, 230), (255, 255, 255)


@dataclass
class Track:
    tid: int
    first_t: float
    last_t: float
    started_in_door: bool
    hist: deque = field(default_factory=lambda: deque(maxlen=60))  # (t, x, y) foot point, normalised
    menu_dwell: float = 0.0
    last_door_t: float | None = None
    in_menu: bool = False
    still: bool = False
    box: tuple = (0, 0, 0, 0)


def point_in_poly(pt, poly):
    if len(poly) < 3:
        return False
    return cv2.pointPolygonTest(np.array(poly, dtype=np.float32), (float(pt[0]), float(pt[1])), False) >= 0


class Analyzer(threading.Thread):
    def __init__(self, config, db, source, camera_id="door-cam-1", realtime=True, loop=False, speed=1.0,
                 idle_pause_sec=None):
        super().__init__(daemon=True)
        self.config = config          # shared dict; zones can be edited live from the web UI
        self.db = db
        self.source = source
        self.camera_id = camera_id
        self.realtime = realtime
        self.loop = loop
        self.speed = speed            # playback speed for video files (fast-forward); ignored for live feeds
        # demo mode on a shared server: pause a video file when nobody has the dashboard open
        self.idle_pause_sec = idle_pause_sec
        self.last_viewer = time.monotonic()

        self.tracks: dict[int, Track] = {}
        self.latest_jpeg = None
        self.latest_raw = None
        self.frame_cond = threading.Condition()
        self._step_dt = 0.1
        self.status = {"state": "starting", "fps": 0.0, "source": source, "error": None, "active_people": 0,
                       "is_file": False, "speed": speed, "position_sec": 0.0, "duration_sec": None}
        self._stop = threading.Event()
        self._today_cache = {}

    # ---------------------------------------------------------------- main loop
    def stop(self):
        self._stop.set()

    def run(self):
        try:
            from ultralytics import YOLO
            self.model = YOLO(self.config.get("model", "yolo11n.pt"))
            while not self._stop.is_set():
                self._process_source()
                if not self.loop:
                    break
                self.tracks.clear()
                self.model.predictor = None  # reset tracker state between loops
            self.status["state"] = "finished"
        except Exception as e:  # surface errors on the dashboard instead of dying silently
            log.exception("Analyzer crashed")
            self.status.update(state="error", error=str(e))

    def _process_source(self):
        self.status["state"] = "connecting"
        src, is_live = resolve_source(self.source)
        cap = open_capture(src)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if fps > 120 or fps < 1:
            fps = 25.0
        reader = LatestFrameReader(cap) if is_live else None
        frames_total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        self.status.update(state="running", is_file=not is_live,
                           duration_sec=round(frames_total / fps, 1) if not is_live and frames_total > 0 else None)
        log.info("Opened %s (live=%s, %.1f fps)", self.source, is_live, fps)

        wall_start = datetime.now()
        mono_start = time.monotonic()
        stride = max(1, round(fps / self.config.get("analysis_fps", 10)))
        frame_idx = -1
        prev_t = 0.0
        fps_t, fps_n = time.monotonic(), 0
        play_t, last_mono = 0.0, mono_start  # video seconds that should have played so far, at self.speed

        try:
            while not self._stop.is_set():
                if is_live:
                    ok, frame = reader.read()
                    t = time.monotonic() - mono_start
                else:
                    if self.idle_pause_sec and time.monotonic() - self.last_viewer > self.idle_pause_sec:
                        self.status["state"] = "paused"
                        time.sleep(0.5)
                        last_mono = time.monotonic()  # don't fast-forward through the paused time on resume
                        continue
                    self.status["state"] = "running"
                    if self.realtime:
                        # keep pace with the playback clock: skip frames we are too slow to analyse.
                        # At higher speeds more frames are skipped, so the CPU load stays the same.
                        now = time.monotonic()
                        play_t += (now - last_mono) * self.speed
                        last_mono = now
                        target = max(int(play_t * fps), frame_idx + stride)
                        while frame_idx + 1 < target and cap.grab():
                            frame_idx += 1
                        wait = (target / fps - play_t) / self.speed
                        if wait > 0:
                            time.sleep(wait)
                    else:
                        for _ in range(stride - 1):
                            cap.grab()
                            frame_idx += 1
                    ok, frame = cap.read()
                    frame_idx += 1
                    t = frame_idx / fps
                if not ok:
                    break

                self._step_dt, prev_t = max(t - prev_t, 1e-3), t
                self.status["position_sec"] = round(t, 1)
                now_wall = wall_start + timedelta(seconds=t)
                self._step(frame, t, now_wall)

                fps_n += 1
                if time.monotonic() - fps_t >= 2:
                    self.status["fps"] = round(fps_n / (time.monotonic() - fps_t), 1)
                    fps_t, fps_n = time.monotonic(), 0
        finally:
            # End of video / stream: close out everyone still on screen
            for tr in list(self.tracks.values()):
                self._finalize(tr, wall_start + timedelta(seconds=tr.first_t), wall_start + timedelta(seconds=tr.last_t))
            self.tracks.clear()
            (reader.release() if reader else cap.release())

    # ---------------------------------------------------------------- per frame
    def _step(self, frame, t, now_wall):
        cfg = self.config
        h, w = frame.shape[:2]
        menu_zone = cfg["zones"].get("menu_zone", [])
        door_zone = cfg["zones"].get("door_zone", [])

        res = self.model.track(frame, persist=True, classes=[0], conf=cfg.get("confidence", 0.35),
                               imgsz=cfg.get("imgsz", 640), tracker="bytetrack.yaml", verbose=False)[0]
        # Ultralytics resets torch to min(8, cores-1) threads when it sets up the model, so re-apply ours
        # afterwards. Too many threads on a shared/virtual CPU made inference ~20x slower in testing.
        if cfg.get("cpu_threads") and torch.get_num_threads() != int(cfg["cpu_threads"]):
            torch.set_num_threads(int(cfg["cpu_threads"]))
        seen = set()
        if res.boxes is not None and res.boxes.id is not None:
            for (x1, y1, x2, y2), tid in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.id.int().cpu().tolist()):
                foot = ((x1 + x2) / 2 / w, y2 / h)   # where the person is standing, normalised 0-1
                box_h = (y2 - y1) / h
                seen.add(tid)
                tr = self.tracks.get(tid)
                if tr is None:
                    tr = Track(tid, t, t, started_in_door=point_in_poly(foot, door_zone))
                    self.tracks[tid] = tr
                # cap so an occlusion gap doesn't count as reading; scales with fast-forward step size
                dt = min(t - tr.last_t, max(0.5, 2 * self._step_dt))
                tr.last_t = t
                tr.box = (int(x1), int(y1), int(x2), int(y2))
                tr.hist.append((t, *foot))

                # speed in "body heights per second" over the last ~1s, so it works at any zoom level
                old = next((p for p in tr.hist if p[0] >= t - 1.0), tr.hist[0])
                span = t - old[0]
                moved_px = np.hypot((foot[0] - old[1]) * w, (foot[1] - old[2]) * h)
                speed = moved_px / max(box_h * h, 1.0) / span if span > 0.2 else 0.0
                tr.still = speed < cfg.get("max_still_speed", 0.45)
                tr.in_menu = point_in_poly(foot, menu_zone)
                if tr.in_menu and tr.still:
                    tr.menu_dwell += dt
                if point_in_poly(foot, door_zone):
                    tr.last_door_t = t

        # anyone not seen for a while has left the frame -> decide their outcome
        lost_after = cfg.get("lost_timeout_sec", 2.0)
        for tid in [k for k, tr in self.tracks.items() if k not in seen and t - tr.last_t > lost_after]:
            tr = self.tracks.pop(tid)
            first = now_wall - timedelta(seconds=t - tr.first_t)
            last = now_wall - timedelta(seconds=t - tr.last_t)
            self._finalize(tr, first, last)

        self.status["active_people"] = len(seen)
        self._publish(frame, menu_zone, door_zone, seen)

    def _classify(self, tr: Track):
        cfg = self.config
        read = tr.menu_dwell >= cfg.get("min_read_sec", 3.0)
        entered = tr.last_door_t is not None and tr.last_t - tr.last_door_t <= cfg.get("door_exit_window_sec", 1.0)
        if tr.started_in_door:
            return "exited_shop", read, False
        if entered:
            return ("read_entered" if read else "walked_in"), read, True
        return ("read_left" if read else "passed_by"), read, False

    def _finalize(self, tr: Track, first: datetime, last: datetime):
        if tr.last_t - tr.first_t < self.config.get("min_track_sec", 0.7):
            return  # flicker / false detection
        outcome, read, entered = self._classify(tr)
        self.db.insert_visit(self.camera_id, tr.tid, first, last, tr.menu_dwell, read, entered, outcome)
        self._today_cache = {}
        (_, x0, y0), (_, x1, y1) = tr.hist[0], tr.hist[-1]
        log.info("person #%d -> %s (menu %.1fs, %.1fs on camera, path %.2f,%.2f -> %.2f,%.2f)",
                 tr.tid, outcome, tr.menu_dwell, tr.last_t - tr.first_t, x0, y0, x1, y1)

    # ---------------------------------------------------------------- drawing
    def _today_totals(self):
        if not self._today_cache:
            s = self.db.daily_stats(datetime.now().strftime("%Y-%m-%d"), self.camera_id)
            self._today_cache = {**s["totals"], "readers": s["menu_readers"]}
        return self._today_cache

    def _publish(self, frame, menu_zone, door_zone, seen):
        raw = frame
        out = frame.copy()
        h, w = out.shape[:2]
        overlay = out.copy()
        for poly, color, name in ((menu_zone, YELLOW, "MENU ZONE"), (door_zone, GREEN, "DOOR")):
            if len(poly) >= 3:
                pts = (np.array(poly) * [w, h]).astype(np.int32)
                cv2.fillPoly(overlay, [pts], color)
                cv2.polylines(out, [pts], True, color, 2)
                cv2.putText(out, name, tuple(pts[0] + [6, 22]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        out = cv2.addWeighted(overlay, 0.18, out, 0.82, 0)

        min_read = self.config.get("min_read_sec", 3.0)
        for tid in seen:
            tr = self.tracks[tid]
            x1, y1, x2, y2 = tr.box
            if tr.menu_dwell >= min_read:
                color, label = ORANGE, f"#{tid} READ {tr.menu_dwell:.0f}s"
            elif tr.in_menu and tr.still:
                color, label = YELLOW, f"#{tid} looking {tr.menu_dwell:.1f}s"
            elif tr.started_in_door:
                color, label = GREY, f"#{tid} leaving shop"
            else:
                color, label = WHITE, f"#{tid}"
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            cv2.rectangle(out, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
            cv2.putText(out, label, (x1 + 3, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)

        tot = self._today_totals()
        lines = [f"Today  passers-by {tot['passed_by'] + tot['readers'] + tot['walked_in']}",
                 f"read menu {tot['readers']}  ->  entered {tot['read_entered']} | left {tot['read_left']}"]
        x0 = max(0, w - 500)  # top-right, away from the doorway
        cv2.rectangle(out, (x0, 0), (w, 58), (0, 0, 0), -1)
        for i, line in enumerate(lines):
            cv2.putText(out, line, (x0 + 10, 24 + i * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 1, cv2.LINE_AA)

        ok, jpg = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 75])
        with self.frame_cond:
            self.latest_raw = raw
            if ok:
                self.latest_jpeg = jpg.tobytes()
            self.frame_cond.notify_all()
