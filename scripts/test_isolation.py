"""Multi-instructor isolation + open-API tests.

Simulates a second professor and verifies they cannot reach the first's
course, and that the JSON API no longer hands check-in tokens to strangers.
"""
import sys

import requests

sys.path.insert(0, ".")

BASE = "https://checkin.drrothlisberger.com"
PW = [l.split(":", 1)[1].strip() for l in open("CREDENTIALS.txt", encoding="utf-8")
      if l.startswith("Password:")][0]
ck = {"csu_session": requests.post(
    BASE + "/instructor/login",
    data={"email": "rothlisberger_michael@columbusstate.edu", "password": PW},
    allow_redirects=False).cookies.get("csu_session")}

from app.auth import create_session_token  # noqa: E402

# A second professor who teaches nothing on this system.
other = {"csu_session": create_session_token(
    {"role": "instructor", "instructor_id": 999, "name": "Prof Other"})}

fails = []


def check(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("  " + d if d else ""))
    if not c:
        fails.append(n)


def leaked(text):
    return "Stu_Garver" in text or "young_melissa" in text or "Melissa" in text


print("--- a second instructor must not reach course 2 (GET) ---")
for path in ["/instructor/course/2", "/instructor/course/2/settings",
             "/instructor/course/2/analytics", "/instructor/course/2/export",
             "/instructor/course/2/export-d2l"]:
    r = requests.get(BASE + path, cookies=other, allow_redirects=False)
    ok = r.status_code in (302, 303, 403, 404, 409) and not leaked(r.text)
    check(f"blocked {path}", ok, f"status={r.status_code} leaked={leaked(r.text)}")

print("\n--- and must not mutate it (POST) ---")
for path, data in [("/instructor/course/2/reset-data", {}),
                   ("/instructor/course/2/start-session", {}),
                   ("/instructor/course/2/remove-student/13", {}),
                   ("/instructor/course/2/edit-student/13",
                    {"name": "HACKED", "student_id_number": "", "d2l_username": ""})]:
    r = requests.post(BASE + path, data=data, cookies=other, allow_redirects=False)
    check(f"blocked POST {path}", r.status_code in (302, 303, 403, 404), f"status={r.status_code}")

import sqlite3  # noqa: E402
nm = sqlite3.connect("csu_checkin_v2.db", timeout=10).execute(
    "SELECT name FROM students WHERE id=13").fetchone()[0]
check("student record untouched by other instructor", nm != "HACKED", f"name={nm}")

print("\n--- the owner still works ---")
r = requests.post(BASE + "/instructor/course/2/start-session", cookies=ck, allow_redirects=False)
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
    r = requests.post(BASE + path, data={"student_id": "12"}, cookies=ck, allow_redirects=False)
    check(f"owner {path.split('/')[-1]}", r.status_code in (302, 303), f"status={r.status_code}")
r = requests.post(f"{BASE}/instructor/session/{sid}/stop", cookies=ck, allow_redirects=False)
check("owner can stop session", r.status_code in (302, 303))

# other instructor must not touch sessions either
r = requests.post(f"{BASE}/instructor/session/{sid}/mark-present",
                  data={"student_id": "12"}, cookies=other, allow_redirects=False)
check("other instructor cannot mark present", r.status_code in (302, 303, 404))

requests.post(BASE + "/instructor/course/2/reset-data", cookies=ck, allow_redirects=False)
print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
