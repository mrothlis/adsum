# Attendance Check-In — production image
FROM python:3.13-slim

WORKDIR /app

# Install runtime deps pinned to the versions verified in production
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code, plus the migration tooling so `alembic upgrade head` can be
# run inside the container when upgrading.
COPY app ./app
COPY run.py backup_db.py alembic.ini ./
COPY alembic ./alembic

# The database and backups are bind-mounted here by docker-compose.
RUN mkdir -p /app/data /app/backups

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]