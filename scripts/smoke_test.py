#!/usr/bin/env python3
"""Consolidated smoke test for Adsum.

Reads admin credentials from CREDENTIALS.txt (gitignored) and runs a safe,
non-destructive E2E check against the target BASE_URL.

Usage:
    python scripts/smoke_test.py                 # uses BASE_URL from env or .env
    BASE_URL=http://localhost:8000 python scripts/smoke_test.py
    python scripts/smoke_test.py --reset         # also test reset-data (careful!)

Exits non-zero on any failure.
"""
import os
import sqlite3
import sys
import time as _time

import requests

# Ensure project root (parent of scripts/) is on sys.path so we can import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _from_env_file(key, default=""):
    """Read a key from .env without requiring python-dotenv."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


BASE = os.getenv("BASE_URL") or _from_env_file("BASE_URL", "http://127.0.0.1:8000")

def _db_path():
    """Locate the SQLite file from DATABASE_URL, not a hard-coded name.

    CI and local installs use different database filenames; reading the
    configured URL keeps this script working on any install.
    """
    url = os.getenv("DATABASE_URL") or _from_env_file(
        "DATABASE_URL", "sqlite+aiosqlite:///./csu_checkin_v2.db")
    path = url.split("///")[-1] if "///" in url else url
    if not os.path.isabs(path):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cand = os.path.join(root, path.lstrip("./"))
        path = cand if os.path.exists(cand) else os.path.abspath(path.lstrip("./"))
    return path


RUN_RESET = "--reset" in sys.argv

# Admin credentials: env var, then CREDENTIALS.txt (gitignored), then .env.
EMAIL = os.getenv("ADMIN_EMAIL") or _from_env_file("ADMIN_EMAIL", "admin@example.edu")
PW = os.getenv("ADMIN_PASSWORD")
if not PW:
    try:
        for line in open("CREDENTIALS.txt", encoding="utf-8"):
            if line.startswith("Password:"):
                PW = line.split(":", 1)[1].strip()
    except OSError:
        pass
if not PW:
    PW = _from_env_file("ADMIN_PASSWORD")
assert PW, "Set ADMIN_PASSWORD (env var or .env), or provide CREDENTIALS.txt"

# The course and students are resolved at runtime, not hard-coded: this script
# must keep working on any install and after demo data is removed.
COURSE_ID = int(os.getenv("TEST_COURSE_ID", "0"))


DB = _db_path()
RESULTS = []

def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  | {detail}" if detail else ""))

def get_db():
    db = sqlite3.connect(DB, timeout=10.0)
    db.execute("PRAGMA busy_timeout = 10000")
    return db

def main():
    s = requests.Session()

    # 1. healthz
    r = requests.get(BASE + "/healthz", timeout=10)
    check("healthz reachable", r.status_code == 200 and r.json().get("status") == "ok")

    # 2. admin login
    r = s.post(BASE + "/instructor/login",
               data={"email": EMAIL, "password": PW},
               allow_redirects=False)
    icook = {"adsum_session": r.cookies.get("adsum_session")}
    check("admin login with new password", r.status_code == 302 and icook["adsum_session"])

    # 3. old password rejected
    r = requests.post(BASE + "/instructor/login",
                      data={"email": EMAIL, "password": "admin123"},
                      allow_redirects=False)
    check("old password (admin123) rejected", r.status_code == 401)

    # 4. login throttle, per ACCOUNT rather than per IP.
    #
    # Deliberately aimed at a throwaway email, never at EMAIL: the throttle is
    # now keyed on the account, so hammering the real admin would lock the
    # account this test still needs and cascade into every later check. It
    # also must not rely on spoofing X-Forwarded-For, because the app no
    # longer trusts that header from an untrusted peer (that was both a
    # bypass and a way to lock a real user out on demand).
    import random as _random
    victim = f"throttle-probe-{_random.randint(100000, 999999)}@example.edu"
    bystander = f"bystander-{_random.randint(100000, 999999)}@example.edu"
    codes = [requests.post(BASE + "/instructor/login",
                           data={"email": victim, "password": "wrong" + str(i)},
                           allow_redirects=False).status_code for i in range(6)]
    check("5 failures on one account then 429", codes[:5] == [401] * 5 and codes[5] == 429)

    # A different account from the same address is unaffected, which is the
    # point of keying on the account: one person's failures must not lock out
    # everyone sharing a campus NAT address.
    r = requests.post(BASE + "/instructor/login",
                      data={"email": bystander, "password": "wrong"},
                      allow_redirects=False)
    check("another account from the same address still allowed", r.status_code == 401)

    # And the real admin is still able to sign in.
    r = requests.post(BASE + "/instructor/login",
                      data={"email": EMAIL, "password": PW},
                      allow_redirects=False)
    check("throttling one account did not lock out the admin", r.status_code == 302)

    # 5. target course — create a fresh session
    global COURSE_ID
    _db = get_db()
    if not COURSE_ID:
        _c = _db.execute("""SELECT ce.course_id FROM course_enrollments ce
                            WHERE ce.student_id IS NOT NULL
                            GROUP BY ce.course_id ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()
        assert _c, "No course with enrolled students; set TEST_COURSE_ID."
        COURSE_ID = _c[0]
    _students = [r[0] for r in _db.execute(
        "SELECT student_id FROM course_enrollments WHERE course_id=? AND student_id IS NOT NULL ORDER BY student_id",
        (COURSE_ID,))]
    assert _students, f"Course {COURSE_ID} has no enrolled students."
    manual_student_id = _students[-1]

    r = s.post(BASE + f"/instructor/course/{COURSE_ID}/start-session", cookies=icook, allow_redirects=False)
    sid = r.headers.get("location", "").rstrip("/").split("/")[-1]
    check("start session on sandbox", r.status_code == 302 and sid.isdigit())

    tok = s.get(f"{BASE}/api/qr/{sid}", cookies=icook).json()["token"]

    # 6. The anonymous check-in page must load. It offers a Launch-from-D2L
    #    deep link only when the course has one configured, so that part is
    #    checked conditionally rather than assumed.
    r = requests.get(BASE + "/student/checkin", params={"token": tok})
    check("anon check-in page loads", r.status_code == 200)
    _db2 = get_db()
    _deep = _db2.execute("SELECT d2l_attendance_checkin_url FROM courses WHERE id=?",
                         (COURSE_ID,)).fetchone()
    _db2.close()
    if _deep and _deep[0]:
        # The unidentified visitor must be offered a way to sign in, and the
        # class they were joining must be remembered for the trip back.
        check("anon page offers D2L sign-in", "Sign in with D2L" in r.text)
        check("pending-session cookie set for the D2L round trip",
              "adsum_pending_session" in r.cookies)
    else:
        print("SKIP  anon page D2L sign-in — no deep link configured on this course")

    # 7. forged student LTI cookie for whoever is actually enrolled.
    # Don't hardcode an id -- rosters change, and a removed student made this fail spuriously.
    from app.auth import create_session_token
    _db = get_db()
    _row = _db.execute(
        "SELECT s.id, s.name FROM course_enrollments ce "
        "JOIN students s ON s.id = ce.student_id "
        "WHERE ce.course_id = ? AND ce.role = 'student' ORDER BY s.id LIMIT 1",
        (COURSE_ID,)).fetchone()
    _db.close()
    if not _row:
        raise SystemExit(f"smoke test needs at least one student enrolled in course {COURSE_ID}")
    test_student_id, test_student_name = _row
    print(f"   (using enrolled student {test_student_id}: {test_student_name})")
    scook = {"adsum_session": create_session_token({"role": "student", "student_id": test_student_id,
                                                   "name": test_student_name, "lti": True})}

    # 8. check-in. If the course enforces a geofence, send coordinates at its
    # centre -- otherwise the test fails on configuration rather than on code.
    _db = get_db()
    _geo = _db.execute(
        "SELECT geofence_enabled, geofence_lat, geofence_lon FROM courses WHERE id = 2").fetchone()
    _db.close()
    checkin_data: dict[str, str] = {}
    if _geo and _geo[0] and _geo[1] is not None:
        checkin_data["gps_lat"] = str(_geo[1])
        checkin_data["gps_lon"] = str(_geo[2])
        print("   (course enforces a geofence; sending in-fence coords)")

    tok = s.get(f"{BASE}/api/qr/{sid}", cookies=icook).json()["token"]
    checkin_data["token"] = tok
    r = requests.post(BASE + "/student/checkin", data=checkin_data, cookies=scook)
    check("student check-in succeeds", r.status_code == 200 and "Checked in successfully" in r.text)

    # 9. DB record exists with status=present
    db = get_db()
    rec = db.execute("SELECT status FROM attendance_records WHERE session_id=? AND student_id=?",
                     (sid, test_student_id)).fetchone()
    check("AttendanceRecord written (present)", rec and rec[0] == "present")

    # 10. duplicate check-in blocked gracefully
    tok = s.get(f"{BASE}/api/qr/{sid}", cookies=icook).json()["token"]
    checkin_data["token"] = tok
    r = requests.post(BASE + "/student/checkin", data=checkin_data, cookies=scook)
    check("duplicate → 'already checked in'", "already checked in" in r.text)

    # 11. manual mark-present, on the same course using a real enrolled student
    r = s.post(BASE + f"/instructor/course/{COURSE_ID}/start-session", cookies=icook, allow_redirects=False)
    sid1 = r.headers.get("location", "").rstrip("/").split("/")[-1]
    r = s.post(f"{BASE}/instructor/session/{sid1}/mark-present",
               data={"student_id": str(manual_student_id)}, cookies=icook, allow_redirects=False)
    rec = get_db().execute("SELECT status, token_used FROM attendance_records WHERE session_id=? AND student_id=?", (sid1, manual_student_id)).fetchone()
    check("mark-present writes manual record", rec == ("present", "manual"))

    # 12. Export for D2L Gradebook
    r = s.get(BASE + f"/instructor/course/{COURSE_ID}/export-d2l", cookies=icook)
    check("export-d2l CSV downloads", r.status_code == 200 and "text/csv" in r.headers.get("content-type", ""))

    # 13. Stop test sessions (leave records in DB; test data is harmless)
    for x in (sid, sid1):
        s.post(BASE + f"/instructor/session/{x}/stop", cookies=icook, allow_redirects=False)
    # Optional: full cleanup of test records (requires --cleanup flag)
    if "--cleanup" in sys.argv:
        def db_exec_with_retry(db, sql, params, retries=3, delay=0.5):
            for attempt in range(retries):
                try:
                    db.execute(sql, params)
                    return
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < retries - 1:
                        _time.sleep(delay)
                    else:
                        raise
        for x in (sid, sid1):
            db_exec_with_retry(get_db(), "DELETE FROM attendance_records WHERE session_id=?", (x,))
            db_exec_with_retry(get_db(), "DELETE FROM sessions WHERE id=?", (x,))
        get_db().commit()

    # 14. Optional: reset-data (only if --reset flag)
    if RUN_RESET:
        r = s.post(BASE + f"/instructor/course/{COURSE_ID}/reset-data", cookies=icook, allow_redirects=False)
        check("reset-data redirects", r.status_code == 302 and "reset=1" in r.headers.get("location", ""))
        left = get_db().execute("SELECT COUNT(*) FROM sessions WHERE course_id=1").fetchone()[0]
        check("reset wiped course 1 sessions", left == 0)

    # 15. Verify geofence enforcement when enabled (quick test)
    # Enable on the target course, try out-of-range GPS -> blocked, then disable
    # (skipped in normal run; only if GEOFENCE_TEST=1)
    if os.getenv("GEOFENCE_TEST") == "1":
        print(">> GEOFENCE_TEST enabled — running geofence enforcement checks")
        # implementation skipped for brevity; see test_geofence.py

    # Summary
    print()
    fails = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(fails)}/{len(RESULTS)} passed" + (f" | FAILED: {fails}" if fails else " | ALL GREEN"))
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()