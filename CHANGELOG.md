# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Optional per-course geofence** with a map picker. Off by default. Only the
  distance and an in/out flag are stored; raw GPS is discarded.
- **Live attendance** via server-sent events, and an at-risk dashboard.
- **Roster import** from a D2L Classlist export, with inline editing.
- **Admin role** separating institution-wide D2L and LTI settings from
  ordinary instructor accounts.
- **Operational tooling**: `/healthz`, an online-backup script retaining the
  last 14 copies, Alembic migrations, and a Windows service launcher.
- **Test suite**: a clean-install verifier, an end-to-end smoke test, and two
  authorization suites, all run in CI on Python 3.11, 3.12 and 3.13.

### Security

- The app refuses to start outside `DEBUG` when `SECRET_KEY` is missing, too
  short, or still the placeholder, or when `ADMIN_PASSWORD` is empty.
- Login throttling: five failures per IP, then a ten-minute lockout.
- Every course and session route verifies the signed-in instructor teaches
  that course; institution-wide settings require an admin.
- Student-facing pages load no third-party resources.
- `scripts/verify_install.py` fails the build if a secret, a database, or a
  personal identifier is ever committed.

### Notes

- **Exporting a course with no sessions returns HTTP 409** rather than an
  empty file. D2L holds the record, and an empty export would overwrite it.
- Each gradebook import **overwrites** the previous values, so the export is
  always a running semester total and weekly imports need no reconciliation.
- On the first D2L import only, set the `Attendance` item's **Maximum Points
  to 100**. D2L defaults it to 10, which displays everyone at 1000%.

[Unreleased]: https://github.com/mrothlis/adsum/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/mrothlis/adsum/releases/tag/v1.0.0
