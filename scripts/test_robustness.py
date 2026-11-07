#!/usr/bin/env python3
"""Robustness regressions: four ways a present student became an absent one.

Every check here was reproduced against a real instance before it was fixed,
the same way test_security.py's cases were. These are not hypotheticals, and
they all end in the same place: a student who attended is recorded absent, or
an instructor is locked out of their own roll call.

  1. Two active sessions for one course. A SELECT-then-INSERT guard with no
     lock meant four simultaneous Start Session POSTs -- one impatient
     double-click -- created four open sessions. The dashboard then 500s
     forever on scalar_one_or_none, the projector's QR encodes one session id
     while students scan into another, and because the export treats every
     session row as a class meeting, one real class becomes several and
     everyone who checked into only one is exported ABSENT for the rest.
  2. No way to delete a session. A stray session (hallway, wrong course,
     started twice) could be ended but never removed, so it cost every
     student an absence and raised the denominator, quietly spending the
     forgiveness allowance that real absences were meant to use.
  3. Roster import 500d on a Windows-encoded or non-CSV file. One accented
     name saved from Excel (cp1252) returned a bare Internal Server Error,
     imported nothing, explained nothing -- and an empty roster means nobody
     in that class can check in at all.
  4. A second tap on the instructor's Present / Excused buttons 500d on the
     unique constraint even though the record had been written.

Runs against a live instance. It creates its own throwaway course, student
and sessions, aims the concurrency check at those, and removes everything it
made. It never starts a session on, or deletes anything from, the course the
rest of the suite uses.
"""

from __future__ import annotations

import os
import pathlib
import socket
import sqlite3
import ssl
import sys
import threading
from urllib.parse import urlparse

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROBE_TAG = "robustness-probe"


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
    """Locate the SQLite file, ENVIRONMENT VARIABLE FIRST.

    Not a stylistic preference: a previous test read DATABASE_URL from .env
    only, passed locally, and failed in CI, which has no .env at all. The
    environment is the authority and the file is the fallback.
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

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def db():
    con = sqlite3.connect(_db_path(), timeout=30)
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def login(email: str, password: str):
    r = requests.post(f"{BASE_URL}/instructor/login",
                      data={"email": email, "password": password},
                      allow_redirects=False, timeout=30)
    tok = r.cookies.get("adsum_session")
    return {"adsum_session": tok} if tok else None


def start_session_concurrently(token: str, course_id: int, n: int) -> list[tuple[str, str]]:
    """Fire n Start Session POSTs that genuinely overlap.

    Threads over `requests` are not enough: the connections are opened one at
    a time and the server drains them in order, so the race closes by itself
    and the check passes against broken code. Every socket is connected
    first, then a barrier releases all of them together.

    n must also comfortably exceed the database connection pool, or the
    requests queue behind the pool and serialise anyway: 8 produced one
    session against the UNFIXED code, 12 and above reliably produced five.

    And the pool has to be WARM first. On a freshly started server the
    connections are created lazily, so the very first burst serialises on
    pool construction and the race does not happen at all -- which made this
    check pass against code that was still broken. /healthz touches the
    database, so n concurrent healthz calls populate the pool before the
    barrier is armed.
    """
    # Parse properly and honour the scheme. The first version assumed
    # "http://host:port" and split on the colon, which raised ValueError
    # against a real deployment -- https with no explicit port -- so this
    # check only ever ran against a scratch instance. Raw sockets are still
    # the right tool (see the docstring), they just have to be able to speak
    # TLS when the target does.
    parsed = urlparse(BASE_URL)
    use_tls = parsed.scheme == "https"
    host = parsed.hostname
    port = parsed.port or (443 if use_tls else 80)
    if not host:
        raise SystemExit(f"BASE_URL has no host: {BASE_URL!r}")
    host_header = f"{host}:{port}" if parsed.port else host

    warm = threading.Barrier(n)

    def warm_up():
        warm.wait()
        try:
            requests.get(f"{BASE_URL}/healthz", timeout=30)
        except requests.RequestException:
            pass

    warmers = [threading.Thread(target=warm_up) for _ in range(n)]
    for t in warmers:
        t.start()
    for t in warmers:
        t.join()
    req = (f"POST /instructor/course/{course_id}/start-session HTTP/1.1\r\n"
           f"Host: {host_header}\r\n"
           f"Cookie: adsum_session={token}\r\n"
           f"Origin: {BASE_URL}\r\n"
           f"Content-Length: 0\r\nConnection: close\r\n\r\n").encode()

    def connect():
        raw = socket.create_connection((host, port), timeout=60)
        if not use_tls:
            return raw
        # server_hostname matters: the deployment sits behind a tunnel that
        # routes on SNI, so a connection without it reaches the wrong vhost.
        return ssl.create_default_context().wrap_socket(
            raw, server_hostname=host)

    socks = [connect() for _ in range(n)]
    barrier = threading.Barrier(n)
    out: list[tuple[str, str]] = []
    lock = threading.Lock()

    def fire(sock):
        barrier.wait()
        buf = b""
        try:
            sock.sendall(req)
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        finally:
            sock.close()
        status = buf.split(b"\r\n")[0].decode(errors="replace")
        loc = ""
        for line in buf.split(b"\r\n"):
            if line.lower().startswith(b"location:"):
                loc = line.decode(errors="replace").split(":", 1)[1].strip()
        with lock:
            out.append((status, loc))

    threads = [threading.Thread(target=fire, args=(s,)) for s in socks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def make_throwaway(instructor_id: int) -> tuple[int, int]:
    """A disposable course with one disposable student, owned by the admin.

    The concurrency and delete checks both create and destroy sessions, so
    they must never be aimed at a course the rest of the suite depends on:
    hammering real data is how a previous test cascaded into every later one.
    """
    con = db()
    cur = con.execute(
        "INSERT INTO courses (name, course_code, geofence_radius_m, geofence_enabled) "
        "VALUES (?, ?, 75.0, 0)", (f"{PROBE_TAG} course", PROBE_TAG))
    course_id = int(cur.lastrowid or 0)
    cur = con.execute(
        "INSERT INTO students (name, email, student_id_number, d2l_verified) "
        "VALUES (?, ?, ?, 0)",
        (f"{PROBE_TAG} student", f"{PROBE_TAG}@example.edu", f"{PROBE_TAG}-id"))
    student_id = int(cur.lastrowid or 0)
    con.execute("INSERT INTO course_enrollments (course_id, instructor_id, role) "
                "VALUES (?, ?, 'instructor')", (course_id, instructor_id))
    con.execute("INSERT INTO course_enrollments (course_id, student_id, role) "
                "VALUES (?, ?, 'student')", (course_id, student_id))
    con.commit()
    con.close()
    return course_id, student_id


def drop_throwaway(course_id: int) -> None:
    con = db()
    con.execute("DELETE FROM attendance_records WHERE session_id IN "
                "(SELECT id FROM sessions WHERE course_id=?)", (course_id,))
    con.execute("DELETE FROM sessions WHERE course_id=?", (course_id,))
    con.execute("DELETE FROM course_enrollments WHERE course_id=?", (course_id,))
    con.execute("DELETE FROM courses WHERE id=?", (course_id,))
    # Students created by this script (the probe student and anything the
    # roster-import check imported) and any enrollment referring to them.
    con.execute("DELETE FROM course_enrollments WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{PROBE_TAG}%",))
    con.execute("DELETE FROM attendance_records WHERE student_id IN "
                "(SELECT id FROM students WHERE student_id_number LIKE ?)",
                (f"{PROBE_TAG}%",))
    con.execute("DELETE FROM students WHERE student_id_number LIKE ?", (f"{PROBE_TAG}%",))
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

    row = db().execute("SELECT id FROM instructors WHERE email=?",
                       (ADMIN_EMAIL,)).fetchone()
    if not row:
        print(f"No instructor row for {ADMIN_EMAIL}; cannot run.")
        return 1
    instructor_id = row[0]

    # A real course is still resolved (never hard-coded) so the read-only
    # duplicate-tolerance check looks at data an instructor really has.
    real_course = db().execute(
        "SELECT course_id FROM course_enrollments WHERE student_id IS NOT NULL "
        "GROUP BY course_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    if not real_course:
        print("No course with enrolled students; cannot run.")
        return 1
    real_course = real_course[0]

    drop_throwaway(-1)  # sweep leftovers from an interrupted earlier run
    course_id, student_id = make_throwaway(instructor_id)
    print(f"(throwaway course {course_id}, student {student_id}; "
          f"detected real course {real_course})")

    try:
        print("\nOne course can never hold two open sessions (DEFECT 1)")
        # The guard in start_session was a SELECT followed by an INSERT with
        # no lock and no constraint. Four simultaneous POSTs produced four
        # distinct open sessions, the dashboard 500d forever afterwards, and
        # the export turned one class meeting into several -- so a student
        # who checked into one of them came out ABSENT for the others.
        results = start_session_concurrently(admin["adsum_session"], course_id, 16)
        codes = {s for s, _ in results}
        locations = {loc for _s, loc in results if loc}
        open_now = db().execute(
            "SELECT COUNT(*) FROM sessions WHERE course_id=? AND ended_at IS NULL",
            (course_id,)).fetchone()[0]
        check("16 simultaneous start-session POSTs leave exactly one open session",
              open_now == 1, f"{open_now} open; codes={sorted(codes)}")
        check("and they all land on the same session",
              len(locations) == 1, str(sorted(locations)))
        check("none of them failed", all("500" not in c for c in codes), str(sorted(codes)))

        # The guarantee is in the schema, not in the route: prove the database
        # itself refuses the second open row.
        con = db()
        rejected = False
        try:
            con.execute("INSERT INTO sessions (course_id, started_at) "
                        "VALUES (?, CURRENT_TIMESTAMP)", (course_id,))
            con.commit()
        except sqlite3.IntegrityError:
            rejected = True
        finally:
            con.rollback()
            con.close()
        check("the database rejects a second open session for the course", rejected)
        idx = db().execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_sessions_one_open_per_course'").fetchone()
        check("the partial unique index exists",
              bool(idx) and "ended_at IS NULL" in (idx[0] or ""),
              str(idx))

        # And every page that looks up a course's open session must survive a
        # database that already holds duplicates, since the live one might.
        r = requests.get(f"{BASE_URL}/instructor/dashboard", cookies=admin, timeout=30)
        check("the dashboard still renders", r.status_code == 200, str(r.status_code))
        src = (ROOT / "app" / "routes").glob("*.py")
        offenders = []
        for path in src:
            body = path.read_text(encoding="utf-8")
            for i, line in enumerate(body.splitlines()):
                if "scalar_one_or_none" in line:
                    window = "\n".join(body.splitlines()[max(0, i - 6):i + 1])
                    if "Session.ended_at.is_(None)" in window:
                        offenders.append(f"{path.name}:{i + 1}")
        check("no route reads a course's open session with scalar_one_or_none",
              not offenders, str(offenders))

        sid_row = db().execute("SELECT id FROM sessions WHERE course_id=? "
                               "AND ended_at IS NULL ORDER BY id DESC",
                               (course_id,)).fetchone()
        check("an open session exists to work with", bool(sid_row))
        sid = sid_row[0] if sid_row else 0

        print("\nA stray session can be deleted, not just ended (DEFECT 2)")
        # Every session row is a class meeting to the exporter, so a session
        # started in the hallway or twice gave every student an absence and
        # raised the denominator. The only previous remedy was
        # reset_course_data, which destroys the whole course's history.
        requests.post(f"{BASE_URL}/instructor/session/{sid}/mark-present",
                      cookies=admin, allow_redirects=False, timeout=30,
                      data={"student_id": str(student_id), "status": "present"})
        before = db().execute("SELECT COUNT(*) FROM attendance_records WHERE session_id=?",
                              (sid,)).fetchone()[0]
        check("the probe session has an attendance record to lose", before == 1, str(before))

        r = requests.post(f"{BASE_URL}/instructor/session/{sid}/delete",
                          cookies=admin, allow_redirects=False, timeout=30)
        check("delete-session without a confirmation is refused",
              r.status_code == 400, str(r.status_code))
        check("and the session is still there",
              db().execute("SELECT COUNT(*) FROM sessions WHERE id=?",
                           (sid,)).fetchone()[0] == 1)

        r = requests.post(f"{BASE_URL}/instructor/session/{sid}/delete",
                          cookies=admin, allow_redirects=False, timeout=30,
                          data={"confirm": "delete"})
        check("delete-session with confirmation redirects to the course",
              r.status_code == 302 and f"/instructor/course/{course_id}"
              in r.headers.get("location", ""),
              f"{r.status_code} {r.headers.get('location', '')}")
        check("the session row is gone",
              db().execute("SELECT COUNT(*) FROM sessions WHERE id=?",
                           (sid,)).fetchone()[0] == 0)
        check("its attendance records are gone with it",
              db().execute("SELECT COUNT(*) FROM attendance_records WHERE session_id=?",
                           (sid,)).fetchone()[0] == 0)

        # Deleting somebody else's session must still be impossible: this
        # route is guarded by owned_session like every other session route.
        foreign = db().execute(
            "SELECT s.id FROM sessions s WHERE s.course_id NOT IN "
            "(SELECT course_id FROM course_enrollments WHERE instructor_id=? "
            " AND role='instructor') LIMIT 1", (instructor_id,)).fetchone()
        if foreign:
            r = requests.post(f"{BASE_URL}/instructor/session/{foreign[0]}/delete",
                              cookies=admin, allow_redirects=False, timeout=30,
                              data={"confirm": "delete"})
            check("delete-session refuses a session this instructor does not own",
                  r.status_code == 404, str(r.status_code))
            check("and that session survives",
                  db().execute("SELECT COUNT(*) FROM sessions WHERE id=?",
                               (foreign[0],)).fetchone()[0] == 1)
        else:
            print("  (skipped: this instructor owns every course on the install)")

        print("\nRoster import survives a Windows-encoded or non-CSV file (DEFECT 3)")
        # `.decode("utf-8-sig")` with no error handler meant one accented name
        # saved from Excel returned a bare 500, imported nothing, explained
        # nothing, and left the roster empty -- so nobody in that class could
        # check in at all.
        #
        # The file has to carry the course's CURRENT roster as well as the new
        # student. The import is a reconciliation now, so a one-line classlist
        # would mean "everyone else dropped" and the wrong-file guard refuses
        # it with 409 -- correctly. That guard is pinned in
        # scripts/test_roster_sync.py; what is being tested here is the
        # decoding, so the file is a realistic add-one-student classlist.
        current = db().execute(
            "SELECT s.name, s.student_id_number, COALESCE(s.d2l_username, '') "
            "FROM course_enrollments e JOIN students s ON s.id = e.student_id "
            "WHERE e.course_id=? AND e.role='student' AND e.dropped_at IS NULL",
            (course_id,)).fetchall()
        lines = ["First Name,Last Name,OrgDefinedId,Username,Email,Role"]
        for name, sid_num, d2l_username in current:
            first, _, last = (name or "").partition(" ")
            lines.append(f"{first},{last},{sid_num},{d2l_username},,Learner")
        lines.append(f"Jos\u00e9,Garc\u00eda,#{PROBE_TAG}-cp1252,jgarcia,"
                     "jgarcia@example.edu,Learner")
        classlist = "\r\n".join(lines) + "\r\n"
        r = requests.post(f"{BASE_URL}/instructor/course/{course_id}/import-roster",
                          cookies=admin, allow_redirects=False, timeout=60,
                          files={"roster_file": ("classlist.csv",
                                                 classlist.encode("cp1252"), "text/csv")})
        check("a cp1252 classlist with an accented name does not 500",
              r.status_code != 500, str(r.status_code))
        imported = db().execute(
            "SELECT name FROM students WHERE student_id_number=?",
            (f"{PROBE_TAG}-cp1252",)).fetchone()
        check("and the student was actually imported", bool(imported), str(imported))
        check("with the accents intact",
              bool(imported) and imported[0] == "Jos\u00e9 Garc\u00eda", str(imported))
        check("and no existing student was marked dropped by it",
              db().execute(
                  "SELECT COUNT(*) FROM course_enrollments WHERE course_id=? "
                  "AND dropped_at IS NOT NULL", (course_id,)).fetchone()[0] == 0)

        r = requests.post(f"{BASE_URL}/instructor/course/{course_id}/import-roster",
                          cookies=admin, allow_redirects=False, timeout=60,
                          files={"roster_file": ("logo.png",
                                                 b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4,
                                                 "image/png")})
        check("a PNG upload does not 500", r.status_code != 500, str(r.status_code))
        check("and it explains itself instead",
              "Could not find First Name" in r.text or "could not be read as a CSV" in r.text,
              r.text[:160])

        r = requests.post(f"{BASE_URL}/instructor/course/{course_id}/import-roster",
                          cookies=admin, allow_redirects=False, timeout=120,
                          files={"roster_file": ("huge.csv", b"a," * 2_000_000, "text/csv")})
        check("an oversized upload is capped rather than parsed",
              r.status_code != 500 and "larger than" in r.text, str(r.status_code))

        print("\nA double-clicked marking button does not 500 (DEFECT 4)")
        # The student check-in path already caught IntegrityError; the
        # instructor's own buttons did not, so a second click returned 500
        # even though the record had been written.
        r = requests.post(f"{BASE_URL}/instructor/course/{course_id}/start-session",
                          cookies=admin, allow_redirects=False, timeout=30)
        loc = r.headers.get("location", "")
        tail = loc.rstrip("/").split("/")[-1]
        check("a session could be started for the marking checks",
              tail.isdigit(), f"{r.status_code} {loc!r}")
        sid2 = int(tail) if tail.isdigit() else 0
        for route, data in ((("mark-present",
                             {"student_id": str(student_id), "status": "present"}),
                            ("mark-excused", {"student_id": str(student_id)}))
                            if sid2 else ()):
            con = db()
            con.execute("DELETE FROM attendance_records WHERE session_id=? AND student_id=?",
                        (sid2, student_id))
            con.commit()
            con.close()
            codes, lock = [], threading.Lock()

            def tap(route=route, data=data, codes=codes, lock=lock):
                rr = requests.post(f"{BASE_URL}/instructor/session/{sid2}/{route}",
                                   cookies=admin, data=data,
                                   allow_redirects=False, timeout=60)
                with lock:
                    codes.append(rr.status_code)

            threads = [threading.Thread(target=tap) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            check(f"10 simultaneous {route} clicks never 500",
                  all(c != 500 for c in codes), str(sorted(set(codes))))
            rows = db().execute(
                "SELECT COUNT(*) FROM attendance_records WHERE session_id=? AND student_id=?",
                (sid2, student_id)).fetchone()[0]
            check(f"and {route} leaves exactly one record", rows == 1, str(rows))

        requests.post(f"{BASE_URL}/instructor/session/{sid2}/delete", cookies=admin,
                      data={"confirm": "delete"}, allow_redirects=False, timeout=30)
    finally:
        drop_throwaway(course_id)
        left = db().execute("SELECT COUNT(*) FROM courses WHERE course_code=?",
                            (PROBE_TAG,)).fetchone()[0]
        print(f"\n(throwaway data removed; {left} probe course(s) left behind)")

    print("-" * 52)
    if failures:
        print(f"ROBUSTNESS CHECKS FAILED ({len(failures)}): {failures}")
        return 1
    print("ALL ROBUSTNESS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
