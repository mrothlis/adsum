# LTI 1.3 implementation plan

Status: **plan only. No LTI 1.3 code exists.** This document specifies what
would be built, in what order, and what each step must get right. It is
written to be checkable: every protocol requirement below cites the
specification section that imposes it, so a reviewer can verify the plan
rather than trust it.

Companion documents: [IT-REVIEW.md](IT-REVIEW.md) for the institutional
review, [BRIGHTSPACE-ADMIN.md](BRIGHTSPACE-ADMIN.md) for the administrator's
install procedure.

---

## 1. Why, and by when

D2L is retiring LTI 1.1. From their deprecation plan, last updated 8 October
2026 (https://community.d2l.com/brightspace/kb/articles/25994-lti-1-1-deprecation-plan):

| Date | What happens |
|---|---|
| February 2027 | No new LTI 1.1 integrations can be configured through the Brightspace UI. Existing links keep working |
| September 2027 | LTI 1.1 launches stop working |

Do not cite a release number for these. "Release 20.27.2" circulates widely,
including in earlier drafts of this project's own documentation, and it does
not appear in D2L's deprecation article. It could not be sourced.

Two consequences:

1. An institution adopting Adsum after February 2027 cannot use the 1.1 path at
   all. The pilot described in the README stops being reproducible.
2. LTI 1.3 registration is an **organisation level administrator** workflow.
   The project's original "no IT ticket" premise expires with 1.1. The honest
   replacement is "one administrator action per institution, then every
   instructor self serves."

Standalone mode is unaffected by all of this and needs no LMS at all.

---

## 2. What 1.3 buys beyond survival

Worth being clear, because this is not only a compatibility exercise.

| Gain | Detail |
|---|---|
| No shared secret | Brightspace signs launches with its private key; Adsum verifies against the published keyset. Adsum holds no Brightspace credential, and there is nothing to leak or rotate on both sides at once |
| Automatic roster | Names and Role Provisioning Service replaces the manual CSV classlist import, which is the largest remaining usability wart |
| Automatic grades | Assignment and Grade Services replaces the manual CSV gradebook import, including the "set Maximum Points to 100" trap that silently renders every student at 1000% |
| Explicit permissions | Scopes are named and auditable, which is a better security conversation than "we share a secret" |

**What does not change:** the gradebook stays the system of record. AGS writes
the same numeric total and the same dated ledger that the CSV path writes
today. `restore-from-d2l` keeps working. The CSV path is retained, not
replaced, both as a fallback and for institutions that decline the service
scopes.

---

## 3. Scope of work, in dependency order

### Phase 1: Core launch (required)

The only mandatory phase. At the end of it Adsum launches from Brightspace over
1.3 with **zero OAuth scopes**, and roster and gradebook stay manual.

**New endpoints**

| Endpoint | Purpose |
|---|---|
| `GET/POST /lti13/login` | OIDC third party initiated login. Receives `iss`, `login_hint`, `target_link_uri`, `lti_message_hint`, `client_id`, `lti_deployment_id`. Mints `state` and `nonce`, redirects to the platform authorization endpoint |
| `POST /lti13/launch` | Receives `id_token` and `state`. Validates, then hands off to the existing provisioning logic |
| `GET /lti13/jwks` | Adsum's public keyset. Brightspace accepts a JWKS URL only, with no option to paste a public key, so this is mandatory even in Phase 1 if services are ever wanted |
| `GET /lti13/config` | Registration detail for an administrator to read, and the target for Dynamic Registration in Phase 4 |

**Every validation, with its source.** Each gets a negative test. A validation
with no test is a validation that silently regresses, which is exactly how
CVE-2022-31158 and CVE-2022-31157 happened in the 1EdTech PHP library.

| # | Check | Source |
|---|---|---|
| 1 | Signature verifies against the platform JWKS for the `kid` in the header | SEC-10 §5.1.3 |
| 2 | `alg` is RS256. Reject `none`, reject HS256, never infer from the token | SEC-10 §5.1.3 |
| 3 | `iss` matches the registered issuer exactly | SEC-10 §5.1.3 |
| 4 | `aud` contains our `client_id` | SEC-10 §5.1.3 |
| 5 | Reject if `aud` has extra audiences and `azp` is absent or not us | SEC-10 §5.1.3 |
| 6 | `exp` in the future, `iat` not implausibly old | SEC-10 §5.1.3 |
| 7 | `nonce` has not been seen before, and is **consumed** on use | SEC-10 §5.1.3 |
| 8 | `state` matches the value in the cookie set at login initiation | SEC-10 §5.1.3 |
| 9 | `https://purl.imsglobal.org/spec/lti/claim/message_type` is a type we accept | LTI-13 §5.3 |
| 10 | `https://purl.imsglobal.org/spec/lti/claim/version` is `1.3.0` | LTI-13 §5.3 |
| 11 | `deployment_id` is one an administrator registered. Never auto trust a new one | LTI-13 §3.1.3.1 |
| 12 | `target_link_uri` is one of ours. Never redirect to a value from the token | LTI-13 §5.3 |
| 13 | Roles parsed from the roles claim, never used to escalate. A learner launch cannot mint an instructor | Adsum requirement, already true in the 1.1 path |

**Key handling.** RS256, 2048 bit minimum, `kid` present even with a single
key, private key never in the repository and never in a log, rotation by
publishing both keys before retiring the old one (SEC-10 §6.4).

**Identity.** The stable subject is the `sub` claim. The existing 1.1 path
matches on D2L user ID first and email second, for a reason recorded in the
code: matching on email alone let a correctly signed launch bind to any
existing account. Keep that precedence and add `sub` ahead of both.

### Phase 2: NRPS, the automatic roster

One scope: `https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly`

The memberships URL arrives in the launch under the
`https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice` claim. Adsum
must use only the URL delivered for the course being acted on, and never
construct or reuse one (LTI-13 §6.1).

This replaces the CSV import, and it should reuse the reconciliation logic
already built rather than becoming a second code path. That logic already
computes added, dropped and changed students, marks drops instead of deleting
them, and guards against a file that would drop most of a roster. An NRPS
response is just another input to the same four phase parse, compute, guard,
apply pipeline.

Two things to get right:

- **Keep the wrong input guard.** An NRPS call returning an unexpectedly small
  roster, for instance because the platform's "include users not known to this
  deployment" setting is off, must not silently mark a class dropped. The
  existing guard already refuses an import that would drop more than half a
  roster. Route NRPS through it.
- **Filter to the Learner role** (NRPS 2.0 §2.4.1) so Adsum does not retrieve
  staff records it has no use for. Follow `Link: rel="next"` for pagination.

Names and email are released only if the platform's deployment settings allow
it, so do not assume they are present.

### Phase 3: AGS, the automatic gradebook

Scopes: `.../lti-ags/scope/score` and `.../lti-ags/scope/lineitem.readonly`

Deliberately **not** `.../lti-ags/scope/lineitem`, which includes DELETE on
gradebook columns. If Adsum must create its own column rather than the
instructor creating one, that scope becomes necessary and should be requested
explicitly rather than bundled. Where the link is already associated with a
grade item, the launch carries a `lineitem` URL and `score` alone is enough
(AGS 2.0 §3.1.1.3).

The Score payload needs `userId`, `scoreGiven`, `scoreMaximum`,
`activityProgress`, `gradingProgress` and `timestamp`.

**The timestamp rule will bite.** Scores for a user must carry a strictly
increasing timestamp; a correction stamped earlier than the value already
recorded is silently ignored by the platform. So stamp at the moment of
correction, not with the class date. "Silently ignored" means a grade that
appears to post and does not, which is the worst failure mode in this
application.

Also: AGS scores are only stored for roles with **Can be graded in Grades**
enabled in Brightspace. Without it the platform returns 422 on score POST
(https://community.d2l.com/brightspace/kb/articles/23752). That is an
administrator setting, so it belongs in the install documentation and in the
error message Adsum shows.

### Phase 4: Dynamic Registration (the highest leverage item)

**Brightspace supports 1EdTech Dynamic Registration**, as a first class radio
choice on the Register Tool screen
(https://community.d2l.com/brightspace/kb/articles/23730, 5 November 2026).
The administrator pastes one URL and Brightspace and Adsum exchange
configuration automatically.

This is worth building for one reason: it turns the single unavoidable IT
interaction from a twenty field form transcribed by hand into pasting one URL.
That directly addresses the objection that 1.3 makes adoption harder.

Mechanics to honour: Brightspace sends a one time token that expires after one
use or one hour; it opens the tool endpoint in a new tab deliberately, to avoid
cross site cookie problems; and the resulting registration is **disabled by
default per the specification**, so the install instructions must tell the
administrator to enable it afterwards. Keep the manual path too, since
Dynamic Registration is at Public Candidate Final stage and an administrator
may prefer to see the fields.

### Phase 5: 1.1 to 1.3 migration, if an existing install is in flight

Brightspace can migrate an existing 1.1 link to 1.3 and sends a migration
claim carrying the legacy identity. **Ignore it and every student in a mid
semester course arrives as a new person and their attendance history
detaches.** Migration is one way.

D2L documents that a migration claim is sent but does not document its
contents. So read every property the specification defines (`user_id`,
`context_id`, `resource_link_id`, `tool_consumer_instance_guid`,
`oauth_consumer_key`, `oauth_consumer_key_sign`), tolerate any of them being
absent, verify `oauth_consumer_key_sign` when present, and fall back to an
explicit administrator confirmation rather than silently re-binding accounts
when it is not. Do not promise specific Brightspace claim contents.

---

## 4. Library choice

`pylti1p3` is the only maintained general option for Python. MIT licensed,
roughly 160,000 downloads a month, no CVEs against it. But its **last release
was 20 November 2022**, it has no FastAPI adapter, and reading its source turned
up three gaps that matter:

1. It sets `verify_aud: False`.
2. It never checks `azp`.
3. `check_nonce()` does not consume the nonce.

Those are checks 4, 5 and 7 in the table above, and 7 is precisely the class of
bug behind the 1EdTech PHP library CVEs.

**Decision: implement validation directly with PyJWT**, pinned to a version
clear of the known `PyJWKClient` and algorithm confusion advisories, with the
thirteen checks written out explicitly and a negative test for each. The
rationale is that the validation is about 150 lines, it is the entire security
boundary, and a dependency that disables two of the checks and has not shipped
in three years is not doing the work the dependency exists to do. Read
`pylti1p3` for reference, and borrow its test vectors.

This decision deserves revisiting if `pylti1p3` resumes releases.

---

## 5. Things that will go wrong

Collected so they are designed for rather than discovered.

- **Extensions must be enabled at BOTH the registration and the deployment.**
  D2L states this explicitly for both NRPS and AGS. It is the single most
  common cause of "AGS is on but grades do not post." The install instructions
  must say it twice, and Adsum's error message for a 403 on a service call
  should name it.
- **Deep linked links can only open in an iframe.** There is no new window
  escape, so third party cookie restrictions are not avoidable by opening a
  tab. The LTI Platform Storage postMessage mechanism is mandatory rather than
  optional for storing `state` and `nonce`. This is the biggest unknown in the
  plan and needs a spike against a real tenant early, because a QR code on a
  projector inside an iframe is the core use case.
- **Adsum currently sends `X-Frame-Options: DENY` on every response**
  (`app/main.py`), which forbids being framed at all. That is correct today,
  because the 1.1 path uses "Open as External Resource" and opens in a new
  window. It would break 1.3 deep linking outright. The fix is not to drop the
  header globally: keep `DENY` everywhere, and relax it only on the launch and
  post launch instructor routes, via `Content-Security-Policy:
  frame-ancestors` naming the institution's Brightspace host rather than a
  blanket `ALLOWALL`. The Brightspace host is already configured, so the
  allowed framer is known. Student check in pages are reached by scanning a
  code on a phone, never framed, and must keep `DENY`.
  **Add a test that asserts the student pages still refuse to be framed**, or
  this is exactly the kind of header that gets loosened globally during
  debugging and never tightened again.
- **The token endpoint and the audience are different values.**
  `https://auth.brightspace.com/core/connect/token` is where tokens are
  fetched; `https://api.brightspace.com/auth/token` is only ever a value in
  the `aud` claim of the client assertion. Posting to the audience is a classic
  implementer error.
- **For the client credentials grant, LTI requires both `iss` and `sub` to be
  the `client_id`**, which is stricter than plain OAuth and was added by
  erratum in May 2019.
- **Redirect URLs must match exactly.** Brightspace does not support
  wildcarding, so every host an institution might use has to be registered.
- **The issuer is opaque.** D2L defines it only as "the URL of the Brightspace
  instance." Have the administrator copy it from the registration screen and
  store it as configuration. Do not construct or derive it.
- **A registration cannot change tool after a deployment exists**, and D2L
  recommends registering disabled, configuring, then enabling.
- **Brightspace creates an "LTI Advantage Service User"** to log LTI grade
  activity. It is shared by all LTI Advantage tools, has no permissions, and is
  invisible in classlists. An administrator who finds it in an audit log
  should not be alarmed, so say so in the documentation.

---

## 6. Order of work

1. Phase 1 behind a feature flag, with 1.1 untouched and still the default.
2. A **mock platform in the test suite** so a full launch can be exercised with
   no institutional sandbox. This is borrowed from
   `cedarville-university/attendance-tracker`, which stands one up in CI, and
   it is the single best idea available to steal: Adsum's launch path is its
   only cryptographic, externally reachable component and nothing currently
   tests it end to end.
3. The iframe and platform storage spike against a real tenant. Do this before
   Phase 2, because a bad answer changes the design.
4. Phase 4, Dynamic Registration, before Phases 2 and 3. It costs little and it
   is what makes the administrator conversation easy, which is the actual
   adoption blocker.
5. Phases 2 and 3, each individually switchable, because an institution may
   approve one scope and not another.
6. Phase 5 only when a real 1.1 install needs migrating.

Keep the 1.1 path for Brightspace instances before the cutoff and for Moodle
and Canvas. Keep standalone mode independent of all of it.

---

## 7. What has not been verified

Stated so nobody builds on it by accident.

- The literal issuer string and OIDC authorization endpoint path for any
  given tenant. The platform keyset pattern
  `https://<tenant>/d2l/.well-known/jwks` was confirmed live and publicly
  reachable against a USG-hosted tenant on 7 November 2026, returning RSA
  signing keys. The other two values must be copied from the registration
  screen, not constructed.
- Whether a Deep Linking checkbox still exists under Extensions. D2L's own
  pages contradict each other, with the current article saying it is enabled by
  default and two others describing a checkbox. Write instructions that
  tolerate both.
- Default on or off state for the AGS and NRPS checkboxes. D2L never states it.
  Every vendor guide tells administrators to tick them.
- Whether Brightspace sends `oauth_consumer_key_sign` in the migration claim.
  It is optional in the specification and undocumented by D2L.
- Whether Dynamic Registration can update an existing registration. 1EdTech
  says no for Brightspace on an undated page; current D2L docs are silent.
- On a multi-campus or system-hosted Brightspace tenant, who holds the
  `Manage LTI Tools` permission at the organisation level, whether a campus
  administrator can register tools at all, and whether a test tenant exists.
  Entirely outside published documentation. Ask first, because it determines
  who the audience for the review packet actually is.
