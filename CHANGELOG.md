# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — standalone mode: Adsum without an LMS

A Course could only ever be created in two places, `app/routes/lti.py` and
`app/seed.py`, and a student's identity at check-in came only from the signed
session cookie an LTI launch issues. Those two facts were the whole of the
app's dependency on D2L — and they meant a fresh install's dashboard was
permanently empty for anyone without Brightspace, and that Adsum would stop
working when D2L retires LTI 1.1 (no new 1.1 integrations after the February
2027 release; launches stop late 2027).

- **Courses can be created in the interface.** *Dashboard → New course*, owned
  by the instructor who creates it. The dashboard's empty state now offers
  both paths honestly — launch from D2L, or create one here — instead of
  explaining that there is no button on purpose. A course created this way
  works with everything that already existed: roster import with add/drop
  reconciliation, sessions, the rotating QR, the typed code, per-course
  policy, the geofence, campus IP ranges and the exports.
- **Identity is a per-course mode.** `courses.mode` is `d2l` or `standalone`,
  NOT NULL with a server default of `d2l`, so every existing row — including
  the live course — keeps the LMS path and nothing about it changes. In
  standalone mode the D2L-only settings (org unit id, deep link) and the
  Brightspace import instructions are hidden rather than shown broken, and the
  gradebook export becomes "download a CSV for whatever gradebook you use".
- **Claim-once device binding.** With no LMS there is no cookie to read, so
  the first time a student scans they pick their own name from the roster;
  that binds their phone and checks them in **in the same request**. There is
  no two-scan problem here because there is no login round trip. Every later
  scan is silent, exactly as before.

### Security — the limits of a claimed identity, enforced

Standalone identity is weaker than an LMS vouching for a student, so the
guards around it are the feature:

- **A claim requires a valid, current QR token.** Claiming is only possible
  from inside a live check-in, with a token that passes the same verification
  a check-in does. Without this, anybody on the internet could bind themselves
  to a student's name, and identity would no longer be tied to being in the
  room. A correctly signed but out-of-date token is refused like any other.
- **One active binding per student per course, enforced by the database.** A
  partial unique index on `device_bindings(course_id, student_id) WHERE
  released_at IS NULL`, following `uq_sessions_one_open_per_course`, because a
  SELECT-then-INSERT loses the race between two phones claiming at the same
  moment. The second device is **refused** — never silently allowed, never
  silently moved — and the route catches `IntegrityError` rather than
  returning 500. One phone also cannot hold two names.
- **The device cookie is unforgeable and scoped.** Signed with the app's
  existing `itsdangerous` machinery under its **own salt**, so an instructor
  session cookie is not a valid device cookie and vice versa. It carries the
  course, the student and the binding row, is named per course, and is
  long-lived (about ten months) because it is the student's identity for the
  term. It is `HttpOnly`, `SameSite=Lax` and `Secure` when `BASE_URL` is
  HTTPS, like the session cookie. Revocation does not wait for the clock: the
  binding row is re-read on every scan, so a release signs that phone out
  immediately.
- **The roster is not published.** The "who are you?" list shows **names
  only** — never student ID numbers or usernames, which are education records
  — and only students on the **active** roster, so somebody a classlist import
  marked as dropped cannot be claimed.
- **Claim attempts are rate limited**, following the typed-code pattern:
  failures only, cleared on success, over a short window, so a student who
  taps the wrong thing is not locked out in front of a class.
- **Every claim, refused claim and release is logged.**

### Added — instructor control over bindings

- The course page lists which phone is checked in as which student and when,
  with a **Release** action for a new phone, a lost phone or a wrong name.
  **Releasing deletes no attendance**: attendance belongs to the student, not
  to the device.
- **Refused claims are shown at the top of the course page**, by name and
  time, as a permanent notice until the instructor acknowledges them. That is
  the main defence against a mistaken or malicious bind, because it is the
  only signal that reaches the person who can fix it while the class is still
  in the room. Acknowledged conflicts are marked reviewed, not deleted.

### Migration

`a7f3c1d05b84`, additive and reversible: adds `courses.mode`, the
`device_bindings` and `claim_conflicts` tables, and the partial unique index.
Tested up, down and up again against a copy of a real backup, with attendance
records intact in every direction; the downgrade touches no attendance.

### Documentation

README and `docs/INSTALL.md` carry a standalone section that states the trade
rather than selling it: the rotating QR still proves the student is in the
room, but at the first scan a student could claim a classmate's name, which is
why one binding per student is enforced, why conflicts are surfaced, and why
bindings can be released. Both say plainly that the D2L/LTI path gives
stronger identity because the LMS vouches for it, that single sign-on would
remove claim-time impersonation entirely and is a known future option that
**does not exist today**, and that standalone mode is what keeps the tool
usable as D2L retires LTI 1.1. `docs/INSTALL.md` section 11 also now lists all
nine test scripts; it had drifted to six.

### Tests

`scripts/test_standalone.py` pins every invariant above: a claim with no
token, a malformed token, a forged signature and an expired token are all
refused with nothing written; a second device cannot claim a bound student;
the database itself refuses a second active binding row; a dropped student is
not claimable and is not on the list; five kinds of hand-made device cookie
identify nobody; a binding for one course does not identify in another;
releasing preserves attendance and frees the name; and a standalone course
never shows a D2L setting. Added to `scripts/ci_local.sh` **and** the
integration job in `.github/workflows/ci.yml` together.

### Added — the classlist import is a reconciliation, not an append

The import counted new and updated students and never noticed anybody who had
**disappeared** from the classlist. A student who dropped stayed enrolled
forever, kept accruing absences, stayed in every denominator, and kept
appearing in the D2L gradebook export. The whole record of an import was a
transient banner reading "32 new, 4 updated".

- **Drops are detected and named.** A student on the roster who is missing
  from a newly imported classlist is marked dropped (`dropped_at` on
  `course_enrollments`) — **marked, never deleted and never unenrolled**,
  because their attendance is real history and the D2L gradebook may still
  refer to them. The same rule the app already follows for instructor
  accounts. A student who reappears in a later import is un-dropped and
  reported as a re-add, with their earlier attendance intact.
- **A wrong file cannot drop the class.** Importing a filtered export,
  another section's classlist or last term's file is the realistic accident,
  and it used to be indistinguishable from a mass drop. The import now
  **refuses and changes nothing** — not the drops and not the adds — when a
  file would drop more than half the active roster, or when it shares no
  students at all with a non-empty roster. The refusal page names how many
  would go and who, explains what to check in D2L, and offers a deliberate
  server-side confirmation (the same pattern as deleting a session) for the
  case where it really is correct. Half is the threshold because a real
  semester's drops are a handful of students, not a third of a section.
- **Dropped students are gone from every grade-facing surface, with their
  data kept.** Audited and fixed: the course roster and its count, the
  early-alert list, the live session page's enrolled total and its
  mark-present list, the live attendance API and its SSE stream, the
  analytics enrolled count, both CSV exports, and the D2L gradebook export —
  where a dropped student's row would be rejected by D2L as an unrecognised
  user. They are shown instead in a collapsed **Dropped from the D2L
  classlist** section with the date they were detected as gone and the
  attendance they had accumulated, so a dispute can still be settled.
  Marking a dropped student present is refused.
- **The import summary is permanent and names names.** It lists who was
  added, who was re-added, who has gone and whose name, username or D2L ID
  changed (with both values, because the export matches on the username and a
  silent change stops a grade landing). Stored on the course rather than
  passed in the redirect, since a query string cannot carry a list of names.
- **A stale roster says so.** The course page and the dashboard card show
  when a classlist was last imported in plain language, and warn after a
  week or when one has never been imported. Worded to be honest: Adsum
  **cannot** ask D2L who has added or dropped — an LTI 1.1 launch carries no
  roster and the Valence enrollments API needs an OAuth application
  registered by a Brightspace administrator — so the warning says to
  re-download the classlist, never that D2L was checked.
- An LTI launch by a student previously marked dropped un-drops them: D2L
  granting them access to the course is fresher evidence than a classlist
  file from last week, and otherwise a re-added student could not check in
  until the instructor remembered to import again.
- The "not listed in D2L" flag now explains both of its meanings — a new add
  that has not been re-imported yet, or somebody who should not be there.
- Migration `c3b7f0d41a92` (additive, reversible): `dropped_at` on
  `course_enrollments`, `roster_last_imported_at` and
  `roster_last_import_summary` on `courses`. NULL everywhere for existing
  rows, which is the honest translation — every existing enrollment is still
  on the classlist as far as the old additive import could tell, and no
  course has an import the app ever observed. Pinned by
  `scripts/test_roster_sync.py`, which runs in CI.

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
