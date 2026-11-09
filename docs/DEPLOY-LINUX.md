# Linux deployment guide

A worked deployment for an institutional Linux host, written for whoever
administers the server rather than for the instructor who will use it.

Two supported paths. Pick one:

- **[Containers](#a-containers)**, with `docker compose`. Fewer decisions,
  and the one most IT teams will want.
- **[A virtualenv and systemd](#b-virtualenv-and-systemd)**, with no
  container runtime. Slightly more setup, slightly more visibility.

Both put the same thing in front: a reverse proxy terminating TLS. Adsum
never listens on a public port in either path.

For Windows, see [DEPLOY-WINDOWS.md](DEPLOY-WINDOWS.md). For the
instructor-facing walkthrough, including how to add the link in D2L, see
[INSTALL.md](INSTALL.md). For a security and privacy review, see
[IT-REVIEW.md](IT-REVIEW.md).

---

## 1. What this is, in one paragraph

Adsum shows a QR code on the projector that changes every 20 seconds.
Students scan it with their phone and are recorded present. Identity comes
from D2L: a student must have opened Adsum from inside their D2L course at
least once, and nothing else can speak for them. The instructor exports
attendance to the D2L gradebook, which is the system of record. Adsum is
the pipe.

Technically it is one Python process, one SQLite file, and no other moving
parts. No message queue, no cache server, no second database.

---

## 2. Requirements

| | |
| --- | --- |
| Host | Any current Linux. 1 vCPU and 1 GB RAM is ample for a campus. Adsum's hot path is a signature check, not a query. |
| Disk | 2 GB is generous. A term of attendance for a few thousand students is a few megabytes; the backups are the same file again, times `BACKUP_KEEP`. |
| Python | 3.11 or newer, for the virtualenv path only. The container brings its own. |
| TLS | A DNS name you control and a certificate. Not optional: a phone camera will not open a page over plain HTTP, and will not accept a certificate it does not trust. |
| D2L | Permission to add an External Learning Tool link, on a Brightspace site where LTI 1.1 is still enabled. See [INSTALL.md section 6](INSTALL.md#6-connecting-d2l-lti-11). |

**Outbound network access is not required.** Adsum makes no outbound calls
of its own. Instructor pages load a map library and map tiles from three
public CDNs; student pages load nothing third-party, deliberately. The
allow-list is in [IT-REVIEW.md](IT-REVIEW.md), and that document tells you
how to verify the claim rather than take it on trust.

**Plan for September 2027.** D2L stops allowing new LTI 1.1 integrations in
February 2027, and existing 1.1 launches stop working in September 2027.
Adsum's D2L integration is LTI 1.1 today. A link created before February
2027 keeps working until September 2027, and after that the tool needs the
LTI 1.3 work described in [LTI-1.3-PLAN.md](LTI-1.3-PLAN.md). Do not deploy
this without knowing that date.

---

## 3. Configuration

Identical on both paths. Copy the template and fill in five values:

```bash
cp .env.example .env
chmod 600 .env
```

| Setting | What to put |
| --- | --- |
| `SECRET_KEY` | 32 characters or more of randomness. `python3 -c "import secrets; print(secrets.token_urlsafe(36))"`. Signs session cookies: changing it signs everyone out, and a guessable one means forgeable sessions. |
| `ADMIN_PASSWORD` | The first administrator's password. Creates the account on first start. |
| `ADMIN_EMAIL` | That administrator's email. Use the address D2L knows them by, so the account can be linked to their D2L identity later. |
| `BASE_URL` | `https://attendance.example.edu`, with no trailing slash. This is what goes in the QR code. Wrong here means every QR code points somewhere students cannot reach. |
| `INSTITUTION_NAME` | Shown in the interface and on the launch instructions. |

**Adsum refuses to start if `SECRET_KEY` is missing, still the placeholder,
or shorter than 32 characters, and if `ADMIN_PASSWORD` is empty.** That is
deliberate: an install that quietly came up with a placeholder key would
issue forgeable session cookies. The refusal names the setting and how to
fix it.

One more you will want, as soon as the reverse proxy is in place:

| Setting | What to put |
| --- | --- |
| `TRUSTED_PROXY_IPS` | Your reverse proxy's address. Adsum's login throttle limits failures per account **and** per address. With this unset, every request appears to come from the proxy, so one person's failed logins count against everybody. With it wrong, a client can claim any address and evade the throttle. Section 5 says how to find the right value. |

Everything else has a working default and is documented in `.env.example`.

---

## A. Containers

### A.1 Build and start

```bash
git clone https://github.com/mrothlis/adsum.git
cd adsum
cp .env.example .env
chmod 600 .env
# Edit .env: see section 3.
docker compose up -d
```

That is the whole deployment. The application creates its own database,
applies its own schema and creates the administrator account on first
start.

```bash
docker compose ps          # should show 'healthy' within a minute
docker compose logs -f     # watch it start
curl -fsS http://127.0.0.1:8000/healthz
```

### A.2 Check the install

The repository ships its own verifier. Run it:

```bash
docker compose exec adsum python scripts/verify_install.py
```

It checks, among other things, that no secret or database is tracked by
git, that an unconfigured install refuses to start, that the administrator
account was created from `.env`, and that the LTI secret was generated for
this install rather than shared with every other one.

### A.3 What the compose file does, and why

Read `docker-compose.yml`; it is commented. The decisions worth knowing:

- **The port is bound to `127.0.0.1`, not `0.0.0.0`.** The container is
  unreachable from the network except through your reverse proxy. Changing
  this publishes an unencrypted attendance system.
- **The root filesystem is read-only**, all capabilities are dropped, and
  `no-new-privileges` is set. Nothing is written inside the image, so this
  costs nothing. It is also why `DATABASE_URL`, `BACKUP_DIR` and
  `STATE_DIR` all point at `/data`.
- **`/data` is a named volume** holding the database, its WAL sidecars, the
  backups and the `.migration-failed` marker. **Destroying this volume
  destroys any attendance not yet exported to D2L.**
- **One worker.** SQLite with WAL handles concurrent readers, but Adsum
  holds server-sent-event connections for the live roster and keeps the
  login throttle counters in process memory, so a second worker would split
  that state and contend for the write lock.
- **Logs are capped** at 10 MB times 5 files, so a long-running install
  cannot fill the disk with its own logs and take the database down.

### A.4 Upgrading

```bash
cd adsum
git pull
docker compose build
docker compose up -d
```

The application backs up the database and applies pending migrations at
startup, in that order. If a migration fails it writes
`/data/.migration-failed` and refuses to serve rather than running against
a half-migrated schema. Clear the cause, delete the marker, restart.

```bash
docker compose exec adsum ls -la /data/.migration-failed   # should not exist
```

### A.5 Backups

The `backup` service runs one backup and exits. It uses SQLite's online
backup API, so it is safe to run while a class is checking in. Do not use
`cp`: that copies the database without its WAL sidecar and produces a
backup missing the most recent check-ins.

```bash
docker compose run --rm backup
```

Nightly, from the host:

```bash
sudo tee /etc/systemd/system/adsum-backup.service >/dev/null <<'EOF'
[Unit]
Description=Adsum database backup
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/adsum
ExecStart=/usr/bin/docker compose run --rm backup
EOF

sudo tee /etc/systemd/system/adsum-backup.timer >/dev/null <<'EOF'
[Unit]
Description=Nightly Adsum database backup

[Timer]
OnCalendar=*-*-* 21:00:00
Persistent=true
RandomizedDelaySec=5m

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now adsum-backup.timer
systemctl list-timers adsum-backup.timer
```

**Copy the backups off this host.** A backup on the same disk as the
database protects you from a bad migration, not from losing the disk.

```bash
docker run --rm -v adsum-data:/data -v /srv/offsite:/out alpine \
  tar czf /out/adsum-$(date +%F).tar.gz -C /data .
```

### A.6 Restoring

```bash
docker compose down
docker run --rm -v adsum-data:/data --entrypoint sh adsum:latest \
  -c 'ls -la /data/backups'
docker run --rm -v adsum-data:/data --entrypoint sh adsum:latest \
  -c 'cp /data/backups/adsum-20270115-210001.db /data/adsum.db && rm -f /data/adsum.db-wal /data/adsum.db-shm'
docker compose up -d
```

Deleting the `-wal` and `-shm` sidecars matters: left behind, they belong to
the old database file and SQLite will refuse or misread.

There is a second copy of the attendance that has been exported. If the
database is lost entirely, an instructor can rebuild a course from a D2L
grade export with **Restore from D2L** on the course page. Attendance that
was never exported exists only here.

---

## B. Virtualenv and systemd

For a host with no container runtime. The repository ships the unit files;
copy them rather than retyping from this page.

### B.1 User, directories, code

```bash
sudo useradd --system --home /opt/adsum --shell /usr/sbin/nologin adsum
sudo install -d -o adsum -g adsum /opt/adsum /var/lib/adsum

sudo -u adsum git clone https://github.com/mrothlis/adsum.git /opt/adsum
cd /opt/adsum
sudo -u adsum python3 -m venv .venv
sudo -u adsum .venv/bin/pip install -r requirements.txt
```

`/var/lib/adsum` holds the database, the backups and the state marker, and
is the only writable path the service gets. Keeping it outside the checkout
means deploying new code cannot touch the data, and lets the unit run with
`ProtectSystem=strict`.

### B.2 Configure

```bash
sudo -u adsum cp /opt/adsum/.env.example /opt/adsum/.env
sudo -u adsum chmod 600 /opt/adsum/.env
sudo -u adsum vi /opt/adsum/.env      # see section 3
```

The unit sets `DATABASE_URL`, `BACKUP_DIR` and `STATE_DIR` to
`/var/lib/adsum`, so leave those commented out in `.env`.

### B.3 Install the service

```bash
sudo cp deploy/systemd/adsum.service /etc/systemd/system/
sudo cp deploy/systemd/adsum-backup.service /etc/systemd/system/
sudo cp deploy/systemd/adsum-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adsum
sudo systemctl enable --now adsum-backup.timer

systemctl status adsum
journalctl -u adsum -f
curl -fsS http://127.0.0.1:8000/healthz
```

Then check the install:

```bash
cd /opt/adsum && sudo -u adsum .venv/bin/python scripts/verify_install.py
```

### B.4 Upgrading

```bash
cd /opt/adsum
sudo -u adsum git pull
sudo -u adsum .venv/bin/pip install -r requirements.txt
sudo systemctl restart adsum
journalctl -u adsum -n 50
ls -la /var/lib/adsum/.migration-failed   # should not exist
```

The service backs up and migrates itself on start, as in A.4.

---

## 5. The reverse proxy

Adsum listens on `127.0.0.1:8000` and speaks plain HTTP. Something in front
terminates TLS. Ready-made configuration is in the repository:

- `deploy/nginx/adsum.conf`
- `deploy/caddy/Caddyfile`

Copy one, replace the hostname, reload. Both have the two settings people
get wrong marked in place.

**Buffering is the one that will waste your afternoon.** The live
attendance list is server-sent events. With nginx's default buffering on,
the stream is held and names appear in a batch minutes late or not at all.
The instructor sees a projector page that looks frozen while students are
checking in successfully, and nothing in any log says why. `proxy_buffering
off` is not a tuning preference. Caddy does not buffer by default, so there
is nothing to switch off there.

**The forwarded address is the other one.** Set `TRUSTED_PROXY_IPS` in
`.env` to the address Adsum sees the proxy arrive from. Not the proxy's
public address: the address on the interface it connects from.

```bash
# Virtualenv path, proxy on the same host:
TRUSTED_PROXY_IPS=127.0.0.1

# Container path, proxy on the host, container on the default bridge:
docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
# usually 172.17.0.1
```

Get it wrong in one direction and one student's mistyped password throttles
the whole building. Get it wrong in the other and the per-address limit can
be evaded entirely. Check it afterwards: fail a login deliberately and
confirm the log names the student's address rather than the proxy's.

```bash
# containers
docker compose logs adsum | grep -i "login failed"
# systemd
journalctl -u adsum | grep -i "login failed"
```

---

## 6. Connecting D2L

This part is the instructor's, not yours, and it is covered step by step in
[INSTALL.md section 6](INSTALL.md#6-connecting-d2l-lti-11). What you need to
know:

- Adsum generates its own LTI consumer secret per install. It is shown in
  `/admin` and is never shared between installs.
- An instructor adds an External Learning Tool link in their course. In many
  Brightspace tenants an instructor can do this themselves; in others it is
  an administrator's job. [BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md) is
  written for that administrator.
- The first launch from a course creates the course, the instructor's
  account and the enrolment. Nothing needs setting up in Adsum first.
- An instructor who already has a password account is offered a one-time
  link to connect their D2L identity. Adsum will not join the two on a
  matching email alone, because a launch is signed with one install-wide
  secret and an LMS administrator can change a user's email address.

---

## 7. Monitoring

| What | How |
| --- | --- |
| Is it up | `GET /healthz` returns `{"status":"ok"}`. No authentication, no database write. Point your monitoring at it. |
| Container health | `docker compose ps`, or `docker inspect --format '{{.State.Health.Status}}' adsum`. |
| Service health | `systemctl is-active adsum`. |
| Logs | `docker compose logs adsum`, or `journalctl -u adsum`. Every refusal a student or instructor could notice is logged at WARNING with the reason, and never with a token, password or secret in it. |
| A failed migration | `/data/.migration-failed` or `/var/lib/adsum/.migration-failed`. Its presence means the app is refusing to serve on purpose. Alert on it. |
| Backups actually running | `systemctl list-timers adsum-backup.timer` and `journalctl -u adsum-backup --since yesterday`. A timer that silently never fires looks exactly like one that works, until the day you need the backup. |
| Disk | The database and `BACKUP_KEEP` copies of it. Small, but a full disk corrupts SQLite writes. |

---

## 8. What to tell a security reviewer

[IT-REVIEW.md](IT-REVIEW.md) is written for them and answers the questions
in the order they ask: what data is stored, what leaves the server, what is
logged, how authentication and authorisation work, what is tested, and what
the tool does **not** do.

The short version, and the parts that get asked first:

- **Student data stored:** name, institutional username and ID number,
  course enrolment, and attendance records. Where the instructor enables the
  geofence, the distance of a check-in from the classroom. Raw coordinates
  are not kept.
- **No network address is recorded against a student.** An earlier version
  stored one and it has been removed.
- **No outbound calls** from the server. Instructor pages load a map library
  and tiles from three public CDNs; student pages load nothing
  third-party.
- **Passwords** are hashed with PBKDF2-SHA256. Sessions are signed cookies,
  12 hours, `HttpOnly` and `SameSite=Lax`.
- **Attendance cannot prove bodily presence**, and the documentation does
  not claim it does. The QR code rotates every 20 seconds and the optional
  geofence narrows it further, but a student can be handed a code by a
  classmate. It is the same assurance as a paper sign-in sheet, with less
  friction and an audit trail.
- **Accessibility:** audited with axe-core, four defects found and fixed,
  documented in [ACCESSIBILITY.md](ACCESSIBILITY.md). **It is not a
  conformance claim.** There has been no screen-reader testing and there is
  no VPAT.

---

## 9. Limits you should know before committing

- **SQLite is the only supported database.** Fine for a campus; this is a
  low-write workload. But there is no replication and no failover, so the
  availability story is "restore the backup onto another host".
- **One process.** No horizontal scaling. See A.3 for why.
- **LTI 1.1 until September 2027.** See section 2.
- **Rosters are imported, not synchronised.** Adsum cannot ask D2L who has
  added or dropped, because LTI 1.1 carries no roster and the Valence
  enrolments API needs an administrator-registered application. The
  instructor uploads a classlist export and Adsum reconciles against it.
- **D2L is the system of record.** Adsum holds a working copy. Attendance
  that has been exported can be rebuilt from the gradebook; attendance that
  has not been exported exists only in Adsum's database and its backups.
