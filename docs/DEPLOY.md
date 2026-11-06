# Deployment Runbook (for IT)

This document describes how to deploy and operate Adsum in a production environment. It assumes a Windows Server or VM.

For a platform-neutral install guide, see [INSTALL.md](INSTALL.md); this
document covers the Windows specifics only.

---

## 1. Architecture overview

```
Internet → Cloudflare Tunnel (cloudflared) → Windows host:8000 → uvicorn (FastAPI) → SQLite (adsum.db)
                         ↑
            Scheduled Task EI-tunnel (runs cloudflared as restart-loop)
                         ↑
            Scheduled Task adsum (runs uvicorn as restart-loop)
                         ↑
            Scheduled Task adsum-backup (runs backup_db.py daily at 21:00)
```

All components run as the `mgrot` user (or a dedicated service account). The `cloudflared` connector uses a named tunnel (`rupert`) with its own credentials file.

---

## 2. Prerequisites

| Requirement | Notes |
|---|---|
| Windows Server 2019+ / Windows 10+ | Any edition with Task Scheduler |
| Python 3.13+ | `C:\Programs\Python\Python313` or similar |
| Cloudflare account | One tunnel (`rupert`), credentials file at `C:\Users\<svc>\.cloudflared\656d3e2a-fd4c-4a41-8da6-561b71add531.json` |
| Domain DNS | `your-host.example.edu` CNAME to the tunnel |

---

## 3. Initial deployment (bare Windows)

### 3.1 Install Python and dependencies

```powershell
# As admin (or service account)
winget install Python.Python.3.13
python -m venv C:\Apps\adsum\.venv
C:\Apps\adsum\.venv\Scripts\pip.exe install --upgrade pip
C:\Apps\adsum\.venv\Scripts\pip.exe install -r requirements.txt
```

### 3.2 Configuration

Copy `.env.example` to `.env` and set:

```
SECRET_KEY=...          # 32+ hex chars, generated once
ADMIN_PASSWORD=...      # Strong, store in password manager
ADMIN_EMAIL=admin@example.edu
SEED_DEMO=false
DATABASE_URL=sqlite+aiosqlite:///./adsum.db
DEBUG=false
```

Never commit `.env`. Keep `CREDENTIALS.txt` (admin email/password) in a secure location.

### 3.3 Create the database + admin

```powershell
cd C:\Apps\adsum
python -m alembic upgrade head
# On first run, ensure_admin() creates the institution + admin instructor
```

### 3.4 Scheduled Tasks

**Task: adsum** (runs the app)

```powershell
# Action: powershell.exe
# Arguments: -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Apps\adsum\start.ps1"
# Trigger: At logon of the service account
# Settings: Restart on failure (1 min, 999 times)
```

`start.ps1` (in project root) restarts uvicorn on crash and writes `server.log`.

**Task: adsum-backup** (nightly backup)

```powershell
# Action: C:\Apps\adsum\.venv\Scripts\python.exe
# Arguments: C:\Apps\adsum\backup_db.py
# Trigger: Daily at 21:00
```

`backup_db.py` uses SQLite's online backup API (safe while app runs). Keeps 14 daily backups in `backups/`.

### 3.5 Cloudflare Tunnel

The tunnel is a separate scheduled task (`EI-tunnel`) that runs `cloudflared` with the config at `<your cloudflared config>`. It routes `your-host.example.edu` → `http://127.0.0.1:8000`.

The config file contains the ingress rules:

```yaml
ingress:
  - hostname: your-host.example.edu
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Restart the `EI-tunnel` task after any config change.

---

## 4. D2L Brightspace integration (LTI 1.1)

**No central IT registration needed.** Any instructor self-configures:

1. D2L → Course → Content → Add Existing Activities → External Learning Tool
2. Title: `Adsum`
3. URL: `https://your-host.example.edu/lti/launch`
4. Consumer Key: from Settings page (default `adsum`)
5. Secret: from Settings page (auto-generated on first save)
6. Check **Open as External Resource**
7. Security Settings → enable: Send system username, Send user email, Send user name

Students launch from D2L → auto-authenticated → one-tap check-in.

---

## 5. Gradebook workflow (no IT needed)

Instructor exports CSV from the course page (**Export for D2L Gradebook**).
Import in D2L: **Grades → gear → Import Grades** → match `OrgDefinedId` → map `Attendance Points Grade`.

This is a one-minute manual step. Fully automatic passback (LTI 1.3 AGS) requires your IT department to register the tool for Advantage services.

---

## 6. Monitoring & health

- **Health endpoint:** `GET /healthz` → `{"status":"ok"}` (point your uptime monitor here)
- **Uptime:** The restart-loop scripts (`start.ps1`, cloudflared's loop) auto-recover from crashes.
- **Logs:** `server.log` (uvicorn), `tunnel.log` (cloudflared), `backups/` (timestamped DB copies).

---

## 7. Backup & restore

- **Automatic:** `backup_db.py` runs daily at 21:00 via scheduled task. Uses SQLite online backup API (safe while live). Keeps 14 days in `backups/`.
- **Manual restore:**
  1. Stop the `adsum` task.
  2. Replace `adsum.db` with the desired backup file.
  3. Start the `adsum` task.

---

## 8. Schema changes (Alembic)

```bash
# After model changes
alembic revision --autogenerate -m "description"

# Apply
alembic upgrade head
```

The production DB is stamped at head (baseline revision `1958aa23331c`). Fresh installs run `alembic upgrade head` automatically on first start.

---

## 9. Credentials & secrets

| Secret | Where stored | Rotation |
|---|---|---|
| `SECRET_KEY` | `.env` | Rotate with `rotate_secrets.py` (updates key + admin password + DB hash) |
| `ADMIN_PASSWORD` | `.env` + `CREDENTIALS.txt` (gitignored) | Same as above |
| Cloudflare tunnel credentials | `C:\Users\<svc>\.cloudflared\*.json` | Cloudflare dashboard |
| D2L LTI secret | Settings page in app (auto-generated) | Regenerate via Settings page |

---

## 10. Privacy / FERPA

- Attendance = education record. Handle per FERPA.
- Location: one-time geofence check; only `in_fence` + distance stored.
- No analytics, tracking, or third-party data sharing.
- Student-facing notice when geofence enforced: *"Your location is checked once against the classroom area and logged with your attendance."*

---

## 11. Emergency contacts

| Role | Contact |
|---|---|
| App owner | set this to whoever maintains the install |
| Cloudflare / Tunnel | your IT department (Tailscale / Cloudflare admin) |
| D2L Brightspace | your IT department (Brightspace admin) |