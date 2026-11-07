# Windows deployment guide

How to run Adsum as a long-lived service on a Windows host: Task Scheduler for
the app, a nightly backup task, and a tunnel or reverse proxy for HTTPS.

Read [INSTALL.md](INSTALL.md) first for the platform-neutral install,
configuration and D2L setup. This document covers only the Windows specifics.
On Linux, use the systemd section of INSTALL.md instead.

Paths below use `C:\Apps\adsum` as an example. Put it wherever you like. The
only requirement is that the service account can read and write that
directory, because the SQLite database, `server.log` and `backups\` all live
in it.

---

## 1. Architecture

```
Internet
   |
   v
HTTPS front end   (tunnel, or IIS / nginx / Caddy reverse proxy)
   |
   v
127.0.0.1:8000    uvicorn (FastAPI)  -->  SQLite file (adsum.db)
```

Scheduled tasks, configured to run whether or not anyone is logged on:

| Task | Runs | Purpose |
|---|---|---|
| `adsum` | `start.ps1`, which loops uvicorn | The application |
| `adsum-backup` | `backup_db.py`, daily | Online SQLite backups, last 14 kept |
| a tunnel task | your connector | HTTPS, if you use a tunnel rather than a local proxy |

Run all of them as a dedicated low-privilege service account that owns the
application directory. Do not run the app as an administrator.

---

## 2. Prerequisites

| Requirement | Notes |
|---|---|
| Windows Server 2019+ or Windows 10+ | Any edition with Task Scheduler |
| Python 3.11 or newer | The project supports 3.11+, and CI covers 3.11, 3.12 and 3.13. Note the full path to `python.exe`: you will need it in section 3.2. |
| A DNS name and an HTTPS front end | A tunnel, or a reverse proxy terminating TLS. Students' phones connect to it directly. |
| A service account | With read and write access to the application directory |

---

## 3. Initial deployment

### 3.1 Code and dependencies

```powershell
git clone https://github.com/mrothlis/adsum.git C:\Apps\adsum
cd C:\Apps\adsum

py -3 -m venv C:\Apps\adsum\.venv
C:\Apps\adsum\.venv\Scripts\python.exe -m pip install --upgrade pip
C:\Apps\adsum\.venv\Scripts\pip.exe install -r requirements.txt
```

Add `-r requirements-dev.txt` too if this machine will run the test suite or
the linter.

### 3.2 Configuration

Copy `.env.example` to `.env` and set at least:

```ini
SECRET_KEY=<64 random hex characters, generated once>
ADMIN_EMAIL=admin@yourschool.edu
ADMIN_PASSWORD=<strong, kept in your password manager>
BASE_URL=https://attendance.yourschool.edu
INSTITUTION_NAME=Your Institution
D2L_HOST=yourschool.brightspace.com

HOST=127.0.0.1
PORT=8000
DEBUG=false
SEED_DEMO=false
DATABASE_URL=sqlite+aiosqlite:///./adsum.db
```

Generate the key with:

```powershell
python -c 'import secrets; print(secrets.token_hex(32))'
```

Single quotes, because PowerShell would otherwise consume the double ones.

`HOST=127.0.0.1` is correct when the HTTPS front end runs on this same
machine. Use `0.0.0.0` only when something on another host has to connect
directly, and check the firewall if you do.

`BASE_URL` must be the public HTTPS address, exactly, with no trailing slash.
It is embedded in every QR code, so a wrong value means phones scan to
nowhere.

**Pin the interpreter.** `start.ps1` resolves Python in this order: `PYTHON=`
in `.env`, then `.venv\Scripts\python.exe`, then `python` on the `PATH`. On
Windows `python` often resolves to a Microsoft Store stub or an unrelated
virtualenv, and the service then fails silently at boot. Set it explicitly:

```ini
PYTHON=C:\Apps\adsum\.venv\Scripts\python.exe
```

`PYTHON` is read by the launcher script, not by the application. Keep it in
`.env` and do **not** add it to `.env.example`: `scripts/verify_install.py`
fails the build when `.env.example` lists a key `app/config.py` does not read.

**A real environment variable beats `.env`.** The app loads `.env` without
overriding the existing environment, so anything set on the scheduled task or
system-wide silently wins. Check that first when a setting appears to have no
effect.

Never commit `.env`. Keep the admin credentials in a password manager rather
than in a file next to the database.

### 3.3 Create the database and the admin account

Start the app once:

```powershell
cd C:\Apps\adsum
.\.venv\Scripts\python.exe run.py
```

On an empty database that creates the schema, stamps it at the current
migration head, creates the institution and one administrator from
`ADMIN_EMAIL` and `ADMIN_PASSWORD`, and generates the LTI consumer secret you
will need in section 4. Browse to the app, sign in, confirm `/healthz` returns
`{"status":"ok"}`, then stop it with Ctrl+C.

**Do not run `alembic upgrade head` on a fresh install.** It fails with
`NoSuchTableError: courses`. The baseline revision deliberately creates no
tables; the schema is built by `create_all` in `app/database.py` on first
start, which then stamps the database at head. Migrations apply to later
upgrades only.

### 3.4 Scheduled task: the application

```
Name:      adsum
Run as:    the service account, "whether or not the user is logged on"
Trigger:   At startup
Action:    powershell.exe
Arguments: -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Apps\adsum\start.ps1"
Start in:  C:\Apps\adsum
Settings:  Restart on failure every 1 minute, up to 999 times
           "Stop the task if it runs longer than": disabled
```

`start.ps1` runs uvicorn in a restart loop and appends everything, including
its own diagnostics, to `server.log` in the project directory. If the service
does not come up, the last lines of that file say why. The usual answer is the
wrong interpreter, so set `PYTHON=` as above.

### 3.5 Scheduled task: nightly backup

```
Name:      adsum-backup
Run as:    the same service account
Trigger:   Daily at 21:00
Action:    C:\Apps\adsum\.venv\Scripts\python.exe
Arguments: backup_db.py
Start in:  C:\Apps\adsum
```

`backup_db.py` uses SQLite's online backup API, so it is safe to run while the
app is serving. It keeps the last 14 copies in `backups\`, named after the
database file, and reads the database location from `DATABASE_URL`. Set
`BACKUP_KEEP` to change the retention.

Copy `backups\` somewhere off the machine as well. Fourteen snapshots on the
same disk do not survive losing the disk.

### 3.6 HTTPS

The app speaks plain HTTP on `127.0.0.1:8000`. Something in front of it must
terminate TLS. Pick one.

**A tunnel** is the quickest route: no inbound firewall rule, no public IP.
Cloudflare Tunnel and Tailscale Funnel both work. Install the connector,
create a named tunnel, point a DNS name at it, and route that hostname to
`http://127.0.0.1:8000`. A Cloudflare ingress rule looks like:

```yaml
ingress:
  - hostname: attendance.yourschool.edu
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Run the connector as its own scheduled task with the same restart-on-failure
settings, restart that task after any config change, and keep its credentials
file outside the application directory, readable only by the service account.

**A reverse proxy** on the same machine (IIS with Application Request Routing,
or nginx or Caddy for Windows) is the better fit if your institution already
runs one. Two settings matter, and both fail quietly:

- Forward `X-Forwarded-For`. The login throttle keys on the client IP, so
  without it one person's failed logins lock out the whole class.
- Disable response buffering for the live attendance stream, or the
  instructor's screen never updates.

Section 4 of [INSTALL.md](INSTALL.md) has the nginx and Caddy snippets.

---

## 4. Connecting D2L

Follow section 6 of [INSTALL.md](INSTALL.md). The short version, in the order
that actually works:

1. Sign in to Adsum as the bootstrap admin and open **`/admin`**.
2. Set the **D2L host**, save, then copy the **launch URL**, the **LTI
   consumer key** and the **LTI consumer secret** from that page. The secret
   was generated on first start and is visible **only here**: the course
   settings page deliberately does not show it.
3. In D2L: course → **Content** → **Add Existing Activities** → **External
   Learning Tool**, with that URL, key and secret, and **Open as External
   Resource** ticked.
4. Under **Security Settings** enable **Send system username**, **Send user
   email** and **Send user name**. All three.
5. Click the link once. That launch creates the course, the instructor account
   and the enrolment. Only then does **Course → Settings** exist, for the
   geofence, the attendance policy and the campus IP ranges; the roster import
   is on the course page itself.

A fresh install has no courses and no way to create one in the interface, so
there is nothing to look at until that first launch happens.

**LTI 1.1 is being retired by D2L.** New 1.1 integrations cannot be configured
in Brightspace after the February 2027 release, and 1.1 launches stop working
in late 2027. The timeline, the sources and what it means for an adopter are
at the top of the [README](../README.md) and in section 6 of
[INSTALL.md](INSTALL.md). Plan on an organisation-level LTI 1.3 registration,
which is an administrator task, not an instructor one.

---

## 5. Gradebook workflow

The instructor exports a CSV from the course page (**Export for D2L
Gradebook**) and imports it in D2L under **Grades → Import**.

The current procedure, including the two grade items it creates and the
Maximum Points trap that otherwise shows everyone at 1000%, is in the
**Getting attendance into the gradebook** section of the
[README](../README.md). It is deliberately not duplicated here, so there is
only one copy to keep correct.

---

## 6. Monitoring and health

- **Health endpoint:** `GET /healthz` returns `{"status":"ok"}`. Monitor it
  over the public HTTPS address, so the check also covers the front end.
- **Recovery:** the scheduled task plus the restart loop in `start.ps1`
  recovers from a crash with nobody logged in.
- **Logs:** `server.log` in the project directory holds uvicorn's output and
  the launcher's messages. There is no systemd here, so `journalctl` does not
  apply. Tail it with:

  ```powershell
  Get-Content C:\Apps\adsum\server.log -Wait -Tail 50
  ```

  Your tunnel or proxy keeps its own log.
- **Rotation:** `server.log` grows without bound. Archive or truncate it on a
  schedule if the host is long-lived.

---

## 7. Backup and restore

- **Automatic:** the `adsum-backup` task, nightly. Online backup API, safe
  while serving, last 14 copies in `backups\`.
- **Manual restore:**
  1. Stop the `adsum` task.
  2. Replace `adsum.db` with the chosen backup file.
  3. Start the `adsum` task.
- **Second layer, and the important one:** because the `Attendance Detail`
  grade item carries the whole per-date ledger, a D2L grade export is a
  complete backup. **Restore from D2L** on the course page rebuilds sessions
  and records from it, additively and idempotently. This is also why the app
  refuses to export a course with no sessions: an empty file would overwrite
  the copy D2L is holding.

---

## 8. Upgrading and schema changes

```powershell
cd C:\Apps\adsum
# Stop the adsum task first.
git pull
.\.venv\Scripts\pip.exe install -r requirements.txt
# Start the adsum task. It backs up and migrates itself on startup.
```

**The app applies pending migrations when it starts, taking a backup first**,
so there is no manual `alembic upgrade head` step and no way to forget it. If
a migration fails the app refuses to start rather than serving an out-of-date
schema, writes `.migration-failed`, and does not retry — important here,
because `start.ps1` restarts uvicorn every 10 seconds forever, and retrying a
failing migration would take a fresh backup each time and prune every good one
within minutes. Check `server.log`, fix the cause, delete
`.migration-failed`, restart.

Set `AUTO_MIGRATE=false` in `.env` to manage migrations yourself instead.

After a model change, in development:

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe the change"
```

Read the generated migration before committing it. Autogenerate does not
always get batch operations or data migrations right.

---

## 9. Credentials and secrets

| Secret | Where it lives | How to change it |
|---|---|---|
| `SECRET_KEY` | `.env` | Edit `.env` and restart. Everyone is signed out and any QR code on screen stops working. There is no rotation script in this repository; do it by hand. |
| `ADMIN_PASSWORD` | `.env`, and hashed in the database | `.env` only seeds the first admin on an empty database. Afterwards use **Reset a password** at `/admin`. |
| LTI consumer secret | The database, shown at `/admin` | Change it at `/admin` **and** in the D2L tool configuration at the same time. They must match, or every launch returns 403 for every instructor on the install. |
| Tunnel or proxy credentials | Wherever that tool keeps them, outside the application directory | That tool's own dashboard |

Keep `.env`, the database and `backups\` out of any web-served directory and
out of version control. `.gitignore` covers them, and
`scripts/verify_install.py` fails the build if an `.env` file, a database or a
log is ever committed.

---

## 10. Privacy and FERPA

Attendance records are student education records. Handle them accordingly.

- **Location:** nothing is collected unless an instructor enables the geofence
  on a course. When it is enabled, the attendance row stores the distance from
  the classroom and an in/out flag; the coordinates themselves are discarded.
  Students see a notice before the check.
- **Network:** the check-in IP address, and whether it fell inside a
  configured campus range, are stored.
- No analytics, no tracking, no third-party data sharing. Student-facing pages
  load no third-party resources.

[BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md) is a one-page summary written to
be forwarded to an IT or privacy reviewer.

---

## 11. Contacts

| Role | Who |
|---|---|
| Application owner | Whoever maintains this install |
| Tunnel or reverse proxy | Your IT department |
| D2L Brightspace | Your Brightspace administrator |

Fill this in for your own deployment. Nobody outside your institution can
recover this service for you.
