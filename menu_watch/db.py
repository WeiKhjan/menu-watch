"""SQLite storage for visitor events and daily statistics."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT    NOT NULL,
    track_id        INTEGER NOT NULL,
    first_seen      TEXT    NOT NULL,   -- ISO timestamp
    last_seen       TEXT    NOT NULL,
    day             TEXT    NOT NULL,   -- YYYY-MM-DD (local time)
    hour            INTEGER NOT NULL,   -- 0-23, hour the person was first seen
    duration_sec    REAL    NOT NULL,   -- total time visible on camera
    menu_dwell_sec  REAL    NOT NULL,   -- time spent standing in the menu zone
    read_menu       INTEGER NOT NULL,   -- 1 if menu_dwell_sec >= threshold
    entered         INTEGER NOT NULL,   -- 1 if they walked in through the door
    outcome         TEXT    NOT NULL,   -- passed_by | read_left | read_entered | walked_in | exited_shop
    seeded          INTEGER NOT NULL DEFAULT 0  -- 1 = sample data from seed_demo.py
);
CREATE INDEX IF NOT EXISTS idx_visits_day ON visits(day, camera_id);
"""

OUTCOMES = ["passed_by", "read_left", "read_entered", "walked_in", "exited_shop"]


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(SCHEMA)
            if "seeded" not in [r["name"] for r in con.execute("PRAGMA table_info(visits)")]:
                con.execute("ALTER TABLE visits ADD COLUMN seeded INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _connect(self):
        # sqlite3's own context manager commits but never closes, so close explicitly
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def insert_visit(self, camera_id, track_id, first_seen: datetime, last_seen: datetime,
                     menu_dwell_sec, read_menu, entered, outcome):
        with self._lock, self._connect() as con:
            con.execute(
                """INSERT INTO visits (camera_id, track_id, first_seen, last_seen, day, hour,
                       duration_sec, menu_dwell_sec, read_menu, entered, outcome)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (camera_id, track_id, first_seen.isoformat(timespec="seconds"),
                 last_seen.isoformat(timespec="seconds"), first_seen.strftime("%Y-%m-%d"),
                 first_seen.hour, round((last_seen - first_seen).total_seconds(), 2),
                 round(menu_dwell_sec, 2), int(read_menu), int(entered), outcome),
            )

    def insert_seeded(self, rows):
        """Bulk-insert sample rows: (camera_id, track_id, first_seen, last_seen, menu_dwell_sec, outcome)."""
        with self._lock, self._connect() as con:
            con.executemany(
                """INSERT INTO visits (camera_id, track_id, first_seen, last_seen, day, hour, duration_sec,
                       menu_dwell_sec, read_menu, entered, outcome, seeded)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                [(cam, tid, a.isoformat(timespec="seconds"), b.isoformat(timespec="seconds"), a.strftime("%Y-%m-%d"),
                  a.hour, round((b - a).total_seconds(), 2), round(dwell, 2),
                  int(outcome in ("read_left", "read_entered")), int(outcome in ("read_entered", "walked_in")), outcome)
                 for cam, tid, a, b, dwell, outcome in rows])

    def delete_seeded(self, camera_id=None):
        where, args = "seeded = 1", []
        if camera_id:
            where += " AND camera_id = ?"
            args.append(camera_id)
        with self._lock, self._connect() as con:
            return con.execute(f"DELETE FROM visits WHERE {where}", args).rowcount

    def delete_day(self, day, camera_id=None):
        where, args = "day = ?", [day]
        if camera_id:
            where += " AND camera_id = ?"
            args.append(camera_id)
        with self._lock, self._connect() as con:
            return con.execute(f"DELETE FROM visits WHERE {where}", args).rowcount

    def days(self, camera_id=None):
        q = "SELECT DISTINCT day FROM visits"
        args = ()
        if camera_id:
            q += " WHERE camera_id = ?"
            args = (camera_id,)
        with self._connect() as con:
            return [r["day"] for r in con.execute(q + " ORDER BY day DESC", args)]

    def cameras(self):
        with self._connect() as con:
            return [r["camera_id"] for r in con.execute("SELECT DISTINCT camera_id FROM visits")]

    def daily_stats(self, day, camera_id=None):
        where = "day = ?"
        args = [day]
        if camera_id:
            where += " AND camera_id = ?"
            args.append(camera_id)
        with self._connect() as con:
            totals = {o: 0 for o in OUTCOMES}
            for r in con.execute(f"SELECT outcome, COUNT(*) n FROM visits WHERE {where} GROUP BY outcome", args):
                totals[r["outcome"]] = r["n"]

            hourly = {h: {o: 0 for o in OUTCOMES} for h in range(24)}
            for r in con.execute(
                    f"SELECT hour, outcome, COUNT(*) n FROM visits WHERE {where} GROUP BY hour, outcome", args):
                hourly[r["hour"]][r["outcome"]] = r["n"]

            seeded = con.execute(f"SELECT COUNT(*) n FROM visits WHERE {where} AND seeded = 1", args).fetchone()["n"]

            avg = con.execute(
                f"SELECT AVG(menu_dwell_sec) a FROM visits WHERE {where} AND read_menu = 1", args).fetchone()["a"]

            recent = [dict(r) for r in con.execute(
                f"""SELECT first_seen, last_seen, track_id, menu_dwell_sec, outcome, camera_id
                    FROM visits WHERE {where} AND outcome != 'passed_by'
                    ORDER BY id DESC LIMIT 25""", args)]

        readers = totals["read_left"] + totals["read_entered"]
        return {
            "day": day,
            "totals": totals,
            "menu_readers": readers,
            "conversion_pct": round(100 * totals["read_entered"] / readers, 1) if readers else None,
            "avg_read_sec": round(avg, 1) if avg else None,
            "hourly": [{"hour": h, **hourly[h]} for h in range(24)],
            "recent": recent,
            "seeded": seeded,
        }

    def trend(self, end_day, n_days=14, camera_id=None):
        """Per-day outcome totals for the n_days up to and including end_day (missing days count as zero)."""
        end = datetime.strptime(end_day, "%Y-%m-%d")
        days = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days - 1, -1, -1)]
        where, args = "day BETWEEN ? AND ?", [days[0], days[-1]]
        if camera_id:
            where += " AND camera_id = ?"
            args.append(camera_id)
        out = {d: {o: 0 for o in OUTCOMES} for d in days}
        with self._connect() as con:
            for r in con.execute(f"SELECT day, outcome, COUNT(*) n FROM visits WHERE {where} GROUP BY day, outcome", args):
                out[r["day"]][r["outcome"]] = r["n"]
        return [{"day": d, **out[d]} for d in days]
