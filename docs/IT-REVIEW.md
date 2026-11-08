# Adsum: information for an IT security and accessibility review

**What this document is.** Everything a university IT office needs to decide
whether to approve Adsum, in the order a reviewer usually asks for it. It is
written to be read by people who review software for a living, so it states
the weak points as plainly as the strong ones.

**It is a template.** The federal requirements and the protocol facts below
apply anywhere. The parts that depend on your institution are marked, because
the arguments that actually decide these reviews are the ones grounded in your
own data classification policy and your own application review process, not in
generic assurances. Sections 4, 6 and 8 are where you fill in local policy.

**Bottom line.** Adsum is open source software your institution installs on
its own server. There is no vendor, no cloud service, no contract, no
procurement, and no student data leaves your infrastructure. Most of a
standard third party risk review is therefore inapplicable, but two things
genuinely do require your attention: the server is yours to secure and patch,
and the accessibility obligation is real and unmet. Both are covered below.

Repository: https://github.com/mrothlis/adsum (MIT licence)

---

## 1. What it does, in four sentences

An instructor opens a check in window from a page in their course. Adsum shows
a QR code on the projector that changes every few seconds. Students scan it
with their own phone and are recorded present. The instructor closes the
window, and the attendance total plus a dated per class ledger is written into
the Brightspace gradebook.

Nothing happens on a timer. The instructor opens and closes every session.

---

## 2. The one thing that shapes every answer below

**Brightspace is the system of record. Adsum is a pipe.**

Attendance is written into the D2L gradebook as a numeric column and a text
column holding the full dated history. If Adsum's server were destroyed with
no backup, the attendance record still exists in Brightspace, and Adsum can
rebuild its own database from a gradebook export.

This is a deliberate response to a documented failure in this product
category. Instructure's Roll Call attendance tool held the authoritative
record itself, miscalculated grades from May 2024 to July 2025, and had no
second copy to reconcile against
(https://community.instructure.com/en/discussion/664791/transparency-update-roll-call-grade-calculation-incident).
Adsum recomputes every percentage from the underlying records on each read and
keeps the full ledger in the LMS.

---

## 3. What this is not

A reviewer's instinct is to reach for a vendor assessment. The following are
true and verifiable from the repository:

| Usual question | Adsum |
|---|---|
| Who is the vendor? | Nobody. Unpaid author, MIT licence, no company, no support contract |
| Where is it hosted? | On your server. There is no Adsum cloud, no SaaS tier, no hosted option |
| What data leaves the institution? | None. Outbound network calls go only to your own Brightspace host |
| Subprocessors? | None |
| What does the author have access to? | Nothing. No telemetry, no phone home, no remote access, no licence check |
| Can we read the source? | All of it. Roughly 14,000 lines of Python, HTML and SQL |
| Can we fork it and stop taking updates? | Yes. MIT, no restrictions |

**Verify the "no outbound calls" claim yourself** rather than taking it on
trust. Student facing pages deliberately load no third party resources at all,
which you can confirm from the Content Security Policy in `app/main.py`.
Instructor only pages reach four external origins, and those are the only ones
the policy permits: `unpkg.com` and `cdn.jsdelivr.net` for Leaflet (the
geofence map) and Chart.js (the analytics page), and
`tile.openstreetmap.org` plus its wildcard subdomains for map tiles. If your standards forbid
CDN loads, those two pages are the only things affected and the libraries can
be vendored locally.

---

## 4. Data inventory

Verified against the schema and the code that writes it, not from memory. Full
column list in `app/models.py`.

### Student data stored

| Field | Source | Why |
|---|---|---|
| Name | LMS launch or classlist import | Shown on the instructor's roster |
| Email | LMS launch or classlist import | Identity matching |
| Institutional ID | Classlist import | Identity matching and gradebook export |
| LMS username and user ID | LMS launch | The identity the gradebook matches on |
| Course enrollment and role | LMS launch or classlist import | Who is in the class |
| Attendance records | Check in | The point of the application |

### What a check in records

Timestamp, the QR token used, the computed distance in metres from the
classroom when the geofence is enabled, and the status (present, late,
excused, absent). That is the whole of it.

**No network address is recorded against a student.** An earlier version
stored the IP address of each check in and a flag for whether it fell inside a
configured campus range. Both are gone, along with the campus range check
itself: behind the reverse proxy this install guide recommends, every student
appeared to arrive from the proxy's own address, so the stored value was
identical on every row. It identified nobody while still looking like personal
data, and the check it fed could not distinguish a student in the classroom
from a student in a residence hall on the same network.

**Raw GPS coordinates are deliberately not stored.** When the geofence is
enabled, the browser's reported position is used to compute one distance and
then discarded. The code writes nulls into the coordinate columns explicitly
(`app/routes/student.py`), because retaining them would turn an attendance
record into a location history. The two unused columns are being removed so
the schema cannot be misread.

### What is never collected

No Social Security numbers. No payment or financial data. No health data. No
date of birth. No government ID. No biometrics. No photographs. No continuous
or background location. No advertising or analytics identifiers of any kind.

### Classification

**[Fill in from your own policy.]** Most institutional data classification
schemes have a middle tier for student education records and a top tier
reserved for Social Security numbers, payment card data, protected health
information and financial account data. Adsum touches none of the latter, so it
usually lands in the middle tier rather than the most restricted one, which
often matters because the top tier triggers a heavier approval path.

Two things are worth settling at intake rather than late:

1. **Which tier applies**, confirmed by the data owner rather than asserted by
   whoever is proposing the tool.
2. **Whether your third party or supplier requirements engage at all.** They
   are commonly written to apply when data is exchanged with, or accessible
   by, an outside party. A self hosted deployment with no external access does
   not meet that condition. Read the exact wording of your own policy; the
   argument is that the requirement is scoped to third party exchange, not
   that anyone is waiving it.

---

## 5. FERPA

Adsum processes education records, so FERPA applies to the institution's use of
it. The relevant point is what self hosting changes.

The school official exception at 34 CFR 99.31(a)(1)(i)(A) permits disclosure to
school officials with a legitimate educational interest
(https://www.ecfr.gov/current/title-34/subtitle-A/part-99/subpart-D/section-99.31).
Subparagraph (B) adds a test for **outside** parties to whom an institution has
outsourced a service: direct control, use limitation, and the annual
notification criteria.

On a self hosted deployment there is no outside party and no disclosure outside
the institution, so the outsourcing test does not engage. FERPA does not
require a written agreement for the school official exception in any case,
unlike the studies exception at 99.31(a)(6)(iii)(C).

**Three caveats, stated because they are the ones that actually matter:**

1. If anyone who is not an institutional employee or contractor holds
   administrator or database access to the server, the outside party test comes
   back regardless of where the hardware sits. The author should not have
   access to a production instance.
2. The institution still owes the annual notification and the directory
   information treatment it already owes. Nothing about Adsum changes that.
3. This is not legal advice. Your Legal Affairs office decides.

---

## 6. Accessibility, which is the weakest part of this proposal

**State of play: an automated audit has been run and everything it found has
been fixed. There is still no VPAT or Accessibility Conformance Report, and no
screen reader testing has been done.** That is the honest answer, and it
should drive a condition of approval rather than a claim of compliance.

What was done: axe-core 4.10.2 against the running application across the
WCAG 2.0/2.1 A and AA rulesets, plus checks for the things axe cannot test
(contrast with alpha compositing, focus visibility under real `Tab`
keypresses, and reflow at 320 CSS pixels). Four defects were found and fixed:
placeholder text at 1.56:1 contrast on the typed-code page, an invalid
`inputmode` value that silently disabled the mobile keyboard hint, fourteen
decorative emoji announced by screen readers, and a projector toggle that
exposed no pressed state and destroyed its own screen-reader markup each time
it was used. Two status emoji that carried meaning were given visually hidden
text alternatives rather than being hidden. All pages now report zero axe
violations, and the checks run on every push via
`scripts/test_accessibility.py`.

**Automated tooling catches roughly a third of WCAG failures**, so this is
emphatically not a conformance claim. Full detail, including two measurement
errors made during the audit and corrected, is in
[ACCESSIBILITY.md](ACCESSIBILITY.md).

The obligation is real and dated:

- DOJ's ADA Title II rule (published 24 April 2024, 89 FR 31337) sets **WCAG
  2.1 Level AA** as the technical standard at 28 CFR 35.200(b)
  (https://www.federalregister.gov/documents/2024/04/24/2024-07758/nondiscrimination-on-the-basis-of-disability-accessibility-of-web-information-and-services-of-state).
- The compliance date was extended by Interim Final Rule published **20 April
  2026** (91 FR 20912). For public entities serving a population of 50,000 or
  more it is now **26 April 2027**
  (https://www.federalregister.gov/documents/2026/04/20/2026-07663/extension-of-compliance-dates-for-nondiscrimination-on-the-basis-of-disability-accessibility-of-web).
- The rule's exceptions (28 CFR 35.201) are narrow: archived content,
  pre-existing conventional electronic documents, third party content posted
  without an arrangement, individualised password protected *documents*, and
  pre-existing social media posts. None covers a web application in active
  use. The password protected exception is for individualised documents such
  as a spreadsheet or a PDF, not for software behind a login.

**[Check your own system or state guidance.]** Several systems have issued
their own memos that are more explicit than the federal rule, including
statements that the obligation covers online course content even when it sits
behind a login. If yours has, it is more useful to cite than the federal rule
alone, because it closes the "it is only visible to enrolled students"
argument before anyone makes it.

A tool launched inside your LMS is in scope. There is no open source
exemption, and self hosting concentrates the obligation rather than reducing it
because there is no vendor to hold responsible.

**What is verified**: zero axe violations on every page tested, every student
input labelled, a visible 3px focus indicator on every control under real
keyboard navigation, no horizontal scrolling at 320 CSS pixels, and all
measured contrast ratios at or above the AA threshold. Student pages are plain
HTML with no JavaScript framework and no third party scripts, which is also
what makes them cheap to audit and cheap to fix. The typed code fallback
exists so that a student whose camera does not work, or who cannot use one,
has a route in that needs no camera and no app.

**What is unexamined**: screen reader behaviour end to end with NVDA, JAWS,
VoiceOver or TalkBack; testing by anyone who actually uses assistive
technology; the error and empty states of each page; the analytics page, which
draws charts on a `canvas` element and has known limits; the projector view at
lecture-theatre distance; and text spacing overrides (WCAG 1.4.12).

**Recommended condition of approval:** a WCAG 2.1 AA review of the three
student facing pages by your accessibility office, including screen reader
testing, before any use beyond a pilot. The entire student facing surface is
three pages (`app/templates/student/`), so this is a small piece of work, and
the automated findings are already cleared out of the way. We would rather you impose that condition than
approve the tool on an unverified claim.

---

## 7. Security

### What LTI 1.3 changes, and why we are asking for it

Adsum currently uses LTI 1.1, which authenticates launches with a **shared
secret** held by both systems. LTI 1.3 replaces that with asymmetric
cryptography: Brightspace signs each launch with its private key and Adsum
verifies it against Brightspace's published public keyset. Adsum never holds a
Brightspace credential, and there is no shared secret to leak, rotate or
mishandle.

This is a security improvement, not only a compatibility exercise. It is also
unavoidable: D2L stops allowing new LTI 1.1 integrations in **February 2027**
and 1.1 launches stop working in **September 2027**
(https://community.d2l.com/brightspace/kb/articles/25994-lti-1-1-deprecation-plan,
updated 8 October 2026).

### Permissions requested, and why each one

LTI Advantage permissions are explicit scopes. We are asking for three, and
deliberately not asking for a fourth.

| Scope | What it allows | Why |
|---|---|---|
| `lti-nrps/.../contextmembership.readonly` | Read the class roster for a course Adsum was launched in | Replaces a manual CSV classlist export and import. Read only. NRPS defines no write operation |
| `lti-ags/scope/score` | Post a score to a gradebook column Adsum owns | The only way to write attendance into the gradebook. The platform rejects scores for columns owned by other tools |
| `lti-ags/scope/lineitem.readonly` | Find Adsum's own gradebook column again after a course copy | Read only |

**We are not requesting `lti-ags/scope/lineitem`**, which is the broader
version of the third permission. It includes DELETE on gradebook columns. If
Adsum must create its own column rather than the instructor creating it, that
scope becomes necessary, and in that case we would prefer to discuss it
explicitly rather than have it granted quietly as part of a bundle.

We are also not requesting `result.readonly` (read all scores in Adsum's
columns), `contextgroup.readonly`, Tool Settings, or Platform Notification
Service.

Three further limits worth recording:

- A basic launch requires **zero scopes**. If you approve launch only and
  decline both services, Adsum still works with manual CSV roster import and
  manual gradebook import, which is how it works today.
- Adsum only ever calls service URLs that Brightspace supplied in a launch for
  the course being acted on. It never constructs or stores a roster URL for a
  course it was not launched in.
- NRPS returns an opaque user ID and roles by default. Names and email are
  released only if your Brightspace deployment settings allow it, so granting
  the roster scope does not by itself release personally identifiable
  information. Adsum queries filtered to the Learner role so it does not
  retrieve staff records it has no use for.

### Launch validation

The LTI 1.3 implementation will validate every item the specification requires
of a tool: issuer, audience, rejection of extra audiences, `azp`, signing
algorithm, expiry, issued at, single use nonce, `state` matched to a cookie,
message type, LTI version, and deployment ID, with the signature checked
against Brightspace's keyset. Roles are read from the launch and never trusted
to escalate privilege: a learner launch cannot produce an instructor account.

This matters because the reference implementations have had real failures here.
CVE-2022-31158 and CVE-2022-31157 were nonce validation flaws in the 1EdTech
PHP library, and CVE-2023-23611 was a line item hijack in Open edX. The
implementation plan (`docs/LTI-1.3-PLAN.md`) lists each required check with its
specification reference and commits to a negative test per check, because a
validation nobody tests is a validation that silently regresses.

### Current security posture, already implemented and tested

| Control | Implementation |
|---|---|
| Transport | HTTPS only. Session cookies are `Secure`, `HttpOnly`, `SameSite=Lax` |
| Cross site request forgery | `SameSite=Lax` plus a same origin check on every state changing request |
| Passwords | Hashed, never stored or logged in plaintext |
| Brute force | Per account and per IP throttling with lockout. Counts failures only, and clears on success, so a student mistyping a code is not locked out during class |
| Authorization | Every route that takes an ID checks ownership. Instructors cannot read each other's rosters |
| Replay | QR tokens are HMAC signed and expire in seconds. LTI nonces are single use |
| Injection and XSS | Parameterised queries throughout. Jinja autoescaping on, no `\|safe` on user data. Content Security Policy set |
| Headers | CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` |
| Audit | Instructor actions and all LTI launches are logged |
| Backup | Nightly, using SQLite's online backup API. A schema migration takes its own backup first and refuses to start if it cannot complete |

These are regression tested, not asserted. `scripts/test_security.py` and
`scripts/test_robustness.py` exercise each previously found hole, and the full
full suite in `scripts/ci_local.sh` runs on every push via GitHub Actions. A
reviewer can read the test files to see exactly what is claimed and verified.
`scripts/test_grade_roundtrip.py` is the one to read first: it proves that a
round trip through the D2L gradebook returns every grade unchanged.

### What you are responsible for

Honestly stated, because this is the real cost of self hosting:

- Operating system and dependency patching on the host.
- TLS certificate management.
- Server hardening to your Minimum Security Standards.
- Backup verification and retention.
- Monitoring and incident response.
- Deciding who holds administrator access.

Adsum is a Python application with a small dependency set, pinned in
`requirements.txt`, and it runs on SQLite with no separate database server. It
has no privileged system requirements, and needs no inbound access to
Brightspace or any other institutional system.

Dependency updates are configured in `.github/dependabot.yml`, so upstream
advisories surface as pull requests on the repository. That is upstream
hygiene, not patching of your server: applying an update to a running install
is still your deploy step.

### Known limitations

- **No penetration test has been performed.** No third party has assessed it.
- **No formal threat model document exists**, though `SECURITY.md` records the
  attacks considered and the responses.
- **Attendance fraud is possible and the design says so.** A student can scan
  the projected code and send it to someone outside the room within the few
  seconds before it rotates. The optional geofence narrows this, and
  neither closes it. Adsum reduces the cost of taking attendance; it does not
  prove bodily presence, and the documentation does not claim it does.
- **MIT licence means no warranty and no indemnification.** This is a real
  difference from a commercial vendor and should be weighed rather than
  overlooked.

---

## 8. What we are asking for

1. **Confirm the data classification**, and confirm whether a self hosted
   deployment with no external access triggers your third party contractual
   agreement requirement. In most policies that requirement is conditioned on
   exchanging data with, or granting access to, an outside party, which does
   not happen here.
2. **Route this through the institution's locally hosted application review**
   rather than a vendor assessment. There is no self hosted variant of HECVAT:
   HECVAT 4 (released 10 February 2025) folded the former On Premise workbook
   into one conditional form, so there is no separate form to submit
   (https://www.educause.edu/hecvat). Peer institutions handle this distinction
   explicitly. UT Austin's published matrix separates an "Internal
   Application, developed and hosted on UT infrastructure, no external vendor
   has access" from vendor products and routes it to architecture review and
   minimum security standards instead (https://security.utexas.edu/risk/appreg).
   We are asking for the ordinary treatment of a locally hosted application,
   not an exemption.
3. **Register the LTI 1.3 tool** per `docs/BRIGHTSPACE-ADMIN.md`, scoped to a
   single course offering for the pilot. Brightspace deployments can be shared
   to specific org units, so the blast radius of the pilot is one course.
4. **Grant the three scopes in section 7**, or tell us to proceed launch only
   with manual CSV import, which also works.
5. **Impose the accessibility audit in section 6 as a condition.** We are
   asking for it rather than waiting to be asked.

**What we are not asking for:** a contract, a purchase, a procurement
exception, an indemnity, a data sharing agreement, or any access for the
author.

---

## 9. Pilot proposal

One course, one semester, one instructor. Attendance also kept by the existing
method for the first two weeks so the two can be compared. A named rollback:
turn off the tool link, and the attendance already written to the gradebook
stays there because the gradebook is the record.

Success is judged on whether the gradebook column matches what the instructor
believes happened in the room, on whether any student could not check in, and
on the accessibility findings.

---

## 10. Where to look in the source

For a reviewer who wants to verify rather than read the whole thing:

| Question | File |
|---|---|
| What data exists | `app/models.py` |
| What a check in records, and that GPS is discarded | `app/routes/student.py` |
| Launch validation | `app/routes/lti.py` |
| Authorization checks | `app/authz.py` |
| Security headers and CSP | `app/main.py` |
| What security properties are claimed and tested | `scripts/test_security.py` |
| Attacks considered | `SECURITY.md` |
| Accessibility audit, findings and gaps | `docs/ACCESSIBILITY.md` |
| Accessibility checks run on every push | `scripts/test_accessibility.py` |
| Planned 1.3 protocol work and every required validation | `docs/LTI-1.3-PLAN.md` |
| Administrator install procedure | `docs/BRIGHTSPACE-ADMIN.md` |

---

## Appendix: sources

Protocol and platform

- LTI 1.3 Core: https://www.imsglobal.org/spec/lti/v1p3/
- LTI Security Framework 1.0: https://www.imsglobal.org/spec/security/v1p0/
- Names and Role Provisioning Services 2.0: https://www.imsglobal.org/spec/lti-nrps/v2p0
- Assignment and Grade Services 2.0: https://www.imsglobal.org/spec/lti-ags/v2p0
- D2L LTI 1.1 deprecation plan (updated 8 October 2026): https://community.d2l.com/brightspace/kb/articles/25994-lti-1-1-deprecation-plan
- D2L tool registration, deployment and links (updated 5 November 2026): https://community.d2l.com/brightspace/kb/articles/23662-tool-registration-deployment-and-links

Regulatory and policy

- FERPA, 34 CFR 99.31: https://www.ecfr.gov/current/title-34/subtitle-A/part-99/subpart-D/section-99.31
- ADA Title II web rule (24 April 2024): https://www.federalregister.gov/documents/2024/04/24/2024-07758/nondiscrimination-on-the-basis-of-disability-accessibility-of-web-information-and-services-of-state
- Compliance date extension (20 April 2026): https://www.federalregister.gov/documents/2026/04/20/2026-07663/extension-of-compliance-dates-for-nondiscrimination-on-the-basis-of-disability-accessibility-of-web
- 28 CFR 35.200 and 35.201: https://www.law.cornell.edu/cfr/text/28/35.200
- HECVAT: https://www.educause.edu/hecvat
- UT Austin application registration and risk matrix, a documented example of
  routing institution-hosted applications away from vendor assessment:
  https://security.utexas.edu/risk/appreg

**Add your own.** This template deliberately cites only federal law and
general practice. The citations that carry the most weight in a review are
your institution's own data classification policy, minimum security standard,
application review procedure, and any system level accessibility guidance. Put
those in before sending it, and cite them with their effective dates.

Prior art and precedent

- Roll Call grade calculation incident: https://community.instructure.com/en/discussion/664791/transparency-update-roll-call-grade-calculation-incident
- Apereo HEOSAT, an open source counterpart to HECVAT, launched 30 September 2026: https://www.apereo.org/news/2026/apereo-foundation-launches-heosat-help-higher-education-evaluate-adopt-and-strengthen

**Ask your IT office for two things rather than guessing.** First, the minimum
security standard the server must meet, since that is what actually governs a
self hosted deployment and it is usually a separate document from the data
classification policy. Second, the controlling version of any system wide IT
standard you are held to, because these move between policy platforms and the
copies in circulation are often out of date.
