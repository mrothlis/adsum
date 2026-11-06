#!/usr/bin/env bash
# Reproduce the CI "integration" job locally, exactly.
#
# Exists because a test was pushed that passed against the live install and
# failed in CI: it read DATABASE_URL from .env only, and CI has no .env. The
# difference between the two environments is the thing that breaks tests, so
# it needs to be reproducible on demand rather than discovered from a red
# build.
#
# Runs against a throwaway database on a spare port, with no .env in scope,
# using the same environment variables the workflow sets.
#
# Usage:  bash scripts/ci_local.sh
set -u

# `pwd -W`, not `pwd`: git is a native Windows binary and MSYS path
# conversion is off, so `git -C /c/Users/...` fails and -- worse -- the
# failure is easy to miss because the pipeline into tar still "succeeds",
# leaving an empty tree and a confusing "No module named 'app'".
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && { pwd -W 2>/dev/null || pwd; })"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  PY="$(grep -E '^PYTHON=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r')"
fi
[ -z "$PY" ] && PY="python"

PORT="${CI_LOCAL_PORT:-8111}"
# A NATIVE path, not `mktemp -d`: on Windows/MSYS mktemp returns /tmp/... which
# the native Python interpreter cannot resolve, so uvicorn failed with
# "No module named 'app'" even though bash had cd'd there successfully.
WORK="${LOCALAPPDATA:-${TMPDIR:-/tmp}}/Temp/adsum-cilocal-$$"
rm -rf "$WORK"; mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

echo "Exporting HEAD to a clean tree (no .env, like a fresh checkout)"
if ! git -C "$ROOT" archive HEAD | tar -x -C "$WORK"; then
  echo "git archive failed"; exit 1
fi
cd "$WORK" || exit 1
# Fail loudly rather than letting an empty tree look like an import bug.
if [ ! -d "$WORK/app" ]; then
  echo "EXPORT PRODUCED NO app/ DIRECTORY -- aborting"; exit 1
fi

# Same values as .github/workflows/ci.yml
export SECRET_KEY="ci-only-secret-key-not-used-anywhere-else-0123456789"
export ADMIN_EMAIL="admin@example.edu"
export ADMIN_PASSWORD="CiTestPassword123!"
export DATABASE_URL="sqlite+aiosqlite:///./ci.db"
export BASE_URL="http://127.0.0.1:${PORT}"
export SEED_DEMO="true"
export DEBUG="false"
unset TEST_COURSE_ID

echo "Starting the server on port ${PORT}"
# Explicit PYTHONPATH: the interpreter is a native Windows binary and does not
# inherit bash's notion of the current directory reliably.
export PYTHONPATH="$WORK"
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$WORK/server.log" 2>&1 &
SERVER_PID=$!
# Kill first, then pause: Windows keeps the directory locked while the
# process holds it, so removing immediately reports "Device or resource busy".
cleanup() {
  kill "$SERVER_PID" 2>/dev/null
  sleep 2
  rm -rf "$WORK" 2>/dev/null
}
trap cleanup EXIT

for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
  echo "SERVER FAILED TO START"; tail -40 "$WORK/server.log"; exit 1
fi
echo "Server up"
echo

FAILED=""
for script in smoke_test test_isolation test_admin_separation test_admin_page; do
  printf '%-26s ' "$script"
  if OUT="$("$PY" "scripts/${script}.py" 2>&1)"; then
    echo "PASS"
  else
    echo "FAIL"
    FAILED="$FAILED $script"
    echo "$OUT" | grep -E "FAIL|Error|Traceback|assert" | head -15 | sed 's/^/    /'
  fi
done

echo
if [ -n "$FAILED" ]; then
  echo "FAILED:$FAILED"
  exit 1
fi
echo "ALL CI INTEGRATION TESTS PASS LOCALLY"
