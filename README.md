# CSU Check-In — Attendance tracking for higher ed

Self-contained Python/FastAPI web app with rotating QR codes, GPS geofencing, D2L Brightspace LTI 1.1 launch, early-alert analytics, and CSV exports for the D2L gradebook.

**Production URL:** https://checkin.drrothlisberger.com

---

## Quick start (developer / local)

```bash
# 1. Clone and install
git clone https://github.com/your-org/csu-checkin.git
cd csu-checkin
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure — copy .env.example to .env and edit
cp .env.example .env
# Set SECRET_KEY, ADMIN_PASSWORD, ADMIN_EMAIL at minimum

# 3. Run
python run.py
# or dev mode with reload:
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000.

---

## Key features

| Feature | Detail |
|---|---|
| **Rotating QR codes** | HMAC-SHA256 signed tokens, 20-second rotation |
| **GPS geofence** | Interactive map picker in Settings; optional enforcement with distance logging |
| **LTI 1.1 launch** | Instructor adds tool in D2L (no IT admin) — students launch from D2L → auto-auth |
| **Live attendance** | SSE real-time count + progress bar on instructor session page |
| **Manual mark-present** | Dropdown on session page for students whose phones/location fail |
| **Early alerts** | Dashboard flags students <75% attendance or 3+ consecutive absences |
| **Export for D2L Gradebook** | One-click CSV matching Brightspace grade-import format (OrgDefinedId, Username, points, `#`) |
| **Reset sessions** | One button on Settings to wipe all sessions/records, keep roster & settings |
| **Nightly backups** | SQLite online backup to `backups/` (keeps 14 days); Scheduled Task at 21:00 |
| **Health endpoint** | `/healthz` for uptime monitoring |

---

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | **required** | 32+ random hex chars — signs cookies & QR tokens |
| `ADMIN_PASSWORD` | **required** | Strong password for the instructor account |
| `ADMIN_EMAIL` | admin@example.edu | Email of the admin instructor |
| `SEED_DEMO` | `false` | `true` = seed demo institution/course/students (dev only) |
| `DATABASE_URL` | `sqlite+aiosqlite:///./csu_checkin_v2.db` | Path to SQLite DB |
| `QR_ROTATION_SECONDS` | 20 | Token rotation interval |
| `DEFAULT_GEOFENCE_LAT` | 32.4600 | Default classroom lat (CSU campus) |
| `DEFAULT_GEOFENCE_LON` | -84.9877 | Default classroom lon |
| `DEFAULT_GEOFENCE_RADIUS_M` | 75 | Default geofence radius |
| `CHECKIN_WINDOW_MINUTES` | 15 | Minutes after session start to accept check-ins |
| `LATE_THRESHOLD_MINUTES` | 10 | Minutes after start to mark "late" |
| `DEBUG` | `false` | Set `true` only for local debugging |

**Never commit `.env`.** The admin credentials are stored in `CREDENTIALS.txt` (gitignored).

---

## Production deployment (Windows)

### 1. Scheduled Tasks (current setup)

Two tasks run at logon, auto-restart on crash:

| Task | Script | Purpose |
|---|---|---|
| `csu-checkin` | `start.ps1` | Runs uvicorn with restart loop; writes `server.log` |
| `csu-checkin-backup` | `backup_db.py` | Nightly SQLite online backup at 21:00 |

The tunnel is handled by a separate scheduled task (`EI-tunnel`) that runs `cloudflared` with the config at `C:\EI\tunnel\cloudflared-rupert.yml`, routing `checkin.drrothlisberger.com` → `127.0.0.1:8000`.

### 2. Docker (for IT handoff)

```bash
# Build & run (uses .env from project root)
docker compose up -d --build
```

The `docker-compose.yml` mounts `./data` and `./backups` so the SQLite file and nightly backups persist outside the container. For fresh installs, set `DATABASE_URL=sqlite+aiosqlite:///./data/csu_checkin_v2.db` in the Docker `.env`.

---

## D2L Brightspace setup (LTI 1.1)

No IT admin needed — any instructor can do this:

1. In D2L: **Course → Content → Add Existing Activities → External Learning Tool**
2. Title: `Attendance Check-In`
2. URL: `https://checkin.drrothlisberger.com/lti/launch`
4. Consumer Key: (from Settings page — `csu-checkin` by default)
5. Secret: (from Settings page — auto-generated on first save)
6. Check **Open as External Resource**
7. Security Settings → enable: Send system username, Send user email, Send user name

Students launch the tool from D2L → auto-identified → check-in with one tap.

---

## Gradebook import (no IT required)

After each class (or at term end):

1. Instructor opens the course page → **Export for D2L Gradebook** (downloads CSV)
2. In D2L: **Grades → gear icon → Import Grades**
3. Upload CSV, match `OrgDefinedId` to student, map `Attendance Points Grade` to a grade item
4. Import → attendance appears in the gradebook

This is a one-minute manual step. Fully automatic passback (AGS) requires CSU IT to register the tool for LTI 1.3 Advantage services.

---

## Database migrations (Alembic)

Schema changes are versioned with Alembic.

```bash
# Create a new migration after model changes
alembic revision --autogenerate -m "describe change"

# Apply to DB
alembic upgrade head
```

The production DB is stamped at `head` (baseline revision `1958aa23331c`). Fresh installs run `alembic upgrade head` automatically on first start (handled by `ensure_admin()`).

---

## Nightly backups

`backup_db.py` uses SQLite's online backup API — safe to run while the app is live. Keeps the last 14 backups in `backups/`. Restore: stop the app, replace `csu_checkin_v2.db` with a backup file, restart.

---

## Privacy & compliance (summary)

- Attendance records are **FERPA-protected education records**.
- Location is checked **once** against the classroom geofence; only `in_fence` boolean + distance are stored.
- Raw GPS is never persisted beyond the single check-in request.
- Students see a notice when geofence is enforced: *"Your location is checked once against the classroom area and logged with your attendance."*
- No analytics, tracking, or third-party data sharing.

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.13+, FastAPI, SQLAlchemy (async), SQLite |
| Frontend | Jinja2 templates, vanilla JS, Leaflet map, Chart.js |
| Auth | `itsdangerous` signed cookies, PBKDF2-SHA256 passwords |
| QR tokens | HMAC-SHA256, 20s rotation, 30s expiry |
| Geofence | Haversine distance, optional enforcement |
| Tunnel | Cloudflare Tunnel (`cloudflared`) |
| Process mgmt | Windows Scheduled Tasks + restart-loop PS1 |

---

## License

Internal tool — Columbus State University.