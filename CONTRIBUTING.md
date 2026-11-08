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
- **Privacy by default.** The geofence is opt-in, no location is requested
  unless an instructor switches it on, and no third-party services receive
  student data. When the geofence *is* on, only the distance from the
  classroom and an in/out flag are stored; the coordinates are discarded.
  An earlier version did store them, which contradicted every document that
  described this behaviour, so `scripts/test_security.py` now asserts that no
  attendance row holds a coordinate. Any change here must keep the docs, the
  code and that test agreeing.

## LTI 1.1, and why that is changing

The project is on LTI 1.1 because the legacy course-level External Learning
Tool flow is the only one an instructor can configure alone. That was a
deliberate adoption decision, not an oversight.

It has a deadline. D2L is retiring LTI 1.1: new 1.1 integrations cannot be
configured in Brightspace after the February 2027 release, and 1.1 launches
stop working in late 2027. The timeline and sources are at the top of the
[README](README.md).

What that means for contributions:

- **LTI 1.3 support is the most valuable thing anyone could work on.** Nothing
  in the codebase speaks 1.3 today. Open an issue before starting, because it
  touches launch handling, identity and configuration at once.
- 1.3 registration in Brightspace is an organisation-level administrator job,
  so a 1.3 implementation changes the adoption story and the docs, not just
  the protocol. Expect to update README, `docs/INSTALL.md` and
  `docs/BRIGHTSPACE-ADMIN.md` in the same pull request.
- Arguments of the form "1.1 is fine because it needs no IT involvement" are
  no longer complete on their own. Say what happens after 2027 too.

## Getting set up

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # Windows: copy .env.example .env
                                # set SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
python run.py
```

`requirements-dev.txt` is what makes `ruff` available; it is not a runtime
dependency.

A fresh database has no courses, because courses are created by an LTI launch.
For development set `SEED_DEMO=true` and point `DATABASE_URL` at a throwaway
file: that creates a demo course with students, which the live-instance tests
need.

## Before opening a pull request

Run `bash scripts/ci_local.sh`, which is exactly what CI runs. If you would
rather run them one at a time, these need nothing but the code:

```bash
python scripts/test_scoring.py           # grade arithmetic, against exact fractions
python scripts/test_migrations.py        # startup backs up, migrates, or refuses
python scripts/test_accessibility.py     # the automatable part of WCAG 2.1 AA
python scripts/verify_install.py         # clean install, from a git archive
ruff check app scripts run.py backup_db.py alembic/env.py
```

The rest need a running instance **and** a course with at least one enrolled
student:

```bash
python scripts/smoke_test.py
python scripts/test_isolation.py
python scripts/test_admin_separation.py
python scripts/test_admin_page.py
```

The easiest way to run that set the way CI does is:

```bash
bash scripts/ci_local.sh
```

which exports `HEAD` to a scratch directory, starts the server on a spare port
with a seeded throwaway database and no `.env` in scope, and runs all four.
That difference between a local install and CI is what usually breaks a test.

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
