"""Admin vs ordinary instructor separation.

Verifies that a second professor cannot rewrite the shared D2L/LTI settings
that every course on the installation depends on, and cannot read the shared
secret, while the admin still can.
"""
import sqlite3
import sys

import requests

sys.path.insert(0, ".")

BASE = "https://checkin.drrothlisberger.com"
PW = [l.split(":", 1)[1].strip() for l in open("CREDENTIALS.txt", encoding="utf-8")
      if l.startswith("Password:")][0]
admin = {"csu_session": requests.post(
    BASE + "/instructor/login",
    data={"email": "rothlisberger_michael@columbusstate.edu", "password": PW},
    allow_redirects=False).cookies.get("csu_session")}

fails = []


def check(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("  " + d if d else ""))
    if not c:
        fails.append(n)


def db():
    return sqlite3.connect("csu_checkin_v2.db", timeout=10)


def inst():
    return db().execute(
        "SELECT d2l_host, lti_consumer_key, lti_consumer_secret FROM institutions WHERE id=1"
    ).fetchone()


# Create a genuine second professor with their own course.
conn = db()
from app.auth import hash_password, create_session_token  # noqa: E402

conn.execute("INSERT OR IGNORE INTO instructors (name,email,password_hash,is_admin) VALUES (?,?,?,0)",
             ("Prof Colleague", "colleague@columbusstate.edu", hash_password("x" * 12)))
conn.commit()
other_id = conn.execute("SELECT id FROM instructors WHERE email='colleague@columbusstate.edu'").fetchone()[0]
conn.execute("INSERT INTO courses (institution_id,name,course_code,geofence_radius_m,geofence_enabled) "
             "VALUES (1,'Colleague Course','COL1000',75.0,0)")
conn.commit()
other_course = conn.execute("SELECT id FROM courses WHERE course_code='COL1000'").fetchone()[0]
conn.execute("INSERT INTO course_enrollments (course_id,instructor_id,role) VALUES (?,?,'instructor')",
             (other_course, other_id))
conn.commit()
conn.close()
other = {"csu_session": create_session_token(
    {"role": "instructor", "instructor_id": other_id, "name": "Prof Colleague"})}
print(f"second professor id={other_id}, their course id={other_course}\n")

original = inst()
print("institution settings before:", original)

# --- the colleague saves THEIR OWN course settings, trying to change shared config
form = {
    "name": "Colleague Course", "course_code": "COL1000",
    "d2l_org_unit_id": "", "d2l_attendance_checkin_url": "",
    "geofence_lat": "", "geofence_lon": "", "geofence_radius_m": "75",
    "allowed_ip_ranges": "",
    "d2l_host": "evil.example.com",
    "lti_consumer_key": "hijacked",
    "lti_consumer_secret": "hijacked-secret",
}
r = requests.post(f"{BASE}/instructor/course/{other_course}/settings",
                  data=form, cookies=other, allow_redirects=False)
after = inst()
check("colleague can save their own course", r.status_code in (302, 303), f"status={r.status_code}")
check("shared D2L host unchanged", after[0] == original[0], f"{original[0]} -> {after[0]}")
check("shared LTI key unchanged", after[1] == original[1])
check("shared LTI secret unchanged", after[2] == original[2])

name = db().execute("SELECT name FROM courses WHERE id=?", (other_course,)).fetchone()[0]
check("their own course details still saved", name == "Colleague Course", name)

# --- the colleague must not see the shared secret
h = requests.get(f"{BASE}/instructor/course/{other_course}/settings", cookies=other).text
check("secret hidden from colleague", (original[2] or "zzz") not in h)
check("colleague told to ask an admin", "ask your administrator" in h.lower() or "only an administrator" in h.lower())

# --- the admin can still see and change it
h = requests.get(f"{BASE}/instructor/course/2/settings", cookies=admin).text
check("admin still sees the secret", (original[2] or "") in h)

form_admin = dict(form, name="Michael Rothlisberger BUSA 2115 Sandbox", course_code="BUSA2115",
                  d2l_host=original[0] or "", lti_consumer_key=original[1] or "",
                  lti_consumer_secret=original[2] or "",
                  d2l_org_unit_id="3573358",
                  geofence_lat="32.502237", geofence_lon="-84.938952",
                  geofence_radius_m="250", geofence_enabled="true")
r = requests.post(f"{BASE}/instructor/course/2/settings", data=form_admin,
                  cookies=admin, allow_redirects=False)
check("admin save works", r.status_code in (302, 303), f"status={r.status_code}")
check("institution intact after admin save", inst() == original, f"{inst()}")

# --- and isolation still holds between the two courses
r = requests.get(f"{BASE}/instructor/course/2", cookies=other, allow_redirects=False)
check("colleague still cannot see Michael's course", r.status_code in (302, 303, 404))
r = requests.get(f"{BASE}/instructor/course/{other_course}", cookies=admin, allow_redirects=False)
check("Michael cannot see colleague's course either", r.status_code in (302, 303, 404),
      f"status={r.status_code}")

# cleanup
conn = db()
conn.execute("DELETE FROM course_enrollments WHERE course_id=?", (other_course,))
conn.execute("DELETE FROM courses WHERE id=?", (other_course,))
conn.execute("DELETE FROM instructors WHERE id=?", (other_id,))
conn.commit()
conn.close()
print("\ncleaned up test professor and course")
print("institution settings after:", inst())
print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
