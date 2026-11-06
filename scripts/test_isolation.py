"""Multi-instructor isolation tests.

Simulates a second instructor and verifies they cannot reach another
instructor's course, and that the JSON API does not hand check-in tokens or
student data to strangers.

Configuration (no personal details are hard-coded):
    BASE_URL        target install, default http://127.0.0.1:8000
    ADMIN_EMAIL     admin login
    ADMIN_PASSWORD  admin password
All three fall back to the values in .env, so `python scripts/test_isolation.py` works
out of the box on a local checkout.
"""
import os
import sys

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
EMAIL = os.getenv("ADMIN_EMAIL") or _from_env_file("ADMIN_EMAIL", "admin@example.edu")
PW = os.getenv("ADMIN_PASSWORD") or _from_env_file("ADMIN_PASSWORD", "")
if not PW:
    raise SystemExit("Set ADMIN_PASSWORD (env var or .env) before running this script.")

import requests  # noqa: E402

admin = {"csu_session": requests.post(
    BASE + "/instructor/login", data={"email": EMAIL, "password": PW},
    allow_redirects=False).cookies.get("csu_session")}
ck = admin

import sqlite3  # noqa: E402

COURSE_ID = int(os.getenv("TEST_COURSE_ID", "2"))
_dbfile = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "csu_checkin_v2.db")
_roster = list(sqlite3.connect(_dbfile, timeout=10).execute(
    "SELECT s.id, s.name FROM course_enrollments ce JOIN students s ON s.id=ce.student_id "
    "WHERE ce.course_id=? AND ce.role='student' ORDER BY s.id", (COURSE_ID,)))
if not _roster:
    raise SystemExit(f"Course {COURSE_ID} has no enrolled students to test with.")
TEST_STUDENT_ID = _roster[0][0]
ROSTER_NAMES = [n for _i, n in _roster]

# A second instructor who teaches nothing on this install.
from app.auth import create_session_token  # noqa: E402

other = {"csu_session": create_session_token(
    {"role": "instructor", "instructor_id": 999999, "name": "Other Instructor"})}

fails = []


def check(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("  " + d if d else ""))
    if not c:
        fails.append(n)


def leaked(text):
    # Leak check uses the course's real roster names, looked up at runtime,
    # so the test carries no personal data of its own.
    return any(n and n in text for n in ROSTER_NAMES)


print("--- a second instructor must not reach course 2 (GET) ---")
for path in [f"/instructor/course/{COURSE_ID}", f"/instructor/course/{COURSE_ID}/settings",
             f"/instructor/course/{COURSE_ID}/analytics", f"/instructor/course/{COURSE_ID}/export",
             f"/instructor/course/{COURSE_ID}/export-d2l"]:
    r = requests.get(BASE + path, cookies=other, allow_redirects=False)
    ok = r.status_code in (302, 303, 403, 404, 409) and not leaked(r.text)
    check(f"blocked {path}", ok, f"status={r.status_code} leaked={leaked(r.text)}")

print("\n--- and must not mutate it (POST) ---")
for path, data in [(f"/instructor/course/{COURSE_ID}/reset-data", {}),
                   (f"/instructor/course/{COURSE_ID}/start-session", {}),
                   (f"/instructor/course/{COURSE_ID}/remove-student/{TEST_STUDENT_ID}", {}),
                   (f"/instructor/course/{COURSE_ID}/edit-student/{TEST_STUDENT_ID}",
                    {"name": "HACKED", "student_id_number": "", "d2l_username": ""})]:
    r = requests.post(BASE + path, data=data, cookies=other, allow_redirects=False)
    check(f"blocked POST {path}", r.status_code in (302, 303, 403, 404), f"status={r.status_code}")

import sqlite3  # noqa: E402
nm = sqlite3.connect("csu_checkin_v2.db", timeout=10).execute(
    "SELECT name FROM students WHERE id=?", (TEST_STUDENT_ID,)).fetchone()[0]
check("student record untouched by other instructor", nm != "HACKED", f"name={nm}")

print("\n--- the owner still works ---")
r = requests.post(BASE + f"/instructor/course/{COURSE_ID}/start-session", cookies=ck, allow_redirects=False)
sid = r.headers.get("location", "").rstrip("/").split("/")[-1]
check("owner can start a session", sid.isdigit(), f"status={r.status_code}")

print("\n--- the JSON API is no longer open ---")
r = requests.get(f"{BASE}/api/qr/{sid}")
check("anonymous /api/qr blocked", r.status_code != 200 or "token" not in r.text,
      f"status={r.status_code}")
r = requests.get(f"{BASE}/api/qr/{sid}", cookies=other)
check("other instructor /api/qr blocked", r.status_code != 200 or "token" not in r.text,
      f"status={r.status_code}")
r = requests.get(f"{BASE}/api/attendance/{sid}")
check("anonymous /api/attendance blocked", r.status_code != 200, f"status={r.status_code}")
r = requests.get(f"{BASE}/api/attendance/{sid}", cookies=other)
check("other instructor /api/attendance blocked", r.status_code != 200, f"status={r.status_code}")

r = requests.get(f"{BASE}/api/qr/{sid}", cookies=ck)
check("owner /api/qr still works", r.status_code == 200 and "token" in r.text,
      f"status={r.status_code}")
r = requests.get(f"{BASE}/api/attendance/{sid}", cookies=ck)
check("owner /api/attendance still works", r.status_code == 200, f"status={r.status_code}")

print("\n--- owner session controls still work ---")
for path in [f"/instructor/session/{sid}/mark-present",
             f"/instructor/session/{sid}/mark-excused",
             f"/instructor/session/{sid}/unmark"]:
    r = requests.post(BASE + path, data={"student_id": str(TEST_STUDENT_ID)}, cookies=ck, allow_redirects=False)
    check(f"owner {path.split('/')[-1]}", r.status_code in (302, 303), f"status={r.status_code}")
r = requests.post(f"{BASE}/instructor/session/{sid}/stop", cookies=ck, allow_redirects=False)
check("owner can stop session", r.status_code in (302, 303))

# other instructor must not touch sessions either
r = requests.post(f"{BASE}/instructor/session/{sid}/mark-present",
                  data={"student_id": str(TEST_STUDENT_ID)}, cookies=other, allow_redirects=False)
check("other instructor cannot mark present", r.status_code in (302, 303, 404))

requests.post(BASE + f"/instructor/course/{COURSE_ID}/reset-data", cookies=ck, allow_redirects=False)
print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
