"""Startup migration safety: does the app bring its own schema up to date?

Covers the four paths through app/migrate.apply_pending(), each in its own
subprocess against its own throwaway database, so the real one is never
touched:

  1. empty database        -> schema built and stamped at head
  2. already at head       -> no-op, and crucially NO backup taken
  3. tables but no version -> stamped, migrations not replayed
  4. a migration that fails -> backup taken, refuses to start, and does NOT
     retry or re-backup on the next start

Case 4 is the one that matters most. The Windows launcher restarts uvicorn
every 10 seconds forever, so a migration that both fails and takes a backup
on every attempt would prune every good backup within minutes -- destroying
the thing the backup exists to protect.

No server required.
"""
import glob
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(BASE, "backups")
MARKER = os.path.join(BASE, ".migration-failed")

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_startup(db_path: str):
    """Run apply_pending() in a fresh process against db_path."""
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite+aiosqlite:///" + db_path.replace("\\", "/")
    env["AUTO_MIGRATE"] = "true"
    return subprocess.run(
        [sys.executable, "-c",
         "import logging,sys;logging.basicConfig(level=logging.INFO,stream=sys.stdout);"
         "from app.migrate import apply_pending; apply_pending()"],
        cwd=BASE, env=env, capture_output=True, text=True, timeout=300,
        check=False)


def version_of(db_path: str):
    if not os.path.exists(db_path):
        return None
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def table_count(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        con.close()


def backups_for(db_path: str) -> list[str]:
    stem = os.path.splitext(os.path.basename(db_path))[0]
    return sorted(glob.glob(os.path.join(BACKUPS, f"{stem}-*.db")))


def head_revision() -> str:
    out = subprocess.run([sys.executable, "-c",
                          "from app.migrate import schema_state; print(schema_state()[2])"],
                         cwd=BASE, capture_output=True, text=True, check=False)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""


def main() -> int:
    if os.path.exists(MARKER):
        os.remove(MARKER)

    # Whatever database this install is configured to use, if it exists. Every
    # case below works on a throwaway copy, and this is the guard that proves
    # it: its revision must be identical at the end.
    guarded_db = None
    guarded_revision = None
    sys.path.insert(0, BASE)
    try:
        from backup_db import database_path
        candidate = database_path()
        if os.path.exists(candidate):
            guarded_db = candidate
            guarded_revision = version_of(candidate)
            print(f"guarding {os.path.basename(candidate)} "
                  f"(revision {guarded_revision})\n")
    except SystemExit:
        pass  # non-SQLite DATABASE_URL; nothing local to guard
    finally:
        if sys.path and sys.path[0] == BASE:
            sys.path.pop(0)

    tmp = tempfile.mkdtemp(prefix="adsum-mig-")
    # A path native Python can see; mktemp-style MSYS paths are invisible to
    # a native interpreter, and this file is handed to a subprocess.
    try:
        print("Case 1: an empty database is built, not migrated")
        db1 = os.path.join(tmp, "case1.db")
        r = run_startup(db1)
        check("startup succeeds on an empty database", r.returncode == 0,
              (r.stderr or "")[-300:])
        check("the schema was created", os.path.exists(db1) and table_count(db1) > 5,
              str(table_count(db1) if os.path.exists(db1) else "no file"))
        head = version_of(db1)
        check("and stamped at a revision", head is not None, str(head))
        check("no backup was taken (there was nothing to lose)",
              len(backups_for(db1)) == 0, str(backups_for(db1)))

        print("\nCase 2: a database already at head is left alone")
        before = os.path.getmtime(db1)
        r = run_startup(db1)
        check("startup succeeds", r.returncode == 0, (r.stderr or "")[-300:])
        check("the revision did not move", version_of(db1) == head)
        check("NO backup was taken on the fast path",
              len(backups_for(db1)) == 0, str(backups_for(db1)))
        check("and it did not rewrite the database", os.path.getmtime(db1) == before)

        print("\nCase 3: an old install with tables but no version is stamped")
        db3 = os.path.join(tmp, "case3.db")
        shutil.copy(db1, db3)
        con = sqlite3.connect(db3)
        con.execute("DELETE FROM alembic_version")
        con.commit()
        con.close()
        check("precondition: no version recorded", version_of(db3) is None)
        tables_before = table_count(db3)
        r = run_startup(db3)
        check("startup succeeds", r.returncode == 0, (r.stderr or "")[-300:])
        check("it is stamped at head rather than replaying history",
              version_of(db3) == head, str(version_of(db3)))
        check("the schema was not rebuilt", table_count(db3) == tables_before)
        check("no backup was taken for a stamp", len(backups_for(db3)) == 0)

        print("\nCase 4: a failing migration backs up once, then refuses")
        db4 = os.path.join(tmp, "case4.db")
        shutil.copy(db1, db4)
        # A revision alembic cannot resolve: the upgrade will fail, which is
        # what a genuinely broken migration looks like from here.
        con = sqlite3.connect(db4)
        con.execute("UPDATE alembic_version SET version_num='0000deadbeef'")
        con.commit()
        con.close()
        r = run_startup(db4)
        check("startup FAILS rather than serving a wrong schema",
              r.returncode != 0, str(r.returncode))
        first = backups_for(db4)
        check("a backup was taken before attempting it", len(first) == 1,
              f"{len(first)} backups")
        check("the failure is recorded", os.path.exists(MARKER))
        check("the database itself was not altered",
              version_of(db4) == "0000deadbeef", str(version_of(db4)))

        r2 = run_startup(db4)
        out2 = (r2.stdout or "") + (r2.stderr or "")
        check("the next start also refuses", r2.returncode != 0, str(r2.returncode))
        check("it did NOT take a second backup (the restart-loop trap)",
              backups_for(db4) == first, f"{len(backups_for(db4))} backups now")
        check("and it says a human has to look at it",
              "has not been retried" in out2 or "delete" in out2.lower())

        print("\nCase 5: a database behind head is backed up, then migrated")
        tree = os.path.join(tmp, "tree")
        os.makedirs(tree)
        for item in ("app", "alembic", "alembic.ini", "backup_db.py", ".env"):
            src = os.path.join(BASE, item)
            if not os.path.exists(src):
                continue
            dst = os.path.join(tree, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst,
                                ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy(src, dst)

        db6 = os.path.join(tree, "case6.db")
        shutil.copy(db1, db6)
        # A real pending migration, in the copied tree only. Deliberately a
        # column on an existing table: that is the case create_all() cannot
        # fix and the whole feature exists for.
        new_rev = "ffff00000001"
        with open(os.path.join(tree, "alembic", "versions",
                               f"{new_rev}_probe.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(
                '"""probe migration (test only)"""\n'
                "import sqlalchemy as sa\n"
                "from alembic import op\n\n"
                f'revision = "{new_rev}"\n'
                f'down_revision = "{head}"\n'
                "branch_labels = None\n"
                "depends_on = None\n\n\n"
                "def upgrade():\n"
                '    op.add_column("courses", sa.Column("probe_col", sa.String(8)))\n\n\n'
                "def downgrade():\n"
                '    op.drop_column("courses", "probe_col")\n')

        env6 = dict(os.environ)
        env6["DATABASE_URL"] = "sqlite+aiosqlite:///" + db6.replace("\\", "/")
        env6["AUTO_MIGRATE"] = "true"
        r = subprocess.run(
            [sys.executable, "-c",
             "import logging,sys;logging.basicConfig(level=logging.INFO,stream=sys.stdout);"
             "from app.migrate import apply_pending; apply_pending()"],
            cwd=tree, env=env6, capture_output=True, text=True, timeout=300,
            check=False)
        out6 = (r.stdout or "") + (r.stderr or "")
        check("startup succeeds with a real pending migration",
              r.returncode == 0, out6[-400:])
        check("the revision advanced to the new head",
              version_of(db6) == new_rev, str(version_of(db6)))
        con = sqlite3.connect(db6)
        cols = [c[1] for c in con.execute("PRAGMA table_info(courses)")]
        con.close()
        check("the new column really exists", "probe_col" in cols)
        tree_backups = sorted(glob.glob(
            os.path.join(tree, "backups", "case6-*.db")))
        check("a backup was taken BEFORE migrating", len(tree_backups) == 1,
              f"{len(tree_backups)} backups")
        if tree_backups:
            con = sqlite3.connect(tree_backups[0])
            bcols = [c[1] for c in con.execute("PRAGMA table_info(courses)")]
            bver = con.execute(
                "SELECT version_num FROM alembic_version").fetchone()[0]
            con.close()
            check("and the backup is the PRE-migration state",
                  "probe_col" not in bcols and bver == head,
                  f"version {bver}, probe_col present: {'probe_col' in bcols}")
        check("no failure marker was left", not os.path.exists(
            os.path.join(tree, ".migration-failed")))
        check("the real alembic/versions was not touched",
              not os.path.exists(os.path.join(
                  BASE, "alembic", "versions", f"{new_rev}_probe.py")))
        # The configured database must be exactly as it was. Compared against
        # its own earlier revision, not against head: there is no configured
        # database at all in CI, and asserting it equals head would fail for
        # a reason that has nothing to do with migrations.
        if guarded_db:
            check("and the configured database was not touched",
                  version_of(guarded_db) == guarded_revision,
                  f"{guarded_revision} -> {version_of(guarded_db)}")

        print("\nAUTO_MIGRATE=false is honoured")
        db5 = os.path.join(tmp, "case5.db")
        shutil.copy(db4, db5)
        env = dict(os.environ)
        env["DATABASE_URL"] = "sqlite+aiosqlite:///" + db5.replace("\\", "/")
        env["AUTO_MIGRATE"] = "false"
        r = subprocess.run(
            [sys.executable, "-c",
             "from app.migrate import apply_pending; apply_pending()"],
            cwd=BASE, env=env, capture_output=True, text=True, check=False)
        check("it starts without touching a stale schema", r.returncode == 0,
              (r.stderr or "")[-200:])
        check("the revision is untouched", version_of(db5) == "0000deadbeef")
        check("no backup taken", len(backups_for(db5)) == 0)

        print("\nCase 6: the duplicate-instructor migration carries its data")
        # This case exists because the fix it covers is a data-carrying
        # migration, and those can only ever run against a database that
        # already exists. A subtlety makes that easy to get wrong when
        # testing: an EMPTY database is built from the current models and
        # stamped at head, so `alembic upgrade <an older revision>` on a
        # fresh file is silently ignored and lands at head with every index
        # already present. A probe written that way reports "the migration
        # did nothing" and it is right, because there was nothing to do.
        #
        # So the older state has to be constructed deliberately: drop the
        # index AND wind the version row back.
        db6 = os.path.join(tmp, "case6.db")
        env6 = dict(env)
        env6["DATABASE_URL"] = f"sqlite+aiosqlite:///{db6.replace(os.sep, '/')}"
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=BASE, env=env6, capture_output=True, text=True,
                       check=False)
        con = sqlite3.connect(db6)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DROP INDEX IF EXISTS "
                    "uq_enrollments_one_instructor_row_per_course")
        con.execute("UPDATE alembic_version SET version_num='a7f3c1d05b84'")
        con.commit()

        def seed(table, **given):
            """Fill NOT NULL columns that have no default, so this does not
            depend on remembering the whole schema."""
            row = dict(given)
            for _c, name, ctype, notnull, dflt, pk in con.execute(
                    f"PRAGMA table_info({table})"):
                if name in row or pk or not notnull or dflt is not None:
                    continue
                t = (ctype or "").upper()
                row[name] = 0 if any(k in t for k in
                                     ("INT", "REAL", "NUM", "FLOAT")) else "x"
            con.execute(
                f"INSERT INTO {table} ({','.join(row)}) "
                f"VALUES ({','.join('?' * len(row))})", tuple(row.values()))

        seed("institutions", id=1, name="T")
        seed("instructors", id=1, name="A", email="a@x.test")
        seed("courses", id=1, institution_id=1, name="C", course_code="C1")
        for _ in range(3):
            seed("course_enrollments", course_id=1, instructor_id=1,
                 role="instructor")
        seed("students", id=1, name="S")
        seed("course_enrollments", course_id=1, student_id=1, role="student")
        con.commit()
        dupes_before = con.execute(
            "SELECT COUNT(*) FROM course_enrollments "
            "WHERE instructor_id IS NOT NULL").fetchone()[0]
        con.close()
        # Three identical rows were accepted, which is the defect itself:
        # uq_course_student(course_id, student_id) looks like it constrains
        # this table but an instructor row has student_id NULL, and every
        # NULL is distinct in a SQLite unique index.
        check("duplicate instructor rows are possible before the migration",
              dupes_before == 3, f"got {dupes_before}")

        # Upgrade to THIS revision, not to head. Winding the version row back
        # and running "upgrade head" replays every later migration too, and
        # those run against a schema that was built from the CURRENT models
        # and so is already in its final shape. A later migration that drops
        # a column therefore fails with KeyError on a column that was never
        # created. Targeting the one revision under test is both narrower and
        # the only thing that makes sense here.
        r = subprocess.run([sys.executable, "-m", "alembic", "upgrade",
                            "e5a91c3f74b2"],
                           cwd=BASE, env=env6, capture_output=True, text=True,
                           check=False)
        check("the migration applies", r.returncode == 0,
              (r.stderr or "")[-200:])
        check("and says what it removed",
              "removing 2 duplicate instructor enrollment" in
              (r.stdout or "") + (r.stderr or ""))

        con = sqlite3.connect(db6)
        con.execute("PRAGMA foreign_keys=ON")
        rows = con.execute(
            "SELECT id FROM course_enrollments "
            "WHERE instructor_id IS NOT NULL").fetchall()
        check("exactly one instructor row survives", len(rows) == 1,
              f"got {rows}")
        check("and it is the lowest id, which holds any dropped_at history",
              bool(rows) and rows[0][0] == 1, f"got {rows}")
        check("the student enrollment is untouched",
              con.execute("SELECT COUNT(*) FROM course_enrollments "
                          "WHERE student_id IS NOT NULL").fetchone()[0] == 1)
        try:
            con.execute("INSERT INTO course_enrollments "
                        "(course_id, instructor_id, role) "
                        "VALUES (1, 1, 'instructor')")
            con.commit()
            check("a duplicate is now refused", False, "it was accepted")
        except sqlite3.IntegrityError:
            check("a duplicate is now refused", True)
        # A second instructor on the same course is legitimate co-teaching
        # and must still work: the index is on the pair, not on course_id.
        seed("instructors", id=2, name="B", email="b@x.test")
        try:
            con.execute("INSERT INTO course_enrollments "
                        "(course_id, instructor_id, role) "
                        "VALUES (1, 2, 'instructor')")
            con.commit()
            check("a co-instructor on the same course is still allowed", True)
        except sqlite3.IntegrityError as exc:
            check("a co-instructor on the same course is still allowed",
                  False, str(exc))
        con.close()

        r = subprocess.run([sys.executable, "-m", "alembic", "downgrade",
                            "a7f3c1d05b84"], cwd=BASE, env=env6,
                           capture_output=True, text=True, check=False)
        check("the downgrade is clean", r.returncode == 0,
              (r.stderr or "")[-200:])

        print("-" * 52)
        for leftover in (backups_for(db1) + backups_for(db3)
                         + backups_for(db4) + backups_for(db5)):
            os.remove(leftover)
        if os.path.exists(MARKER):
            os.remove(MARKER)
        if failures:
            print(f"{len(failures)} FAILED: {failures}")
            return 1
        print("ALL CHECKS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
