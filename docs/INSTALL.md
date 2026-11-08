# Installing Adsum

A complete guide to running this on your own infrastructure.

This application is entirely self-contained. It does not call home, does not
depend on any server belonging to the original author, and no data leaves the
machine you install it on except the requests you make to your own D2L.

---

## 1. What you need

| Requirement | Notes |
| --- | --- |
| A machine to run it on | Any Linux, macOS or Windows host. A 1 vCPU / 1 GB VM is ample for a department. There is no Dockerfile and no compose file in this repository, so a container runtime is not a supported path today. |
| Python 3.11 or newer | |
| A public HTTPS address | Students scan a QR code with their phones; their browsers must reach the server. |
| A D2L Brightspace course | You need permission to add an External Learning Tool link, which instructors normally have, on a Brightspace site where LTI 1.1 is still enabled. See section 6. |

You do **not** need: a database server (SQLite is the default and the only
supported one), an IT ticket for LTI 1.1 today, or any paid service.

### Where it can run

- A small cloud VM (DigitalOcean, Linode, AWS Lightsail, Azure).
- A campus-hosted VM, which is the right answer for anything beyond a pilot.
- A department server or lab machine with a stable address.
- Your own workstation, for testing only. If the machine sleeps, class
  check-in stops.

---

## 2. Install

### Linux and macOS

```bash
git clone https://github.com/mrothlis/adsum.git
cd adsum

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env, see section 3.

python run.py
```

### Windows (PowerShell)

```powershell
git clone https://github.com/mrothlis/adsum.git
cd adsum

py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
notepad .env                       # see section 3

python run.py
```

If `Activate.ps1` is blocked, either run
`Set-ExecutionPolicy -Scope Process RemoteSigned` in that window, or skip
activation entirely and call the interpreter by path:
`.venv\Scripts\python.exe run.py`.

The app listens on port 8000. By default it binds `0.0.0.0`, every interface,
so it is reachable from your whole network over plain HTTP. Set
`HOST=127.0.0.1` in `.env` while you are still setting up.

To run the test suite or the linter later, also install the development
dependencies:

```bash
pip install -r requirements-dev.txt
```

### Generating a SECRET_KEY

Linux, macOS, and Windows `cmd.exe`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

PowerShell parses double quotes itself, so wrap the whole argument in single
quotes instead:

```powershell
python -c 'import secrets; print(secrets.token_hex(32))'
```

This signs session cookies **and** QR check-in tokens. If it is weak, shared
between installs, or committed to a repository, anyone can forge both. Generate
a fresh one per install.

---

## 3. Configuration

Settings live in `.env`. Nothing is hard-coded.

Two things are **not** in `.env`:

- **The LTI consumer secret.** It is generated on first start, kept in the
  database, and shown at `/admin`.
- **The attendance policy.** It is per course, set by each instructor.

And one thing to know before you debug a setting that "will not apply": **a
real operating-system environment variable overrides `.env`.** The app loads
`.env` without overriding what is already in the environment, so a value
exported in your shell, set in a systemd unit, or set on a Windows scheduled
task wins over the file.

### Required

```ini
SECRET_KEY=<64 random hex characters>
ADMIN_EMAIL=you@yourschool.edu
ADMIN_PASSWORD=<a strong password>
BASE_URL=https://attendance.yourschool.edu
```

`BASE_URL` is embedded in every QR code. If it is wrong, students' phones
cannot reach the check-in page. It must be the **public** address, including
`https://`, with no trailing slash.

### Institution identity

```ini
INSTITUTION_NAME=Your University
D2L_HOST=yourschool.brightspace.com
LTI_CONSUMER_KEY=adsum
```

`D2L_HOST` is your Brightspace hostname. Some institutions are hosted by a
consortium, so check the address you actually log in to, because it is not
always `<school>.brightspace.com`.

### Attendance policy: not your job

Forgiven absences, the late threshold and whether a late arrival counts as
present are set **per course by each instructor**, in the web interface under
Course → Settings → Attendance Policy. There is no server-wide setting for
them, deliberately: an administrator should not be deciding a colleague's
syllabus. New courses start at 3 forgiven absences, late after 10 minutes,
late counting as present.

### Display

```ini
DISPLAY_TIMEZONE=America/New_York
QR_ROTATION_SECONDS=20
```

Timestamps are stored in UTC and displayed in `DISPLAY_TIMEZONE`. On Windows
the `tzdata` package is required for this, and it is already in
`requirements.txt`.

### Leave these alone in production

```ini
DEBUG=false
SEED_DEMO=false
```

`SEED_DEMO=true` creates a demo course with fictional students. Useful for
trying the app out, wrong for real use. See section 5 for why you probably
want one deliberate run with it switched on, against a throwaway database.

### Network binding

```ini
HOST=0.0.0.0
PORT=8000
```

`HOST=0.0.0.0` means every interface. On a laptop or a shared network that
makes a local try-out reachable by anyone on the same LAN over plain HTTP. Set
`HOST=127.0.0.1` when you are evaluating it, and in production when a reverse
proxy on the same machine is the only thing that should reach the app
directly.

### Database

```ini
DATABASE_URL=sqlite+aiosqlite:///./adsum.db
```

SQLite is the only supported database. PostgreSQL does not work today:
`asyncpg` is not in `requirements.txt`, `alembic/env.py` cannot build a
synchronous engine from an async PostgreSQL URL, and at least one migration
uses boolean-as-integer SQL that PostgreSQL rejects. Do not point this at
PostgreSQL expecting it to work.

---

## 4. Making it reachable over HTTPS

Students' phones must reach the server, and it must be HTTPS. QR tokens and
session cookies travel in the request otherwise. Pick one:

### Campus DNS plus a reverse proxy (best for institutional hosting)

Ask for a DNS name pointing at your VM, then terminate TLS with Caddy or
nginx. Caddy obtains and renews certificates automatically:

```
attendance.yourschool.edu {
    reverse_proxy 127.0.0.1:8000
}
```

nginx equivalent, with certificates from Let's Encrypt via certbot:

```nginx
server {
    listen 443 ssl;
    server_name attendance.yourschool.edu;

    ssl_certificate     /etc/letsencrypt/live/attendance.yourschool.edu/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/attendance.yourschool.edu/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

`X-Forwarded-For` matters: the login throttle keys on it. Without it every
request appears to come from the proxy and one person's failed logins would
lock out everybody.

Server-sent events power the live attendance list. If you use nginx, disable
buffering for the stream or the instructor's screen will not update live:

```nginx
location /api/attendance/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

### A tunnel (fastest, good for a pilot)

Cloudflare Tunnel or Tailscale Funnel expose a local port over HTTPS with no
firewall changes and no public IP:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Fine for trialling. For anything a colleague depends on, use real hosting.

---

## 5. First run

1. Browse to your `BASE_URL`. You should see the sign-in page.
2. Sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. On an empty database that
   account is created on first start and is marked as an administrator.
3. Check `https://your-host/healthz` returns `{"status":"ok"}`. Point your
   uptime monitor at it.

### Your dashboard starts empty, and there are two ways to fill it

A fresh install has no courses, no students and no sessions. Pick whichever of
these describes you:

**You have D2L.** Do section 6. The first time an instructor launches the tool
from their D2L course, the course, the instructor account and the enrolment are
all created automatically by that launch. This gives the stronger identity: the
LMS proves who each student is, so nobody can check in as somebody else.

**You have no LMS.** Use **Dashboard → New course**, set the mode to
**standalone**, and skip section 6 entirely. See section 6b below. Nothing about
LTI, keys, secrets or Brightspace applies to you.

Both kinds of course can exist on the same install, and the choice is per
course.

### Recommended: one practice run with demo data first

Before you wire up D2L, take a tour on a database you are going to throw away:

```ini
SEED_DEMO=true
DATABASE_URL=sqlite+aiosqlite:///./demo.db
```

Start the app, sign in, and you will have a demo course with fictional
students. From there you can start a session, scan the QR code with your own
phone, mark someone present by hand, export the gradebook CSV to see its
shape, and run the full test suite (section 11). Four of the six test scripts
need a course with at least one enrolled student, and this is the only way to
have one before D2L is connected.

When you are done:

1. Stop the app.
2. Delete `demo.db`.
3. Set `SEED_DEMO=false` and `DATABASE_URL` back to `sqlite+aiosqlite:///./adsum.db`.
4. Start it again and continue with section 6.

---

## 6. Connecting D2L (LTI 1.1)

> ### Before you start: D2L is retiring LTI 1.1
>
> *Written November 2026. Confirm the current position with your Brightspace
> administrator.*
>
> This section describes the legacy course-level External Learning Tool flow,
> which is what lets an instructor connect Adsum without an IT ticket. D2L has
> announced its retirement:
>
> | When | What happens |
> | --- | --- |
> | Sept 2026 | D2L announced its intent to end support for LTI 1.1. |
> | Oct 2026 | Formal customer notification. |
> | **Feb 2027** | Customers can **no longer configure or deploy new LTI 1.1 integrations** through the Brightspace UI, and the Valence API for creating legacy LTI tool providers is removed. Links already installed keep working. |
> | Mar 2027 | Institutions may opt in to disabling LTI 1.1 early. |
> | **Sept 2027** | **LTI 1.1 launches stop working entirely.** |
>
> What this means for you: following this section today works, and a link
> created before February 2027 keeps launching until September 2027.
> After that release you cannot create a new LTI 1.1 link at all, and your
> institution may have switched 1.1 off sooner. Adsum does not support LTI 1.3
> yet; that is the project's next step, not a shipped feature, and 1.3
> registration is an organisation-level administrator job rather than
> something an instructor can do alone. Talk to your Brightspace
> administrator early, and give them
> [BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md).
>
> The plan for the 1.3 version, including the administrator's install
> procedure and the exact permissions it would request, is in
> [LTI-1.3-PLAN.md](LTI-1.3-PLAN.md). A packet written for an institutional
> IT/security review is in [IT-REVIEW.md](IT-REVIEW.md).
>
> The dates above come from D2L's deprecation plan, last updated 8 Oct 2026.
> D2L's other pages have at times given release numbers instead; where they
> disagree, the deprecation plan is the article maintained for this purpose.
>
> Sources:
> [LTI 1.1 deprecation plan](https://community.d2l.com/brightspace/kb/articles/25994-lti-1-1-deprecation-plan),
> [D2L press release](https://www.d2l.com/newsroom/d2l-announces-security-enhancements-with-retirement-of-lti-1-1-support/),
> [High Impact Changes Timeline](https://community.d2l.com/brightspace/kb/articles/1047-high-impact-changes-timeline),
> [LTI migration](https://community.d2l.com/brightspace/kb/articles/23664-lti-migration),
> [1EdTech deprecation schedule](https://www.1edtech.org/lti-security-announcement-and-deprecation-schedule).

**The order below matters.** A fresh install has no courses, so there is no
course page to copy credentials from. They live at `/admin`, and the course
page only comes into existence at step 6.

1. **Sign in to Adsum as the bootstrap admin** (`ADMIN_EMAIL` /
   `ADMIN_PASSWORD`) and open **`/admin`**.
2. On the **D2L Brightspace connection** card, enter your **D2L host** (for
   example `yourschool.brightspace.com`) and save. Then copy three values from
   that card:
   - the **launch URL**, shown as `https://your-host/lti/launch`
   - the **LTI consumer key**, `adsum` unless you changed `LTI_CONSUMER_KEY`
   - the **LTI consumer secret**, which was generated automatically on the
     first start. Leave the field blank when saving to keep the existing one.
3. **In D2L**: your course → **Content** → pick a module → **Add Existing
   Activities** → **External Learning Tool** → create a new link:
   - **Title**: `Adsum`
   - **URL**: the launch URL from step 2
   - **Key**: the consumer key from step 2
   - **Secret**: the consumer secret from step 2
4. Tick **Open as External Resource**.
5. Under **Security Settings**, enable all three of **Send system username**,
   **Send user email** and **Send user name**. Without them the launch arrives
   unidentified and nobody can be matched to a roster.
6. **Click the link once, in D2L, as yourself.** This is the step that creates
   the course in Adsum, creates or links your instructor account, and enrols
   you as its instructor. You should land in the app, signed in, on your
   course.
7. **Now** open **Course → Settings**, which exists for the first time, and
   set the geofence, the attendance policy and campus IP ranges. The roster
   import, which takes a D2L Classlist export, is on the course page itself.

### The roster is reconciled only when you import a classlist

**Adsum cannot poll D2L for the class list.** An LTI 1.1 launch carries the
course and the person launching it and no roster at all, and the Valence
`enrollments` API that could return one needs an OAuth application registered
by a Brightspace *administrator*: the IT ticket this project exists to avoid.
Do not promise users a roster that syncs itself.

So the instructor exports the classlist from D2L and imports it on the course
page, and that import is a reconciliation: students on the file are added,
students missing from it are **marked dropped rather than deleted** (their
attendance is real history, and the D2L gradebook may still refer to them),
someone who reappears in a later file is put back, and changed names,
usernames and D2L IDs are reported. A dropped student is excluded from the
gradebook export and from every attendance count, and shown separately on the
course page with the date and the attendance they had.

#### Where the classlist file actually comes from

Not where you would guess, so tell your instructors precisely this:

> **Grades → Export**, set **Key Field** to Username or Org Defined ID, tick
> **Last Name** and **First Name** under *User Details*, then **Export to CSV**.

Three things make the obvious guess wrong, all confirmed against a live
Brightspace course:

- **The Classlist tool has no export.** It can email the class and print, but
  there is no CSV anywhere in it. The only export is in Grades.
- **The two name checkboxes are off by default.** An export done without
  noticing them contains identifiers and no names at all. Adsum refuses such a
  file and says which boxes to tick, rather than creating a roster of blanks.
- **D2L prefixes the key field's values with `#`** (`#jsmith`), and which
  column is the key field is chosen at export time. Adsum strips it from every
  identifier column. This matters more than it looks: stored verbatim, a
  username would match no existing student (creating a duplicate) and the
  gradebook export would then emit a username D2L does not recognise, so the
  grade would silently land nowhere.

Any of OrgDefinedId, Username or Email is enough to identify a student, and
Adsum matches on all three (plus its own stored ID), case-insensitively,
because a real export keyed on Username has no OrgDefinedId or Email column
at all.

An import that would drop more than half the active roster, or that shares no
students with a non-empty roster, is **refused outright and changes nothing**
until the instructor confirms it explicitly. The common cause is a filtered
or wrong-course export, not a real mass drop. Both thresholds are code
constants in `app/routes/instructor.py`; like attendance policy they are
deliberately not install settings. Because nothing is reconciled until an
import happens, the course page and the dashboard warn when a roster has not
been imported for about a week, or never.

### If you are not the Adsum administrator

The course settings page deliberately does **not** show the consumer secret,
because it is shared by every course on the installation and anyone holding it
can forge a launch as any user. Ask whoever administers the install for the
launch URL, key and secret.

The better arrangement for a shared install is to ask IT to register Adsum
once as an **organisation-level** External Learning Tool. Instructors can then
add the link to their own courses without ever seeing the secret.
[BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md) is a one-page briefing written to
be forwarded to them.

### Rotating the secret

The secret must match on both sides at the same time. Change it at `/admin`
and in the D2L tool configuration together, or every launch fails, for every
instructor on the install.

---

## 6b. Running without an LMS (standalone mode)

Skip this if you did section 6. This is the whole setup for an instructor with
no D2L, or for anyone planning past D2L's LTI 1.1 retirement, since nothing
here depends on LTI at all.

1. Sign in and choose **New course** on the dashboard.
2. Give it a name and code, and choose the **No LMS** option, where students
   claim their name once (standalone).
3. On the course page, **import your class roster**: any CSV with first name,
   last name and a student ID column. In this mode the roster is also the list
   students pick their own name from, so load it before the first class.
4. Start a session and project the QR code as normal.

The first time a student scans, they are asked who they are and pick their name.
That binds their phone to that name and checks them in **in the same request**.
There is no login round trip, so no second scan is needed. Every later scan is
silent.

Attendance leaves as a plain CSV: **Download a CSV for your gradebook** on the
course page. Adsum does not know which gradebook you use and does not pretend
to integrate with one.

### What you are trading away, and what to do about it

Be clear with yourself about this before you rely on it.

The rotating QR code still proves **presence**: the code changes every twenty
seconds, so a student has to be looking at the screen in the room. What this
mode cannot prove on its own is **identity**. At that very first scan, nothing
stops a student picking a classmate's name. With D2L that is impossible without
being signed in as the classmate. The LMS has already proved who they are.

The guards, and what you have to do:

- **One name, one phone**, enforced by a unique index in the database. A second
  phone claiming a bound name is refused outright.
- **Refused claims are shown at the top of your course page**, by name and
  time, until you acknowledge them. **Look at them.** That alert is the main
  defence: the student whose name was taken is refused, comes to you, and you
  can fix it while the class is still in the room.
- **Release** a binding (course page → *Phones checked in as a student*) for a
  new phone, a lost phone, or a wrong name. Releasing deletes **no attendance**.
- Students marked dropped by a roster import are not claimable, and the claim
  list never shows student ID numbers or usernames.

**Single sign-on (Google or Microsoft) would remove claim-time impersonation
entirely**, and is a known future option. It does not exist in Adsum today;
nothing here speaks OAuth or OIDC.

One operational difference: a D2L course keeps a full second copy of its
attendance in the Brightspace gradebook, and `Restore from D2L` can rebuild the
app from it. **A standalone course has no such mirror**, so your database
backups (section 9) are the only copy. Download the attendance CSV from time to
time as well.

---

## 7. Daily use

- **Start Session** on the course page, project the QR code, students scan.
- **End Session** when you are done. Sessions never end on their own.
- Anyone whose phone failed: **Mark Present**, during or after the session.
- Documented absence: **Excuse**, which removes that class from their
  denominator.
- **Export for D2L Gradebook**, then in D2L **Grades → Import**.

On the first import only, tick *Create new grade item when an unrecognized
item is referenced*, and set **Maximum Points to 100** for the *Attendance*
item. D2L defaults that to 10, which displays everyone as 1000%. The
*Attendance Detail* text item needs no properties.

Later imports overwrite with current running totals, so there is nothing to
reconcile.

---

## 8. Running it as a service

### systemd (Linux)

`/etc/systemd/system/adsum.service`:

```ini
[Unit]
Description=Adsum
After=network.target

[Service]
Type=simple
User=checkin
WorkingDirectory=/opt/adsum
ExecStart=/opt/adsum/.venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now adsum
sudo systemctl status adsum
```

### Windows

Use Task Scheduler with a task that runs at startup or at logon of the service
account, restarts on failure, and runs the bundled launcher:

```
Action:    powershell.exe
Arguments: -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\path\to\adsum\start.ps1"
Settings:  Restart on failure, every 1 minute, up to 999 times
```

`start.ps1` restarts uvicorn if it exits and appends everything to
`server.log`. It picks its interpreter in this order: `PYTHON=` in `.env`,
then `.venv\Scripts\python.exe`, then `python` on the `PATH`. On Windows
`python` often resolves to a Store stub, so set `PYTHON=` to the full path of
the interpreter you installed the dependencies into. That key is read by the
launcher, not by the app, so put it in `.env` and do not add it to
`.env.example`.

[DEPLOY.md](DEPLOY.md) is a complete worked Windows deployment.

---

## 9. Backups

Two independent layers, and you want both.

**The database.** `backup_db.py` uses SQLite's online backup API, so it is
safe while the app is running. It keeps the last 14 copies in `backups/`.
Schedule it nightly.

Linux and macOS, with cron:

```cron
0 21 * * * cd /opt/adsum && .venv/bin/python backup_db.py
```

Windows, with Task Scheduler:

```
Action:    C:\path\to\adsum\.venv\Scripts\python.exe
Arguments: backup_db.py
Start in:  C:\path\to\adsum
Trigger:   Daily at 21:00
```

`backup_db.py` resolves a relative `DATABASE_URL` against its own directory,
not the working directory, so it backs up the right file either way. Setting
**Start in** is still worth doing so the task behaves predictably.

To restore: stop the app, replace the database file with a backup, start it.

**D2L itself.** Because the *Attendance Detail* column holds every class date
and status, a D2L grade export is a complete backup. If the database is ever
lost, use **Restore from D2L** on the course page and upload that export; the
sessions and attendance are rebuilt. Restoring is additive and idempotent, so
running it twice is harmless.

This is why the app refuses to export when a course has no sessions: an empty
file would otherwise overwrite the record D2L is holding.

---

## 10. Upgrading

```bash
git pull
pip install -r requirements.txt
# restart the app -- it migrates itself
```

On Windows, in the project directory with the virtualenv active, the same
commands work.

**The schema is brought up to date at startup, and a backup is taken first.**
You do not need to run `alembic upgrade head` by hand. This is deliberate:
forgetting it meant the app started happily against the old schema and then
threw 500s on whichever page touched a column that was never added, possibly
days later, in front of a class.

What startup does, in order:

| Database state | What happens | Backup taken? |
|---|---|---|
| Empty | Schema built and stamped at head | No, nothing to lose |
| Already at head | Nothing | No |
| Tables but no migration version | Stamped at head, history *not* replayed | No |
| Behind head | **Backed up**, then migrated | Yes, before any change |
| Migration fails | Refuses to start, records the failure | Yes, once only |

Two behaviours worth knowing:

- **A failed migration stops the app rather than degrading it.** Serving on a
  schema that could not be brought up to date fails in scattered, confusing
  ways; refusing to start fails in one obvious way, and your data is as it was.
  Fix the cause, delete the `.migration-failed` file, and start again.
- **A failed migration is not retried, and takes no further backups.** The
  launcher restarts the app every 10 seconds indefinitely, so a naive
  back-up-then-migrate would take a fresh backup on every attempt and, with
  `BACKUP_KEEP` at 14, would delete every good backup within a couple of
  minutes, destroying the one thing the backup exists to protect.

Set `AUTO_MIGRATE=false` in `.env` if your deployment runs migrations as its
own step. Automatic migration is SQLite-only, because the backup uses SQLite's
online backup API; on any other engine, run the migration in your deploy
pipeline and use that engine's backup tool.

> **Never run `alembic upgrade head` on a brand-new install by hand.** It
> crashes with `NoSuchTableError: courses`. The baseline revision creates no
> tables: the schema is built by SQLAlchemy's `create_all` and then stamped at
> the current head. Starting the app does the right thing in every case.

---

## 11. Verifying the install

Install the development dependencies first, otherwise `ruff` is not present:

```bash
pip install -r requirements-dev.txt
```

`scripts/ci_local.sh` is the authoritative list and runs them in CI's order.
**These four need no server:**

```bash
python scripts/test_scoring.py           # the grade arithmetic
python scripts/test_migrations.py        # startup backs up, migrates, or refuses
python scripts/test_accessibility.py     # the automatable part of WCAG 2.1 AA
python scripts/verify_install.py         # clean-install check
ruff check app scripts run.py backup_db.py alembic/env.py
```

`test_accessibility.py` locks in the fixes from a browser-based axe-core
audit: contrast ratios (including placeholder opacity), a focus indicator that
is actually drawn, labels on every student input, valid `inputmode` values,
decorative emoji hidden from screen readers, and the page shell. It covers the
roughly one third of WCAG that can be automated and says so in its own output.
It is **not** a conformance claim: see [ACCESSIBILITY.md](ACCESSIBILITY.md) for
what was audited, what was fixed, and what still needs a human.

`test_migrations.py` exercises every path through startup migration against
throwaway databases: empty, already current, tables with no version, genuinely
behind head, and a migration that fails. It asserts a backup is taken *before*
any schema change, that the backup holds the pre-migration state, and that a
failed migration is not retried and takes no further backups.

`verify_install.py` exports the committed tree with `git archive`, configures
it from `.env.example` alone, and starts it, which is also what CI does on
every push. The app refuses to start until `SECRET_KEY` and `ADMIN_PASSWORD`
are set, then creates the admin account and starts with no demo data.

**Seven need a running instance *and* a course with at least one enrolled
student:**

```bash
python scripts/smoke_test.py             # end-to-end happy path
python scripts/test_isolation.py         # instructors cannot see each other's data
python scripts/test_admin_separation.py  # non-admins cannot change shared settings
python scripts/test_admin_page.py        # /admin stays inside its boundary
python scripts/test_security.py          # every hole that was once open
python scripts/test_robustness.py        # every way a present student was marked absent
python scripts/test_roster_sync.py       # roster adds, drops and the wrong-file guard
python scripts/test_standalone.py        # claim-once identity, with no LMS
```

They read `BASE_URL`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` from the environment
or `.env`, and pick a course automatically. Set `TEST_COURSE_ID` to choose
one. `test_standalone.py` also needs `SECRET_KEY`, because it mints an
out-of-date QR token to prove such a token cannot claim an identity.

A fresh install has no courses, so these cannot pass until you have one:
either from D2L (section 6), created in the UI (section 6b), or from a
`SEED_DEMO=true` throwaway database as described in section 5. The quickest
way to run the whole set the way CI does, on a scratch copy with its own
database and port, is:

```bash
bash scripts/ci_local.sh
```

Note that `ci_local.sh` and `verify_install.py` both export **committed**
HEAD, so neither sees uncommitted work in your checkout.

Run the isolation tests after any upgrade. They are the ones that catch a
privacy regression.

---

## 12. Troubleshooting

| Symptom | Cause |
| --- | --- |
| QR code scans to an unreachable page | `BASE_URL` is wrong, or is a private address students' phones cannot resolve. |
| D2L link returns 403 | Consumer key or secret mismatch. They must match on both sides exactly. The secret is at `/admin`, not on the course page. |
| D2L link returns 403 and the key and secret are definitely right | The site may have LTI 1.1 disabled. See the notice in section 6. |
| Launch works but the student is unidentified | Security Settings in the D2L link are not sending username, email and name. |
| Everyone shows as 1000% in the gradebook | Maximum Points on the *Attendance* grade item is 10. Set it to 100. |
| Times are several hours off | `DISPLAY_TIMEZONE` is unset, or `tzdata` is missing on Windows. |
| Live attendance list does not update | Reverse proxy is buffering server-sent events. See section 4. |
| One failed login locks out everyone | The proxy is not forwarding `X-Forwarded-For`. |
| "Nothing to export" | The course has no sessions. This is a deliberate guard, see section 9. |
| The dashboard is empty on a fresh install | Expected. Either connect D2L and launch the link once (section 6), or use **New course** and run the course standalone (section 6b). |
| A standalone student is told their name is already checked in on another phone | Somebody has already claimed that name. Check the refused-claim alert at the top of the course page, confirm who the student is, and **Release** the phone holding the name. No attendance is lost. |
| A setting in `.env` seems to be ignored | The same name is set as a real environment variable, which wins. See section 3. |
| `alembic upgrade head` fails with `NoSuchTableError: courses` | You ran it on an empty database. Start the app once instead. See section 10. |
| `ruff: command not found` | `pip install -r requirements-dev.txt`. |
| Windows service does not start at boot, no obvious error | `start.ps1` could not find a usable interpreter. Check the last lines of `server.log` and set `PYTHON=` in `.env`. |

Logs:

- **Linux (systemd):** `journalctl -u adsum -f`
- **Windows (Task Scheduler and `start.ps1`):** `server.log` in the project
  directory. Tail it with
  `Get-Content .\server.log -Wait -Tail 50`.
- **Running in the foreground:** the terminal you started it in.

---

## 13. What leaves your server

Nothing goes to the author or any third party at runtime.

| Destination | When | Why |
| --- | --- | --- |
| Your D2L host | Instructor clicks a D2L link | Your own LMS |
| `unpkg.com`, `tile.openstreetmap.org` | Instructor opens course Settings | Leaflet and map tiles for the geofence picker |
| `cdn.jsdelivr.net` | Instructor opens Analytics | Chart.js |

Student-facing pages load no third-party resources at all. If your institution
requires a fully self-contained deployment, download those three assets into
`app/static/` and update the two template references; nothing else depends on
them.

---

## 14. Before you go live

In order. The test scripts come after D2L is connected, not before, because
four of them need a real course with an enrolled student.

**Configuration**

- [ ] `SECRET_KEY` is unique and random
- [ ] `ADMIN_PASSWORD` is strong, and `.env` is not in version control
- [ ] `DEBUG=false`, `SEED_DEMO=false`, and any demo database has been deleted
- [ ] `HOST` is what you intended: `127.0.0.1` behind a local reverse proxy,
      `0.0.0.0` only when something else must connect directly
- [ ] HTTPS works, and `BASE_URL` matches it exactly

**Before D2L is connected**

- [ ] `python scripts/test_scoring.py` passes
- [ ] `python scripts/verify_install.py` passes
- [ ] `ruff check app scripts run.py backup_db.py` passes
      (needs `requirements-dev.txt`)
- [ ] You have had one practice run with `SEED_DEMO=true` on a throwaway
      database, and the four live-instance scripts passed against it, for
      example with `bash scripts/ci_local.sh`

**After D2L is connected (section 6)**

- [ ] A launch from your D2L course works and lands you on a course page
- [ ] The LTI secret is not a default, and matches D2L
- [ ] `python scripts/smoke_test.py`, `test_isolation.py`,
      `test_admin_separation.py` and `test_admin_page.py` all pass against the
      real instance, with `TEST_COURSE_ID` pointing at the real course
- [ ] One full dry run: start a session, check in from a phone, export, and
      import into a sandbox D2L course

**Operations**

- [ ] Nightly backups are scheduled and you have restored one once
- [ ] `/healthz` is monitored
- [ ] You know where the logs are: `journalctl -u adsum` or `server.log`

See [SECURITY.md](../SECURITY.md) for the security model, and
[BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md) for the page to hand your IT
department. Remember that attendance records are student education records.
Host them accordingly.
