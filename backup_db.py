"""Back up csu_checkin_v2.db using SQLite's online backup API. Keeps the last 14.

Run manually or by the scheduled task 'csu-checkin-backup' (daily).
"""
import datetime
import glob
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "csu_checkin_v2.db")
DEST_DIR = os.path.join(BASE, "backups")
KEEP = 14


def main() -> None:
    os.makedirs(DEST_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(DEST_DIR, f"csu_checkin-{stamp}.db")
    src = sqlite3.connect(SRC)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    backups = sorted(glob.glob(os.path.join(DEST_DIR, "csu_checkin-*.db")))
    for old in backups[:-KEEP]:
        os.remove(old)
    print(f"backed up to {dest} ({len(backups[-KEEP:])} kept)")


if __name__ == "__main__":
    main()
