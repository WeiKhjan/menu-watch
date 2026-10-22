"""Video sources: local files, RTSP/HTTP cameras, webcams and YouTube (incl. live streams)."""
import threading
import time
from pathlib import Path

import cv2


def resolve_source(source: str):
    """Return (opencv_source, is_live). YouTube links are resolved to a direct stream URL."""
    if source.isdigit():
        return int(source), True
    if Path(source).exists():
        return str(Path(source).resolve()), False
    if "youtube.com" in source or "youtu.be" in source:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True,
                "format": "best[height<=720]/bv*[height<=720]/best/bv*"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=False)
        return info["url"], bool(info.get("is_live"))
    # rtsp://, http(s):// m3u8, etc. are treated as live camera feeds
    return source, True


class LatestFrameReader:
    """Reads a live stream on a background thread and only keeps the newest frame,
    so slow analysis never builds up lag behind the real camera."""

    def __init__(self, cap):
        self.cap = cap
        self.frame = None
        self.ok = True
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while self.ok:
            ok, frame = self.cap.read()
            with self._cond:
                if not ok:
                    self.ok = False
                else:
                    self.frame = frame
                self._cond.notify_all()
            if not ok:
                break

    def read(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.frame is None and self.ok:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, None
                self._cond.wait(remaining)
            frame, self.frame = self.frame, None
            return frame is not None, frame

    def release(self):
        self.ok = False
        self.cap.release()


def open_capture(source):
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video source: {source}")
    return cap
