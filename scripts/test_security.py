#!/usr/bin/env python3
"""Security regressions: each check here is a hole that was actually open.

Every case in this file was verified exploitable against a real instance
before it was fixed, so none of these are hypothetical. If one starts failing,
treat it as a live vulnerability rather than a style nit.

Runs against a live instance and needs a course with at least one enrolled
student. It creates and removes its own throwaway accounts.
"""

from __future__ import annotations

import os
import pathlib
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

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok and detail else ""))
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


def main() -> int:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("ADMIN_EMAIL / ADMIN_PASSWORD not available; cannot run.")
        return 1
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not admin:
        print("Could not sign in as the admin.")
        return 1

    print("Cross-site requests cannot act as a signed-in user (CSRF)")
    # The session cookie was SameSite=None, and there was no token or origin
    # check, so an auto-submitting form on any page an instructor visited
    # could create an admin account or wipe a course.
    evil = {"Origin": "https://evil.example.com",
            "Referer": "https://evil.example.com/x",
            "Sec-Fetch-Site": "cross-site"}
    probe_email = f"csrf-probe-{secrets.token_hex(3)}@example.edu"
    r = requests.post(f"{BASE_URL}/admin/instructors/add", cookies=admin,
                      headers=evil, allow_redirects=False, timeout=30,
                      data={"name": "CSRF", "email": probe_email,
                            "password": "x" * 16, "is_admin_flag": "true"})
    check("cross-site POST to /admin/instructors/add refused",
          r.status_code == 403, str(r.status_code))
    check("and no account was created",
          db().execute("SELECT COUNT(*) FROM instructors WHERE email=?",
                       (probe_email,)).fetchone()[0] == 0)

    course_id = db().execute(
        "SELECT course_id FROM course_enrollments WHERE student_id IS NOT NULL "
        "GROUP BY course_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    if not course_id:
        print("No course with enrolled students; cannot finish.")
        return 1
    course_id = course_id[0]
    before = db().execute("SELECT COUNT(*) FROM sessions WHERE course_id=?",
                          (course_id,)).fetchone()[0]
    r = requests.post(f"{BASE_URL}/instructor/course/{course_id}/reset-data",
                      cookies=admin, headers=evil, allow_redirects=False, timeout=30)
    check("cross-site POST to reset-data refused", r.status_code == 403, str(r.status_code))
    check("and the course's sessions are untouched",
          db().execute("SELECT COUNT(*) FROM sessions WHERE course_id=?",
                       (course_id,)).fetchone()[0] == before)

    # Same-origin requests must still work, or the fix broke the app.
    good = {"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"}
    r = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=admin,
                     headers=good, timeout=30)
    check("same-origin requests still work", r.status_code == 200, str(r.status_code))

    print("\nThe session cookie is not cross-site")
    r = requests.post(f"{BASE_URL}/instructor/login",
                      data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      allow_redirects=False, timeout=30)
    setc = r.headers.get("set-cookie", "").lower()
    check("cookie is SameSite=Lax", "samesite=lax" in setc, setc[:160])
    check("cookie is HttpOnly", "httponly" in setc)

    print("\nThe LTI launch endpoint does not reflect input (XSS)")
    # This endpoint needs no authentication, and it used to interpolate the
    # submitted consumer key straight into an HTML f-string. An attacker's
    # script then ran on the app's own origin and could read the shared LTI
    # secret out of /admin.
    payload = "<script>alert(1)</script>"
    r = requests.post(f"{BASE_URL}/lti/launch",
                      data={"oauth_consumer_key": payload}, timeout=30)
    check("unknown consumer key is rejected", r.status_code == 403, str(r.status_code))
    check("the payload is not reflected into the page", payload not in r.text,
          r.text[:200])
    r = requests.post(f"{BASE_URL}/lti/launch",
                      data={"oauth_consumer_key": "x"},
                      headers={"X-Forwarded-Host": 'evil"><script>alert(2)</script>'},
                      timeout=30)
    check("a forged X-Forwarded-Host is not reflected either",
          "<script>alert(2)</script>" not in r.text)

    print("\nSecurity headers are present")
    r = requests.get(f"{BASE_URL}/", timeout=30)
    csp = r.headers.get("content-security-policy", "")
    check("Content-Security-Policy is set", bool(csp))
    check("CSP restricts connect-src to self", "connect-src 'self'" in csp, csp[:160])
    check("CSP restricts form-action to self", "form-action 'self'" in csp)
    check("X-Content-Type-Options is nosniff",
          r.headers.get("x-content-type-options") == "nosniff")

    print("\nOne instructor cannot reach another's students (IDOR)")
    # mark-present took student_id from the form with no roster check, so an
    # instructor could post arbitrary ids into their own session and read the
    # names and ID numbers back out of the attendance API.
    con = db()
    foreign = con.execute(
        "SELECT s.id FROM students s WHERE s.id NOT IN "
        "(SELECT student_id FROM course_enrollments WHERE course_id=? "
        " AND student_id IS NOT NULL) LIMIT 1", (course_id,)).fetchone()
    loc = requests.post(f"{BASE_URL}/instructor/course/{course_id}/start-session",
                        cookies=admin, allow_redirects=False,
                        timeout=30).headers.get("location", "")
    sid = loc.rstrip("/").split("/")[-1] if loc else None
    if sid and foreign:
        for route in ("mark-present", "mark-excused", "unmark"):
            rr = requests.post(f"{BASE_URL}/instructor/session/{sid}/{route}",
                               cookies=admin, allow_redirects=False, timeout=30,
                               data={"student_id": str(foreign[0]), "status": "present"})
            check(f"{route} refuses a student not on the roster",
                  rr.status_code == 404, str(rr.status_code))
        check("no attendance row was written for the foreign student",
              db().execute("SELECT COUNT(*) FROM attendance_records "
                           "WHERE session_id=? AND student_id=?",
                           (sid, foreign[0])).fetchone()[0] == 0)
    elif not foreign:
        print("  (skipped: no student outside this course to test with)")
    if sid:
        requests.post(f"{BASE_URL}/instructor/session/{sid}/stop",
                      cookies=admin, allow_redirects=False, timeout=30)
        db_con = db()
        db_con.execute("DELETE FROM attendance_records WHERE session_id=?", (sid,))
        db_con.execute("DELETE FROM sessions WHERE id=?", (sid,))
        db_con.commit()

    print("\nRevoking access actually revokes it")
    # A signed cookie kept working for its full 12 hours after the account was
    # deactivated, after its password was reset, and after Logout.
    pw = "Zq" + secrets.token_urlsafe(20)
    email = f"revoke-probe-{secrets.token_hex(3)}@example.edu"
    requests.post(f"{BASE_URL}/admin/instructors/add", cookies=admin,
                  allow_redirects=False, timeout=30,
                  data={"name": "Revoke Probe", "email": email, "password": pw})
    victim = login(email, pw)
    check("the probe account can sign in", victim is not None)
    row = db().execute("SELECT id FROM instructors WHERE email=?", (email,)).fetchone()
    if victim and row:
        vid = row[0]
        r = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=victim, timeout=30)
        check("its session works before revocation", r.status_code == 200)

        requests.post(f"{BASE_URL}/admin/instructors/{vid}/toggle-active",
                      cookies=admin, allow_redirects=False, timeout=30)
        r = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=victim,
                         allow_redirects=False, timeout=30)
        check("deactivation kills the existing session",
              r.status_code in (302, 401, 403), str(r.status_code))

        requests.post(f"{BASE_URL}/admin/instructors/{vid}/toggle-active",
                      cookies=admin, allow_redirects=False, timeout=30)
        victim2 = login(email, pw)
        if victim2:
            requests.post(f"{BASE_URL}/admin/instructors/{vid}/reset-password",
                          cookies=admin, allow_redirects=False, timeout=30,
                          data={"new_password": "Rt" + secrets.token_urlsafe(20)})
            r = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=victim2,
                             allow_redirects=False, timeout=30)
            check("a password reset kills the existing session",
                  r.status_code in (302, 401, 403), str(r.status_code))

        con = db()
        con.execute("DELETE FROM course_enrollments WHERE instructor_id=?", (vid,))
        con.execute("DELETE FROM instructors WHERE id=?", (vid,))
        con.commit()
        print("  (throwaway account removed)")

    print("\nRaw coordinates are never stored")
    # SECURITY.md and the README both promise this, and the code did not
    # honour it: latitude and longitude were written on every geofenced
    # check-in.
    stored = db().execute(
        "SELECT COUNT(*) FROM attendance_records "
        "WHERE gps_lat IS NOT NULL OR gps_lon IS NOT NULL").fetchone()[0]
    check("no attendance record holds a latitude or longitude", stored == 0, str(stored))

    print("\nThe login throttle cannot be bypassed with a header")
    victim_email = f"throttle-{secrets.token_hex(3)}@example.edu"
    codes = []
    for i in range(6):
        rr = requests.post(f"{BASE_URL}/instructor/login", allow_redirects=False,
                           timeout=30,
                           headers={"X-Forwarded-For": f"10.9.{i}.{i}"},
                           data={"email": victim_email, "password": f"wrong{i}"})
        codes.append(rr.status_code)
    check("varying X-Forwarded-For no longer grants extra attempts",
          codes[5] == 429, str(codes))
    r = requests.post(f"{BASE_URL}/instructor/login", allow_redirects=False,
                      timeout=30, data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    check("and the admin was not locked out as a side effect",
          r.status_code == 302, str(r.status_code))

    print("-" * 52)
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
