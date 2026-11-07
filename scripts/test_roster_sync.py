#!/usr/bin/env python3
"""Roster reconciliation: adds, drops, re-adds, and the wrong-file guard.

The classlist import used to be additive only. It counted new and updated
students and never noticed that somebody had disappeared from the classlist,
so a student who dropped stayed enrolled forever, kept accruing absences, and
kept appearing in the D2L gradebook export. Every check here pins one part of
the fix, and two of them are the ones that protect real grades:

  * a wrong file (a filtered export, another section, last term's) must
    change NOTHING rather than quietly mark the class dropped, and
  * a dropped student must not appear in the D2L export, because D2L rejects
    the row as an unrecognised user, nor in any denominator.

Runs against a live instance. It builds its own throwaway course, students,
sessions and attendance rather than touching a real roster -- these checks
mark students as dropped, which is not something to do to a real class -- and
removes all of it in a finally.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import pathlib
import re
import secrets
import sqlite3
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _from_env_file(key: str, default: str = "") -> str:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return default


def _db_path() -> str:
    """The SQLite file, with the environment variable taking precedence.

    CI has no .env at all, and a test that reads only the file passed
    locally and failed there.
    """
    url = os.getenv("DATABASE_URL") or _from_env_file(
        "DATABASE_URL", "sqlite+aiosqlite:///./adsum.db")
    path = url.split("///")[-1] if "///" in url else url
    if not os.path.isabs(path):
        cand = os.path.join(str(ROOT), path.lstrip("./"))
        path = cand if os.path.exists(cand) else os.path.abspath(path.lstrip("./"))
    return path


BASE_URL = (os.getenv("BASE_URL") or _from_env_file("BASE_URL")
            or "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") or _from_env_file("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    try:
        for line in open(ROOT / "CREDENTIALS.txt", encoding="utf-8"):
            if line.startswith("Password:"):
                ADMIN_PASSWORD = line.split(":", 1)[1].strip()
    except OSError:
        pass
ADMIN_PASSWORD = ADMIN_PASSWORD or _from_env_file("ADMIN_PASSWORD")

# Must match app/routes/instructor.py. Deliberately duplicated rather than
# imported: the point is to pin the published behaviour, so a change to the
# constant should make somebody look at this file too.
MAX_DROP_FRACTION = 0.5
STALE_AFTER_DAYS = 7

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  -> {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def db():
    return sqlite3.connect(_db_path(), timeout=20)


def login(email: str, password: str):
    r = requests.post(f"{BASE_URL}/instructor/login",
                      data={"email": email, "password": password},
                      allow_redirects=False, timeout=30)
    tok = r.cookies.get("adsum_session")
    return {"adsum_session": tok} if tok else None


# ── The throwaway course ────────────────────────────────────

TAG = secrets.token_hex(3)
SID_PREFIX = f"rs{TAG}"


def sid(n: int) -> str:
    return f"{SID_PREFIX}{n}"


def uname(n: int) -> str:
    return f"u{SID_PREFIX}{n}"


def sname(n: int) -> str:
    return f"Rsync{TAG}{n} Testcase"


def classlist_csv(indexes, names=None, usernames=None) -> str:
    """A D2L-shaped Classlist export for the given student indexes."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["OrgDefinedId", "Username", "Last Name", "First Name",
                "Email", "Role"])
    for n in indexes:
        full = (names or {}).get(n, sname(n))
        first, _, last = full.partition(" ")
        w.writerow([sid(n), (usernames or {}).get(n, uname(n)), last, first,
                    f"{uname(n)}@students.example.edu", "Learner"])
    return buf.getvalue()


def post_classlist(cookies, course_id: int, csv_text: str, confirm: bool = False):
    data = {"confirm_drops": "drop"} if confirm else None
    return requests.post(
        f"{BASE_URL}/instructor/course/{course_id}/import-roster",
        cookies=cookies, timeout=30, allow_redirects=False, data=data,
        files={"roster_file": ("classlist.csv", csv_text, "text/csv")})


def setup(instructor_id: int) -> tuple[int, list[int]]:
    """Create the course, six students, two past sessions and some attendance."""
    con = db()
    inst_row = con.execute("SELECT id FROM institutions LIMIT 1").fetchone()
    institution_id = inst_row[0] if inst_row else None
    cur = con.execute(
        "INSERT INTO courses (institution_id, name, course_code, "
        "geofence_radius_m, geofence_enabled) VALUES (?,?,?,?,0)",
        (institution_id, f"Roster Sync Probe {TAG}", f"RSYNC{TAG}", 75.0))
    course_id = cur.lastrowid
    con.execute("INSERT INTO course_enrollments (course_id, instructor_id, role) "
                "VALUES (?,?,'instructor')", (course_id, instructor_id))
    student_ids = []
    for n in range(6):
        c = con.execute(
            "INSERT INTO students (name, email, student_id_number, d2l_user_id, "
            "d2l_username, d2l_verified) VALUES (?,?,?,?,?,1)",
            (sname(n), f"{uname(n)}@students.example.edu", sid(n), sid(n),
             uname(n)))
        student_ids.append(c.lastrowid)
        con.execute("INSERT INTO course_enrollments (course_id, student_id, role) "
                    "VALUES (?,?,'student')", (course_id, c.lastrowid))
    # Four finished sessions, so there are denominators and at-risk figures.
    # Four rather than two because an early alert needs either a sub-75% grade
    # or three consecutive absences, and with the default forgiveness a
    # student who missed two classes still grades at 100%.
    base = dt.datetime.utcnow() - dt.timedelta(days=9)
    session_ids = []
    for day in (0, 2, 4, 6):
        started = base + dt.timedelta(days=day)
        c = con.execute(
            "INSERT INTO sessions (course_id, started_at, ended_at) VALUES (?,?,?)",
            (course_id, started.strftime("%Y-%m-%d %H:%M:%S"),
             (started + dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")))
        session_ids.append(c.lastrowid)
    # Student 0 attended all four. Student 5 -- the one the second import
    # drops -- attended only the first, so they carry real accumulated
    # attendance and would otherwise raise an early alert.
    for s_idx, sessions in ((0, session_ids), (5, session_ids[:1])):
        for session_id in sessions:
            con.execute(
                "INSERT INTO attendance_records (session_id, student_id, "
                "checked_in_at, token_used, status) VALUES (?,?,?, 'manual', 'present')",
                (session_id, student_ids[s_idx],
                 dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()
    return course_id, student_ids


def teardown(course_id: int) -> None:
    con = db()
    con.execute(
        "DELETE FROM attendance_records WHERE session_id IN "
        "(SELECT id FROM sessions WHERE course_id=?)", (course_id,))
    con.execute("DELETE FROM attendance_records WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{SID_PREFIX}%",))
    con.execute("DELETE FROM sessions WHERE course_id=?", (course_id,))
    con.execute("DELETE FROM course_enrollments WHERE course_id=?", (course_id,))
    con.execute("DELETE FROM course_enrollments WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{SID_PREFIX}%",))
    con.execute("DELETE FROM students WHERE student_id_number LIKE ?",
                (f"{SID_PREFIX}%",))
    con.execute("DELETE FROM courses WHERE id=?", (course_id,))
    con.commit()
    con.close()


def roster_state(course_id: int) -> dict:
    """student_id_number -> dropped_at, for this course only."""
    con = db()
    rows = con.execute(
        "SELECT s.student_id_number, e.dropped_at FROM course_enrollments e "
        "JOIN students s ON s.id = e.student_id WHERE e.course_id=?",
        (course_id,)).fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}


def stored_summary(course_id: int) -> dict:
    con = db()
    row = con.execute("SELECT roster_last_import_summary, roster_last_imported_at "
                      "FROM courses WHERE id=?", (course_id,)).fetchone()
    con.close()
    data = json.loads(row[0]) if row and row[0] else {}
    data["_imported_at"] = row[1] if row else None
    return data


def set_last_import(course_id: int, value) -> None:
    con = db()
    con.execute("UPDATE courses SET roster_last_imported_at=? WHERE id=?",
                (value, course_id))
    con.commit()
    con.close()


def main() -> int:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("ADMIN_EMAIL / ADMIN_PASSWORD not available; cannot run.")
        return 1
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not admin:
        print("Could not sign in as the admin.")
        return 1
    con = db()
    row = con.execute("SELECT id FROM instructors WHERE email=?",
                      (ADMIN_EMAIL,)).fetchone()
    con.close()
    if not row:
        print("The admin account is not in the database; cannot run.")
        return 1
    instructor_id = row[0]

    course_id, student_ids = setup(instructor_id)
    try:
        return run_checks(admin, course_id, student_ids)
    finally:
        teardown(course_id)
        print(f"\nThrowaway course {course_id} and its students removed.")


def run_checks(admin, course_id: int, student_ids: list[int]) -> int:  # noqa: PLR0915
    course_url = f"{BASE_URL}/instructor/course/{course_id}"
    all_six = list(range(6))

    print("An import that matches the roster reports no changes and records the date")
    r = post_classlist(admin, course_id, classlist_csv(all_six))
    check("a matching classlist is accepted", r.status_code in (302, 303),
          str(r.status_code))
    s = stored_summary(course_id)
    check("it is recorded as an import", bool(s.get("_imported_at")), str(s))
    check("nobody was added", s.get("added") == [], str(s.get("added")))
    check("nobody was dropped", s.get("dropped") == [], str(s.get("dropped")))
    check("all six matched unchanged", s.get("unchanged") == 6, str(s.get("unchanged")))
    body = requests.get(course_url, cookies=admin, timeout=30).text
    check("the course page shows a standing import summary",
          "Last classlist import" in body)
    check("the summary is permanent, not auto-dismissed",
          "alert-permanent" in body)

    print("\nA student missing from the classlist is marked dropped, by name")
    r = post_classlist(admin, course_id, classlist_csv(all_six[:5]))
    check("a one-student drop is accepted without confirmation",
          r.status_code in (302, 303), str(r.status_code))
    state = roster_state(course_id)
    check("the missing student is marked dropped",
          state.get(sid(5)) is not None, str(state.get(sid(5))))
    check("and the other five are not",
          all(state.get(sid(n)) is None for n in range(5)), str(state))
    check("the enrollment row still exists (marked, never deleted)",
          sid(5) in state)
    s = stored_summary(course_id)
    check("the summary names the dropped student",
          s.get("dropped") == [sname(5)], str(s.get("dropped")))

    print("\nThe dropped student vanishes from every grade-facing surface")
    body = requests.get(course_url, cookies=admin, timeout=30).text
    check("the roster count excludes them", "Enrolled Students (5)" in body,
          "heading not found")
    check("they are shown in a separate dropped section",
          "Dropped from the D2L classlist (1)" in body)
    check("with the date they were detected as dropped",
          "Detected as dropped" in body)
    check("and their accumulated attendance is still visible",
          re.search(r"Rsync\w+5 Testcase[\s\S]{0,900}?1 / 4", body) is not None)

    export = requests.get(f"{course_url}/export-d2l", cookies=admin, timeout=30)
    rows = list(csv.reader(io.StringIO(export.text)))[1:]
    ids_in_export = {r[0] for r in rows if r}
    check("the D2L gradebook export has one row per active student",
          len(rows) == 5, f"{len(rows)} rows")
    check("the dropped student is NOT in the D2L export",
          sid(5) not in ids_in_export, str(sorted(ids_in_export)))
    check("the active students still are",
          all(sid(n) in ids_in_export for n in range(5)), str(sorted(ids_in_export)))
    check("their D2L username is nowhere in the file",
          uname(5) not in export.text)

    own = requests.get(f"{course_url}/export", cookies=admin, timeout=30).text
    check("the spreadsheet export excludes them too", sid(5) not in own)
    check("but still has the active five",
          all(sid(n) in own for n in range(5)))

    analytics = requests.get(f"{course_url}/analytics", cookies=admin, timeout=30).text
    check("the analytics enrolled count excludes them",
          ">5</div>" in analytics or ">5<" in analytics, "count not 5")

    dash = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=admin,
                        timeout=30).text
    check("the early-alert list does not warn about a student who left",
          sname(5) not in dash)
    check("but does still warn about an at-risk active student",
          sname(1) in dash, "expected a 0%-attendance active student to alert")

    rr = requests.post(f"{course_url}/start-session", cookies=admin, timeout=30,
                       allow_redirects=True)
    m = re.search(r"/instructor/session/(\d+)", rr.url)
    session_id = int(m.group(1)) if m else None
    check("a session could be opened", session_id is not None, rr.url)
    if session_id:
        sbody = requests.get(f"{BASE_URL}/instructor/session/{session_id}",
                             cookies=admin, timeout=30).text
        check("the session page's enrolled total excludes them",
              'id="att-total">5<' in sbody,
              re.search(r'id="att-total">\d+<', sbody).group(0)
              if re.search(r'id="att-total">\d+<', sbody) else "not found")
        check("they are not offered in the mark-present list",
              sname(5) not in sbody)
        api = requests.get(f"{BASE_URL}/api/attendance/{session_id}",
                           cookies=admin, timeout=30).json()
        check("the live attendance API's denominator excludes them",
              api.get("enrolled") == 5, str(api.get("enrolled")))
        r = requests.post(f"{BASE_URL}/instructor/session/{session_id}/mark-present",
                          cookies=admin, timeout=30, allow_redirects=False,
                          data={"student_id": student_ids[5], "status": "present"})
        check("marking a dropped student present is refused",
              r.status_code == 404, str(r.status_code))
        requests.post(f"{BASE_URL}/instructor/session/{session_id}/stop",
                      cookies=admin, timeout=30)
        requests.post(f"{BASE_URL}/instructor/session/{session_id}/delete",
                      cookies=admin, timeout=30, data={"confirm": "delete"})

    print("\nA wrong file must change NOTHING, not drop the class")
    before = roster_state(course_id)
    before_summary = stored_summary(course_id)
    # Two of the five active students, plus a student who is not in the app at
    # all: three of five would be dropped, which is over the threshold.
    wrong = classlist_csv([0, 1, 99])
    r = post_classlist(admin, course_id, wrong)
    check(f"an import dropping over {int(MAX_DROP_FRACTION * 100)}% is refused",
          r.status_code == 409, str(r.status_code))
    check("the refusal says how many would be dropped",
          "3 of your 5" in r.text, r.text[:200].replace("\n", " "))
    check("it names them", sname(2) in r.text and sname(4) in r.text)
    check("it offers a way to proceed deliberately",
          'name="confirm_drops"' in r.text and 'value="drop"' in r.text)
    check("nothing was dropped", roster_state(course_id) == before)
    check("and the adds in that file were not applied either",
          sid(99) not in roster_state(course_id))
    check("the stored summary was not touched",
          stored_summary(course_id).get("_imported_at")
          == before_summary.get("_imported_at"))
    con = db()
    stray = con.execute("SELECT COUNT(*) FROM students WHERE student_id_number=?",
                        (sid(99),)).fetchone()[0]
    con.close()
    check("no student row was created by the refused import", stray == 0, str(stray))

    print("\nA classlist for a different course is refused even when it is small")
    other = classlist_csv([91, 92])
    r = post_classlist(admin, course_id, other)
    check("a file sharing no students with the roster is refused",
          r.status_code == 409, str(r.status_code))
    check("and it is explained as a different course, not a mass drop",
          "different course" in r.text)
    check("nothing was changed", roster_state(course_id) == before)

    print("\nThe instructor can still proceed deliberately")
    r = post_classlist(admin, course_id, wrong, confirm=True)
    check("a confirmed import is applied", r.status_code in (302, 303),
          str(r.status_code))
    state = roster_state(course_id)
    check("the three students are now dropped",
          all(state.get(sid(n)) is not None for n in (2, 3, 4)), str(state))
    check("the two on the file are still active",
          all(state.get(sid(n)) is None for n in (0, 1)), str(state))
    check("the new student on the file was added",
          state.get(sid(99)) is None and sid(99) in state, str(state))
    s = stored_summary(course_id)
    check("the summary records that the drops were confirmed",
          s.get("confirmed_drops") is True, str(s.get("confirmed_drops")))
    check("and names the three dropped students",
          sorted(s.get("dropped", [])) == sorted([sname(2), sname(3), sname(4)]),
          str(s.get("dropped")))

    print("\nA student who reappears is un-dropped and reported as a re-add")
    r = post_classlist(admin, course_id, classlist_csv(all_six))
    check("the full classlist is accepted", r.status_code in (302, 303),
          str(r.status_code))
    state = roster_state(course_id)
    check("every original student is active again",
          all(state.get(sid(n)) is None for n in range(6)), str(state))
    s = stored_summary(course_id)
    check("the four who came back are reported as re-adds",
          sorted(s.get("re_added", []))
          == sorted([sname(n) for n in (2, 3, 4, 5)]), str(s.get("re_added")))
    check("the student not on this classlist is now dropped",
          state.get(sid(99)) is not None, str(state.get(sid(99))))
    export = requests.get(f"{course_url}/export-d2l", cookies=admin, timeout=30).text
    check("a re-added student is back in the D2L export", sid(5) in export)
    check("and their earlier attendance survived the round trip",
          [r for r in csv.reader(io.StringIO(export))
           if r and r[0] == sid(5) and r[2] != "0"] != [])

    print("\nChanged names and usernames are reported by name")
    r = post_classlist(admin, course_id, classlist_csv(
        all_six, names={0: f"Renamed{TAG}0 Testcase"},
        usernames={1: f"changed{TAG}1"}))
    check("the import is accepted", r.status_code in (302, 303), str(r.status_code))
    s = stored_summary(course_id)
    changed = {c["name"]: " ".join(c["changes"]) for c in s.get("changed", [])}
    check("a changed name is reported", f"Renamed{TAG}0 Testcase" in changed,
          str(list(changed)))
    check("a changed D2L username is reported, with both values",
          any("D2L username" in v and f"changed{TAG}1" in v
              for v in changed.values()), str(changed))

    print("\nA stale roster is nagged about, without claiming D2L was checked")
    set_last_import(course_id, (dt.datetime.utcnow()
                                - dt.timedelta(days=STALE_AFTER_DAYS + 23)
                                ).strftime("%Y-%m-%d %H:%M:%S"))
    body = requests.get(course_url, cookies=admin, timeout=30).text
    check("the course page warns the roster may be out of date",
          "roster may be out of date" in body.lower())
    check("it tells the professor to re-download the classlist from D2L",
          "classlist" in body.lower() and "D2L" in body)
    check("it does not claim Adsum checked D2L",
          "Adsum cannot look at D2L" in body)
    dash = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=admin,
                        timeout=30).text
    check("the dashboard card warns too",
          "Roster may be out of date" in dash)

    set_last_import(course_id, None)
    body = requests.get(course_url, cookies=admin, timeout=30).text
    check("a course that has never been imported says so",
          "No classlist has been imported" in body)
    check("and that is a warning, not a quiet note",
          "roster may be out of date" in body.lower())

    print("\nA fresh import clears the warning")
    post_classlist(admin, course_id, classlist_csv(all_six))
    body = requests.get(course_url, cookies=admin, timeout=30).text
    check("the stale warning is gone", "roster may be out of date" not in body.lower())
    check("and the date is shown in plain language",
          "imported a D2L classlist today" in body)

    print("\nAn LTI-created student still carries the 'not listed' flag")
    con = db()
    c = con.execute(
        "INSERT INTO students (name, email, student_id_number, d2l_user_id, "
        "d2l_verified) VALUES (?,?,?,?,0)",
        (f"Launch{TAG} Testcase", f"launch{TAG}@students.example.edu",
         f"{SID_PREFIX}L", f"{SID_PREFIX}L"))
    con.execute("INSERT INTO course_enrollments (course_id, student_id, role) "
                "VALUES (?,?,'student')", (course_id, c.lastrowid))
    con.commit()
    con.close()
    body = requests.get(course_url, cookies=admin, timeout=30).text
    check("they appear on the roster", f"Launch{TAG} Testcase" in body)
    check("flagged as not listed in D2L", "Not listed" in body)
    check("and the help text explains both meanings",
          "added the course since your last import" in body
          and "should not be in this" in body)

    print("\nA REAL D2L export keyed on Username (no OrgDefinedId, no Email)")
    # The exact shape of a file from Grades -> Export with Key Field =
    # Username: identifiers carry D2L's "#" marker and there is no
    # OrgDefinedId or Email column at all. Every synthetic fixture above has
    # an ID column, so this format was silently rejected until a real export
    # was tried against a live course.
    #
    # List everyone currently active, so a correct import drops nobody --
    # otherwise the wrong-file guard fires and the 409 tells us nothing about
    # whether the format was understood.
    before = roster_state(course_id)
    active = sorted(k for k, dropped in before.items() if dropped is None)
    con = db()
    rows = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT s.student_id_number, s.name, s.d2l_username FROM "
        "course_enrollments e JOIN students s ON s.id = e.student_id "
        "WHERE e.course_id=? AND e.dropped_at IS NULL", (course_id,))}
    con.close()
    lines = ["Username,Last Name,First Name,End-of-Line Indicator"]
    for num in active:
        name, user = rows[num]
        parts = name.split()
        first, last = parts[0], (parts[-1] if len(parts) > 1 else "Surname")
        # Keyed on username, with D2L's marker, exactly as D2L writes it.
        lines.append(f"#{user or num},{last},{first},#")
    real = "\n".join(lines) + "\n"

    r = post_classlist(admin, course_id, real)
    check("a username-keyed export is accepted", r.status_code == 302,
          f"{r.status_code} (302 is the success redirect; 409 is the "
          f"wrong-file guard, 400 a parse refusal)")
    after = roster_state(course_id)
    check("it matched the existing students instead of duplicating them",
          len(after) == len(before), f"{len(before)} -> {len(after)}")
    check("and nobody was marked dropped by it",
          sum(1 for v in after.values() if v is not None)
          == sum(1 for v in before.values() if v is not None))
    con = db()
    stored = [row[0] for row in con.execute(
        "SELECT s.d2l_username FROM students s JOIN course_enrollments e "
        "ON e.student_id = s.id WHERE e.course_id = ? AND s.d2l_username "
        "IS NOT NULL", (course_id,))]
    con.close()
    check("the '#' marker is stripped from the stored username",
          all(not u.startswith("#") for u in stored), repr(stored[:4]))

    print("\nAn export with the name boxes left unticked is refused clearly")
    nameless = ("Username,End-of-Line Indicator\n"
                f"#{uname(1)},#\n#{uname(2)},#\n")
    r = post_classlist(admin, course_id, nameless)
    body = r.text
    check("it does not silently create nameless students",
          "Could not find First Name" in body or r.status_code >= 400,
          str(r.status_code))
    check("and it says where to tick the boxes",
          "Grades" in body and "First Name" in body)

    print("-" * 52)
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
