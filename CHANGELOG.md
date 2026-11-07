# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — a present student could be recorded absent

These were reproduced against a disposable copy before being fixed, and each
is now pinned by `scripts/test_robustness.py`, which runs in CI.

- **Two open sessions for one course.** Starting a session was a SELECT then an
  INSERT with no lock and no constraint, so one double-click on Start Session
  created duplicates: 16 simultaneous requests produced five open sessions.
  After that the dashboard returned 500 for every instructor of that course,
  and because the export counts each session row as a class meeting, one real
  class day became several meetings and every student who checked into only one
  of them exported as **absent** for the rest. The database now refuses a
  second open session per course (a partial unique index), a collision
  redirects to the session already running, and every query for a course's open
  session tolerates duplicates so a database that already has them cannot wedge
  a page. The migration closes any it finds rather than deleting them, so no
  attendance is lost.
- **A stray session could be ended but never removed**, and each one handed
  every student an absence while quietly spending the forgiven-absence
  allowance that real absences were meant to use. The only previous remedy
  destroyed the whole course's history. Finished sessions can now be deleted
  from the course page, with a server-enforced confirmation.
- **A failed QR refresh permanently blanked the projected code** and told the
  instructor "Session ended." while the session was still live, so nobody could
  scan for the rest of class. Any 5xx or network error now keeps the code on
  screen and retries with backoff; only a repeatedly confirmed 404/410 closes
  it, and that case no longer borrows the word "ended".
- **A roster import of a Windows-encoded classlist returned 500** and imported
  nothing — any accented name saved by Excel, or an `.xlsx` picked by mistake.
  The roster then stayed empty, which means nobody in that class could check in
  at all. Uploads are now decoded defensively, size-capped, and explained
  instead of crashing.
- **Double-clicking Present or Excused returned 500** from an unhandled unique
  violation, despite the record having been written.

### Fixed — privacy

- **The projected session page showed every student's name and ID number**, and
  the mark-present dropdown listed exactly who was absent, to the whole room.
  Names and that dropdown are now off by default while a session is live, with
  a large count in their place; ID numbers are gone from the live view entirely
  and remain only on the after-class review.

### Added

- **A "Project this" mode** and a much larger QR code — 605px at 1080p instead
  of 300px, upscaled with hard module edges rather than blurred — plus the
  check-in URL in text for a student whose camera will not focus, and a live
  status indicator so a dropped connection no longer looks identical to
  "nobody has scanned yet".

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
