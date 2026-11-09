# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed: three defects that produced wrong grades

A ten-agent review found these. All three were invisible to the existing test
suite, and all three changed a number that goes into a gradebook.

**Grades were rounded down at an exact half.** `score()` computed
`(credited + forgiven) / counted_total * 100` in binary floating point. With
18 attended of 40 and 5 forgiven, the true value is exactly 57.5 and must
round to 58, but the expression evaluated to 57.49999999999999, so the
half-up rounding produced 57. Checked against `fractions.Fraction` across
every reachable input, the old code disagreed with exact arithmetic 1,329
times and was **always one point low, never high**. The default late-credit
setting is 100, so this was not confined to partial-credit courses: it touched
every course. The percentage is now computed entirely in integers scaled by
100, and `unexcused_over_limit` with it, so no grade boundary is left to a
binary fraction. `scripts/test_scoring.py` now checks every grade against
exact rational arithmetic rather than against the previous implementation's
expectations, which is how the defect survived it.

**Exporting during a live class lowered grades.** All four grade-bearing
queries selected every session in the course, including one the instructor had
opened and not yet closed. An open session is a class in progress: counting it
put a meeting in the denominator that nobody had been given the chance to
attend. On a three-session course, 100/50/50 exported to D2L as 67/50/50 while
the QR code was still on the wall. There is now one definition of which
sessions count, `_graded_sessions_stmt`, used by the gradebook export, the CSV
export, the analytics page and the at-risk list. A session that has been
closed with nobody present still counts, correctly: that is a class everyone
missed.

**Restore from D2L collapsed two meetings held on one day.** The restore
grouped existing sessions into a dictionary keyed on the local date. Two
meetings on one day is ordinary (a lab and a lecture, a Monday double period)
and the ledger records them as two tokens carrying the same date. The
dictionary kept only one, the duplicate guard then skipped the second token,
and the absence won. A course standing at 100/50 came back as 100/100. The
information was never missing from the export, only from that dictionary; the
Nth token for a date now maps to the Nth session on that date.

### Fixed: two test suites could not fail a build

`scripts/test_isolation.py` and `scripts/test_admin_separation.py` both ended
by printing `FAILURES: ...` and returning zero. Every cross-instructor
privacy assertion and every admin-separation assertion in CI was decorative:
the job went green no matter what leaked. Both now exit non-zero, verified by
injecting a failure and confirming the exit code.

`test_isolation.py` also finished by posting to `reset-data` on the course it
had been pointed at, which deletes every session and attendance record in that
course. Against seeded demo data that looked like tidying up; pointed at a
real install, and `BASE_URL` is configurable, it silently destroyed a
semester of attendance. It now deletes only the session it created.

### Added: `scripts/test_grade_roundtrip.py`

Drives a real server and reads the actual CSV D2L would import, because none
of the above is observable without both. Proves that opening a session changes
no grade, that closing an empty one correctly counts it, that deleting it
restores the originals, and that an export/restore round trip returns every
grade unchanged with both of a day's two meetings rebuilt.

Every fixture sets the absence allowance and late credit to zero. The first
draft used the default policy, where three absences are forgiven; forgiveness
absorbed the missing meeting, so broken and correct code produced identical
output and the suite passed against known-broken code.

### Changed: documentation that contradicted the code

- The login throttle was documented as "five failures per IP" in two places.
  It is five per account **or** twenty-five per IP, then a ten-minute lockout.
  The per-IP figure is the looser of the two deliberately, because a campus
  network can put a whole building behind one address.
- The number of test scripts was stated in four files and was wrong in all
  four, with three different numbers. The counts are gone;
  `scripts/ci_local.sh` is named as the authoritative list.
- `docs/ACCESSIBILITY.md` claimed two defects in its header while its own body
  described four, and `docs/IT-REVIEW.md` said three. It is four.


### Added: a container deployment, and a Linux deployment guide

An institution's IT team asks how to run this before it asks anything else,
and the honest answer was "no container, and the Linux path is 27 lines in
the install guide". Both are fixed.

`Dockerfile` and `docker-compose.yml` are the supported container path.
Non-root, read-only root filesystem, all capabilities dropped,
`no-new-privileges`, bounded logs so an install cannot fill its own disk and
take the database with it, and the port published on `127.0.0.1` only so the
container is unreachable except through a reverse proxy.

Deliberately not `--proxy-headers` or `--forwarded-allow-ips`. Adsum reads
`X-Forwarded-For` itself through one helper governed by `TRUSTED_PROXY_IPS`,
and the login throttle's per-address limit depends on that answer. Two
mechanisms deciding the same thing is a defect waiting to happen, and the
permissive form of that flag would let anyone who can reach the port claim
any address and evade the throttle.

Three writes had to move out of the application directory before a
read-only filesystem could work, so three settings are new. All default to
exactly their previous paths, so no existing install changes:

- `BACKUP_DIR`, which `backup_db.py` had hardcoded to `backups/` next to
  itself.
- `STATE_DIR`, for the `.migration-failed` marker. This one is
  load-bearing: that marker is what stops a failed migration being retried
  on every restart, so it must not itself be the write that fails.
- `BACKUP_KEEP`, which existed but was undocumented.

`docs/DEPLOY-LINUX.md` is a worked Linux deployment for both paths, with
configuration, backups and restores, upgrades, monitoring, the reverse
proxy, and a section on what to tell a security reviewer. `docs/DEPLOY.md`
is renamed `docs/DEPLOY-WINDOWS.md` so neither is the implied default.
`docs/README.md` is an index that routes a reader by who they are.

`deploy/` holds configuration to copy rather than prose to retype: nginx and
Caddy server blocks, a systemd unit with `ProtectSystem=strict` and a single
`ReadWritePaths`, and a backup service with a timer. The two proxy settings
people get wrong are marked in place. Buffering is the worse one: with it on,
the live attendance list arrives in a batch minutes late or never, so the
instructor watches a projector page that looks frozen while students check
in successfully and nothing in any log says why.

A CI job builds the image and exercises it on real Docker, which is the only
thing that ever has: the machine this was written on has none. It checks
that no `.env`, database, credentials or git history is baked into any
layer; that an unconfigured container refuses to start and names the missing
setting; that it starts, migrates itself and serves over a read-only root;
that Docker's own `HEALTHCHECK` reaches healthy; that the backup lands on the
volume; that data survives a restart with no failure marker; and that
`docker compose config` parses.

### Changed: `HOST` defaults to the loopback

`.env.example` shipped `HOST=0.0.0.0` with a warning attached. Following the
quick start therefore published a fresh install, over plain HTTP with no
certificate, to anyone who could route to the machine. It is now
`127.0.0.1`, which is also correct in production behind a reverse proxy on
the same host. The container sets `0.0.0.0` itself, because a container has
to accept connections from outside its own network namespace, and the
compose file then publishes the port on the loopback only.

### Fixed: two README claims that the code contradicted

The quick start said there is no "new course" button anywhere in the
interface. There is one, and the dashboard links to it twice. It also said
`HOST` defaults to `0.0.0.0`, which was true of `.env.example` and not of
the code.

### Removed: standalone mode

Adsum identifies a student in exactly one way: D2L vouches for them through
an LTI launch. The second path is gone.

Standalone mode let a course run with no LMS. A student scanned, picked their
own name from the roster, and that phone was bound to that name for the term.
It was the weaker path and the interface said so, because at that first scan
a student could pick a classmate's name. The mitigations were real but all
after the fact: one active binding per student per course, the refused second
attempt recorded and shown to the instructor, and a release button. None of
them stop the first wrong claim. They only make it visible afterwards, and
only if the instructor is looking.

One identity path means no mode to choose, no weaker door to reason about,
and no trade to explain.

Standalone mode was added after `v1.0.0` was tagged and removed before the
next release, so no released version ever had it. Its changelog entries have
been removed rather than kept and contradicted.

Dropped: the `device_bindings` and `claim_conflicts` tables, the
`courses.mode` column, `app/devices.py`, the `/student/claim` route and its
template, the release and review routes, the mode choice on New Course and
Course Settings, and `scripts/test_standalone.py`.

Kept: the typed six-character code, which is not a second identity path. The
student is still identified by their D2L launch; typing the code only
replaces scanning the image, for a camera that will not focus. The geofence
and manual mark-present are unchanged, and so is instructor password login.

Migration `c3f7a2b54e18`. A course that was running standalone keeps every
record it had. Its students cannot check in until they open Adsum from D2L,
because that is now the only thing that can say who they are.

### Removed: the campus-IP check

A course could restrict check-ins to configured campus IP ranges. Measured
three ways, it failed in three directions, so it is removed rather than
fixed.

- **It blocked the whole class.** The check read the raw peer address instead
  of the trusted-proxy-aware helper the login throttle uses. Behind the
  reverse proxy the install guide recommends, every student arrives as the
  proxy, so with any range configured the check refused everyone.
- **It could not produce a true positive.** Campus WiFi reaches the residence
  halls. A student in their room passes and is indistinguishable from one in
  the third row.
- **It produced false negatives.** A student in the classroom on cellular
  data, which is the common case for a quick scan, was refused.

A control that cannot produce a true positive, cannot avoid a false positive,
and does produce false negatives is worse than nothing, because it invites an
instructor to rely on it.

`attendance_records.ip_address` and `ip_in_range` are dropped with it. They
recorded a network address against a named student on every check-in, and
behind a proxy that value was identical on every row: it identified nobody
while still reading as personal data. `docs/IT-REVIEW.md`, `SECURITY.md` and
the institutional packet said the IP was stored and now say it is not.

The geofence is unaffected. It measures distance from the room rather than
membership of a network, it stays optional and off by default, and any
student it refuses can be marked present by the instructor.

Migration `b8c4e1f29a07`.

### Added: connect a D2L account to an existing password account

An LTI launch for an email that already had a password account answered
"Account Already Exists. Sign in with your password instead." and stopped
there. Whoever installs Adsum is almost always also its first instructor, so
they have both a password account and a D2L identity with the same email,
and that message told them to stop using the primary path.

The refusal was correct and stays. Adopting an account on a matching email
would be unsafe: a launch is signed with one install-wide shared secret and
an LMS administrator can change a user's email address, so email alone would
make that secret a master key for the admin account.

Linking instead requires both identities to be proved. A signed, ten-minute
cookie proves a correctly signed launch happened for that D2L user, and the
session cookie proves possession of the password. Forging a launch is not
enough, because an attacker would also need the victim's session cookie, at
which point they are already signed in as them.

The launch cannot read the session cookie, because it is a cross-site POST
and the cookie is `SameSite=Lax`. It can set one, so the D2L identity is
handed back in a cookie and the linking happens on a same-site request,
which does carry the session.

Refused, each with its own message: an expired request, a session signed in
as a different account than the launch named, and a D2L identity already held
by another account. Signing in while a link is pending returns to finish it.

`scripts/test_security.py` gains the property the original guard existed for
and which nothing had been testing: a launch alone never issues a session
cookie.

### Changed: `templates` moved out of `app/main.py`

Route modules imported `templates` from `app/main.py`, which also imports the
routers, so the cycle worked only by luck of ordering. Adding one import to
`app/routes/lti.py` made `import app.routes.lti` fail outright with
`ImportError: cannot import name 'router' from partially initialized module`.
The Jinja environment and the timezone helpers now live in
`app/templating.py`, which imports nothing from `app.main`, and every route
module can be imported on its own.

### Added: the classlist import is a reconciliation, not an append

The import counted new and updated students and never noticed anybody who had
**disappeared** from the classlist. A student who dropped stayed enrolled
forever, kept accruing absences, stayed in every denominator, and kept
appearing in the D2L gradebook export. The whole record of an import was a
transient banner reading "32 new, 4 updated".

- **Drops are detected and named.** A student on the roster who is missing
  from a newly imported classlist is marked dropped (`dropped_at` on
  `course_enrollments`). They are **marked, never deleted and never
  unenrolled**, because their attendance is real history and the D2L
  gradebook may still refer to them. The same rule the app already follows
  for instructor accounts. A student who reappears in a later import is
  un-dropped and reported as a re-add, with their earlier attendance intact.
- **A wrong file cannot drop the class.** Importing a filtered export,
  another section's classlist or last term's file is the realistic accident,
  and it used to be indistinguishable from a mass drop. The import now
  **refuses and changes nothing** (not the drops and not the adds) when a
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
  analytics enrolled count, both CSV exports, and the D2L gradebook export,
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
  **cannot** ask D2L who has added or dropped, because an LTI 1.1 launch
  carries no roster and the Valence enrollments API needs an OAuth application
  registered by a Brightspace administrator. So the warning says to
  re-download the classlist, never that D2L was checked.
- An LTI launch by a student previously marked dropped un-drops them: D2L
  granting them access to the course is fresher evidence than a classlist
  file from last week, and otherwise a re-added student could not check in
  until the instructor remembered to import again.
- The "not listed in D2L" flag now explains both of its meanings: a new add
  that has not been re-imported yet, or somebody who should not be there.
- Migration `c3b7f0d41a92` (additive, reversible): `dropped_at` on
  `course_enrollments`, `roster_last_imported_at` and
  `roster_last_import_summary` on `courses`. NULL everywhere for existing
  rows, which is the honest translation: every existing enrollment is still
  on the classlist as far as the old additive import could tell, and no
  course has an import the app ever observed. Pinned by
  `scripts/test_roster_sync.py`, which runs in CI.

### Fixed: a present student could be recorded absent

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
  nothing: any accented name saved by Excel, or an `.xlsx` picked by mistake.
  The roster then stayed empty, which means nobody in that class could check in
  at all. Uploads are now decoded defensively, size-capped, and explained
  instead of crashing.
- **Double-clicking Present or Excused returned 500** from an unhandled unique
  violation, despite the record having been written.

### Fixed: privacy

- **The projected session page showed every student's name and ID number**, and
  the mark-present dropdown listed exactly who was absent, to the whole room.
  Names and that dropdown are now off by default while a session is live, with
  a large count in their place; ID numbers are gone from the live view entirely
  and remain only on the after-class review.

### Added

- **A "Project this" mode** and a much larger QR code (605px at 1080p instead
  of 300px, upscaled with hard module edges rather than blurred), plus the
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
