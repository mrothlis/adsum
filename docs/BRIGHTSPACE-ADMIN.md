# Adsum: information for Brightspace administrators

*Last reviewed: November 2026.*

An instructor can forward this page to IT. It describes what Adsum is, what it
stores, where it runs, and the single registration it needs in Brightspace.

---

## 1. What it is

Adsum is a small self-hosted web application that records classroom
attendance. The instructor projects a QR code that changes every 20 seconds,
students scan it with their phones and are marked present, and at the end of
the term the instructor exports a CSV and imports it into the Brightspace
gradebook.

It is one Python process (FastAPI under uvicorn) and one SQLite file. There is
no container image, no message queue and no external service. The project is
MIT licensed. Source: https://github.com/mrothlis/adsum

It is **not** a system of record. The full per-date attendance ledger is
written into the gradebook as a text grade item, so Brightspace holds the
authoritative copy and Adsum can be rebuilt from a grade export.

## 2. Where it runs and who runs it

Adsum runs wherever the adopting department puts it: a campus VM, a cloud VM,
or in a pilot, an instructor's own machine behind a tunnel. It must have a
public HTTPS address because students' phones connect to it directly.

It is **not** hosted by the author and does not call home. At runtime it talks
to no server other than your own Brightspace. The person who installs it is
the data controller for whatever it holds.

If your institution would rather it ran on managed infrastructure, that is the
correct answer once more than one or two people depend on it. See
`docs/INSTALL.md`.

## 3. What it stores

Per student, taken from the LTI launch and from an optional Brightspace
Classlist export:

| Data | Source | Why |
| --- | --- | --- |
| Name | LTI launch, or Classlist export | Roster display |
| Brightspace username (`lis_person_sourcedid`) | LTI launch | The match key for launches and for gradebook import |
| Email address | LTI launch | Fallback identity, contact |
| Institutional student ID (`OrgDefinedId`) | Classlist export | Roster display |
| Attendance records: one row per student per class meeting, with status (present, late, excused, absent) and a timestamp | The check-in itself | The product |
| IP address of the check-in, and whether it fell inside a configured campus range | The check-in | Optional anti-proxy check |
| Distance from the classroom, in metres, plus an inside/outside flag | The check-in, **only when the instructor has enabled a geofence on that course** | Geofence enforcement. The latitude and longitude are used to compute the distance and are then discarded |

The geofence is off by default. When it is off, no location data is requested
or stored. When it is on, students see a notice before the browser asks for
location, the server computes how far that single reading was from the
classroom, and only that distance is written to the attendance row. The
coordinates are never stored, so there is no record of where a student was,
only whether they were close enough. Nothing tracks a student between
check-ins.

Instructor accounts hold a name, an email address and a PBKDF2-SHA256 password
hash.

## 4. What it does not store or do

- No continuous or background location. One reading per check-in, only on
  geofenced courses.
- No analytics, no telemetry, no advertising identifiers, no third-party data
  sharing. Student-facing pages load no third-party resources at all.
- No grades other than the attendance percentage it computes itself.
- No access to any Brightspace data. Adsum makes **no API calls to
  Brightspace**. It only receives LTI launches, and the instructor moves
  grades across by downloading a CSV and importing it by hand.
- No cross-course or institution-wide reporting. An administrator of the Adsum
  install cannot read any course's attendance; that is enforced in code and
  asserted by its test suite.

## 5. FERPA posture

Attendance records are student education records under FERPA (and equivalent
rules elsewhere). Adsum takes no position on your obligations; it is
self-hosted software, so the institution or department that deploys it remains
the custodian.

Practical points for a review:

- The data never leaves the host you install it on, except the CSV the
  instructor downloads and imports into Brightspace.
- There is no vendor, no processor agreement to sign, and no third party with
  access.
- Access control is per course: an instructor can only reach courses they
  teach. There is no self-registration and no impersonation feature.
- Data minimisation is the default: no location unless a geofence is switched
  on, and no identifiers beyond those needed to match a student to the
  gradebook.
- Deletion: the database is a single SQLite file, and dropping a course or the
  file removes the data. Accounts are deactivated rather than deleted so that
  grades already imported into Brightspace still refer to a real course.
- Hosting it on a machine without disk encryption, backups or patching is the
  real risk, not the application. `SECURITY.md` has the deployment checklist.

## 6. The registration being requested

**Today (LTI 1.1).** Adsum uses the legacy External Learning Tool flow. An
instructor can configure this themselves inside their own course, and in that
case IT is not involved at all. The instructor needs three values from the
Adsum administrator: the launch URL, a consumer key and a consumer secret.

What IT is being asked for, if anything:

1. Optionally, register Adsum **once at organisation level** as an External
   Learning Tool, with the launch URL `https://<adsum-host>/lti/launch`, a
   consumer key and a consumer secret. Instructors can then add the link to
   their own courses without ever holding the secret. This is the preferred
   arrangement for a shared install, because anyone holding key and secret can
   forge a launch as any user.
2. In the tool link's **Security Settings**, enable **Send system username**,
   **Send user email** and **Send user name**. Without these the launch
   arrives unidentified and no student can be matched to a roster.
3. Nothing else. No API key, no Valence application registration, no service
   account, no scopes, no network access into Brightspace.

**Soon (LTI 1.3).** D2L is retiring LTI 1.1. New 1.1 integrations cannot be
configured in Brightspace after the February 2027 release, and 1.1 launches
stop working in late 2027. See the timeline and citations at the top of the
project README. LTI 1.3 registration is an organisation-level administrator
workflow, so when Adsum gains 1.3 support an administrator will have to
register it as an LTI Advantage tool. Adsum does not support 1.3 today, and
that work is the project's stated next step rather than a shipped feature.

## 7. Security summary

- HTTPS is required. QR tokens and session cookies are otherwise readable in
  transit.
- LTI 1.1 launches are verified as OAuth 1.0a HMAC-SHA1 signatures against the
  configured consumer secret. A bad signature is rejected.
- QR check-in tokens are HMAC-SHA256 over the session id and a rotating time
  slot, valid for 20 to 40 seconds, so a forwarded screenshot is stale.
- Sign-in is throttled: five failures per IP, then a ten-minute lockout.
- The application refuses to start with a placeholder or short `SECRET_KEY`,
  or an empty administrator password.

Report a vulnerability privately through the repository's Security tab, as
described in `SECURITY.md`.

## 8. Questions an administrator usually asks

**Does it need a firewall rule into Brightspace?** No. Traffic is one way:
browsers reach Adsum, and Brightspace only posts an LTI launch to it.

**Does it write to the gradebook automatically?** No. A human downloads a CSV
and imports it. Automatic passback would need either LTI Advantage (AGS) or
the Valence Grades API, and both require a registration from your office.

**Can it read or write the Brightspace Attendance tool?** No. There is no
Valence API for writing attendance registers, confirmed by D2L staff in 2026.
Attendance data is readable through Brightspace Data Hub, but that does not
help with capture, which is why Adsum writes to the gradebook instead.

**How many users does it handle?** SQLite comfortably handles course and
department scale. It has not been tested beyond that, and PostgreSQL is not
supported today.

**Who do we call?** Whoever installed it. The author maintains it in spare
time, through GitHub issues, with no support contract.
