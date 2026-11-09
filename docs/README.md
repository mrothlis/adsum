# Adsum documentation

Four audiences. Start with the one that describes you.

## I administer the server

**[DEPLOY-LINUX.md](DEPLOY-LINUX.md)** is the worked deployment: containers
with `docker compose`, or a virtualenv under systemd, then a reverse proxy
terminating TLS. Covers configuration, backups and restores, upgrades,
monitoring, and what to tell a security reviewer.

**[DEPLOY-WINDOWS.md](DEPLOY-WINDOWS.md)** is the same thing for a Windows
host, using Task Scheduler.

Configuration files to copy rather than retype are in
[`../deploy/`](../deploy): nginx and Caddy server blocks, a systemd unit, and
a backup service with a timer.

After installing, run the repository's own checker:

```bash
python scripts/verify_install.py            # or:
docker compose exec adsum python scripts/verify_install.py
```

## I have to review this before it goes anywhere

**[IT-REVIEW.md](IT-REVIEW.md)** is written for you, and answers the
questions in the order they get asked: what data is stored, what leaves the
server, what is logged, how authentication and authorisation work, what is
tested, and what the tool does not do. It also tells you how to verify its
claims rather than take them on trust.

**[SECURITY.md](../SECURITY.md)** is the reporting policy and the list of
security properties that are tested.

**[ACCESSIBILITY.md](ACCESSIBILITY.md)** is the axe-core audit, the four
defects found and fixed, and the measurement method. **It is not a
conformance claim**: there has been no screen-reader testing and there is no
VPAT.

Two dates belong in your decision. D2L stops allowing new LTI 1.1
integrations in **February 2027**, and existing 1.1 launches stop working in
**September 2027**. Adsum uses LTI 1.1 today; the migration is planned in
[LTI-1.3-PLAN.md](LTI-1.3-PLAN.md).

## I administer Brightspace

**[BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md)** is the LTI side: what
Adsum needs registered, what permissions it asks for, and what it does not
ask for.

## I am an instructor who wants to use it

**[INSTALL.md](INSTALL.md)** walks the whole thing end to end, including
adding the link in your own D2L course and getting attendance into the
gradebook. The [README](../README.md) is the shorter tour.

---

## Everything here

| File | What it is |
| --- | --- |
| [INSTALL.md](INSTALL.md) | Full installation and first-run walkthrough, D2L setup, troubleshooting |
| [DEPLOY-LINUX.md](DEPLOY-LINUX.md) | Worked Linux deployment: containers or systemd, proxy, backups, monitoring |
| [DEPLOY-WINDOWS.md](DEPLOY-WINDOWS.md) | Worked Windows deployment with Task Scheduler |
| [IT-REVIEW.md](IT-REVIEW.md) | Security, privacy and FERPA review packet |
| [BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md) | What a Brightspace administrator needs to do |
| [ACCESSIBILITY.md](ACCESSIBILITY.md) | Accessibility audit and its limits |
| [LTI-1.3-PLAN.md](LTI-1.3-PLAN.md) | The LTI 1.3 migration, and why it has a deadline |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed, and why |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | How to work on it, and how to run the tests |
