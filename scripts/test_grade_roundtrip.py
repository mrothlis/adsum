#!/usr/bin/env python3
"""Grade-integrity checks: the export must not change a grade, and a round
trip through D2L must come back identical.

Why this file exists as its own suite
-------------------------------------
test_scoring.py proves the policy arithmetic in isolation, with no server and
no database. It passed throughout while two separate defects were silently
altering real grades, because neither lived in the arithmetic:

  1. All four grade-bearing queries counted sessions that had not ENDED yet.
     Opening check-in put a meeting in the denominator that nobody had been
     given the chance to attend, so exporting to the gradebook during a live
     class lowered everyone who had not yet scanned. On a three-session
     course, 100/50/50 exported as 67/50/50.

  2. restore-from-d2l grouped existing sessions into a dict keyed on the
     local date. Two meetings on one day is ordinary -- a lab and a lecture,
     a Monday double period -- and the ledger writes them as two tokens
     sharing a date. The dict collapsed them, the duplicate guard then
     skipped the second token, and the absence won.

Both are only observable end to end, which is why these checks drive a real
server over HTTP and read the actual CSV that D2L would import.

A trap worth recording: the first version of check 1 used the default policy,
where three absences are forgiven. Forgiveness absorbed the missing meeting,
so a broken restore and a correct one produced identical output and the test
passed against known-broken code. Every course here sets the allowance to 0
and late credit to 0 so that one absence moves the grade by a visible amount.
If a future edit reintroduces forgiveness into these fixtures, these checks
stop proving anything while still printing PASS.

Usage:  python scripts/test_grade_roundtrip.py
Env:    BASE_URL (default http://127.0.0.1:8000)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import pathlib
import re
import sqlite3
import sys

import requests

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "adsum.db"

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS ' if ok else 'FAIL '} {label}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))
    if not ok:
        failures.append(label)


def credentials() -> tuple[str, str]:
    path = ROOT / "CREDENTIALS.txt"
    if not path.exists():
        print("SKIP: no CREDENTIALS.txt, cannot sign in")
        sys.exit(0)
    email = password = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Email:"):
            email = line.split(":", 1)[1].strip()
        elif line.startswith("Password:"):
            password = line.split(":", 1)[1].strip()
    if not (email and password):
        print("SKIP: CREDENTIALS.txt has no Email:/Password: lines")
        sys.exit(0)
    return email, password


def sql(statements: list[tuple[str, tuple]]) -> None:
    """Direct writes, used only to build fixtures with exact timestamps."""
    db = sqlite3.connect(DB)
    db.execute("PRAGMA foreign_keys=ON")
    for stmt, args in statements:
        db.execute(stmt, args)
    db.commit()
    db.close()


def query(stmt: str, args: tuple = ()) -> list:
    db = sqlite3.connect(DB)
    rows = list(db.execute(stmt, args))
    db.close()
    return rows


def main() -> int:
    if not DB.exists():
        print(f"SKIP: {DB} not present, nothing to drive")
        return 0

    email, password = credentials()
    s = requests.Session()
    s.post(f"{BASE}/instructor/login",
           data={"email": email, "password": password}, timeout=30)

    created: list[int] = []

    def new_course(code: str, name: str) -> int:
        r = s.post(f"{BASE}/instructor/courses/new", timeout=30,
                   allow_redirects=True,
                   data={"name": name, "course_code": code})
        m = re.search(r"/instructor/course/(\d+)", r.url)
        if not m:
            raise SystemExit(f"could not create fixture course {code}")
        cid = int(m.group(1))
        created.append(cid)
        s.post(f"{BASE}/instructor/course/{cid}/import-roster", timeout=60,
               files={"roster_file": ("roster.csv", (
                   "Username,Last Name,First Name,End-of-Line Indicator\n"
                   "#rt1,One,Alpha,#\n#rt2,Two,Beta,#\n").encode(),
                   "text/csv")})
        # Allowance 0 and late credit 0: see the module docstring. Without
        # this the checks below pass against broken code.
        sql([("UPDATE courses SET allowed_unexcused_absences=0, "
              "late_credit_percent=0 WHERE id=?", (cid,))])
        return cid

    def students(cid: int) -> list[int]:
        return [r[0] for r in query(
            "SELECT s.id FROM students s "
            "JOIN course_enrollments e ON e.student_id = s.id "
            "WHERE e.course_id = ?", (cid,))]

    def closed_session(cid: int, days_ago: int, hour: int,
                       attendees: list[int]) -> None:
        start = (dt.datetime.now(dt.UTC).replace(tzinfo=None)
                 - dt.timedelta(days=days_ago)).replace(
                     hour=hour, minute=0, second=0, microsecond=0)
        db = sqlite3.connect(DB)
        db.execute("PRAGMA foreign_keys=ON")
        sid = db.execute(
            "INSERT INTO sessions (course_id, started_at, ended_at) "
            "VALUES (?,?,?)",
            (cid, start.isoformat(" "),
             (start + dt.timedelta(hours=1)).isoformat(" "))).lastrowid
        for stu in attendees:
            db.execute(
                "INSERT INTO attendance_records (session_id, student_id, "
                "checked_in_at, token_used, status) VALUES (?,?,?,'rtest',"
                "'present')", (sid, stu, start.isoformat(" ")))
        db.commit()
        db.close()

    def grades(cid: int) -> dict[str, str]:
        r = s.get(f"{BASE}/instructor/course/{cid}/export-d2l", timeout=60)
        rows = [x for x in csv.reader(io.StringIO(r.text)) if x]
        grade_col = next(i for i, h in enumerate(rows[0])
                         if "Points Grade" in h)
        return {row[0].lstrip("#"): row[grade_col] for row in rows[1:]}

    def ledger(cid: int, user: str) -> str:
        r = s.get(f"{BASE}/instructor/course/{cid}/export-d2l", timeout=60)
        rows = [x for x in csv.reader(io.StringIO(r.text)) if x]
        detail = next(i for i, h in enumerate(rows[0])
                      if "Attendance Detail" in h)
        for row in rows[1:]:
            if row[0].lstrip("#") == user:
                return row[detail]
        return ""

    try:
        print("Exporting during a live class must not change any grade")
        cid = new_course("ZZRT1", "Roundtrip: live session")
        roster = students(cid)
        closed_session(cid, 6, 9, roster)              # everyone present
        closed_session(cid, 4, 9, [roster[0]])         # beta absent
        before = grades(cid)
        check("beta sits at 50 before any session is opened",
              before.get("rt2"), "50")
        r = s.post(f"{BASE}/instructor/course/{cid}/start-session",
                   timeout=30, allow_redirects=False)
        live = int(r.headers["location"].rstrip("/").split("/")[-1])
        during = grades(cid)
        check("grades are identical while a session is open",
              during, before)

        # Closing it is a different matter, and the distinction is the point.
        # An OPEN session is a class in progress: nobody has been given the
        # chance to attend, so it must not count. A CLOSED session with no
        # attendance is a class that happened and that everyone missed, so it
        # must count. The first draft of this check asserted that grades
        # returned to baseline after stopping, which would have been a demand
        # that the app silently discard a real meeting.
        s.post(f"{BASE}/instructor/session/{live}/stop", timeout=30)
        after_stop = grades(cid)
        check("closing an empty session does count it against everyone",
              after_stop, {"rt1": "67", "rt2": "33"})

        # And deleting it returns the grades exactly, which is the instructor's
        # remedy for a session opened by mistake.
        s.post(f"{BASE}/instructor/session/{live}/delete", timeout=30,
               data={"confirm": "delete"})
        check("deleting that session restores the original grades",
              grades(cid), before)

        print("\nAn export is refused while every session is still open")
        # Asserted here rather than in smoke_test.py because it only holds on
        # a course with no previously closed sessions, and this fixture is
        # built fresh. A course whose only session is open has nothing final
        # to send: the file would change the moment the session closed.
        cid = new_course("ZZRT3", "Roundtrip: nothing closed yet")
        r = s.post(f"{BASE}/instructor/course/{cid}/start-session",
                   timeout=30, allow_redirects=False)
        open_sid = int(r.headers["location"].rstrip("/").split("/")[-1])
        r = s.get(f"{BASE}/instructor/course/{cid}/export-d2l", timeout=60)
        check("the export refuses with 409", r.status_code, 409)
        check("and explains that nothing has been closed yet",
              "none of them have been closed" in r.text, True)
        check("and does NOT claim the data was lost, which would be false",
              "was reset or lost" in r.text, False)
        s.post(f"{BASE}/instructor/session/{open_sid}/stop", timeout=30)
        r = s.get(f"{BASE}/instructor/course/{cid}/export-d2l", timeout=60)
        check("and succeeds once the session is closed", r.status_code, 200)

        print("\nThe QR endpoint tells a lapsed sign-in apart from a closed session")
        # The projector page erases the code and announces "This session is
        # closed" on a 404. The endpoint used to answer 404 for four separate
        # conditions, one of which was simply "not signed in", so an expired
        # cookie wiped the code off the wall mid-class and told the room the
        # session was over while it was wide open.
        cid = new_course("ZZRT4", "Roundtrip: qr status codes")
        r = s.post(f"{BASE}/instructor/course/{cid}/start-session",
                   timeout=30, allow_redirects=False)
        qr_sid = int(r.headers["location"].rstrip("/").split("/")[-1])
        check("signed in, on an open session: 200",
              s.get(f"{BASE}/api/qr/{qr_sid}", timeout=30).status_code, 200)
        check("NOT signed in: 401, never 404",
              requests.get(f"{BASE}/api/qr/{qr_sid}", timeout=30).status_code,
              401)
        check("a session that does not exist: 404",
              s.get(f"{BASE}/api/qr/99999999", timeout=30).status_code, 404)
        s.post(f"{BASE}/instructor/session/{qr_sid}/stop", timeout=30)
        check("a genuinely closed session: 410",
              s.get(f"{BASE}/api/qr/{qr_sid}", timeout=30).status_code, 410)

        print("\nTwo meetings on one date survive an export/restore round trip")
        cid = new_course("ZZRT2", "Roundtrip: repeated date")
        roster = students(cid)
        closed_session(cid, 5, 9, roster)              # morning: both there
        closed_session(cid, 5, 14, [roster[0]])        # afternoon: beta out
        before = grades(cid)
        detail = ledger(cid, "rt2")
        tokens = re.findall(r"\d\d/\d\d/\d\d:[PLAE]", detail)
        check("beta's ledger carries two tokens for the one date",
              len(tokens), 2)
        check("and they disagree, so the restore has to keep both",
              len({t.split(':')[1] for t in tokens}), 2)
        check("beta sits at 50, so an absence is visible in the grade",
              before.get("rt2"), "50")

        export = s.get(f"{BASE}/instructor/course/{cid}/export-d2l",
                       timeout=60).text.encode()
        sql([("DELETE FROM attendance_records WHERE session_id IN "
              "(SELECT id FROM sessions WHERE course_id=?)", (cid,)),
             ("DELETE FROM sessions WHERE course_id=?", (cid,))])
        check("the wipe really emptied the course",
              query("SELECT COUNT(*) FROM sessions WHERE course_id=?",
                    (cid,))[0][0], 0)
        s.post(f"{BASE}/instructor/course/{cid}/restore-from-d2l", timeout=60,
               files={"grades_file": ("grades.csv", export, "text/csv")})
        check("both meetings were rebuilt, not collapsed into one",
              query("SELECT COUNT(*) FROM sessions WHERE course_id=?",
                    (cid,))[0][0], 2)
        check("every grade came back unchanged", grades(cid), before)

    finally:
        for cid in created:
            s.post(f"{BASE}/instructor/course/{cid}/delete",
                   data={"confirm": "delete"}, timeout=30)
        print(f"\ncleaned up {len(created)} fixture course(s)")

    print("-" * 56)
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
