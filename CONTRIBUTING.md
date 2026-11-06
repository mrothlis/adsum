# Contributing

Thanks for taking an interest. This started as one instructor's tool for his
own classes, so the guiding principle is that it should stay simple enough for
a non-specialist to run and reason about.

## Principles

- **Simple over clever.** An instructor should be able to install this and use
  it without a systems administrator.
- **D2L is the record.** This app is a capture tool. Anything that makes it
  the irreplaceable system of record is a step backwards. The attendance
  ledger is deliberately round-trippable so the app can be rebuilt from a D2L
  grade export.
- **Never destroy the record.** Exports refuse to produce a file that would
  blank out the gradebook. Keep it that way.
- **Privacy by default.** The geofence is opt-in, raw GPS is discarded, and
  no third-party services receive student data.

## Getting set up

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
python run.py
```

## Before opening a pull request

Run all three scripts against a local instance:

```bash
python scripts/verify_install.py         # no server needed
python scripts/smoke_test.py
python scripts/test_isolation.py
python scripts/test_admin_separation.py
ruff check app scripts run.py backup_db.py
```

CI runs all of these on every push, so a failure there is reproducible
locally with the same commands.

If you touch routing, authorization, or anything that reads `course_id` or
`session_id` from a URL, the isolation and admin tests are the ones that
matter. Every such route must go through `app/authz.py`.

Schema changes need an Alembic revision:

```bash
alembic revision --autogenerate -m "describe change"
```

## Style

Plain Python, no framework beyond FastAPI and SQLAlchemy. Comments should
explain *why* something is the way it is, especially where it looks odd —
those are usually the places a past bug is being prevented.

## Reporting bugs

Open an issue with the version, what you expected, and what happened. For
security issues see [SECURITY.md](SECURITY.md) instead.
