# CSU Check-In 🎓

**Classroom attendance tracking for higher education** — built for Columbus State University.

A self-contained Python/FastAPI web app with rotating QR code attendance,
GPS geofencing, D2L Brightspace LTI 1.3 integration, and early-alert analytics.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and configure environment
cp .env.example .env
# Edit .env with your settings (defaults work for demo)

# 3. Run
python run.py
```

Open **http://localhost:8000** — demo credentials are pre-seeded:

| Role       | Email                                      | Password  |
|------------|--------------------------------------------|-----------|
| Instructor | rothlisberger_michael@columbusstate.edu     | admin123  |

10 demo students (IDs `900100001` – `900100010`) are also created.

## How It Works

### For Instructors
1. Log in → Dashboard shows your courses
2. Click **Start Session** on a course
3. Display the QR code on the projector — it rotates every 20 seconds
4. Watch students check in live (SSE auto-refresh)
5. Click **End Session** when done
6. View analytics, export CSV, or check early alerts

### For Students
1. Scan the QR code with your phone camera
2. Enter your Student ID number
3. Browser sends GPS for geofence verification
4. Instant ✅ or ❌ feedback

### Features
- **Rotating QR codes** — HMAC-SHA256 signed tokens, 20-second rotation
- **GPS geofencing** — configurable radius (default 75m), Haversine distance
- **IP range verification** — soft check for campus network (logged, not blocking)
- **Live attendance** — Server-Sent Events for real-time count updates
- **Early alerts** — students below 75% attendance or 3+ consecutive absences
- **Analytics** — per-course attendance trends via Chart.js
- **CSV export** — full attendance matrix download
- **D2L Brightspace LTI 1.3** — roster sync (NRPS) and grade passback (AGS)
- **Standalone + LTI modes** — works independently or embedded in Brightspace

## Architecture

- **Backend**: Python 3 / FastAPI / SQLAlchemy (async) / SQLite
- **Frontend**: Jinja2 templates, vanilla JS, custom CSS
- **No build step** — pure server-rendered HTML
- **Single process** — run.py starts uvicorn

## Configuration

All settings via environment variables (or `.env` file):

| Variable                  | Default         | Description                        |
|---------------------------|-----------------|------------------------------------|
| SECRET_KEY                | (change me!)    | HMAC signing + session cookies     |
| DATABASE_URL              | sqlite+aiosqlite:///./csu_checkin.db | Database |
| ADMIN_PASSWORD            | admin123        | Default instructor password        |
| QR_ROTATION_SECONDS       | 20              | QR code rotation interval          |
| DEFAULT_GEOFENCE_RADIUS_M | 75              | Default geofence radius (meters)   |
| LATE_THRESHOLD_MINUTES    | 10              | Minutes before marking late        |

## D2L / LTI 1.3 Setup

1. Register CSU Check-In as an LTI 1.3 tool in D2L Brightspace
2. Configure the institution's LTI settings in **Settings** (from the course page)
3. Set launch URL to `https://your-domain/lti/launch`
4. Set OIDC login URL to `https://your-domain/lti/login`
5. JWKS URL: `https://your-domain/lti/jwks`

## License

Internal tool — Columbus State University.
