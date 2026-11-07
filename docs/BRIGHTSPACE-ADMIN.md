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
stop working in September 2027. See the timeline and citations at the top of the
project README. LTI 1.3 registration is an organisation-level administrator
workflow, so when Adsum gains 1.3 support an administrator will have to
register it as an LTI Advantage tool. Adsum does not support 1.3 today, and
that work is the project's stated next step rather than a shipped feature.

**The full 1.3 registration procedure is in section 9 below**, written so you
can review the work before approving it. The implementation plan is in
[LTI-1.3-PLAN.md](LTI-1.3-PLAN.md) and a packet for an institutional security
and accessibility review is in [IT-REVIEW.md](IT-REVIEW.md).

## 7. Security summary

- HTTPS is required. QR tokens and session cookies are otherwise readable in
  transit.
- LTI 1.1 launches are verified as OAuth 1.0a HMAC-SHA1 signatures against the
  configured consumer secret. A bad signature is rejected.
- QR check-in tokens are HMAC-SHA256 over the session id and a rotating time
  slot, valid for 20 to 40 seconds, so a forwarded screenshot is stale.
- Sign-in is throttled: five failures for one account or twenty-five from one
  IP address, then a ten-minute lockout. Only failures are counted and a
  success clears the count, so a mistyped password during class does not lock
  anyone out.
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

---

## 9. Registering Adsum as an LTI 1.3 Advantage tool

**Read this before approving.** Adsum does not speak LTI 1.3 yet, so this
section describes the procedure you would follow once it does. It is published
now so you can review the permissions and the work involved before committing
to anything.

Sources are D2L's own documentation, with the article number and the date it
was last updated. Where D2L's pages contradict each other, that is noted rather
than smoothed over.

### 9.1 Permission you need

**External Learning Tools > Manage LTI Tools, at the organisation level.**
Required both to register and to deploy (article 23662). On a USG-hosted
tenant this may sit with USG rather than the campus, which is the first thing
to establish, because it determines who can approve this at all.

Also relevant: **Grades > Manage Items and Categories** if Adsum will create
its own gradebook column, and **External Learning Tools > Manage LTI
Migrations** if you are converting an existing 1.1 link.

### 9.2 Values Adsum gives you

Adsum publishes these at `https://<adsum-host>/lti13/config`, so they can be
read rather than transcribed.

| Field | Value |
|---|---|
| Name | Adsum |
| Domain | `<adsum-host>` (exact host, no wildcards) |
| Redirect URL | `https://<adsum-host>/lti13/launch` |
| OpenID Connect Login URL | `https://<adsum-host>/lti13/login` |
| Target Link URI | `https://<adsum-host>/lti13/launch` |
| Keyset URL | `https://<adsum-host>/lti13/jwks` |

Two notes. **Redirect URLs must match exactly**, because Brightspace does not
support wildcarding (23662), so every hostname the install might answer on
needs registering. And Brightspace accepts **a keyset URL only**, with no
option to paste a public key: "Brightspace supports a tool's JWK Key Set only"
(article 23661, 9 May 2026). RS256, 2048-bit.

### 9.3 Values you give back to Adsum

Brightspace generates these. They go into Adsum's `.env`.

| Brightspace label | What Adsum does with it |
|---|---|
| Client Id | Expected `aud` on every launch, and the OAuth client ID |
| Brightspace Keyset URL | Where Adsum fetches your public keys to verify launch signatures |
| Brightspace OAuth2 Access Token URL | Where Adsum requests service tokens |
| Brightspace OAuth 2 Audience | The `aud` in Adsum's token request. **Not** a URL anything is posted to |
| OpenID Connect Authentication Endpoint | Where Adsum redirects during login |
| Issuer | Stored verbatim and matched on every launch |
| Deployment Id | Issued when you create the deployment, not at registration |

**Copy the Issuer and the authentication endpoint from the confirmation screen
rather than constructing them.** D2L defines the issuer only as "the URL of the
Brightspace instance" and publishes no rule for deriving it, so Adsum treats it
as an opaque string.

For reference, your platform keyset is normally at
`https://<your-brightspace-host>/d2l/.well-known/jwks`. That pattern was
confirmed against a live USG-hosted tenant on 7 November 2026: it answered
unauthenticated with RSA signing keys. The token endpoint
`https://auth.brightspace.com/core/connect/token` and the audience
`https://api.brightspace.com/auth/token` are published by D2L in article
23661. They are different values, and confusing them is a common
implementation error.

### 9.4 Registering, the short way

Brightspace supports **1EdTech Dynamic Registration** (article 23730,
5 November 2026). You paste one URL and the two systems exchange configuration.

1. **Admin Tools > Manage Extensibility > LTI Advantage > Register Tool**
2. Choose **Dynamic**, then **Register**
3. Enter `https://<adsum-host>/lti13/register`
4. Brightspace opens Adsum in a new tab, deliberately, to avoid cross-site
   cookie problems. The one-time token expires after one use or one hour.
5. **Open the new registration, enable it, and save.** Dynamic Registration
   leaves the registration **disabled by default, per the specification**
   (23662). This step is skipped often enough to be worth stating twice.

**Configure Deployment** is on by default, so a deployment is usually created
in the same pass. If deployment creation fails, the registration still counts
as successful, so check both.

### 9.5 Registering, the manual way

If you would rather see every field:

1. **Admin Tools > Manage Extensibility > LTI Advantage > Register Tool**
2. Choose **Standard**, then **Register**
3. Fill in the fields from 9.2
4. Under **Extensions**, tick only what you are approving. See 9.6.
5. Under **Roles**, tick **Send Institution Role** only if you want
   institution-level roles in the launch. Adsum does not need it; context roles
   are enough.
6. **Register**, then copy the values in 9.3

D2L recommends registering the tool **disabled**, configuring it fully, then
enabling (23730).

A note so D2L's own documentation does not confuse you: current D2L articles
steer administrators towards **Manage Extensibility > Apps**. That is the
partner catalogue, and a self-hosted MIT-licensed tool is not in it. The
**LTI Advantage > Register Tool** path above is the right one.

### 9.6 Permissions, and the one that catches everyone

**Extensions must be enabled at BOTH the registration and the deployment.**
D2L states this for Names and Roles ("Once enabled at the tool registration
level, you must also enable it at the deployment level", article 23756) and
again for grades ("enable the Assignment and Grade Services extension for both
the registration and the deployment", article 5876). Enabling it in one place
only is the most common cause of a tool that launches fine and then cannot post
grades.

What Adsum asks for, and what each is for:

| Extension | Needed for | Decline it and... |
|---|---|---|
| *(none)* | Launching, and recording attendance | Everything still works. Roster and gradebook stay manual CSV |
| **Names and Role Provisioning Services** | Reading the class roster | You keep importing a classlist CSV each time the roster changes |
| **Assignment and Grade Services** | Writing attendance to the gradebook | You keep importing a grades CSV at the end of term |

Adsum requests the read-only roster scope, the score-posting scope, and the
read-only line item scope. It does **not** request the full `lineitem` scope,
which would allow it to delete gradebook columns. If Adsum needs to create its
own column rather than the instructor creating one, that permission becomes
necessary and should be discussed rather than granted as part of a bundle.
Full per-scope justification is in [IT-REVIEW.md](IT-REVIEW.md) section 7.

Adsum does not use **Platform Notification Service**, **Asset Processor**,
**Activity Item Profile** or **Submission Review**. Leave them off.

**Deep Linking**: current D2L documentation says it is enabled by default on
new registrations (23662, 5 November 2026), while other D2L pages still
describe a checkbox under Extensions (23755). If your version shows the
checkbox, tick it.

### 9.7 Creating the deployment, and scoping the pilot

Deployments are on a different screen from registrations.

1. **Admin Tools > External Learning Tools > LTI Advantage > New Deployment**
2. Select **Adsum** from the Tool dropdown
3. Name and describe it
4. Under **Extension**, tick the same extensions you ticked at registration.
   See 9.6.
5. Under **Security Settings**, enable **User Information** (Name, Email, User
   ID, Username, Org Defined Id) and **Org Unit Information**. Without these
   the launch arrives unidentified and no student can be matched.
6. **Add Org Units**, and for a pilot select **one course offering**
7. **Create Deployment**, then copy the Deployment Id

**Org-unit scoping is the pilot control.** Deployments can be shared to
specific org units or to descendants of a parent (23662), so the pilot's blast
radius is exactly one course. D2L recommends this approach for testing (5876).

Two settings worth deciding deliberately:

- **Classlist including users not known to this deployment.** With it off, a
  roster request returns only users who have already launched the tool. With it
  on, Adsum can request the full classlist (23756). Off is the conservative
  choice, and Adsum's roster reconciliation is built to refuse an import that
  would mark most of a class as dropped, so a short roster will not silently
  wreck anyone's attendance.
- **Gradeable roles.** AGS scores are only stored for roles with **Can be
  graded in Grades** enabled. Without it, score posting fails with HTTP 422
  (article 23752).

Once a deployment exists, **the tool for that deployment cannot be changed**
(23662).

### 9.8 What the instructor does next

From the deployment page, **View Links > New Link**, or the instructor adds it
in their own course through **Course Admin > External Learning Tools > LTI
Advantage**. They launch it once, which creates the course and their instructor
account in Adsum, and from then on they manage their own settings.

### 9.9 Migrating an existing LTI 1.1 link

Only relevant if a 1.1 pilot is already running mid-semester.

Brightspace can migrate a 1.1 link to 1.3 and sends a migration claim carrying
the legacy user identity. **Adsum must honour it, or every student in the
course arrives as a new person and their attendance history detaches from
them.** Migration is one-way.

D2L confirms a migration claim is sent but does not document its contents, so
Adsum reads every property the specification defines, tolerates any being
absent, and falls back to an explicit administrator confirmation rather than
silently re-binding accounts. Do not migrate a live course without a gradebook
export taken first.

### 9.10 One thing you might see and wonder about

Brightspace automatically creates an **LTI Advantage Service User** role and
user to log LTI grade activity (article 23660). It is created at first tool
registration, shared by all LTI Advantage tools, has no permissions, is
invisible in classlists, and regenerates if deleted. It is not Adsum's, and
Adsum does not control it.

