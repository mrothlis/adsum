"""Apply pending database migrations at startup.

Why this exists: updating Adsum used to mean remembering to run
`alembic upgrade head` by hand. Forget it and the app starts happily against
the old schema, then throws 500s on whichever page touches a column that was
never added -- which might be days later, in front of a class. The upgrade is
not optional, so it should not be a thing a person has to remember.

Three rules shape everything here:

1. **Back up before changing the schema, never after.** The backup is the only
   way out of a migration that goes wrong.
2. **Refuse to serve on a schema we could not bring up to date.** Serving with
   a known-wrong schema is worse than not serving: it fails in scattered,
   confusing ways instead of one obvious one.
3. **Never do destructive work in a retry loop.** The Windows launcher
   restarts uvicorn every 10 seconds forever. A naive "back up, then migrate"
   would take a fresh backup on every single retry and, with BACKUP_KEEP at
   14, would prune every good backup within a couple of minutes -- destroying
   the one thing rule 1 exists to protect. Hence the failure marker: a
   migration that has already failed is not retried, and takes no new backup,
   until a human has looked at it.
"""
import logging
import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect

from app.config import settings

log = logging.getLogger("adsum.migrate")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_dir() -> str:
    """Where Adsum writes state that is not the database itself.

    Defaults to the application directory, which is what every existing
    install expects. STATE_DIR overrides it, and must be set when the
    application directory is not writable: a container image is read-only
    and only its data volume can be written to.

    This matters more than a path usually would. The marker below is what
    stops a failed migration being retried forever, and if writing it raises
    then the safety mechanism is the thing that breaks.
    """
    configured = os.getenv("STATE_DIR")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return BASE_DIR


# Records a migration that failed, so the restart loop stops trying it. Holds
# the revision pair, so a different pending migration later is still attempted.
FAILED_MARKER = os.path.join(_state_dir(), ".migration-failed")


def _sync_url() -> str:
    """The DATABASE_URL with the async driver stripped.

    Alembic runs synchronously, and the app's URL names an async driver.
    """
    return settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "")


def schema_state() -> tuple[bool, str | None, str | None]:
    """(tables_exist, current_revision, head_revision).

    Uses its own short-lived engine, deliberately. Inspecting the connection
    Alembic is about to migrate leaves an implicit transaction open, and the
    version update is then rolled back: the log says "Running upgrade" while
    alembic_version never moves. That cost hours once already.
    """
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(BASE_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BASE_DIR, "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()

    engine = create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            tables_exist = bool(inspect(conn).get_table_names())
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    return tables_exist, current, head


def _stamp(revision: str) -> None:
    """Record a revision without running anything, on its own connection."""
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(BASE_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BASE_DIR, "alembic"))
    engine = create_engine(_sync_url())
    try:
        with engine.begin() as conn:
            MigrationContext.configure(conn).stamp(
                ScriptDirectory.from_config(cfg), revision)
    finally:
        engine.dispose()


def _backup() -> str:
    """Snapshot the database, returning the path. Raises on failure."""
    sys.path.insert(0, BASE_DIR)  # backup_db.py lives at the repo root
    try:
        import backup_db
        return backup_db.backup_now()
    finally:
        if sys.path and sys.path[0] == BASE_DIR:
            sys.path.pop(0)


def _run_upgrade() -> None:
    """`alembic upgrade head` in a subprocess. Raises on a non-zero exit.

    A subprocess rather than `command.upgrade` in-process: it is exactly what
    a human would run, it cannot interfere with the app's own engine or event
    loop, and its stdout goes to the log as-is for diagnosis.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BASE_DIR, capture_output=True, text=True, timeout=300, check=False)
    for line in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines():
        if line.strip():
            log.info("alembic: %s", line.rstrip())
    if proc.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (exit {proc.returncode}). "
            f"{(proc.stderr or proc.stdout or '').strip()[-500:]}")


def apply_pending() -> None:
    """Bring the database schema up to head, or refuse to start.

    Called before anything else touches the database.
    """
    if not settings.AUTO_MIGRATE:
        log.info("AUTO_MIGRATE is off; not touching the schema. Run "
                 "'alembic upgrade head' yourself after an update.")
        return
    if not settings.DATABASE_URL.startswith("sqlite"):
        # The backup step is SQLite's online backup API. Without a backup
        # this would be an unguarded schema change, so leave other engines to
        # their own tooling and migration step.
        log.info("Automatic migration is SQLite-only (it backs up first). "
                 "Run 'alembic upgrade head' as part of your deploy.")
        return

    tables_exist, current, head = schema_state()
    if head is None:
        # No migration scripts at all. Nothing to apply, and stamping a None
        # revision would raise rather than doing nothing.
        log.warning("no migration scripts found; leaving the schema alone")
        return

    if not tables_exist:
        # Brand-new database. Nothing to back up, and alembic/env.py builds
        # the schema and stamps head rather than replaying history.
        log.info("empty database: building schema at %s", head)
        _run_upgrade()
        return

    if current is None:
        # An install that predates migration tracking. Its schema is assumed
        # to match head -- the long-standing behaviour -- so record that
        # rather than replaying migrations over an existing schema.
        log.info("existing schema with no migration version: stamping %s", head)
        _stamp(head)
        return

    if current == head:
        log.debug("schema is current (%s)", current)
        return

    pending = f"{current}->{head}"
    if os.path.exists(FAILED_MARKER):
        with open(FAILED_MARKER, encoding="utf-8") as fh:
            failed = fh.read().strip()
        if failed == pending:
            raise RuntimeError(
                f"Migration {pending} failed on an earlier start and has not "
                f"been retried. Look at server.log, fix the cause, then "
                f"delete {FAILED_MARKER} to try again. Your database is "
                f"untouched and a backup was taken before the first attempt."
            )

    log.warning("schema is behind: %s. Backing up, then migrating.", pending)
    backup = _backup()
    log.warning("backed up to %s", backup)
    try:
        _run_upgrade()
    except Exception as exc:
        with open(FAILED_MARKER, "w", encoding="utf-8") as fh:
            fh.write(pending)
        log.error("migration %s FAILED: %s", pending, exc)
        log.error("the database is as it was; restore from %s if needed", backup)
        raise
    if os.path.exists(FAILED_MARKER):
        os.remove(FAILED_MARKER)
    _, now_at, _ = schema_state()
    if now_at != head:
        # The log can say "Running upgrade" while the version never moves.
        # Check the recorded revision rather than trusting the exit code.
        raise RuntimeError(
            f"alembic reported success but the schema is still at {now_at}, "
            f"not {head}. Restore from {backup} and investigate.")
    log.warning("schema migrated to %s", head)
