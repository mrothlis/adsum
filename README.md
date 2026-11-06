# Attendance Check-In

Classroom attendance via a rotating QR code, with results pushed into the
D2L Brightspace gradebook.

The instructor starts a session and projects a QR code that rotates every 20
seconds. Students scan it with their phone and are marked present. At any
point the instructor exports a CSV that D2L imports directly, writing both an
attendance grade and a complete per-date record into the gradebook.

Built by an instructor for his own classes, and released under the MIT licence
in case it is useful elsewhere.

## Why it exists

D2L has a built-in Attendance tool, but attendance must be entered by hand,
one student at a time, and there is no API or CSV import to automate it
([D2L confirms there is no Valence API for attendance registers][d2l-api]).
This tool captures attendance in seconds and writes it to the gradebook, which
*is* machine-writable.

[d2l-api]: https://community.d2l.com/brightspace/discussion/6519/is-there-api-support-for-attendance-registers

## What it does

- **Rotating QR check-in.** HMAC-SHA256 signed tokens, 20-second rotation, so
  a screenshot shared outside the room stops working.
- **Optional geofence.** Per course, off by default, with an interactive map
  picker. Only distance and in/out are stored; raw GPS is discarded.
- **Launches from D2L** via LTI 1.1, so students arrive already identified and
  need no separate account. An instructor can set this up without IT.
- **Writes the gradebook column.** Export a CSV, import it in D2L.
- **Keeps the full record in D2L.** Alongside the numeric grade, a Text grade
  item carries every class date and its status in one cell, so the gradebook
  holds the whole history without one column per class meeting.
- **Rebuilds itself from D2L.** Because that column is parseable, a D2L grade
  export is a complete backup. *Restore from D2L* reconstructs the app's data
  from it, so this app never has to be the system of record.
- **Attendance policy built in.** Excused absences leave the denominator; the
  first N unexcused absences are forgiven (default 3, configurable).
- **Correct after the fact.** Add, remove or excuse a student on a session
  that has already ended.
- **Live attendance.** Server-sent events update the instructor's screen as
  students check in.
- **Early alerts.** Dashboard flags students below 75% or with 3+ consecutive
  absences.

## Requirements

- Python 3.11+
- A D2L Brightspace course you can add an External Learning Tool link to
- A public HTTPS URL for the server (students' phones must reach it)

## Installation

For a complete walkthrough — hosting options, HTTPS, running as a service,
backups, upgrades and troubleshooting — see **[docs/INSTALL.md](docs/INSTALL.md)**.

The short version:

```bash
git clone <your-fork> attendance-checkin
cd attendance-checkin
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, INSTITUTION_NAME
python run.py
```

Open `http://127.0.0.1:8000` and sign in with the admin credentials from
`.env`. On an empty database that admin account is created automatically.

For development with auto-reload: `python -m uvicorn app.main:app --reload`.

## Connecting D2L (LTI 1.1)

No IT admin required — any instructor can do this.

1. In the app: **Course → Settings**, note the launch URL, consumer key and
   secret.
2. In D2L: **Content → Add Existing Activities → External Learning Tool**.
3. Title `Attendance Check-In`, URL `https://your-host/lti/launch`, plus the
   key and secret from step 1.
4. Tick **Open as External Resource**.
5. Under Security Settings enable **Send system username**, **Send user
   email** and **Send user name**.

Students then launch the tool from D2L, are identified automatically, and
check in with one tap.

## Getting attendance into the gradebook

1. **Export for D2L Gradebook** on the course page.
2. In D2L: **Grades → Import**, choose the file, and tick *Create new grade
   item when an unrecognized item is referenced*.
3. Set **Maximum Points to 100** for the *Attendance* item. D2L defaults to
   10, which renders everyone as 1000%. The *Attendance Detail* text item
   needs no properties.
4. Continue through the preview, then Import.

Re-importing later overwrites both columns with the current running totals, so
there is nothing to reconcile. It also overwrites any manual edit made in D2L
— correct attendance in the app and re-export instead.

Fully automatic passback would need LTI 1.3 Advantage (AGS), which requires
your IT department to register the tool.

## Configuration

All settings live in `.env`; see `.env.example` for the full list.

| Key | Purpose |
| --- | --- |
| `SECRET_KEY` | **Required.** Signs session cookies and QR tokens. Must be random. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | **Required.** Bootstrap admin, created on an empty DB. |
| `BASE_URL` | Public URL embedded in QR codes. |
| `INSTITUTION_NAME`, `D2L_HOST`, `LTI_CONSUMER_KEY` | Institution identity, used when seeding. |
| `ALLOWED_UNEXCUSED_ABSENCES` | Free absences before the grade is affected (default 3). |
| `DISPLAY_TIMEZONE` | Zone for displayed times. Timestamps are stored as UTC. |
| `QR_ROTATION_SECONDS` | Token rotation interval (default 20). |
| `DEFAULT_GEOFENCE_LAT/LON/RADIUS_M` | Starting map position and radius for new courses. |
| `CHECKIN_WINDOW_MINUTES`, `LATE_THRESHOLD_MINUTES` | Late handling. |
| `DATABASE_URL` | Defaults to local SQLite. |
| `SEED_DEMO` | `true` seeds a demo course and students. Development only. |
| `DEBUG` | Leave `false` outside local debugging. |

Never commit `.env`.

## Roles

- **Instructor** — manages their own courses only. Cannot see or alter another
  instructor's courses, rosters or attendance.
- **Admin** (`is_admin`) — additionally manages the shared D2L host and LTI
  credentials that every course on the installation depends on.

## Tests

Run against a running instance. They read `BASE_URL`, `ADMIN_EMAIL` and
`ADMIN_PASSWORD` from the environment or `.env`.

```bash
python scripts/smoke_test.py             # end-to-end happy path, 12 checks
python scripts/test_isolation.py         # one instructor cannot reach another's data
python scripts/test_admin_separation.py  # ordinary instructors cannot change shared config
```

The latter two create and clean up their own throwaway records. All three
expect a course with at least one enrolled student; override the target with
`TEST_COURSE_ID`.

## Database migrations

Schema changes are versioned with Alembic.

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Backups

`backup_db.py` uses SQLite's online backup API, so it is safe to run while the
app is live. It keeps the last 14 backups in `backups/`. To restore: stop the
app, replace the database file with a backup, restart.

A D2L grade export containing the *Attendance Detail* column is also a
complete backup — see *Restore from D2L* on the course page.

## Deployment

See [docs/INSTALL.md](docs/INSTALL.md) for the full installation guide, and
[docs/DEPLOY.md](docs/DEPLOY.md) for notes on the original Windows setup. A `Dockerfile` and `docker-compose.yml`
are included; the compose file mounts `./data` and `./backups` so the database
and backups persist outside the container. SQLite is the default and is fine
at course or department scale; point `DATABASE_URL` at PostgreSQL for anything
larger.

## Privacy

- Attendance records are student education records. Host accordingly.
- Location is checked once against the classroom geofence; only the distance
  and an in/out flag are stored. Raw GPS is never persisted.
- Students see a notice before any location check.
- No analytics, tracking, or third-party data sharing.

## Tech stack

Python 3.11+, FastAPI, SQLAlchemy (async), SQLite by default. Jinja2
templates with vanilla JS, Leaflet for the map picker and Chart.js for
trends. Signed cookies via `itsdangerous`; PBKDF2-SHA256 password hashing.

## Licence

MIT — see [LICENSE](LICENSE).
