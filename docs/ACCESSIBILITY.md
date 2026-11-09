# Accessibility

**Status: four defects found and fixed. Automated checks pass. This is not a
conformance claim.**

Adsum has been audited with axe-core against the running application, plus a
set of checks written for the things axe cannot test. Everything found was
fixed. What has *not* happened is testing with an actual screen reader by an
actual user, and until that happens nobody should describe this application as
WCAG 2.1 AA conformant.

Automated tooling catches roughly a third of WCAG failures. The rest are
judgement calls that need a person: whether a heading describes what follows
it, whether an error message tells you how to recover, whether focus lands
somewhere sensible after an action, whether the reading order makes sense.

## Why this matters here

For a public institution this is a legal obligation with a date, not a
nice-to-have.

- The US Department of Justice's ADA Title II rule (published 24 April 2024,
  89 FR 31337) sets **WCAG 2.1 Level AA** as the technical standard at
  28 CFR 35.200(b).
- Compliance dates were extended by an Interim Final Rule published **20 April
  2026** (91 FR 20912): **26 April 2027** for public entities serving 50,000
  or more, and 26 April 2028 for smaller entities and special districts.
- The rule's exceptions (28 CFR 35.201) are narrow and none of them covers a
  web application in use. The "password protected" exception is for
  individualised *documents*, not for software behind a login.

There is no open source exemption, and self hosting concentrates the
obligation rather than reducing it, because there is no vendor to hold
responsible.

## What was audited, and how

Audited against the running application, not against the templates:

| Page | Who sees it |
|---|---|
| `student/checkin.html` | Every student, every class |
| `student/code.html` | A student whose camera will not focus |
| `instructor/session.html` | The live session and the projector view |
| `instructor/course_detail.html` | The roster and settings |
| `login.html` | Everyone signing in |

Tools and methods:

- **axe-core 4.10.2**, run in a real browser against the live pages with the
  rulesets `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`.
- **Contrast computed with alpha compositing**, because a translucent
  background has to be flattened against what is behind it before the ratio
  means anything.
- **Real `Tab` keypresses** dispatched through the browser's input layer, so
  `:focus-visible` behaves as it does for a human.
- **Reflow at 320 CSS pixels** (WCAG 1.4.10), checking for content wider than
  the viewport.

## What was found and fixed

**1. Placeholder text at 1.56:1 contrast (WCAG 1.4.3, AA).**
The typed code field styled its placeholder with `opacity: 0.35`, which took
`#757575` from a passing 4.6:1 down to a measured 1.56:1. This was the worst
contrast anywhere in the application, and it was on the page a student reaches
*because their camera has already failed them*. Now 6.41:1.

**2. `inputmode="latin"`, which is not a real value.**
The valid set is `none`, `text`, `tel`, `url`, `email`, `numeric`, `decimal`,
`search`. Anything else is silently ignored, so the mobile keyboard hint did
nothing at all and no error was reported anywhere. Now `text`.

**3. Decorative emoji announced by screen readers.**
Fourteen emoji in headings and buttons were exposed, so a screen reader
announced "chart increasing Analytics" and "graduation cap Adsum". Decorative
ones are now wrapped in `aria-hidden="true"`.

Two of them were *not* decorative and needed the opposite treatment. The
install check table used a tick or a warning triangle as the only indicator of
pass or fail, and the roster used a triangle as the only marker for "not
matched to the D2L classlist". Hiding those would have left a screen reader
with a column that means nothing, so they now carry visually hidden text
alternatives instead.

**4. A toggle that announced nothing.** The projector button changed its own
label with `textContent`, which destroyed the `aria-hidden` wrapper each time
it was pressed, and it never exposed its state. It now rebuilds the markup and
sets `aria-pressed`. Two separate code paths set that label, the click handler
and the Escape key, and fixing only the obvious one left the other regressing
quietly.

## Two false positives, recorded because they cost time

Both were my own measurement errors, and both would have led to "fixing"
something that was already correct.

**A passing alert that read as failing.** The warning box measured 3.72:1
against a 4.5:1 requirement, until the measurement accounted for the
background's alpha. The box is `rgba(250,176,5,0.14)` over white, which
composites to a pale cream; comparing the text against *solid amber* instead
produced a failure that does not exist. Against the real rendered colour it is
6.27:1. **Contrast must be computed against the composited background.**

**Focus rings that looked absent.** Calling `element.focus()` from script does
not trigger `:focus-visible` on links and buttons, so every control reported
`outline-style: none` and the whole application appeared to have no keyboard
focus indicator at all. Dispatching real `Tab` keypresses showed a 3px outline
on every control. **Focus visibility cannot be tested with programmatic
focus.**

## What is tested on every push

`scripts/test_accessibility.py` runs in CI and needs no browser. It locks in
the fixes above: palette contrast ratios, the presence of a `:focus-visible`
rule that actually draws an outline, labels on every student input, valid
`inputmode` values, placeholder contrast including opacity, decorative emoji
being hidden, and the page shell (`lang`, skip link, `main` landmark, labelled
navigation).

It deliberately does **not** attempt to certify conformance, and its own
output says so.

The full browser audit is not in CI, because it needs a browser and a running
instance with a live session. Re-run it by hand after any significant change to
the student pages.

## What has not been done

Stated plainly, because this is the part that matters to a reviewer.

- **No screen reader testing.** Not with NVDA, JAWS, VoiceOver or TalkBack, and
  not by anyone who uses one daily. This is the biggest gap.
- **No testing with users with disabilities.** An audit is not a substitute.
- **No formal VPAT or Accessibility Conformance Report.**
- **Not audited:** the error and empty states of each page, the analytics page
  (which draws charts via Chart.js, a canvas element with known accessibility
  limits), the projector view at realistic lecture-theatre distance, and
  anything behind the admin section.
- **Magnification beyond 320px reflow** and text spacing overrides (WCAG
  1.4.12) have not been checked.

## If you are deploying this at an institution

Commission a WCAG 2.1 AA audit of the three student facing pages before use
beyond a pilot. That is a small piece of work, those pages are where every
student goes, and they are plain HTML with no framework and no third party
scripts, which makes them cheap to audit and cheap to fix.

`docs/IT-REVIEW.md` recommends making that a condition of approval rather than
waiting to be asked for it.
