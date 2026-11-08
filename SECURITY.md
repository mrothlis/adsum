# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's **Report a vulnerability**
button on the Security tab, rather than opening a public issue. Include the
steps to reproduce and what an attacker could achieve.

This is a small project maintained in spare time; expect an acknowledgement
within a week.

## What this application holds

Student names, institutional usernames and ID numbers, and attendance records.
On a course where the instructor has enabled the geofence, the distance of each
check-in from the classroom. Raw coordinates are not kept, and neither is any
network address: the IP address and campus-range flag that earlier versions
stored have been removed.
In the United States these are education records protected by FERPA, and
equivalent rules apply elsewhere. Anyone deploying this is responsible for
hosting it accordingly.

## Deployment checklist

- [ ] `SECRET_KEY` is random and unique to the install. It signs session
      cookies and QR check-in tokens; a shared or guessable value lets anyone
      forge both.
- [ ] `ADMIN_PASSWORD` is strong, and `CREDENTIALS.txt` is never committed.
- [ ] `DEBUG=false` and `SEED_DEMO=false`.
- [ ] Served over HTTPS. QR tokens and session cookies travel in the clear
      otherwise.
- [ ] The LTI consumer secret is unique, not a default, and is rotated on both
      the app and the D2L side together. It lives at `/admin`, not in `.env`.
- [ ] Database file and `backups/` are not inside the web root and are backed
      up somewhere durable.
- [ ] Only trusted people hold `is_admin`, which grants control of the shared
      D2L and LTI settings for every course on the install.

## Security model

- **Authentication.** Signed cookies (`itsdangerous`), PBKDF2-SHA256 password
  hashing, and a login throttle of 5 failures per account or 25 per IP, then
  a 10-minute lockout. The per-IP figure is the looser of the two on purpose:
  a whole campus can share one address, so a tight per-IP limit locks out
  bystanders. Only failures are counted, and a success clears the counter.
- **Authorization.** Every course and session route verifies that the signed-in
  instructor teaches that course. Institution-wide settings require `is_admin`.
  `scripts/test_isolation.py` and `scripts/test_admin_separation.py` assert
  both, and should be run after changes to routing or authorization.
- **QR tokens.** HMAC-SHA256 over the session id and a rotating time slot, so
  tokens expire shortly after display. The endpoint that issues them is
  instructor-only; exposing it would let anyone check in remotely.
- **LTI 1.1.** OAuth 1.0a HMAC-SHA1 signatures are verified against the
  configured consumer secret. Launches with a bad signature are rejected.
- **Location.** Nothing is collected unless an instructor enables the geofence
  on a course. When it is enabled, the browser reports a position once, the
  server computes the distance from the classroom, and only that distance and
  an in/out flag are stored. The latitude and longitude are discarded. There
  is no background or repeated location collection.
  An audit found that an earlier version did write the coordinates to the
  database, contradicting this file. That is fixed; the migration shipped with
  the fix erases any coordinates captured before it, and
  `scripts/test_security.py` fails the build if an attendance row ever holds
  one again.
