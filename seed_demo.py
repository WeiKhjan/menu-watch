"""Fill the database with realistic sample traffic so the dashboard has history to show.

Sample rows are flagged (seeded = 1) and the dashboard labels days that contain them.
Running this again replaces the previous sample rows; live counts are never touched.

  python seed_demo.py                 # last 14 days + today up to now
  python seed_demo.py --days 30
  python seed_demo.py --remove        # delete all sample rows
"""
import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

from menu_watch.db import Database

ROOT = Path(__file__).parent

# People walking past the door per hour on a weekday (outlet open 08:00-22:00)
FOOT_TRAFFIC = {8: 45, 9: 55, 10: 65, 11: 115, 12: 195, 13: 175, 14: 95, 15: 70,
                16: 80, 17: 115, 18: 175, 19: 195, 20: 150, 21: 90, 22: 35}
MEAL_HOURS = {12, 13, 18, 19}


def simulate_hour(rng, day, hour, weekday_factor, day_factor, until=None):
    """Return (first_seen, last_seen, dwell, outcome) tuples for one hour of foot traffic."""
    people = rng.gauss(FOOT_TRAFFIC.get(hour, 0) * weekday_factor * day_factor, 8)
    rows = []
    for _ in range(max(0, round(people))):
        t0 = datetime(day.year, day.month, day.day, hour) + timedelta(seconds=rng.uniform(0, 3600))
        if until and t0 >= until:
            continue
        r = rng.random()
        read_rate = 0.11 if hour in MEAL_HOURS else 0.085
        if r < read_rate:
            dwell = min(60.0, 3.0 + rng.lognormvariate(1.55, 0.6))      # median about 8 s at the menu
            # longer readers are more likely to come in; meal times convert better
            p_enter = (0.30 if hour in MEAL_HOURS else 0.22) + min(0.25, dwell / 80)
            outcome = "read_entered" if rng.random() < p_enter else "read_left"
            dur = dwell + rng.uniform(3, 9)
        elif r < read_rate + 0.045:
            outcome, dwell, dur = "walked_in", rng.uniform(0, 1.5), rng.uniform(2, 5)
        else:
            outcome, dwell, dur = "passed_by", (rng.uniform(0.3, 2.6) if rng.random() < 0.15 else 0.0), rng.uniform(3, 8)
        rows.append((t0, t0 + timedelta(seconds=dur), dwell, outcome))
    # diners leave 30-70 minutes after going in
    for t0, _, _, outcome in list(rows):
        if outcome in ("read_entered", "walked_in") and rng.random() < 0.9:
            t1 = t0 + timedelta(minutes=rng.uniform(30, 70))
            if t1.date() == day and (not until or t1 < until):
                rows.append((t1, t1 + timedelta(seconds=rng.uniform(2, 5)), 0.0, "exited_shop"))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(ROOT / "menu_watch.db"))
    p.add_argument("--camera-id", default="door-cam-1")
    p.add_argument("--days", type=int, default=14, help="past days to fill, plus today up to the current time")
    p.add_argument("--seed", type=int, default=5, help="random seed, for repeatable numbers")
    p.add_argument("--remove", action="store_true", help="only delete existing sample rows")
    args = p.parse_args()

    db = Database(args.db)
    removed = db.delete_seeded(args.camera_id)
    if args.remove:
        print(f"Removed {removed} sample rows.")
        return

    rng = random.Random(args.seed)
    now = datetime.now()
    rows = []
    for back in range(args.days, -1, -1):
        day = (now - timedelta(days=back)).date()
        weekday_factor = 1.3 if day.weekday() >= 5 else (1.12 if day.weekday() == 4 else 1.0)
        day_factor = rng.uniform(0.8, 1.12) * (0.65 if rng.random() < 0.12 else 1.0)  # the odd rainy day
        events = []
        for hour in FOOT_TRAFFIC:
            events += simulate_hour(rng, day, hour, weekday_factor, day_factor, until=now if back == 0 else None)
        events.sort()
        rows += [(args.camera_id, 10000 + i, a, b, dwell, outcome) for i, (a, b, dwell, outcome) in enumerate(events, 1)]

    db.insert_seeded(rows)
    print(f"Added {len(rows)} sample rows over {args.days + 1} days (replaced {removed}).")


if __name__ == "__main__":
    main()
