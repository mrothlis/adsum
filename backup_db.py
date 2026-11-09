"""Back up the Adsum database using SQLite's online backup API.

Safe to run while the app is serving: the online backup API takes a
consistent snapshot without locking writers out. Keeps the most recent
BACKUP_KEEP copies in BACKUP_DIR (default: backups/ next to this file).

Set BACKUP_DIR when the application directory is not writable, which is the
normal case in a container: the image is read-only and the database lives on
a mounted volume, so the backups have to land on that volume too. Pointing it
at a different disk is also the only way a backup survives losing the one the
database is on.

Run manually, or on a schedule (cron / Task Scheduler) -- see docs/INSTALL.md.

The database location comes from DATABASE_URL, so this works on any install
rather than only the one it was written on.
"""
import datetime
import glob
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
KEEP = int(os.getenv("BACKUP_KEEP", "14"))


def _from_env_file(key: str, default: str = "") -> str:
    """Read a key from .env without requiring python-dotenv."""
    try:
        with open(os.path.join(BASE, ".env"), encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def backup_dir() -> str:
    """Where backups are written.

    BACKUP_DIR wins, then the .env file, then backups/ next to this script.
    Resolved through the same .env reader as DATABASE_URL so a scheduled run
    with a bare environment behaves like a manual one.
    """
    configured = os.getenv("BACKUP_DIR") or _from_env_file("BACKUP_DIR", "")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(BASE, "backups")


DEST_DIR = backup_dir()


def database_path() -> str:
    """Resolve the SQLite file from DATABASE_URL."""
    url = os.getenv("DATABASE_URL") or _from_env_file(
        "DATABASE_URL", "sqlite+aiosqlite:///./adsum.db")
    if not url.startswith("sqlite"):
        raise SystemExit(
            f"This script only backs up SQLite. DATABASE_URL is {url.split('://')[0]}://..., "
            "so use that engine's own backup tool (for PostgreSQL, pg_dump)."
        )
    path = url.split("///")[-1] if "///" in url else url
    if not os.path.isabs(path):
        path = os.path.join(BASE, path.lstrip("./"))
    return os.path.normpath(path)


def backup_now() -> str:
    """Take a backup and return its path. Importable by the app at startup.

    Separate from main() so the app can back up before migrating without
    shelling out, while this file stays importable with nothing but the
    standard library -- it must keep working as a bare cron/Task Scheduler
    script on an install that has no virtualenv activated.
    """
    src_path = database_path()
    if not os.path.exists(src_path):
        raise SystemExit(f"No database at {src_path}. Start the app once to create it.")

    os.makedirs(DEST_DIR, exist_ok=True)
    # Name backups after the database itself, so several installs can share a
    # directory and the pruning glob below never deletes a sibling's copies.
    stem = os.path.splitext(os.path.basename(src_path))[0]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(DEST_DIR, f"{stem}-{stamp}.db")

    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()

    backups = sorted(glob.glob(os.path.join(DEST_DIR, f"{stem}-*.db")))
    for old in backups[:-KEEP]:
        os.remove(old)
    return dest


def main() -> None:
    dest = backup_now()
    stem = os.path.splitext(os.path.basename(database_path()))[0]
    kept = sorted(glob.glob(os.path.join(DEST_DIR, f"{stem}-*.db")))
    print(f"backed up to {dest} ({len(kept)} kept)")


if __name__ == "__main__":
    sys.exit(main())
