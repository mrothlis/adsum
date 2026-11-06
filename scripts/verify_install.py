"""Verify that a fresh clone installs, configures and starts correctly.

This is the clean-room check: it proves someone who has never seen this
project can clone it, copy .env.example, set a few values and get a working
server with no demo data and no leftovers from the original author.

Run it locally or in CI:

    python scripts/verify_install.py

It uses a throwaway database and a spare port, and cleans up after itself.
Nothing it does touches an existing install.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------
# 1. .env.example must document every setting, or new installers hit a wall
#    on a key they were never told about.
# --------------------------------------------------------------------------
def check_env_example_matches_config() -> None:
    cfg = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    declared = set(re.findall(r"(?m)^    ([A-Z][A-Z0-9_]+)\s*:\s*(?:str|int|bool|float)\s*=", cfg))
    documented = set(re.findall(r"(?m)^([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8")))
    check("every setting appears in .env.example", not (declared - documented),
          f"missing: {sorted(declared - documented)}")
    check("no phantom keys in .env.example", not (documented - declared),
          f"not real settings: {sorted(documented - declared)}")


# --------------------------------------------------------------------------
# 2. Nothing belonging to the original author may ship in the repo.
# --------------------------------------------------------------------------
def check_no_personal_identifiers() -> None:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True).stdout.split()
    # LICENSE legitimately carries the copyright holder's name.
    pattern = re.compile(
        r"rothlisberger|drrothlisberger|colstate\.view|columbusstate|"
        r"stu_garver|young_melissa|csu_session|CSU Check-In",
        re.I)
    hits = []
    for rel in tracked:
        if rel == "LICENSE":
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if pattern.search(text):
            hits.append(rel)
    check("no personal identifiers in tracked files", not hits, f"found in: {hits}")


def check_no_secrets_tracked() -> None:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True).stdout.split()
    bad = [f for f in tracked
           if f == ".env" or f.endswith((".db", ".log")) or "CREDENTIALS" in f.upper()]
    check("no secrets or databases tracked by git", not bad, f"tracked: {bad}")


# --------------------------------------------------------------------------
# 3. A clean install must refuse to run insecurely, then work once configured.
# --------------------------------------------------------------------------
def check_clean_install() -> None:
    work = Path(tempfile.mkdtemp(prefix="checkin-verify-"))
    proc = None
    try:
        # Export the committed tree into a scratch directory. This is what a
        # stranger actually receives, and it keeps the developer's own .env
        # out of the way: python-dotenv resolves .env relative to the calling
        # module, so running against the live checkout silently picks up real
        # credentials and the test proves nothing.
        src = work / "src"
        src.mkdir()
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT,
                                 capture_output=True)
        if archive.returncode != 0:
            check("git archive of HEAD succeeded", False,
                  archive.stderr.decode(errors="replace")[:200])
            return
        tar = subprocess.run(["tar", "-x", "-C", str(src)], input=archive.stdout,
                             capture_output=True)
        check("exported a pristine copy of the committed tree", tar.returncode == 0,
              tar.stderr.decode(errors="replace")[:200])
        check("exported tree contains no .env", not (src / ".env").exists())

        env_text = (src / ".env.example").read_text(encoding="utf-8")
        env_path = src / ".env"
        env_path.write_text(env_text, encoding="utf-8")

        base_env = {**os.environ, "PYTHONPATH": str(src)}
        # Strip anything inherited that would mask what .env itself provides.
        for k in ("SECRET_KEY", "ADMIN_PASSWORD", "ADMIN_EMAIL", "DEBUG",
                  "DATABASE_URL", "BASE_URL", "PORT", "INSTITUTION_NAME",
                  "D2L_HOST", "LTI_CONSUMER_KEY"):
            base_env.pop(k, None)

        # 3a. unconfigured -> must refuse
        r = subprocess.run([sys.executable, "-c", "from app.config import settings"],
                           cwd=src, env=base_env, capture_output=True, text=True)
        check("unconfigured install refuses to start", r.returncode != 0)
        check("refusal explains how to fix it",
              "SECRET_KEY" in r.stderr and "ADMIN_PASSWORD" in r.stderr)

        # 3b. configured -> must start
        port = free_port()
        key = secrets.token_hex(32)
        pw = "VerifyInstall!" + secrets.token_hex(4)
        cfg = env_text
        for k, v in {
            "SECRET_KEY": key,
            "ADMIN_PASSWORD": pw,
            "ADMIN_EMAIL": "newadmin@example.edu",
            "INSTITUTION_NAME": "Example College",
            "PORT": str(port),
            "BASE_URL": f"http://127.0.0.1:{port}",
            "DATABASE_URL": "sqlite+aiosqlite:///./verify.db",
        }.items():
            cfg = re.sub(rf"(?m)^{k}=.*$", f"{k}={v}", cfg)
        env_path.write_text(cfg, encoding="utf-8")

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=src, env=base_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        health = None
        for _ in range(60):
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r2:
                    health = r2.read().decode()
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1)

        if health is None and proc.poll() is not None:
            print((proc.stdout.read() if proc.stdout else "")[-1500:])
        check("configured install starts and answers /healthz", health is not None and "ok" in health)

        if health:
            import sqlite3
            db = sqlite3.connect(src / "verify.db")
            admins = db.execute("SELECT email, is_admin FROM instructors").fetchall()
            check("admin account created from .env",
                  admins == [("newadmin@example.edu", 1)], str(admins))
            check("no demo courses on a fresh install",
                  db.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 0)
            check("no demo students on a fresh install",
                  db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0)
            inst = db.execute(
                "SELECT name, lti_consumer_secret FROM institutions").fetchone()
            check("institution named from .env", inst and inst[0] == "Example College", str(inst))
            # Every install must get its own LTI secret, never a shipped constant.
            check("LTI secret generated per install",
                  bool(inst) and len(inst[1] or "") >= 20
                  and inst[1] != "demo-secret-change-in-production")
            db.close()
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    print("Verifying a clean install\n" + "-" * 48)
    check_env_example_matches_config()
    check_no_secrets_tracked()
    check_no_personal_identifiers()
    check_clean_install()
    print("-" * 48)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
