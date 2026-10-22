# Menu Watch: menu-reader counter for F&B door cameras

![Menu Watch dashboard demo](docs/demo.gif)

▶ **[Watch the full demo video (MP4, 41 s)](docs/demo.mp4)**: live camera analysis, menu funnel, hourly and 14-day charts, opening a past day, dark mode and the zone editor.

| Light | Dark |
|---|---|
| ![Dashboard, light theme](docs/dashboard-light.png) | ![Dashboard, dark theme](docs/dashboard-dark.png) |

Menu Watch reads the CCTV camera at your outlet's entrance in real time. For each person who walks past, it decides one of these outcomes:

| Outcome | Meaning |
|---|---|
| `passed_by` | Walked past without stopping |
| `read_left` | **Stood at the menu for at least N seconds, then walked away (a lost customer)** |
| `read_entered` | Stood at the menu, then came in |
| `walked_in` | Came in without stopping at the menu |
| `exited_shop` | A customer coming out of the shop. Not counted as a visitor |

Each person is saved as one row in a SQLite database (`menu_watch.db`, table `visits`). The web dashboard shows the daily totals, an hourly chart, menu conversion rate and average reading time. It also has a CSV export.

## Quick start (Windows)

Double-click **`start_demo.bat`**. It plays the sample footage in a loop and opens http://localhost:5000.

To start it by hand:

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py --source footage\shopfront_bradford.mp4 --loop
```

## Sample history

`python seed_demo.py` fills the last 14 days (and today up to now) with realistic sample traffic: lunch and dinner peaks, busier weekends and the odd rainy day. Sample rows are flagged in the database, and the dashboard shows an "Includes N sample records" chip on days that have them. Running it again replaces the old sample rows, and `python seed_demo.py --remove` deletes them. Live counts are never touched.

## Fast-forward (recorded footage)

When the source is a video file, the dashboard shows **1× 2× 4× 8×** buttons. You can also start at a set speed with `--speed 4`. Fast-forward doesn't use more CPU; instead the app analyses fewer frames per second of video. Each analysed frame should cover about 0.5 s of video or less, or people move too far between frames to be tracked. On roughly 10 fps hardware that means up to about 4×. The dashboard warns you when the speed is too high for the PC. Timestamps follow the video's own time, so 8 hours of recording played at 4× still fills the hourly chart correctly.

This is useful for analysing a full day of exported NVR footage in a few hours.

## Using a real outlet camera

```bat
:: Hikvision / Dahua style RTSP (use the sub-stream for less CPU)
.venv\Scripts\python run.py --source "rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102" --camera-id outlet-1

:: A YouTube live stream
.venv\Scripts\python run.py --source "https://www.youtube.com/watch?v=XXXXXXXX"

:: USB webcam
.venv\Scripts\python run.py --source 0
```

After that, open **http://localhost:5000/zones** and draw two zones on the camera image:

1. **Menu zone**: the floor right in front of the menu board or window, where people stand while they read.
2. **Door**: the doorway. People who disappear here have gone in, and people who first appear here are coming out.

Zones are saved to `config.json` and take effect immediately.

## How it decides

1. **Detect and track.** YOLO11n finds people in each frame and ByteTrack gives each person a stable ID.
2. **Reading time.** The app checks where each person's feet are. A person is "reading" when their feet are inside the menu zone **and** they are standing still: moving slower than `max_still_speed` body-heights per second. Walking past the menu zone does not count.
3. **Menu reader.** Once the reading time reaches `min_read_sec` (3 s by default), the person counts as a menu reader. On the live view the box turns orange.
4. **Final outcome.** When a person has been out of view for `lost_timeout_sec`, the app gets their outcome from their path: whether they read the menu, and whether their last position was in the door zone. That row is written to the database.

## Settings (`config.json`)

| Key | Default | Notes |
|---|---|---|
| `min_read_sec` | 3.0 | Seconds standing at the menu before a person counts as a "reader" |
| `max_still_speed` | 0.45 | Body-heights per second. Raise it if slow browsers are missed |
| `analysis_fps` | 10 | Frames analysed per second (CPU budget) |
| `confidence` | 0.35 | Person-detection threshold |
| `model` | yolo11n.pt | Use `yolo11s.pt` for better accuracy if you have a GPU |

## Testing the decision logic

`.venv\Scripts\python tests\test_logic.py` runs six scripted people through the analyzer. They are a reader who leaves, a reader who enters, a walk-in, someone coming out of the shop, a passer-by and a 1.5 s glance. The test checks that each one gets the right outcome.

## Database

```sql
-- daily summary straight from SQLite
SELECT day, outcome, COUNT(*) FROM visits GROUP BY day, outcome;
```

The **Clear day** button on the dashboard deletes all records for the day you are viewing. You have to click it twice to confirm. To wipe everything, stop the app and delete `menu_watch.db`.

API endpoints: `/api/stats?day=YYYY-MM-DD`, `/api/export.csv?day=...`, `/api/status`, `POST /api/clear {"day": "YYYY-MM-DD"}`.

## Limitations

- If someone is hidden for more than about 3 seconds (behind a pillar or another person), they can come back with a new ID and be counted twice.
- The camera should look **down at the pavement** in front of the shop, like the sample footage. Head-height cameras get much more occlusion.
- Groups who stand close together can merge into one detection.
- This is a counting tool only. It stores no faces or images, just anonymous track IDs and timings. Check your local privacy rules (Malaysia's PDPA, for example) and put up CCTV signage.

## Sample footage

`footage/shopfront_bradford.mp4` is real exterior shop-front CCTV from Bradford town centre, published on YouTube (video `SZgLB9cUsrI`). It is included only for testing. Public CCTV of an F&B outlet's exterior with people reading menus is basically never published, so for real numbers, point the app at your own outlet's camera.

## Deploying to a server

See [DEPLOY.md](DEPLOY.md). It uses a one-command installer (`deploy/install.sh`) for Ubuntu/Debian or Amazon Linux, with nginx, systemd and a login.
