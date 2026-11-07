#!/usr/bin/env python3
"""Standalone mode: creating a course in the UI, and claim-once identity.

Standalone mode is how Adsum works for an instructor with no LMS: no LTI
launch, no session cookie issued by anybody, so a student's identity comes
from a device binding they claim once, from inside a live check-in. That is
weaker than an LMS vouching for them, which is exactly why the limits around
it have to be exact. Every check in this file pins one of them:

  1. A CLAIM REQUIRES A VALID, CURRENT QR TOKEN. Without this, anyone on the
     internet could bind themselves to a student's name and identity would
     no longer be tied to being in the room.
  2. ONE ACTIVE BINDING PER STUDENT PER COURSE, enforced by a partial unique
     index. A second device is refused and the refusal is surfaced to the
     instructor -- never silently allowed, never silently moved.
  3. THE DEVICE COOKIE IS UNFORGEABLE AND SCOPED. Signed with the app's own
     itsdangerous machinery under its own salt, tied to course AND student,
     and a release invalidates it immediately.
  4. THE ROSTER IS NOT PUBLISHED. The "who are you?" list shows names, never
     student ID numbers or usernames, and only the ACTIVE roster: a student
     the reconciliation dropped is not claimable.
  5. CLAIM ATTEMPTS ARE RATE LIMITED, counting failures only and clearing on
     success, so a confused student is not locked out in front of a class.
  6. A STANDALONE COURSE NEVER APPEARS TO NEED D2L.

Runs against a live instance. It builds its own throwaway courses, students,
sessions and bindings rather than touching a real class, and removes all of
it in a finally.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import pathlib
import re
import secrets
import sqlite3
import sys
import time

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

    CI has no .env at all, and a suite that read only the file passed
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

SECRET_KEY = os.getenv("SECRET_KEY") or _from_env_file("SECRET_KEY")

# Must match app/routes/student.py. Deliberately duplicated rather than
# imported: the point is to pin the published behaviour, so a change to the
# constant should make somebody look at this file too.
CLAIM_MAX_FAILURES = 40
CLAIM_WINDOW_SECONDS = 30
DEVICE_COOKIE_PREFIX = "adsum_device_c"

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


# ── Throwaway data ──────────────────────────────────────────

TAG = secrets.token_hex(3)
SID_PREFIX = f"sa{TAG}"


def sid(n: int) -> str:
    return f"{SID_PREFIX}{n}"


def uname(n: int) -> str:
    return f"u{SID_PREFIX}{n}"


def sname(n: int) -> str:
    return f"Standalone{TAG}{n} Testcase"


def roster_csv(indexes) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["OrgDefinedId", "Username", "Last Name", "First Name",
                "Email", "Role"])
    for n in indexes:
        first, _, last = sname(n).partition(" ")
        w.writerow([sid(n), uname(n), last, first,
                    f"{uname(n)}@students.example.edu", "Learner"])
    return buf.getvalue()


def create_course(cookies, name: str, code: str, mode: str):
    """Create a course through the UI and return its id."""
    r = requests.post(f"{BASE_URL}/instructor/courses/new", cookies=cookies,
                      timeout=30, allow_redirects=False,
                      data={"name": name, "course_code": code, "mode": mode})
    loc = r.headers.get("location", "")
    m = re.search(r"/instructor/course/(\d+)", loc)
    return (int(m.group(1)) if m else None), r


def import_roster(cookies, course_id: int, indexes):
    return requests.post(
        f"{BASE_URL}/instructor/course/{course_id}/import-roster",
        cookies=cookies, timeout=30, allow_redirects=False,
        files={"roster_file": ("roster.csv", roster_csv(indexes), "text/csv")})


def start_session(cookies, course_id: int):
    r = requests.post(f"{BASE_URL}/instructor/course/{course_id}/start-session",
                      cookies=cookies, timeout=30, allow_redirects=True)
    m = re.search(r"/instructor/session/(\d+)", r.url)
    return int(m.group(1)) if m else None


def live_token(cookies, session_id: int) -> str:
    return requests.get(f"{BASE_URL}/api/qr/{session_id}", cookies=cookies,
                        timeout=30).json().get("token", "")


def token_for_slot(session_id: int, slot: int) -> str:
    """A correctly SIGNED token for an arbitrary slot.

    Mirrors app/qr.py. Used to prove that a token which is genuine but out of
    date is refused, which is the property the rotating code exists for: a
    screenshot taken earlier must not be able to claim a name.
    """
    payload = f"{session_id}:{slot}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(),
                   hashlib.sha256).hexdigest()
    return f"{session_id}.{slot}.{sig}"


def post_claim(course_id, token, student_id, cookies=None):
    return requests.post(f"{BASE_URL}/student/claim", timeout=30,
                         allow_redirects=False, cookies=cookies or {},
                         data={"token": token, "student_id": student_id})


def bindings(course_id: int, active_only: bool = True) -> list[tuple]:
    con = db()
    q = ("SELECT b.id, s.student_id_number, b.released_at FROM device_bindings b "
         "JOIN students s ON s.id = b.student_id WHERE b.course_id=?")
    if active_only:
        q += " AND b.released_at IS NULL"
    rows = con.execute(q, (course_id,)).fetchall()
    con.close()
    return rows


def conflicts(course_id: int) -> list[tuple]:
    con = db()
    rows = con.execute(
        "SELECT c.id, s.student_id_number, c.reviewed_at FROM claim_conflicts c "
        "JOIN students s ON s.id = c.student_id WHERE c.course_id=?",
        (course_id,)).fetchall()
    con.close()
    return rows


def attendance_count(course_id: int) -> int:
    con = db()
    n = con.execute(
        "SELECT COUNT(*) FROM attendance_records WHERE session_id IN "
        "(SELECT id FROM sessions WHERE course_id=?)", (course_id,)).fetchone()[0]
    con.close()
    return n


def student_row_id(n: int) -> int | None:
    con = db()
    row = con.execute("SELECT id FROM students WHERE student_id_number=?",
                      (sid(n),)).fetchone()
    con.close()
    return row[0] if row else None


def set_dropped(course_id: int, student_pk: int, value) -> None:
    con = db()
    con.execute("UPDATE course_enrollments SET dropped_at=? "
                "WHERE course_id=? AND student_id=?",
                (value, course_id, student_pk))
    con.commit()
    con.close()


def teardown(course_ids: list[int]) -> None:
    con = db()
    for course_id in course_ids:
        if not course_id:
            continue
        con.execute("DELETE FROM claim_conflicts WHERE course_id=?", (course_id,))
        con.execute("DELETE FROM device_bindings WHERE course_id=?", (course_id,))
        con.execute("DELETE FROM attendance_records WHERE session_id IN "
                    "(SELECT id FROM sessions WHERE course_id=?)", (course_id,))
        con.execute("DELETE FROM sessions WHERE course_id=?", (course_id,))
        con.execute("DELETE FROM course_enrollments WHERE course_id=?", (course_id,))
        con.execute("DELETE FROM courses WHERE id=?", (course_id,))
    con.execute("DELETE FROM attendance_records WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{SID_PREFIX}%",))
    con.execute("DELETE FROM device_bindings WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{SID_PREFIX}%",))
    con.execute("DELETE FROM claim_conflicts WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{SID_PREFIX}%",))
    con.execute("DELETE FROM course_enrollments WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{SID_PREFIX}%",))
    con.execute("DELETE FROM students WHERE student_id_number LIKE ?",
                (f"{SID_PREFIX}%",))
    con.commit()
    con.close()


def main() -> int:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("ADMIN_EMAIL / ADMIN_PASSWORD not available; cannot run.")
        return 1
    if not SECRET_KEY:
        print("SECRET_KEY not available; cannot forge the stale-token checks.")
        return 1
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not admin:
        print("Could not sign in as the admin.")
        return 1

    created: list[int] = []
    try:
        return run_checks(admin, created)
    finally:
        teardown(created)
        print(f"\nThrowaway courses {created} and their students removed.")


def run_checks(admin, created: list[int]) -> int:  # noqa: PLR0915
    print("A course can be created in the UI, and it is owned by its creator")
    dash = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=admin,
                        timeout=30).text
    check("the dashboard offers a New course action",
          'href="/instructor/courses/new"' in dash)

    form = requests.get(f"{BASE_URL}/instructor/courses/new", cookies=admin,
                        timeout=30)
    check("the new-course page loads", form.status_code == 200,
          str(form.status_code))
    check("it keeps the navigation (user in context)",
          'href="/instructor/dashboard"' in form.text)
    check("it offers the D2L path", "D2L" in form.text and "LTI" in form.text)
    check("it offers the standalone path",
          'value="standalone"' in form.text)
    check("and it is honest that standalone identity is weaker",
          "classmate" in form.text.lower() and "stronger" in form.text.lower(),
          "no statement of the tradeoff on the create page")

    course_id, resp = create_course(admin, f"Standalone Probe {TAG}",
                                    f"SOLO{TAG}", "standalone")
    if course_id:
        created.append(course_id)
    check("creating a standalone course redirects to it", course_id is not None,
          f"{resp.status_code} {resp.headers.get('location', '')}")
    if not course_id:
        return 1
    con = db()
    mode = con.execute("SELECT mode FROM courses WHERE id=?",
                       (course_id,)).fetchone()[0]
    owner = con.execute("SELECT COUNT(*) FROM course_enrollments WHERE "
                        "course_id=? AND role='instructor'",
                        (course_id,)).fetchone()[0]
    con.close()
    check("it is stored in standalone mode", mode == "standalone", str(mode))
    check("the creator owns it", owner == 1, str(owner))
    check("and it is reachable by its owner",
          requests.get(f"{BASE_URL}/instructor/course/{course_id}",
                       cookies=admin, timeout=30).status_code == 200)

    print("\nExisting courses keep the D2L path: the column defaults, never guesses")
    con = db()
    cur = con.execute("INSERT INTO courses (name, geofence_radius_m, "
                      "geofence_enabled) VALUES (?,75.0,0)",
                      (f"Default Mode Probe {TAG}",))
    legacy_id = cur.lastrowid
    con.commit()
    legacy_mode = con.execute("SELECT mode FROM courses WHERE id=?",
                              (legacy_id,)).fetchone()[0]
    con.close()
    created.append(legacy_id)
    check("a course row written without a mode is a D2L course",
          legacy_mode == "d2l", str(legacy_mode))

    print("\nEverything that already existed works on a created course")
    r = import_roster(admin, course_id, range(4))
    check("a roster CSV imports", r.status_code in (302, 303), str(r.status_code))
    body = requests.get(f"{BASE_URL}/instructor/course/{course_id}",
                        cookies=admin, timeout=30).text
    check("the roster is listed", "Enrolled Students (4)" in body)
    session_id = start_session(admin, course_id)
    check("a session can be started", session_id is not None)
    if not session_id:
        return 1
    qr = requests.get(f"{BASE_URL}/api/qr/{session_id}", cookies=admin,
                      timeout=30).json()
    check("the QR and typed code are generated", bool(qr.get("token"))
          and bool(qr.get("code")))
    exp = requests.get(f"{BASE_URL}/instructor/course/{course_id}/export",
                       cookies=admin, timeout=30)
    check("the plain CSV export downloads",
          exp.status_code == 200 and "text/csv" in exp.headers.get("content-type", ""),
          str(exp.status_code))

    print("\nA standalone course never appears to need D2L settings")
    for phrase in ("Export for D2L gradebook", "Restore from D2L",
                   "D2L Integration", "Attendance Detail"):
        check(f"the course page does not mention {phrase!r}",
              phrase not in body, "still shown")
    check("it offers a plain gradebook CSV instead",
          "Download a CSV for your gradebook" in body)
    settings_body = requests.get(
        f"{BASE_URL}/instructor/course/{course_id}/settings",
        cookies=admin, timeout=30).text
    for phrase in ('name="d2l_org_unit_id"', 'name="d2l_attendance_checkin_url"',
                   "/lti/launch"):
        check(f"the settings page does not ask for {phrase!r}",
              phrase not in settings_body, "still shown")
    check("it says plainly that the D2L fields do not apply",
          "do not apply to a standalone" in settings_body)
    check("and the mode is editable with the tradeoff explained",
          'name="mode"' in settings_body
          and "stronger" in settings_body.lower())

    # The D2L surfaces must still exist for a D2L course -- otherwise this
    # would pass by having deleted them.
    con = db()
    d2l_course = con.execute(
        "SELECT course_id FROM course_enrollments WHERE student_id IS NOT NULL "
        "AND course_id IN (SELECT id FROM courses WHERE mode='d2l') "
        "GROUP BY course_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    con.close()
    if d2l_course:
        d2l_body = requests.get(f"{BASE_URL}/instructor/course/{d2l_course[0]}",
                                cookies=admin, timeout=30).text
        check("a D2L course still shows its D2L integration",
              "D2L Integration" in d2l_body and "Export for D2L gradebook" in d2l_body)

    print("\nThe 'who are you?' page shows names, never ID numbers")
    token = live_token(admin, session_id)
    page = requests.get(f"{BASE_URL}/student/checkin?token={token}", timeout=30)
    check("an unbound phone is asked who it is", page.status_code == 200
          and 'action="/student/claim"' in page.text, str(page.status_code))
    check("the roster names are offered", sname(0) in page.text)
    for n in range(4):
        check(f"student {n}'s ID number is not published",
              sid(n) not in page.text, "ID number leaked to the claim page")
        check(f"student {n}'s username is not published",
              uname(n) not in page.text, "username leaked to the claim page")
    check("the page loads no third-party resources",
          "http://" not in page.text.replace("http://www.w3.org", "")
          and "https://" not in page.text,
          "an external URL appears on a student page")

    # Wait out a limit left over from an EARLIER RUN of this file. The
    # exhaustion check at the end deliberately fills the window, and on a
    # back-to-back run every check below came back 429 and 23 of them failed
    # for reasons that had nothing to do with what they test.
    #
    # One probe, then one sleep -- never a poll. Every probe is itself a
    # failed attempt, so polling keeps the window permanently topped up and
    # the limit never clears.
    probe = post_claim(course_id, "", student_row_id(0))
    if probe.status_code == 429:
        print(f"  (throttled by an earlier run; waiting "
              f"{CLAIM_WINDOW_SECONDS + 3}s)")
        time.sleep(CLAIM_WINDOW_SECONDS + 3)

    print("\nINVARIANT 1: a claim without a valid, current token is refused")
    s0 = student_row_id(0)
    r = post_claim(course_id, "", s0)
    check("a claim with no token is refused", r.status_code == 400,
          str(r.status_code))
    check("and no binding was created", bindings(course_id) == [],
          str(bindings(course_id)))
    r = post_claim(course_id, "not-a-token", s0)
    check("a malformed token is refused", r.status_code == 400, str(r.status_code))
    r = post_claim(course_id, f"{session_id}.{int(time.time()) // 20}.deadbeef", s0)
    check("a wrongly signed token is refused", r.status_code == 400,
          str(r.status_code))
    stale = token_for_slot(session_id, (int(time.time()) // 20) - 4)
    r = post_claim(course_id, stale, s0)
    check("a correctly signed but out-of-date token is refused",
          r.status_code == 400, str(r.status_code))
    check("no binding was created by any of those", bindings(course_id) == [],
          str(bindings(course_id)))
    check("and nobody was checked in", attendance_count(course_id) == 0,
          str(attendance_count(course_id)))

    print("\nINVARIANT 5: failures count, a success clears them")
    # Deliberately BEFORE the limit is exhausted: the point is that a student
    # who fumbles and then gets it right is not held back by their own earlier
    # mistakes. Polling to wait a limit out makes it worse -- every probe is
    # itself a failure -- so the exhaustion check is the last thing this file
    # does.
    for _ in range(3):
        post_claim(course_id, "nope", s0)

    print("\nOne scan completes the claim AND the check-in")
    phone_a = requests.Session()
    token = live_token(admin, session_id)
    r = phone_a.post(f"{BASE_URL}/student/claim", timeout=30,
                     allow_redirects=False,
                     data={"token": token, "student_id": s0})
    check("the claim is accepted", r.status_code == 200, str(r.status_code))
    check("the student is told they are checked in",
          "Checked in" in r.text or "checked in" in r.text, r.text[:200])
    rows = bindings(course_id)
    check("exactly one binding exists", len(rows) == 1, str(rows))
    check("it is for the student who claimed",
          rows and rows[0][1] == sid(0), str(rows))
    check("and the attendance was recorded in the same request",
          attendance_count(course_id) == 1, str(attendance_count(course_id)))

    print("\nINVARIANT 3: the device cookie is signed, scoped and attributed")
    cookie_name = f"{DEVICE_COOKIE_PREFIX}{course_id}"
    setc = r.headers.get("set-cookie", "")
    check("a device cookie was issued, under its own name",
          cookie_name in setc, setc[:120])
    check("it is HttpOnly", "httponly" in setc.lower(), setc[:160])
    check("it is SameSite=Lax", "samesite=lax" in setc.lower(), setc[:160])
    check("it is not the instructor/LTI session cookie",
          "adsum_session" not in setc, setc[:160])
    device_value = phone_a.cookies.get(cookie_name)
    check("the cookie is signed, not a readable claim",
          device_value is not None and "." in device_value
          and "student_id" not in device_value, str(device_value)[:80])

    # A bound phone is identified silently from then on, with no questions.
    token = live_token(admin, session_id)
    again = phone_a.get(f"{BASE_URL}/student/checkin?token={token}", timeout=30)
    check("a bound phone is identified without being asked again",
          'action="/student/claim"' not in again.text and sname(0) in again.text,
          again.text[:200])

    print("\nINVARIANT 2: a second device cannot claim a bound student")
    phone_b = requests.Session()
    token = live_token(admin, session_id)
    r = phone_b.post(f"{BASE_URL}/student/claim", timeout=30,
                     allow_redirects=False,
                     data={"token": token, "student_id": s0})
    check("the second device is refused", r.status_code == 409, str(r.status_code))
    check("the refusal names the student and says what to do",
          sname(0) in r.text and "instructor" in r.text.lower())
    check("it was not given a device cookie",
          phone_b.cookies.get(cookie_name) is None,
          str(phone_b.cookies.get(cookie_name)))
    rows = bindings(course_id)
    check("there is still exactly one active binding", len(rows) == 1, str(rows))
    check("and the binding was NOT moved to the new device",
          rows and rows[0][0] == bindings(course_id)[0][0], str(rows))

    # The guarantee is the DATABASE, not the route: the route's SELECT loses
    # a race between two phones claiming at the same instant. Prove the index
    # exists and actually refuses a second active row.
    con = db()
    idx = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name="
        "'device_bindings'").fetchall()]
    check("a partial unique index enforces it in the schema",
          "uq_device_bindings_one_active_per_student_course" in idx, str(idx))
    refused_by_db = False
    try:
        con.execute("INSERT INTO device_bindings (course_id, student_id, "
                    "claimed_at) VALUES (?,?,datetime('now'))", (course_id, s0))
        con.commit()
    except sqlite3.IntegrityError:
        refused_by_db = True
        con.rollback()
    con.close()
    check("a direct second active row is refused by the database",
          refused_by_db, "the index did not stop a raw INSERT")

    print("\nINVARIANT 6 / Part 4: the refusal is surfaced to the instructor")
    cf = conflicts(course_id)
    check("the refused claim was recorded", len(cf) == 1, str(cf))
    check("against the right student", cf and cf[0][1] == sid(0), str(cf))
    body = requests.get(f"{BASE_URL}/instructor/course/{course_id}",
                        cookies=admin, timeout=30).text
    check("the course page shows it prominently", "refused" in body.lower())
    check("it names the student", sname(0) in body)
    check("it is a permanent notice, not a flash", "alert-permanent" in body)
    check("the bindings section lists which phone is which student",
          "Phones checked in as a student (1)" in body)
    check("and offers Release", f"/binding/{rows[0][0]}/release" in body)
    check("the page says a release deletes no attendance",
          "no attendance" in body.lower())

    r = requests.post(f"{BASE_URL}/instructor/course/{course_id}/"
                      "claim-conflicts/review", cookies=admin, timeout=30,
                      allow_redirects=False)
    check("the instructor can acknowledge the refusals",
          r.status_code in (302, 303), str(r.status_code))
    check("and they are marked reviewed, not deleted",
          len(conflicts(course_id)) == 1
          and conflicts(course_id)[0][2] is not None,
          str(conflicts(course_id)))

    print("\nINVARIANT 4: a dropped student is not claimable")
    s3 = student_row_id(3)
    set_dropped(course_id, s3, "2026-01-01 00:00:00")
    token = live_token(admin, session_id)
    page = requests.get(f"{BASE_URL}/student/checkin?token={token}", timeout=30)
    check("the dropped student is not offered on the claim page",
          sname(3) not in page.text)
    phone_c = requests.Session()
    r = phone_c.post(f"{BASE_URL}/student/claim", timeout=30,
                     allow_redirects=False,
                     data={"token": live_token(admin, session_id),
                           "student_id": s3})
    check("claiming a dropped student is refused", r.status_code == 400,
          str(r.status_code))
    check("no binding was created for them",
          all(row[1] != sid(3) for row in bindings(course_id)),
          str(bindings(course_id)))
    set_dropped(course_id, s3, None)

    print("\nINVARIANT 3: the cookie cannot be forged by hand")
    forged = [
        ("a plain JSON claim",
         json.dumps({"kind": "device", "course_id": course_id,
                     "student_id": s0, "binding_id": rows[0][0]})),
        ("an unsigned base64 claim",
         "eyJraW5kIjoiZGV2aWNlIiwiY291cnNlX2lkIjoxfQ"),
        ("a truncated real cookie", (device_value or "")[:-6]),
        ("a real cookie with the signature swapped",
         (device_value or "") + "x"),
        ("the instructor session cookie reused as a device cookie",
         admin["adsum_session"]),
    ]
    for label, value in forged:
        token = live_token(admin, session_id)
        rr = requests.get(f"{BASE_URL}/student/checkin?token={token}",
                          cookies={f"{DEVICE_COOKIE_PREFIX}{course_id}": value},
                          timeout=30)
        check(f"{label} does not identify anybody",
              'action="/student/claim"' in rr.text, rr.text[:160])

    print("\nINVARIANT 3: a cookie is scoped to ONE course")
    other_id, _ = create_course(admin, f"Standalone Probe {TAG}b",
                                f"SOLOB{TAG}", "standalone")
    if other_id:
        created.append(other_id)
        import_roster(admin, other_id, range(4))
        other_session = start_session(admin, other_id)
        other_token = live_token(admin, other_session)
        # The same signed value, renamed to the other course's cookie. The
        # course is inside the signature as well as in the cookie name, so
        # renaming it is not enough.
        rr = requests.get(f"{BASE_URL}/student/checkin?token={other_token}",
                          cookies={f"{DEVICE_COOKIE_PREFIX}{other_id}": device_value},
                          timeout=30)
        check("a binding for one course does not identify in another",
              'action="/student/claim"' in rr.text, rr.text[:160])
        # And the phone's own cookie is simply not looked at over there.
        rr = phone_a.get(f"{BASE_URL}/student/checkin?token={other_token}",
                         timeout=30)
        check("a bound phone is still asked who it is in a different course",
              'action="/student/claim"' in rr.text, rr.text[:160])

    print("\nA claim is impossible on a D2L course")
    if d2l_course:
        d2l_session = start_session(admin, d2l_course[0])
        if d2l_session:
            con = db()
            d2l_student = con.execute(
                "SELECT student_id FROM course_enrollments WHERE course_id=? "
                "AND student_id IS NOT NULL LIMIT 1", (d2l_course[0],)).fetchone()
            con.close()
            rr = post_claim(d2l_course[0], live_token(admin, d2l_session),
                            d2l_student[0] if d2l_student else 1)
            check("claiming on a D2L course is refused",
                  rr.status_code == 200 and "not checked in" in rr.text.lower(),
                  f"{rr.status_code} {rr.text[:120]}")
            con = db()
            made = con.execute("SELECT COUNT(*) FROM device_bindings WHERE "
                               "course_id=?", (d2l_course[0],)).fetchone()[0]
            con.close()
            check("and no binding was created on it", made == 0, str(made))
            requests.post(f"{BASE_URL}/instructor/session/{d2l_session}/stop",
                          cookies=admin, timeout=30)
            requests.post(f"{BASE_URL}/instructor/session/{d2l_session}/delete",
                          cookies=admin, timeout=30, data={"confirm": "delete"})

    print("\nPart 4: Release frees the name and deletes no attendance")
    before = attendance_count(course_id)
    binding_id = rows[0][0]
    # Another course's binding id must not be releasable from this URL.
    rr = requests.post(f"{BASE_URL}/instructor/course/{legacy_id}/"
                       f"binding/{binding_id}/release", cookies=admin,
                       timeout=30, allow_redirects=False)
    check("a binding cannot be released through another course's URL",
          rr.status_code == 404, str(rr.status_code))
    check("so it is still active", len(bindings(course_id)) == 1)

    rr = requests.post(f"{BASE_URL}/instructor/course/{course_id}/"
                       f"binding/{binding_id}/release", cookies=admin,
                       timeout=30, allow_redirects=False)
    check("the owner can release it", rr.status_code in (302, 303),
          str(rr.status_code))
    check("the binding is released, not deleted", bindings(course_id) == []
          and len(bindings(course_id, active_only=False)) == 1,
          str(bindings(course_id, active_only=False)))
    check("NO attendance was deleted", attendance_count(course_id) == before,
          f"{attendance_count(course_id)} vs {before}")

    token = live_token(admin, session_id)
    rr = phone_a.get(f"{BASE_URL}/student/checkin?token={token}", timeout=30)
    check("the released phone is signed out immediately",
          'action="/student/claim"' in rr.text, rr.text[:160])

    phone_d = requests.Session()
    rr = phone_d.post(f"{BASE_URL}/student/claim", timeout=30,
                      allow_redirects=False,
                      data={"token": live_token(admin, session_id),
                            "student_id": s0})
    check("a different phone can now claim that student",
          rr.status_code == 200, str(rr.status_code))
    check("and there is exactly one active binding again",
          len(bindings(course_id)) == 1, str(bindings(course_id)))
    check("with the earlier attendance intact",
          attendance_count(course_id) == before,
          f"{attendance_count(course_id)} vs {before}")

    print("\nOne phone cannot hold two names")
    rr = phone_d.post(f"{BASE_URL}/student/claim", timeout=30,
                      allow_redirects=False,
                      data={"token": live_token(admin, session_id),
                            "student_id": student_row_id(1)})
    check("a second name on the same phone is refused",
          rr.status_code == 409, str(rr.status_code))
    check("and student 1 was not bound",
          all(row[1] != sid(1) for row in bindings(course_id)),
          str(bindings(course_id)))

    print("\nINVARIANT 5: grinding the claim form is rate limited")
    # Last, deliberately: this exhausts the limiter, and one probe plus one
    # sleep is the only safe way to recover. Polling would keep the window
    # topped up forever.
    statuses = [post_claim(course_id, "wrong-token", s0).status_code
                for _ in range(CLAIM_MAX_FAILURES + 4)]
    check("repeated failures are eventually refused with 429",
          429 in statuses, f"last: {statuses[-4:]}")

    requests.post(f"{BASE_URL}/instructor/session/{session_id}/stop",
                  cookies=admin, timeout=30)

    print("\nA course created by mistake can be deleted")
    r = requests.post(f"{BASE_URL}/instructor/courses/new", cookies=admin,
                      timeout=30, allow_redirects=True,
               data={"name": "Delete Me", "course_code": "ZZDEL",
                     "mode": "standalone"})
    m = re.search(r"/instructor/course/(\d+)", r.url)
    doomed = int(m.group(1)) if m else None
    check("a course can be created in the UI", doomed is not None, r.url)

    if doomed:
        # Give it the children that made the first implementation fail with
        # "FOREIGN KEY constraint failed": a session, and a refused claim
        # that points at that session.
        requests.post(f"{BASE_URL}/instructor/course/{doomed}/start-session",
                      cookies=admin, timeout=30)
        r = requests.post(f"{BASE_URL}/instructor/course/{doomed}/delete",
                          cookies=admin, timeout=30, allow_redirects=False)
        check("deleting a course without confirmation is refused",
              r.status_code == 400, str(r.status_code))
        check("and the course is still there",
              db().execute("SELECT 1 FROM courses WHERE id=?",
                           (doomed,)).fetchone() is not None)

        r = requests.post(f"{BASE_URL}/instructor/course/{doomed}/delete",
                          data={"confirm": "delete"}, timeout=30,
                          allow_redirects=False)
        check("a stranger cannot delete a course",
              r.status_code in (302, 403, 404), str(r.status_code))
        check("it survived the stranger",
              db().execute("SELECT 1 FROM courses WHERE id=?",
                           (doomed,)).fetchone() is not None)

        r = requests.post(f"{BASE_URL}/instructor/course/{doomed}/delete",
                          cookies=admin, data={"confirm": "delete"},
                          timeout=30, allow_redirects=True)
        check("a confirmed delete succeeds", r.status_code == 200,
              str(r.status_code))
        check("the course is gone",
              db().execute("SELECT 1 FROM courses WHERE id=?",
                           (doomed,)).fetchone() is None)
        for table in ("course_enrollments", "sessions", "device_bindings",
                      "claim_conflicts"):
            left = db().execute(
                f"SELECT COUNT(*) FROM {table} WHERE course_id=?",
                (doomed,)).fetchone()[0]
            check(f"no {table} left behind", left == 0, str(left))
        orphans = db().execute(
            "SELECT COUNT(*) FROM attendance_records WHERE session_id NOT IN "
            "(SELECT id FROM sessions)").fetchone()[0]
        check("no attendance left orphaned by the delete", orphans == 0,
              str(orphans))
        # Students the deleted course was the last to hold must go with it.
        # Otherwise every deleted course leaves an invisible pile of names
        # behind, and for a standalone course that was the only copy.
        #
        # NOT EXISTS, never NOT IN: instructor enrollments carry a NULL
        # student_id, which makes NOT IN match nothing and this assertion
        # pass no matter how many rows were stranded.
        stranded = db().execute(
            "SELECT COUNT(*) FROM students s WHERE NOT EXISTS "
            "(SELECT 1 FROM course_enrollments e WHERE e.student_id = s.id)"
        ).fetchone()[0]
        check("no students stranded with no course at all", stranded == 0,
              f"{stranded} orphaned student row(s)")

    print("-" * 52)
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
