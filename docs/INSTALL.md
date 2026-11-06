# Installing Adsum

A complete guide to running this on your own infrastructure.

This application is entirely self-contained. It does not call home, does not
depend on any server belonging to the original author, and no data leaves the
machine you install it on except the requests you make to your own D2L.

---

## 1. What you need

| Requirement | Notes |
| --- | --- |
| A machine to run it on | Any Linux, macOS or Windows host, or a container runtime. A 1 vCPU / 1 GB VM is ample for a department. |
| Python 3.11 or newer | |
| A public HTTPS address | Students scan a QR code with their phones; their browsers must reach the server. |
| A D2L Brightspace course | You need permission to add an External Learning Tool link, which instructors normally have. |

You do **not** need: a database server (SQLite is the default), an IT ticket
for LTI 1.1, or any paid service.

### Where it can run

- A small cloud VM (DigitalOcean, Linode, AWS Lightsail, Azure).
- A campus-hosted VM, which is the right answer for anything beyond a pilot.
- A department server or lab machine with a stable address.
- Your own workstation, for testing only — if the machine sleeps, class
  check-in stops.

---

## 2. Install

### Install

```bash
git clone https://github.com/<owner>/adsum.git
cd adsum

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — see section 3.

python run.py
```

The app listens on port 8000.

### Generating a SECRET_KEY

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

This signs session cookies **and** QR check-in tokens. If it is weak, shared
between installs, or committed to a repository, anyone can forge both. Generate
a fresh one per install.

---

## 3. Configuration

Everything lives in `.env`. Nothing is hard-coded.

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
consortium, so check the address you actually log in to — it is not always
`<school>.brightspace.com`.

### Attendance policy

```ini
ALLOWED_UNEXCUSED_ABSENCES=3     # free absences before the grade drops
DISPLAY_TIMEZONE=America/New_York
QR_ROTATION_SECONDS=20
LATE_THRESHOLD_MINUTES=10
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
trying the app out, wrong for real use.

---

## 4. Making it reachable over HTTPS

Students' phones must reach the server, and it must be HTTPS — QR tokens and
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

Courses are not created by hand. The first time an instructor launches the
tool from their D2L course, the course, the instructor account and the
enrolment are created automatically.

---

## 6. Connecting D2L

Any instructor can do this; it needs no administrator.

1. In the app, open any course → **Settings**. Note the launch URL, consumer
   key and consumer secret. (Only administrators can see the secret.)
2. In D2L, go to your course → **Content** → pick a module → **Add Existing
   Activities** → **External Learning Tool**.
3. Create a new tool link:
   - **Title**: `Adsum`
   - **URL**: `https://your-host/lti/launch`
   - **Key**: your `LTI_CONSUMER_KEY`
   - **Secret**: the secret from step 1
4. Tick **Open as External Resource**.
5. Under **Security Settings**, enable **Send system username**, **Send user
   email**, and **Send user name**. Without these the launch arrives
   unidentified and nobody can be matched to a roster.

Click the link once yourself. You should land in the app, signed in, on your
course.

### Rotating the secret

The secret must match on both sides at the same time. Change it in the app's
settings and in the D2L tool configuration together, or every launch fails.

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

Use Task Scheduler with a task that runs at startup, restarts on failure, and
launches `run.py`. See `docs/DEPLOY.md` for a worked example.

---

## 9. Backups

Two independent layers, and you want both.

**The database.** `backup_db.py` uses SQLite's online backup API, so it is
safe while the app is running. It keeps the last 14 copies in `backups/`.
Schedule it nightly:

```cron
0 21 * * * cd /opt/adsum && .venv/bin/python backup_db.py
```

To restore: stop the app, replace the database file with a backup, start it.

**D2L itself.** Because the *Attendance Detail* column holds every class date
and status, a D2L grade export is a complete backup. If the database is ever
lost, use **Restore from D2L** on the course page and upload that export; the
sessions and attendance are rebuilt. Restoring is additive and idempotent, so
running it twice is harmless.

This is why the app refuses to export when a course has no sessions — an empty
file would otherwise overwrite the record D2L is holding.

---

## 10. Upgrading

```bash
git pull
pip install -r requirements.txt
alembic upgrade head                # apply any schema changes
```

Take a backup first. `alembic upgrade head` is safe to run when there is
nothing to do.

---

## 11. Verifying the install

The install is verified from a clean clone on every push by CI, and can be
reproduced locally with `python scripts/verify_install.py`: fresh
virtualenv, `.env` copied from `.env.example`, empty database. The app refuses
to start until `SECRET_KEY` and `ADMIN_PASSWORD` are set, then creates the
admin account and starts with no demo data.

Run the three test scripts against your instance. They read `BASE_URL`,
`ADMIN_EMAIL` and `ADMIN_PASSWORD` from the environment or `.env`.

```bash
python scripts/smoke_test.py             # end-to-end happy path
python scripts/test_isolation.py         # instructors cannot see each other's data
python scripts/test_admin_separation.py  # non-admins cannot change shared settings
```

The last two need a course with at least one enrolled student; set
`TEST_COURSE_ID` to point at it. Run the isolation tests after any upgrade —
they are the ones that catch a privacy regression.

---

## 12. Troubleshooting

| Symptom | Cause |
| --- | --- |
| QR code scans to an unreachable page | `BASE_URL` is wrong, or is a private address students' phones cannot resolve. |
| D2L link returns 403 | Consumer key or secret mismatch. They must match on both sides exactly. |
| Launch works but the student is unidentified | Security Settings in the D2L link are not sending username, email and name. |
| Everyone shows as 1000% in the gradebook | Maximum Points on the *Attendance* grade item is 10. Set it to 100. |
| Times are several hours off | `DISPLAY_TIMEZONE` is unset, or `tzdata` is missing on Windows. |
| Live attendance list does not update | Reverse proxy is buffering server-sent events. See section 4. |
| One failed login locks out everyone | The proxy is not forwarding `X-Forwarded-For`. |
| "Nothing to export" | The course has no sessions. This is a deliberate guard — see section 9. |

Logs: `journalctl -u adsum` or
`server.log` depending on how you run it.

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

- [ ] `SECRET_KEY` is unique and random
- [ ] `ADMIN_PASSWORD` is strong, and `.env` is not in version control
- [ ] `DEBUG=false`, `SEED_DEMO=false`
- [ ] HTTPS works, and `BASE_URL` matches it exactly
- [ ] The LTI secret is not a default, and matches D2L
- [ ] Nightly backups are scheduled and you have restored one once
- [ ] `/healthz` is monitored
- [ ] The three test scripts pass
- [ ] You have done one full dry run: start a session, check in from a phone,
      export, and import into a sandbox D2L course

See [SECURITY.md](../SECURITY.md) for the security model, and remember that
attendance records are student education records. Host them accordingly.
