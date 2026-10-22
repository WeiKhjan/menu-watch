"""Checks the visitor state machine with scripted walking paths (no camera / YOLO needed).

Run:  .venv\\Scripts\\python tests\\test_logic.py
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from menu_watch.analyzer import Analyzer  # noqa: E402
from menu_watch.db import Database  # noqa: E402

W, H, FPS = 1280, 720, 10
BOX_H = 0.30  # person height as a fraction of the frame


def path(*legs):
    """legs: (x, y, seconds) waypoints in normalised coords. Returns a foot position per frame."""
    pts = []
    for (x0, y0, _), (x1, y1, secs) in zip(legs, legs[1:]):
        n = max(1, int(secs * FPS))
        pts += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
    return pts


class FakeModel:
    """Stands in for YOLO+ByteTrack: returns the scripted people for the current frame."""
    def __init__(self, people):
        self.people, self.frame = people, 0

    def track(self, frame, **_):
        boxes, ids = [], []
        for tid, (start, pts) in self.people.items():
            i = self.frame - start
            if 0 <= i < len(pts):
                fx, fy = pts[i][0] * W, pts[i][1] * H
                boxes.append([fx - 40, fy - BOX_H * H, fx + 40, fy])
                ids.append(tid)
        self.frame += 1
        b = SimpleNamespace(xyxy=torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
                            id=torch.tensor(ids) if ids else None)
        return [SimpleNamespace(boxes=b)]


def main():
    config = json.loads((ROOT / "config.json").read_text())
    config["zones"] = {  # simple test geometry: menu on the left, door at the bottom-left
        "menu_zone": [[0.10, 0.30], [0.40, 0.30], [0.40, 0.60], [0.10, 0.60]],
        "door_zone": [[0.00, 0.85], [0.30, 0.85], [0.30, 1.00], [0.00, 1.00]],
    }
    stand = 5  # seconds at the menu (threshold is config["min_read_sec"])
    people = {
        1: (0,  path((0.9, 0.45, 0), (0.25, 0.45, 3), (0.25, 0.45, stand), (0.95, 0.45, 3))),            # read, walked off
        2: (10, path((0.9, 0.40, 0), (0.20, 0.45, 3), (0.20, 0.45, stand), (0.15, 0.95, 2))),            # read, went in
        3: (20, path((0.9, 0.70, 0), (0.15, 0.95, 3))),                                                   # straight in
        4: (30, path((0.10, 0.95, 0), (0.90, 0.60, 3))),                                                  # came out of shop
        5: (40, path((0.95, 0.45, 0), (0.05, 0.45, 4))),                                                  # walked past menu
        6: (50, path((0.9, 0.45, 0), (0.30, 0.45, 2), (0.30, 0.45, 1.5), (0.95, 0.45, 2))),               # glanced 1.5s only
    }
    expected = {1: "read_left", 2: "read_entered", 3: "walked_in", 4: "exited_shop", 5: "passed_by", 6: "passed_by"}

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        a = Analyzer(config, db, "fake", "test")
        a.model = FakeModel(people)
        frame = np.zeros((H, W, 3), np.uint8)
        end = max(s + len(p) for s, p in people.values()) + 5 * FPS
        for i in range(end):
            a._step(frame, i / FPS, datetime.now())

        rows = {r["track_id"]: r for r in db.daily_stats(datetime.now().strftime("%Y-%m-%d"))["recent"]}
        stats = db.daily_stats(datetime.now().strftime("%Y-%m-%d"))
        got = {tid: (rows[tid]["outcome"] if tid in rows else "passed_by") for tid in expected}
        failures = 0
        for tid, want in expected.items():
            ok = got[tid] == want
            failures += not ok
            dwell = rows[tid]["menu_dwell_sec"] if tid in rows else 0
            print(f"{'PASS' if ok else 'FAIL'}  person {tid}: expected {want:13s} got {got[tid]:13s} (menu {dwell:.1f}s)")
        print("totals:", stats["totals"], "| conversion:", stats["conversion_pct"], "%")
        assert stats["totals"]["passed_by"] == 2, stats["totals"]
        sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
