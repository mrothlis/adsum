"""Admin vs ordinary instructor separation.

Verifies that an ordinary instructor cannot rewrite the shared D2L/LTI
settings every course depends on, nor read the shared secret, while an
admin still can.

Configuration (no personal details are hard-coded):
    BASE_URL        target install, default http://127.0.0.1:8000
    ADMIN_EMAIL     admin login
    ADMIN_PASSWORD  admin password
All three fall back to the values in .env, so `python scripts/test_admin_separation.py` works
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

import sqlite3  # noqa: E402

import requests  # noqa: E402


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



def _default_course_id():
    """The course with the most enrolled students, unless TEST_COURSE_ID says
    otherwise. Installs differ, so nothing may assume a particular id."""
    env = os.getenv("TEST_COURSE_ID")
    if env:
        return int(env)
    import sqlite3 as _s
    row = _s.connect(_db_path(), timeout=10).execute(
        "SELECT course_id FROM course_enrollments WHERE student_id IS NOT NULL "
        "GROUP BY course_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit("No course has enrolled students; set TEST_COURSE_ID.")
    return row[0]


COURSE_ID = _default_course_id()

admin = {"adsum_session": requests.post(
    BASE + "/instructor/login", data={"email": EMAIL, "password": PW},
    allow_redirects=False).cookies.get("adsum_session")}
ck = admin

fails = []


def check(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("  " + d if d else ""))
    if not c:
        fails.append(n)


def db():
    return sqlite3.connect(_db_path(), timeout=10)


def inst():
    return db().execute(
        "SELECT d2l_host, lti_consumer_key, lti_consumer_secret FROM institutions WHERE id=1"
    ).fetchone()


# Create a genuine second professor with their own course.
conn = db()
from app.auth import create_session_token, hash_password  # noqa: E402

conn.execute("INSERT OR IGNORE INTO instructors (name,email,password_hash,is_admin) VALUES (?,?,?,0)",
             ("Test Colleague", "colleague@example.edu", hash_password("x" * 12)))
conn.commit()
other_id = conn.execute("SELECT id FROM instructors WHERE email='colleague@example.edu'").fetchone()[0]
conn.execute("INSERT INTO courses (institution_id,name,course_code,geofence_radius_m,geofence_enabled) "
             "VALUES (1,'Colleague Course','COL1000',75.0,0)")
conn.commit()
other_course = conn.execute("SELECT id FROM courses WHERE course_code='COL1000'").fetchone()[0]
conn.execute("INSERT INTO course_enrollments (course_id,instructor_id,role) VALUES (?,?,'instructor')",
             (other_course, other_id))
conn.commit()
conn.close()
other = {"adsum_session": create_session_token(
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
h = requests.get(f"{BASE}/instructor/course/{COURSE_ID}/settings", cookies=admin).text
check("admin still sees the secret", (original[2] or "") in h)

_own = db().execute("SELECT name, course_code, d2l_org_unit_id, geofence_lat, geofence_lon, "
                    "geofence_radius_m, geofence_enabled FROM courses WHERE id=?",
                    (COURSE_ID,)).fetchone()
form_admin = dict(form, name=_own[0], course_code=_own[1] or "",
                  d2l_host=original[0] or "", lti_consumer_key=original[1] or "",
                  lti_consumer_secret=original[2] or "",
                  d2l_org_unit_id=_own[2] or "",
                  geofence_lat="" if _own[3] is None else str(_own[3]),
                  geofence_lon="" if _own[4] is None else str(_own[4]),
                  geofence_radius_m=str(_own[5] or 75))
if _own[6]:
    form_admin["geofence_enabled"] = "true"
r = requests.post(f"{BASE}/instructor/course/{COURSE_ID}/settings", data=form_admin,
                  cookies=admin, allow_redirects=False)
check("admin save works", r.status_code in (302, 303), f"status={r.status_code}")
check("institution intact after admin save", inst() == original, f"{inst()}")

# --- and isolation still holds between the two courses
r = requests.get(f"{BASE}/instructor/course/{COURSE_ID}", cookies=other, allow_redirects=False)
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
