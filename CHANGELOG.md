# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- **Corrected the D2L setup sequence.** README, `docs/INSTALL.md` section 6 and
  `docs/DEPLOY.md` all told the reader to copy the launch URL, consumer key and
  consumer secret from Course → Settings. That is impossible on a fresh
  install: there are no courses until the first LTI launch creates one, and the
  course settings page deliberately hides the secret. The credentials come from
  `/admin`, and the course page only exists after the first launch.
- **Documented D2L's retirement of LTI 1.1**, dated, with sources, in README
  and `docs/INSTALL.md`: no new 1.1 integrations in Brightspace after the
  February 2027 release, and 1.1 launches stop working in late 2027. LTI 1.3 is
  named as the project's next step, with no dates promised.
- **Added `docs/BRIGHTSPACE-ADMIN.md`**, a one-page briefing an instructor can
  forward to IT: what Adsum stores, where it runs, its FERPA posture, and
  exactly what registration is being asked for.
- **Added `requirements-dev.txt`** (`ruff`, `requests`), because the documented
  `ruff check` command failed in a fresh virtualenv.
- **Rewrote `docs/DEPLOY.md`** as a generic Windows deployment guide. Removed
  every personal identifier, the hardcoded layout, the reference to a
  `rotate_secrets.py` script that does not exist, the Python 3.13+ requirement
  that contradicted the project's 3.11+, the gradebook section that referenced
  a grade column that no longer exists, and the `alembic upgrade head` step
  that crashes on an empty database.
- **Added Windows instructions throughout `docs/INSTALL.md`**: virtualenv,
  copying `.env`, the PowerShell quoting for the `SECRET_KEY` one-liner, the
  Task Scheduler backup job, and where the logs are.
- **Stopped storing raw GPS, and erased what was stored.** README,
  `SECURITY.md`, `CONTRIBUTING.md` and `docs/DEPLOY.md` all promised that only
  the distance from the classroom and an in/out flag were kept, and the code
  wrote the latitude and longitude on every geofenced check-in. The code now
  honours the promise, the migration erases coordinates captured by earlier
  versions, and `scripts/test_security.py` fails the build if an attendance
  row ever holds one again.
- **Corrected other false claims**: the settings count, "automatic passback
  needs LTI 1.3 AGS" (the Valence Grades API can write grades too; both routes
  need an administrator), PostgreSQL support (SQLite only today), "or a
  container runtime" as a supported host, the number of test scripts and which
  of them need a live server and a course, and the 2019 attendance-API
  citation, replaced with a 2026 one.
- **Documented what a new installer actually sees**: an empty dashboard, no way
  to create a course in the interface, and a recommended first run with
  `SEED_DEMO=true` on a throwaway database. The go-live checklist is now
  ordered so the four tests that need an enrolled student come after D2L is
  connected.
- **Documented `HOST` and `PORT`**, including that the `0.0.0.0` default
  exposes a local try-out to the whole network, and that a real environment
  variable overrides `.env`.
- **Added a section on checking an attendance percentage by hand**, with the
  formula and a worked example.

## [1.0.0] - 2026-11-06

First public release. Adsum has been used for a live D2L course, including a
verified end-to-end gradebook round trip.

### Added

- **QR check-in.** HMAC-SHA256 signed tokens that rotate every 20 seconds, so
  a screenshot shared outside the room stops working.
- **LTI 1.1 launch from D2L**, so students arrive already identified and no
  separate account is needed. An instructor can set this up without IT.
- **Gradebook export.** A numeric `Attendance` column plus an
  `Attendance Detail` **Text** grade item holding the whole per-date ledger in
  a single cell, instead of one column per class meeting.
- **Restore from D2L.** The ledger is round-trippable, so a D2L grade export
  is a complete backup and can rebuild the app's sessions and records.
- **Attendance policy.** Excused absences leave the denominator; the first N
  unexcused absences are forgiven, configurable and defaulting to three.
- **Post-hoc corrections.** Mark present, unmark, or excuse a student on a
  session that has already ended.
- **Optional per-course geofence** with a map picker. Off by default. (This
  release stored the check-in's coordinates despite the documentation saying
  otherwise. Fixed after 1.0.0; see Unreleased.)
- **Live attendance** via server-sent events, and an at-risk dashboard.
- **Roster import** from a D2L Classlist export, with inline editing.
- **Admin role** separating institution-wide D2L and LTI settings from
  ordinary instructor accounts.
- **Operational tooling**: `/healthz`, an online-backup script retaining the
  last 14 copies, Alembic migrations, and a Windows service launcher.
- **Test suite**: a scoring check, a clean-install verifier, an end-to-end
  smoke test and three authorization suites, six scripts in all, run in CI on
  Python 3.11, 3.12 and 3.13.

### Security

- The app refuses to start outside `DEBUG` when `SECRET_KEY` is missing, too
  short, or still the placeholder, or when `ADMIN_PASSWORD` is empty.
- Login throttling: five failures per IP, then a ten-minute lockout.
- Every course and session route verifies the signed-in instructor teaches
  that course; institution-wide settings require an admin.
- Student-facing pages load no third-party resources.
- `scripts/verify_install.py` fails the build if an `.env` file, a database, a
  log or a credentials file is ever committed, and checks the tree against a
  list of identifiers belonging to the original author.

### Notes

- **Exporting a course with no sessions returns HTTP 409** rather than an
  empty file. D2L holds the record, and an empty export would overwrite it.
- Each gradebook import **overwrites** the previous values, so the export is
  always a running semester total and weekly imports need no reconciliation.
- On the first D2L import only, set the `Attendance` item's **Maximum Points
  to 100**. D2L defaults it to 10, which displays everyone at 1000%.

[Unreleased]: https://github.com/mrothlis/adsum/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/mrothlis/adsum/releases/tag/v1.0.0
