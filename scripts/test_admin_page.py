#!/usr/bin/env python3
"""Does the installation-administration page stay inside its boundary?

Two distinct things are asserted here, and the second matters more:

  1. An admin can actually manage accounts and the D2L connection.
  2. A plain instructor is REFUSED on every /admin route, and an admin cannot
     reach any student's attendance from this page.

The second is the reason the page is deliberately small. Attendance records
are student education records; the isolation suite guarantees one instructor
cannot read another's, and that guarantee would be worthless if it ended at
"unless you are an admin". If someone later adds a convenient "all courses"
view to /admin, this file should start failing.

Runs against a live instance. Creates and removes its own throwaway account.
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
    """Read one key from .env without requiring python-dotenv."""
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return default


BASE_URL = (os.getenv("BASE_URL") or _from_env_file("BASE_URL")
            or "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") or _from_env_file("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD") or _from_env_file("ADMIN_PASSWORD")

DB_PATH = ROOT / (_from_env_file("DATABASE_URL", "sqlite+aiosqlite:///./adsum.db")
                  .rsplit("/", 1)[-1] or "adsum.db")

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def login(email: str, password: str):
    r = requests.post(f"{BASE_URL}/instructor/login",
                      data={"email": email, "password": password},
                      allow_redirects=False, timeout=30)
    tok = r.cookies.get("adsum_session")
    return {"adsum_session": tok} if tok else None


def main() -> int:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("ADMIN_EMAIL / ADMIN_PASSWORD not set; cannot run.")
        return 1

    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not admin:
        print(f"Could not sign in as {ADMIN_EMAIL}.")
        return 1

    print("Admin can administer the installation")
    r = requests.get(f"{BASE_URL}/admin", cookies=admin, timeout=30)
    check("admin loads /admin", r.status_code == 200, str(r.status_code))
    page = r.text
    for probe in ("Instructors", "Add an instructor", "D2L Brightspace connection",
                  "Install health", "Deployment settings"):
        check(f"section present: {probe}", probe in page)
    check("page states attendance is not visible here",
          "Attendance is not visible here" in page)
    check("nav offers the Admin link", '<a href="/admin">Admin</a>' in page)

    print("\nAccount management works")
    password = "Zq" + secrets.token_urlsafe(20)
    email = f"probe_{secrets.token_hex(4)}@example.edu"
    r = requests.post(f"{BASE_URL}/admin/instructors/add", cookies=admin,
                      allow_redirects=False, timeout=30,
                      data={"name": "Probe Instructor", "email": email,
                            "password": password})
    check("add an instructor", r.status_code == 302
          and "error" not in r.headers.get("location", ""),
          r.headers.get("location", ""))
    plain = login(email, password)
    check("the new instructor can sign in", plain is not None)

    r = requests.post(f"{BASE_URL}/admin/instructors/add", cookies=admin,
                      allow_redirects=False, timeout=30,
                      data={"name": "Dup", "email": email, "password": "x" * 14})
    check("duplicate email refused",
          "already" in r.headers.get("location", "").lower())

    r = requests.post(f"{BASE_URL}/admin/instructors/add", cookies=admin,
                      allow_redirects=False, timeout=30,
                      data={"name": "Weak", "email": f"weak_{secrets.token_hex(3)}@example.edu",
                            "password": "short"})
    check("short password refused", "error" in r.headers.get("location", ""))

    con = sqlite3.connect(DB_PATH, timeout=20)
    row = con.execute("SELECT id FROM instructors WHERE email=?", (email,)).fetchone()
    target_id = row[0] if row else None
    me_id = con.execute("SELECT id FROM instructors WHERE email=?",
                        (ADMIN_EMAIL,)).fetchone()[0]

    print("\nA plain instructor is refused everywhere under /admin")
    if plain:
        for path in ("/admin", "/admin/"):
            rr = requests.get(f"{BASE_URL}{path}", cookies=plain,
                              allow_redirects=False, timeout=30)
            check(f"GET {path} -> 403", rr.status_code == 403, str(rr.status_code))
        posts = {
            "/admin/instructors/add": {"name": "Sneak", "email": "sneak@example.edu",
                                       "password": "y" * 14},
            "/admin/d2l": {"d2l_host": "evil.example.com",
                           "lti_consumer_key": "evil",
                           "lti_consumer_secret": "evil"},
            f"/admin/instructors/{me_id}/toggle-admin": {},
            f"/admin/instructors/{me_id}/toggle-active": {},
            f"/admin/instructors/{me_id}/reset-password": {"new_password": "z" * 14},
        }
        for path, data in posts.items():
            rr = requests.post(f"{BASE_URL}{path}", cookies=plain, data=data,
                               allow_redirects=False, timeout=30)
            check(f"POST {path} -> 403", rr.status_code == 403, str(rr.status_code))

        rr = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=plain, timeout=30)
        check("nav hides the Admin link from a plain instructor",
              '<a href="/admin">Admin</a>' not in rr.text)

        host = con.execute("SELECT d2l_host FROM institutions WHERE id=1").fetchone()[0]
        check("refused POST did not alter the D2L host",
              host != "evil.example.com", str(host))
        still = con.execute("SELECT is_admin, is_active FROM instructors WHERE id=?",
                            (me_id,)).fetchone()
        check("refused POST did not alter the admin account", still == (1, 1), str(still))
        check("sneak account was not created",
              con.execute("SELECT COUNT(*) FROM instructors WHERE email=?",
                          ("sneak@example.edu",)).fetchone()[0] == 0)

    print("\nAn admin cannot reach student data from here")
    # The page must not leak any enrolled student's name or username. An
    # admin seeing a COUNT of a colleague's courses is fine; seeing who is in
    # them is not.
    names = [r[0] for r in con.execute(
        "SELECT name FROM students WHERE name IS NOT NULL AND name != ''").fetchall()]
    usernames = [r[0] for r in con.execute(
        "SELECT d2l_username FROM students WHERE d2l_username IS NOT NULL "
        "AND d2l_username != ''").fetchall()]
    leaked = [n for n in names + usernames if n and n in page]
    check("no student name or username appears on /admin", not leaked, str(leaked))
    for word in ("Attendance %", "Present", "Absent", "Export"):
        check(f"no attendance control on /admin: {word!r}",
              f">{word}<" not in page)

    print("\nSelf-lockout is prevented")
    for action in ("toggle-admin", "toggle-active"):
        rr = requests.post(f"{BASE_URL}/admin/instructors/{me_id}/{action}",
                           cookies=admin, allow_redirects=False, timeout=30)
        check(f"admin cannot {action} on their own account",
              "error=" in rr.headers.get("location", ""))
    still = con.execute("SELECT is_admin, is_active FROM instructors WHERE id=?",
                        (me_id,)).fetchone()
    check("own account still admin and active", still == (1, 1), str(still))

    print("\nDeactivation blocks sign-in but keeps the account")
    if target_id:
        requests.post(f"{BASE_URL}/admin/instructors/{target_id}/toggle-active",
                      cookies=admin, allow_redirects=False, timeout=30)
        check("deactivated account cannot sign in", login(email, password) is None)
        check("deactivated account still exists",
              con.execute("SELECT COUNT(*) FROM instructors WHERE id=?",
                          (target_id,)).fetchone()[0] == 1)
        requests.post(f"{BASE_URL}/admin/instructors/{target_id}/toggle-active",
                      cookies=admin, allow_redirects=False, timeout=30)
        check("reactivated account can sign in again", login(email, password) is not None)

        new_password = "Rt" + secrets.token_urlsafe(20)
        requests.post(f"{BASE_URL}/admin/instructors/{target_id}/reset-password",
                      cookies=admin, allow_redirects=False, timeout=30,
                      data={"new_password": new_password})
        check("password reset takes effect", login(email, new_password) is not None)
        check("old password no longer works", login(email, password) is None)

        con.execute("DELETE FROM instructors WHERE id=?", (target_id,))
        con.commit()
        print("\n  (throwaway account removed)")

    print("-" * 48)
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
