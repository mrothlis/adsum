# Adsum

[![CI](https://github.com/mrothlis/adsum/actions/workflows/ci.yml/badge.svg)](https://github.com/mrothlis/adsum/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/github/v/release/mrothlis/adsum)](https://github.com/mrothlis/adsum/releases)

**Classroom attendance in about four seconds, written straight into the D2L
Brightspace gradebook.**

*Adsum* is Latin for "I am present" — the answer given at roll call.

You start a session and project a QR code that changes every twenty seconds.
Students scan it with their phones and are marked present. When you want the
grades in D2L, you export one CSV and import it. That's the whole product.

Built by an instructor for his own classes, and released under the MIT licence
in case it is useful elsewhere.

---

> ### Read this first: D2L is retiring LTI 1.1
>
> *Notice written November 2026. Check the sources below for the current state.*
>
> Adsum connects to Brightspace using **LTI 1.1**, the legacy course-level
> External Learning Tool flow. That flow is the only reason an instructor can
> set this up without an IT ticket. D2L has announced that it is going away.
>
> | When | What happens |
> |---|---|
> | Sept 2026 | D2L announced its intent to end support for LTI 1.1. |
> | Oct 2026 | Formal customer notification. |
> | **Feb 2027** (release 20.27.2) | Customers can **no longer configure or deploy new LTI 1.1 integrations** through the Brightspace UI, and the Valence API for creating legacy LTI tool providers is removed. |
> | Mar 2027 | Institutions may opt in to disabling LTI 1.1 early. |
> | **Late 2027** | **LTI 1.1 launches stop working entirely.** |
>
> What that means if you are considering Adsum: installing it today works, and
> a tool link created before the February 2027 release keeps launching until
> late 2027. After that release you cannot create a *new* LTI 1.1 link in
> Brightspace, so a new course cannot be connected the way this README
> describes, and your Brightspace administrator may have switched 1.1 off
> earlier than that.
>
> LTI 1.3 support is therefore the project's priority. Being honest about
> where it stands: nothing in Adsum speaks 1.3 today, there is no dated
> roadmap, and this is the known next step rather than work in progress. Note
> also that 1.3 registration in Brightspace is an **organisation-level
> administrator** workflow, so "no IT ticket required" has a limited life
> whatever this project does. If you are adopting Adsum now, read
> [docs/BRIGHTSPACE-ADMIN.md](docs/BRIGHTSPACE-ADMIN.md) and talk to your
> Brightspace administrator early.
>
> **What you can rely on regardless:
> [standalone mode](#running-adsum-without-an-lms-standalone-mode).** A course
> can be created in Adsum and run with no LMS at all, with students claiming
> their own name once on their own phone. It needs no LTI, no registration and
> no administrator, so it is unaffected by any of the dates above. The honest
> caveat is in that section: identity from an LMS is stronger than identity a
> student claims for themselves.
>
> D2L's own pages disagree about the final cut-off: the KB article says
> September 2027, the High Impact Changes Timeline says release 20.27.08, and
> the press release says October 2027. Treat it as "late 2027" and confirm
> with your administrator.
>
> Sources: [LTI 1.1 deprecation plan][d2l-dep1],
> [D2L press release][d2l-dep2],
> [High Impact Changes Timeline][d2l-dep3],
> [LTI migration][d2l-dep4],
> [1EdTech deprecation schedule][d2l-dep5].

[d2l-dep1]: https://community.d2l.com/brightspace/kb/articles/25994-lti-1-1-deprecation-plan
[d2l-dep2]: https://www.d2l.com/newsroom/d2l-announces-security-enhancements-with-retirement-of-lti-1-1-support/
[d2l-dep3]: https://community.d2l.com/brightspace/kb/articles/1047-high-impact-changes-timeline
[d2l-dep4]: https://community.d2l.com/brightspace/kb/articles/23664-lti-migration
[d2l-dep5]: https://www.1edtech.org/lti-security-announcement-and-deprecation-schedule

---

## Contents

- [Why it exists](#why-it-exists)
- [Screenshots](#screenshots)
- [How it works](#how-it-works)
- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Running without an LMS](#running-adsum-without-an-lms-standalone-mode)
- [Connecting D2L](#connecting-d2l-lti-11)
- [Day-to-day use](#day-to-day-use)
- [Getting attendance into the gradebook](#getting-attendance-into-the-gradebook)
- [Attendance policy](#attendance-policy)
- [Checking the arithmetic by hand](#checking-the-arithmetic-by-hand)
- [Why D2L holds the record](#why-d2l-holds-the-record)
- [Configuration](#configuration)
- [Roles and multi-instructor use](#roles-and-multi-instructor-use)
- [Security](#security)
- [Privacy and FERPA](#privacy-and-ferpa)
- [Backups and recovery](#backups-and-recovery)
- [Tests](#tests)
- [Deployment](#deployment)
- [Limitations](#limitations)
- [FAQ](#faq)
- [Contributing](#contributing)

---

## Why it exists

D2L Brightspace has a built-in Attendance tool. There is **no API for writing
attendance records** into it and no CSV import, so attendance has to be
entered by hand, one student at a time, every single class. A D2L staff member
[confirmed this again in 2026][d2l-api]. For a 40-person class that is a few
minutes of clicking after every meeting, and nobody keeps it up.

To be precise, because "it has no API" is an overclaim: attendance data can be
*read* out of Brightspace through Data Hub and the Data Export API. It is the
writing side that does not exist, which is the side that matters when you are
trying to avoid typing.

The gradebook, unlike the attendance tool, *is* machine-writable. So Adsum
captures attendance in seconds and writes it there instead.

[d2l-api]: https://community.d2l.com/brightspace/discussion/9098/attendance-and-grades-api

## Screenshots

**Live session.** Project this. The list fills in as students scan, without
refreshing. Anyone whose phone fails is marked present with one click.

![Live session with rotating QR code and live attendance list](docs/images/session.png)

**Course.** Roster, session history, attendance percentages, and the export
that feeds the gradebook.

![Course page showing roster and session history](docs/images/course.png)

**Analytics.** Attendance trend across the term.

![Analytics page with attendance trend chart](docs/images/analytics.png)

*All screenshots use the built-in demo data. No real students appear.*

## How it works

```
  Instructor                    Student's phone                D2L
  ----------                    ---------------                ---
  Start Session
  QR code on screen  --scan-->  check-in page
    (rotates 20s)               identified by LTI
                                tap Check In
  live list updates  <----------'
  End Session
  Export CSV         -------------------------------->  Grades -> Import
                                                          Attendance: 93
                                                          Attendance Detail:
                                                          09/01:P 09/03:A ...
```

The QR code encodes a token signed with HMAC-SHA256 over the session id and a
rotating time slot. A token is valid for its own slot and the one before it,
so it lives twenty to forty seconds. A screenshot sent to a friend outside the
room is useless by the time it arrives.

Students are identified by LTI, not by typing a name. They launch the tool
once from inside your D2L course; after that their phone is known.

**No LMS?** A course can be created in Adsum and run in
[standalone mode](#running-adsum-without-an-lms-standalone-mode), where a
student claims their own name on first scan and that phone is theirs for the
term. Same QR, same exports; weaker identity, and that section says exactly
how much weaker.

## Features

**Taking attendance**
- Rotating QR check-in, signed and short-lived
- Works with D2L, or with no LMS at all: per-course **standalone mode**, where
  a student claims their name once and that phone is theirs for the term
- Live roster over server-sent events — no refreshing
- Sessions end when *you* end them; nothing closes on a timer
- Mark present, unmark, or excuse anyone, including on a session that already
  ended
- Optional per-course geofence with a map picker, off by default
- Optional campus-IP restriction

**Grading**
- Per-course attendance policy: forgiven absences, late threshold, and whether
  late counts as present
- Excused absences leave the denominator entirely
- One numeric gradebook column plus one Text column holding every date
- Early alerts for students below 75% or with three consecutive absences

**Operating it**
- `/healthz` for uptime monitoring
- Online SQLite backups that are safe to run while serving
- Alembic migrations
- Roster import from a D2L class list export, which reconciles adds and drops
  and tells you by name what changed
- Multi-instructor, with real isolation between courses

## Requirements

| | |
|---|---|
| Python | 3.11 or newer |
| Database | SQLite. PostgreSQL is **not** supported today, see [Limitations](#limitations) |
| D2L | A course you can add an External Learning Tool link to, on a Brightspace site where LTI 1.1 is still enabled — **or nothing at all**, if you run courses in [standalone mode](#running-adsum-without-an-lms-standalone-mode) |
| Network | A public HTTPS URL students' phones can reach |

No container runtime, no message queue, no Redis. It is one Python process and
a file.

## Quick start

```bash
git clone https://github.com/mrothlis/adsum.git
cd adsum
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                             # Windows: copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste as SECRET_KEY
# Edit .env: SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, INSTITUTION_NAME

python run.py
```

Open `http://127.0.0.1:8000` and sign in with the admin credentials from
`.env`. The admin account is created automatically on an empty database.

`HOST` defaults to `0.0.0.0`, which means the server is reachable from your
whole network over plain HTTP, not just from `127.0.0.1`. For a local try-out
on a laptop on a shared or campus network, set `HOST=127.0.0.1` in `.env`.

**The dashboard will be empty, and that is expected.** There is no "new
course" button anywhere in the interface: courses, instructor accounts and
enrolments are created by the first LTI launch from D2L. Until you have
connected D2L there is nothing to look at and nothing to take attendance for.

So do this instead, on a throwaway database, to see the app working before you
touch D2L:

```bash
# in .env: SEED_DEMO=true  and  DATABASE_URL=sqlite+aiosqlite:///./demo.db
python run.py
```

That creates a demo course with fictional students, so you can start a
session, scan the QR code with your own phone, export the gradebook CSV, and
run the test suite. Then delete `demo.db`, set `SEED_DEMO=false`, point
`DATABASE_URL` back at `adsum.db` and do the real install.

The app refuses to start with a placeholder `SECRET_KEY` or an empty
`ADMIN_PASSWORD` unless `DEBUG=true`, so a half-configured install cannot
quietly end up in production.

**For anything beyond a local try-out, read [docs/INSTALL.md](docs/INSTALL.md).**
It covers hosting, HTTPS and reverse proxies, running as a service, backups,
upgrades, and a troubleshooting table.

## Running Adsum without an LMS (standalone mode)

You do not need D2L, or any LMS, to use Adsum. A course can be created in the
app — **Dashboard → New course** — and set to **standalone**, where students
identify themselves by claiming their own name on their own phone, once.

This is also what keeps the tool usable as D2L retires LTI 1.1. Read the dated
notice at the top of this file: **no new LTI 1.1 integrations can be created in
Brightspace after the February 2027 release, and 1.1 launches stop working in
late 2027.** Standalone mode has no dependency on any of that.

The mode is **per course**, and existing courses keep the D2L path. Running one
standalone course changes nothing about a colleague's LMS-connected one.

### How it works

1. You import your class roster (any CSV with first name, last name and a
   student ID column) on the course page.
2. You start a session and project the QR code, exactly as before.
3. The first time a student scans, they are asked **who they are** and pick
   their own name from the roster. That binds their phone to that name and
   checks them in, in one go.
4. Every scan after that checks them in silently. They are never asked again.

Everything else is unchanged: the rotating QR code, the typed six-character
fallback, the per-course attendance policy, the geofence, the campus-IP
restriction, manual marking, and the exports. The gradebook export becomes
"download a CSV for whatever gradebook you use" instead of the Brightspace
import file.

### The trade, stated plainly

**The LMS path gives stronger identity, and this one does not match it.**

The rotating QR code proves *presence* either way: the code changes every
twenty seconds, so a student has to be looking at the screen in the room. What
standalone mode cannot prove on its own is *identity*. At the moment of that
very first scan, nothing stops a student picking a classmate's name instead of
their own. With D2L, that is impossible without being signed into D2L as that
classmate — the LMS has already proved who they are, and Adsum simply believes
it.

What standalone mode does about it:

- **One name, one phone.** A name can have only one active device binding, and
  that is enforced by a unique index in the database, not by a check in the
  code that two simultaneous scans could slip past. The second phone is
  **refused** — never silently allowed, and never silently moved onto the new
  device.
- **The instructor is told.** Every refused claim is shown at the top of the
  course page, with the student's name and the time, until you acknowledge it.
  That is the main defence: the student whose name was taken is refused, comes
  to you, and you can fix it while the class is still in the room.
- **You can release a binding.** New phone, lost phone, or wrong name tapped:
  release it on the course page and the name can be claimed again.
  **Releasing deletes no attendance** — attendance belongs to the student, not
  to the phone.
- **The roster is not published.** The "who are you?" list shows names only.
  Student ID numbers and usernames never appear on it, and only students on the
  current roster are selectable — somebody your last roster import marked as
  dropped cannot be claimed.
- **Claiming is only possible from inside a live check-in**, with a QR token
  that passes the same verification a check-in does. Nobody can bind themselves
  to a name from outside the room, and a screenshot of an old code is useless.
- **Claim attempts are rate limited**, counting failures only and clearing on
  success, so a student who taps the wrong thing is not locked out in front of
  the class.

**Single sign-on (Google or Microsoft) would remove claim-time impersonation
entirely**, because the identity would again come from outside Adsum. It is a
known future option. **It does not exist today — nothing in Adsum speaks
OAuth or OIDC**, and this paragraph is a statement of direction, not of
anything you can switch on.

Choose standalone if you have no LMS, or if Brightspace is not available to
you. Choose the D2L path if you have it.

## Connecting D2L (LTI 1.1)

An instructor who is also the Adsum administrator can do this alone, with no
IT ticket. Read the LTI 1.1 retirement notice at the top of this file first:
this flow cannot be used for new links in Brightspace after the February 2027
release.

The order matters. A fresh install has **no courses**, so there is no course
page to get credentials from. They live at `/admin`.

1. **In Adsum, sign in as the bootstrap admin** (`ADMIN_EMAIL` /
   `ADMIN_PASSWORD` from `.env`) and go to **`/admin`**.
2. On the **D2L Brightspace connection** card, set your **D2L host** (for
   example `yourschool.brightspace.com`) and save. Then copy three things from
   that same card:
   - the **launch URL**, shown as `https://your-host/lti/launch`
   - the **LTI consumer key** (`adsum` by default)
   - the **LTI consumer secret**, generated automatically on the first start
3. **In D2L**: your course → **Content** → a module → **Add Existing
   Activities** → **External Learning Tool** → create a new link.
   - **Title**: `Adsum`
   - **URL**: the launch URL from step 2
   - **Key** and **Secret**: the two values from step 2
4. Tick **Open as External Resource**.
5. Under **Security Settings**, enable **Send system username**, **Send user
   email** and **Send user name**. All three are required. Without them the
   launch arrives unidentified and nobody can be matched to the roster.
6. **Click the link once, in D2L, as yourself.** This is the step that creates
   things: the launch creates the course in Adsum, creates or links your
   instructor account, and enrols you on it.
7. **Only now does Course → Settings exist.** Open it to set the geofence, the
   attendance policy and campus IP ranges. The roster import, which takes a
   D2L Classlist export, is on the course page itself.

**If you are not the Adsum administrator**, the course settings page
deliberately does not show the secret. Ask whoever administers the install for
the launch URL, key and secret, or better, ask them to have IT register Adsum
once at organisation level so you never handle the secret at all. There is a
one-page briefing for IT at
[docs/BRIGHTSPACE-ADMIN.md](docs/BRIGHTSPACE-ADMIN.md).

### Keeping the roster up to date

**Adsum cannot poll D2L for your class list, and does not pretend to.** An
LTI 1.1 launch carries the course and the person launching it, and no roster
at all; the Valence `enrollments` API that could return one needs an OAuth
application registered by a Brightspace *administrator*, which is exactly the
IT ticket this project exists to avoid. So the roster is reconciled at one
moment only: when you export the Classlist from D2L and import it on the
course page.

What that import does:

- students on the file who are not in Adsum are **added**;
- students in Adsum who are **not on the file are marked as dropped** — never
  deleted, because their attendance is real history and your D2L gradebook may
  still refer to them. A dropped student stops counting towards attendance and
  is left out of the gradebook export (D2L would reject the row as an
  unrecognised user), and appears in a **Dropped from the D2L classlist**
  section on the course page with the date and the attendance they had;
- someone who reappears in a later import is put back automatically, with
  their earlier attendance;
- changed names, usernames and D2L IDs are reported, because the gradebook
  export matches on the username and a silent change stops a grade landing.

You get a summary naming everybody who was added, dropped or changed, and it
stays on the page until you dismiss it.

**If the file would remove more than half your roster, or contains none of
your current students, the import refuses and changes nothing** — including
the adds. That case is almost always a filtered export, a different section,
or last term's file rather than a real mass drop. The page says how many would
go and who, and lets you confirm deliberately if it really is correct.

Because nothing happens until you import, the course page and your dashboard
show when you last did, and warn you after about a week. Re-download the
Classlist whenever you want to know who has added or dropped — typically after
the add/drop deadline and then every week or two.

> **Rotating the secret?** Change it at `/admin` and in D2L at the same time.
> They must match, or every launch returns 403, for every instructor on the
> install.

## Day-to-day use

1. **Start Session** on the course page and project the QR code.
2. Students scan. The list fills in live.
3. **End Session** when you're done.
4. Phone trouble? **Mark Present** — during or after the session.
5. Documented absence? **Excuse** — it leaves that student's denominator.

A student's very first check-in takes two scans: they scan, get sent to D2L to
sign in, and scan again. After that it's a single scan all term. Telling the
class to open the Adsum link in D2L once in week one avoids it entirely.

## Getting attendance into the gradebook

1. **Export for D2L Gradebook** on the course page.
2. In D2L: **Grades → Import**, choose the file, tick *Create new grade item
   when an unrecognized item is referenced*.
3. **Set Maximum Points to 100** for the *Attendance* item. D2L defaults it to
   **10**, which displays everyone at 1000%. The *Attendance Detail* text item
   needs no properties.
4. Continue through the preview, then Import.

You get two columns:

| Column | Type | Contents |
|---|---|---|
| `Attendance` | Numeric 0–100 | The policy-adjusted percentage |
| `Attendance Detail` | Text | `5/8 present, 3 unexcused (3 allowed) \| 09/01:P 09/03:A ...` |

Re-importing **overwrites** both columns with current running totals, so
weekly imports need no reconciliation. It also overwrites manual edits made in
D2L — correct attendance in Adsum and re-export instead.

Tick *Create new grade item* on the **first** import only.

> **Why CSV and not automatic passback?** Both automated routes need somebody
> in your IT department to register something first. LTI 1.3 Advantage (AGS)
> needs Adsum registered as an LTI Advantage tool at organisation level. D2L's
> Valence Grades API can also write grades (scope
> `grades:gradevalues:write`), but it needs an API application registered and
> approved. Adsum uses a CSV so that an individual instructor can adopt it
> unaided. That trade-off is being reconsidered, because LTI 1.1 is being
> retired and an administrator will have to be involved regardless: see the
> notice at the top of this file.

## Attendance policy

**Each instructor sets their own, per course**, under **Course → Settings →
Attendance Policy**. None of it is an install setting: how many absences a
student is allowed is a syllabus decision, and the person who deploys the
server has no business making it for a department. Two instructors sharing
one installation can run entirely different policies.

| Setting | Meaning | Default for a new course |
|---|---|---|
| **Unexcused absences forgiven** | How many a student may have before the percentage drops | 3 |
| **A late arrival is worth** | Share of a class a late arrival earns, 0–100% | 100% |
| **Marked late after** | Minutes from session start before a *scanned* check-in records as late | 10 |

Leave a box empty to use the default. Changing a policy re-scores that
course's existing attendance immediately — the percentage is always computed
from the records, never stored.

**Late is mostly a manual decision, not a timer.** In practice you project the
QR code at the start of class and take it down, so a student who arrives at
ten past cannot scan at all — they talk to you, and you mark them from the
session page with **Present**, **Late** or **Excused**. That click is where
the real judgement happens, which is why late credit is a percentage rather
than an on/off switch: you can decide a late arrival is worth half a class.
The "marked late after" threshold only matters if you leave the code up.

Excused absences are always removed from the denominator — a documented
absence neither helps nor hurts. A course with no sessions yet scores 100, so
nobody is punished in week one. Percentages round half **up**, so 62.5%
becomes 63.

**How much attendance is worth is set in D2L**, not here. Adsum emits 0–100;
your gradebook's Maximum Points and category weighting decide what that means.
Duplicating weighting here would create two sources of truth that silently
disagree.

## Checking the arithmetic by hand

You should be able to check an attendance grade yourself, without trusting the
software. Instructure's Canvas Roll Call miscalculated attendance grades for
fourteen months before anyone caught it
([their own write-up][roll-call]), and the reason it went unnoticed is that
nobody could see the inputs.

Adsum ships the whole per-date ledger to D2L in the `Attendance Detail`
column, and it never stores a percentage: every percentage you see anywhere in
the app is recomputed from the records on each read. So the inputs are in
front of you, in the gradebook, and you can redo the sum on paper.

**The recipe.** From that student's `Attendance Detail` cell:

1. Count the class dates listed. That is **N**.
2. Count the excused entries, **E**. Excused classes leave the calculation
   entirely, so the denominator is **D = N - E**.
3. Count the present entries, **P**, and the late entries, **L**.
4. Work out the credit earned:
   **credited = P + L x (late credit percent / 100)**.
   Late credit is per course, under Course → Settings → Attendance Policy, and
   defaults to 100%, in which case a late arrival is simply worth a full
   class.
5. Work out what was lost: **lost = D - credited**.
6. Forgive some of it: **forgiven = whichever is smaller, `lost` or the
   forgiven-absence allowance** (default 3).
7. **percentage = (credited + forgiven) / D x 100**, rounded half **up**, and
   capped at 100.

If D is zero, because every class was excused or none has happened yet, the
score is 100. Nobody is punished for an empty record.

**Worked example.** 20 classes held, 2 of them excused, so D = 18. The student
was present 13 times, late twice, and the course sets a late arrival at 50%.

```
credited = 13 + 2 x 0.50        = 14.0
lost     = 18 - 14.0            =  4.0
forgiven = min(4.0, 3)          =  3.0
percent  = (14.0 + 3.0) / 18    =  0.9444  ->  94.44  ->  94
```

Two details that trip people up. The allowance is applied to **lost credit**,
not to a count of absences, so two late arrivals at 50% consume one class of
the allowance between them. And rounding is half **up**, not Python's default
banker's rounding, so 62.5 becomes 63 and not 62: no syllabus has ever
described banker's rounding, so the code deliberately does not use it.

The displayed summary `5/8 present` counts bodies in the room, present plus
late, regardless of credit. A student who arrived late was still there, and
the percentage is where the credit is docked.

[roll-call]: https://community.instructure.com/en/discussion/664791/transparency-update-roll-call-grade-calculation-incident

## Why D2L holds the record

Adsum is a capture tool, not a system of record. That is a deliberate design
constraint, and three things enforce it:

1. **The whole history ships to D2L.** The `Attendance Detail` text column
   carries every class date and status in one cell, so the gradebook holds the
   complete record — not just a summary.
2. **That column is parseable.** A D2L grade export is therefore a full
   backup. **Restore from D2L** on the course page rebuilds Adsum's sessions
   and records from it. Restoring is additive and idempotent.
3. **Export refuses to run on a course with no sessions** (HTTP 409). If the
   app were wiped mid-term, an empty export would otherwise overwrite D2L's
   copy with nothing. That cannot happen.

If Adsum disappeared tomorrow, your attendance record would still be in D2L,
readable by a human and parseable by a machine.

## Configuration

Configuration lives in `.env`, and `.env.example` documents every setting the
app reads. The ones that matter:

| Key | Purpose |
|---|---|
| `SECRET_KEY` | **Required.** Signs session cookies *and* QR tokens. Must be random and unique per install. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | **Required.** Bootstrap admin, created on an empty database. |
| `BASE_URL` | Public URL embedded in every QR code. Must be the external HTTPS address. |
| `HOST` | Interface to bind. Defaults to `0.0.0.0`, which is **every interface**: on a laptop that exposes the app to the whole network over plain HTTP. Use `127.0.0.1` for a local try-out, or when a reverse proxy on the same machine is the only thing that should reach it. |
| `PORT` | Port to listen on (8000). |
| `INSTITUTION_NAME`, `D2L_HOST`, `LTI_CONSUMER_KEY` | Institution identity. |
| `QR_ROTATION_SECONDS` | Rotation interval (20). A token lives 20–40s. |
| `DISPLAY_TIMEZONE` | Display zone. Timestamps are stored as UTC. |
| `DEFAULT_GEOFENCE_LAT/LON/RADIUS_M` | Starting position of the map picker. |
| `DATABASE_URL` | SQLite. PostgreSQL is not supported today, see [Limitations](#limitations). |
| `SEED_DEMO` | `true` creates a demo course. Development only. |
| `DEBUG` | Leave `false` outside local development. |

Never commit `.env`.

**A real operating-system environment variable wins over `.env`.** The app
loads `.env` without overriding what is already set, so if `DATABASE_URL` or
`SECRET_KEY` is exported in the shell, a service definition or a scheduled
task, that value is used and the line in `.env` is ignored. This is the first
thing to check when a setting "will not take effect".

The LTI **consumer secret** is not in `.env` at all. It lives in the database,
is generated on first start, and is shown and changed at `/admin`.

**Attendance policy is not in here.** Forgiven absences, the late threshold
and whether late counts as present are set per course by each instructor —
see [Attendance policy](#attendance-policy). Whoever installs the server does
not decide how many absences a colleague's students are allowed.

## Roles and multi-instructor use

Two scopes, two places, and the split is deliberate.

**Course settings** — `Course → Settings`, owned by the instructor who teaches
it: name, D2L org unit, deep link, attendance policy, geofence, campus IPs.
Everything here stops at the course boundary.

**Administration** — `/admin`, admin-only: instructor accounts, the shared D2L
host and LTI credentials, and a read-only install health panel. These are
install-wide, which is exactly why they are *not* on a course page — a change
that breaks every instructor's launches should not happen at a URL that says
*course 2*.

| | Instructor | Admin |
|---|---|---|
| Their own courses and attendance | yes | yes |
| Another instructor's attendance | **no** | **no** |
| Add, deactivate, reset accounts | no | yes |
| Shared D2L host and LTI credentials | no | yes |

**An admin cannot read any course's attendance.** That is a design decision,
not an oversight. Attendance records are student education records; the
isolation suite guarantees instructor A cannot reach instructor B's students,
and that guarantee would be worth nothing if it ended at "unless you are an
admin". Someone keeping the server running does not need to read attendance to
do it. An admin who needs a course's data has to be added to that course as an
instructor, visibly.

So `/admin` has no cross-course browsing, no global attendance report and no
impersonation — and `scripts/test_admin_page.py` fails the build if one
appears. Deployment values (`SECRET_KEY`, `DATABASE_URL`, `DEBUG`) are shown
read-only; editing those from a web form is how an installation ends up unable
to start.

Accounts are **deactivated, never deleted** — grades already imported into D2L
refer to their courses. There is no self-registration, and an admin cannot
remove their own rights or lock themselves out.

## Security

- **Session cookies and QR tokens** are signed with `SECRET_KEY`
  (`itsdangerous`, HMAC-SHA256). Passwords use PBKDF2-SHA256.
- **Login throttling**: five failures per IP, then a ten-minute lockout.
- **LTI 1.1 launches** are verified as OAuth 1.0a HMAC-SHA1 signatures; a bad
  signature is rejected.
- **The app refuses to start** outside `DEBUG` with a placeholder or short
  `SECRET_KEY`, or an empty `ADMIN_PASSWORD`.
- **Student pages load no third-party resources.** Leaflet and Chart.js appear
  only on instructor screens.

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Privacy and FERPA

Attendance records are student education records, protected by FERPA in the US
and equivalent rules elsewhere. Whoever deploys this is responsible for
hosting it accordingly.

- **Location is only collected on a geofenced course.** The geofence is **off
  by default** and switched on per course. When it is off, no location is
  requested and none is stored.
- **Raw coordinates are never stored.** When the geofence is on, the browser
  reports a position, the server works out the distance from the classroom,
  and only that distance in metres plus an in/out flag are written to the
  attendance row. The latitude and longitude are discarded.
- Students see a notice before any location check.
- The check-in IP address, and whether it fell inside a configured campus
  range, are also stored.
- No analytics, no tracking, no third-party data sharing.

An audit found that earlier versions did store the latitude and longitude,
contradicting this section. That is fixed, and the migration that ships with
the fix erases any coordinates a previous version captured, rather than
leaving them in the database contradicting the notice students were shown.
`scripts/test_security.py` asserts that no attendance row holds a coordinate.

There is a one-page briefing for an IT or privacy review at
[docs/BRIGHTSPACE-ADMIN.md](docs/BRIGHTSPACE-ADMIN.md).

## Backups and recovery

Two independent layers, and you want both.

**The database.** `backup_db.py` uses SQLite's online backup API, so it is safe
while the app is serving. It keeps the last 14 copies in `backups/` and reads
the database location from `DATABASE_URL`.

```bash
python backup_db.py              # run nightly from cron or Task Scheduler
```

**D2L itself.** Because the `Attendance Detail` column is round-trippable, a
D2L grade export is a complete backup. Use **Restore from D2L** on the course
page. This is verified end to end: the test suite wipes a course and rebuilds
it from an export alone.

## Tests

Six scripts. The linter and the live-instance scripts need the development
dependencies as well as the runtime ones:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

**No server needed:**

```bash
python scripts/test_scoring.py           # grade arithmetic
python scripts/verify_install.py         # clean-install check
ruff check app scripts run.py backup_db.py
```

**Need a running instance, and a course with at least one enrolled student:**

```bash
python scripts/smoke_test.py             # end-to-end happy path
python scripts/test_isolation.py         # one instructor cannot reach another's data
python scripts/test_admin_separation.py  # non-admins cannot change shared config
python scripts/test_admin_page.py        # /admin stays inside its boundary
```

`verify_install.py` exports the committed tree with `git archive`, configures
it from `.env.example` alone, and starts it, proving a fresh clone works. It
also fails if an `.env` file, a database, a log or a credentials file is ever
committed, and it checks the committed tree against a list of identifiers
belonging to the original author. That list is a fixed set of patterns, not a
general-purpose secret scanner, so it will not catch an identifier nobody has
added to it.

The four live-instance scripts read `BASE_URL`, `ADMIN_EMAIL` and
`ADMIN_PASSWORD` from the environment or `.env`, and pick their target course
automatically (`TEST_COURSE_ID` overrides). They create and clean up their own
throwaway records. On a fresh install there is no course for them to use until
D2L is connected, so run them against a `SEED_DEMO=true` throwaway database,
which is what CI does. `bash scripts/ci_local.sh` reproduces the CI
integration job exactly: a clean export of `HEAD`, a throwaway database, a
spare port, no `.env` in scope.

All of them run on every push via
[GitHub Actions](.github/workflows/ci.yml), across Python 3.11, 3.12 and 3.13.

## Deployment

[docs/INSTALL.md](docs/INSTALL.md) is the full guide: hosting options, HTTPS
via reverse proxy or tunnel, running under systemd or Task Scheduler, backups,
upgrades, verification and troubleshooting.
[docs/DEPLOY.md](docs/DEPLOY.md) is a worked Windows deployment, with Task
Scheduler, a nightly backup task and a tunnel.

Two proxy details are worth knowing, because both fail silently:

- **Forward `X-Forwarded-For`**, or the login throttle sees every request as
  one IP and a single person's failures lock out the whole class.
- **Disable buffering for server-sent events**, or the live attendance list
  never updates.

SQLite is fine at course or department scale, and it is the only database that
works today. There is no container image: it is a plain `uvicorn` process, so
run it under whatever your institution already uses.

## Limitations

Worth knowing before you adopt it.

- **LTI 1.1 is being retired by D2L.** New 1.1 links cannot be created in
  Brightspace after the February 2027 release, and 1.1 launches stop working
  in late 2027. Adsum does not support LTI 1.3 yet. See the notice at the top
  of this file for the timeline and sources. This is the biggest thing to know
  before adopting it.
- **Grades move by CSV export and import**, not automatic passback. Both
  automated routes, LTI Advantage (AGS) and the Valence Grades API, need an
  administrator to register the tool first, which the CSV avoids. When 1.3
  arrives that trade-off goes away, because an administrator will be involved
  anyway.
- **SQLite only.** The `.env.example` comment showing a PostgreSQL URL does
  not work today, and neither does the claim that PostgreSQL is supported.
  Three things are missing: `asyncpg` is not in `requirements.txt`;
  `alembic/env.py` builds a synchronous engine by stripping `+aiosqlite` from
  the URL, so a `postgresql+asyncpg://` URL reaches a sync engine and fails;
  and at least one migration compares a boolean to `0` in raw SQL, which
  PostgreSQL rejects. Fixing all three is a small job, but until someone does
  it and runs the test suite against PostgreSQL, treat it as unsupported.
- **The LTI consumer secret is shared by every course on an install.** Anyone
  holding it can forge a launch as any user, so a shared departmental install
  really wants an organisation-level registration by IT rather than the secret
  passed around.
- **A student's first check-in needs two scans** on the D2L path, because they
  must sign in to D2L in between and the first token expires while they do.
  Standalone mode has no such problem: there is no login round trip, so the
  first scan claims the name and records the attendance in one request.
- **Standalone identity is claimed, not proved.** A student's first scan could
  claim a classmate's name. One phone per name is enforced in the database,
  the clash is shown to the instructor, and a binding can be released — see
  [standalone mode](#running-adsum-without-an-lms-standalone-mode) for the
  full trade. Single sign-on would remove this and does not exist yet.
- **D2L truncates usernames and emails to 20 characters.** That truncated form
  is the real identity for both launches and gradebook imports. Don't "correct"
  it by hand.
- **Re-importing overwrites manual gradebook edits.** Correct attendance in
  Adsum, not in D2L.
- **Deleting a session changes everyone's percentage**, because it changes the
  denominator.
- **A standalone course has no second copy of its data.** With D2L the
  gradebook holds the full per-date ledger, so a grade export is a complete
  backup and `Restore from D2L` can rebuild the app from it. Standalone mode
  has no such mirror: the server's own backups are the only copy, so download
  the attendance CSV periodically.
- **No mobile app.** It is a web page; students need a working camera and a
  browser.

## FAQ

**Does this work with Canvas, Moodle or Blackboard?**
Not today. The LTI 1.1 launch is standard and would likely work, but the
gradebook export is written to D2L's import format. Porting it means a new
exporter, not a rewrite.

**What if a student has no phone, or a dead battery?**
Mark them present with one click, during or after the session.

**Can students check in from outside the room?**
The code changes every twenty seconds, so a forwarded screenshot is stale on
arrival. If you want a hard guarantee, enable the geofence or restrict to
campus IP ranges — both are per course.

**Does the session close automatically?**
No. It accepts check-ins until you end it. Someone arriving late is a
conversation, not a lockout.

**What happens if the app breaks mid-semester?**
Your record is in D2L. Reinstall, then use Restore from D2L with a grade
export.

**Can two instructors share one installation?**
Yes. Each sees only their own courses, and each sets their own attendance
policy. Shared D2L credentials are admin-only.

**Does it need to run on a university server?**
No. Any host with a public HTTPS address works, including a small VM or a
tunnel from a desktop. Institutional hosting is the right answer once more
than a couple of people depend on it.

## Contributing

Bug reports and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, the test suite, and the design
principles a change is weighed against. The main one: **D2L is the system of
record, and Adsum is the pipe.** Changes that make the app irreplaceable are
likely to be declined.

Release notes are in [CHANGELOG.md](CHANGELOG.md).

## Tech stack

Python 3.11+, FastAPI, SQLAlchemy (async), SQLite by default, Alembic for
migrations. Jinja2 templates with vanilla JavaScript — no build step. Leaflet
for the map picker, Chart.js for trends, both instructor-only.

## Licence

MIT — see [LICENSE](LICENSE) for the full text and copyright holder.
