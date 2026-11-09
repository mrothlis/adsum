# Adsum container image.
#
# Design decisions worth knowing before you change anything here:
#
# * The image is read-only in practice. Everything that gets written lives
#   under /data, which is a volume: the SQLite database, its WAL sidecars,
#   the backups, and the .migration-failed marker (STATE_DIR). Nothing is
#   written inside the image, so the container can run with a read-only root
#   filesystem. The marker is the reason STATE_DIR exists: it is what stops
#   a failed migration being retried on every restart, so it must not be the
#   write that fails.
# * It runs as a non-root user. SQLite needs write access to the DIRECTORY
#   holding the database, not just the file, because it creates
#   adsum.db-wal and adsum.db-shm alongside it, so /data is chowned to that
#   user at build time and the volume inherits it.
# * The schema is applied by the application itself at startup, not by an
#   entrypoint script. app/migrate.py takes a backup, runs the migrations,
#   and writes a .migration-failed marker if anything goes wrong rather than
#   serving against a half-migrated database. One code path for every
#   deployment is the point.
# * tzdata is installed as a Python package in requirements.txt, so this
#   image needs no system timezone database to render local class times.
#
# Build:  docker build -t adsum:latest .
# Run:    see docker-compose.yml, which is the supported path.

FROM python:3.13-slim

# Python behaviour, not preference:
#   PYTHONUNBUFFERED      logs reach docker logs immediately instead of
#                         sitting in a buffer while you wonder what broke.
#   PYTHONDONTWRITEBYTECODE  no .pyc files in a read-only image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first and on their own layer, so editing application code does
# not reinstall them. requirements.txt is fully pinned.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the application. .dockerignore keeps .env, the database, the backups
# and the git history out of the image, so a built image carries no secrets
# and no institution's data.
COPY . .

# A non-root account owning only what it must write to.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 adsum \
    && mkdir -p /data \
    && chown -R adsum:adsum /data \
    && chown -R adsum:adsum /app

USER adsum

# The database and the backups both live on the volume. Backups inside the
# image would be destroyed by the next deployment, which is the opposite of
# a backup.
ENV DATABASE_URL="sqlite+aiosqlite:////data/adsum.db" \
    BACKUP_DIR="/data/backups" \
    STATE_DIR="/data" \
    HOST="0.0.0.0" \
    PORT="8000"

VOLUME ["/data"]
EXPOSE 8000

# python rather than curl: the slim image has no curl, and adding one for a
# health check is a package and a CVE feed for no reason. Exits non-zero on
# any failure, which is what the orchestrator reads.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else sys.exit(1)"

# Exec form, so uvicorn is PID 1 and receives SIGTERM directly. Without this
# a shell would swallow the signal and every restart would wait for Docker's
# ten second kill timeout.
#
# NOT --proxy-headers / --forwarded-allow-ips. Adsum reads X-Forwarded-For
# itself, through one helper governed by TRUSTED_PROXY_IPS, and the login
# throttle's per-address limit depends on that answer. Letting uvicorn
# rewrite the client address as well would give two mechanisms deciding the
# same thing, and "--forwarded-allow-ips *" would let anyone who can reach
# the port claim any address they like and so evade the throttle. Set
# TRUSTED_PROXY_IPS to your reverse proxy instead; docker-compose.yml shows
# where.
#
# One worker deliberately. SQLite with WAL tolerates concurrent readers, but
# this app also holds server-sent-event connections for the live roster and
# keeps per-process state (the login throttle counters), so a second worker
# would both split that state and contend for the write lock. One worker
# serves a campus comfortably; the QR endpoint is the only hot path and it
# is a signature check, not a query.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", "--log-level", "info"]
