"""Accessibility regressions: the WCAG 2.1 AA checks that can be automated.

Why this exists: a public institution has a hard WCAG 2.1 AA obligation (28
CFR 35.200(b), compliance date 26 April 2027 for larger public entities), and
an LMS-integrated tool is in scope even though it sits behind a login. See
docs/IT-REVIEW.md section 6.

**What this script is not.** Automated testing catches somewhere around a
third of WCAG failures. Passing here does NOT mean the application is WCAG 2.1
AA conformant, and no document in this repository should say that it does.
Screen reader behaviour, meaningful focus order, link purpose, error recovery
and whether the language is understandable all need a human. This script locks
in the defects that were found and fixed, so they cannot come back silently.

**It needs no browser**, deliberately, so it runs in CI next to the other
suites. It parses templates and CSS and computes colour contrast the way a
browser would, including alpha compositing. The full browser audit that found
these (axe-core against the live site) is documented in docs/ACCESSIBILITY.md.

Two findings from the original audit are worth remembering, because both were
my own false positives before being checked properly:

1. Contrast must be computed against the COMPOSITED background. Ignoring the
   alpha channel on `rgba(250,176,5,0.14)` compared text against solid amber
   and reported a passing 6.3:1 alert as a failing 3.7:1.
2. Focus visibility cannot be tested with programmatic `element.focus()`.
   That does not trigger `:focus-visible` on links and buttons, so every
   control looks like it has no focus ring. Real Tab keypresses showed the
   rings were there all along.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "app" / "templates"
CSS = ROOT / "app" / "static" / "style.css"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- colour ----
def _srgb(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    r, g, b = (_srgb(x) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def parse_colour(text: str):
    """#rgb, #rrggbb or rgb()/rgba() -> (r, g, b, a)."""
    text = text.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", text)
    if m:
        return tuple(int(c * 2, 16) for c in m.group(1)) + (1.0,)
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", text)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.fullmatch(r"rgba?\(([^)]+)\)", text)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", " ").split(",")]
        if len(parts) == 1:
            parts = m.group(1).split()
        nums = [float(p.rstrip("%")) for p in parts[:4]]
        while len(nums) < 3:
            nums.append(0.0)
        return (nums[0], nums[1], nums[2], nums[3] if len(nums) > 3 else 1.0)
    return None


def over(fg, bg):
    """Composite fg (with alpha) onto an opaque bg. The step that, when
    skipped, turns a passing alert into a phantom failure."""
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)


def css_vars() -> dict:
    text = CSS.read_text(encoding="utf-8")
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", text)}


def resolve(value: str, variables: dict, depth: int = 0):
    value = value.strip()
    m = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    if m and depth < 5:
        return resolve(variables.get(m.group(1), ""), variables, depth + 1)
    return parse_colour(value)


def main() -> int:
    variables = css_vars()
    white = (255, 255, 255, 1.0)

    print("Palette meets WCAG 1.4.3 where the stylesheet claims it does")
    # Each of these carries a contrast claim in a comment in style.css.
    # A claim nobody checks drifts the first time someone tweaks a hex code.
    for var, bg, need, label in [
        ("--color-text-muted", white, 4.5, "muted body text on white"),
        ("--color-primary", white, 4.5, "primary link/button text on white"),
        ("--color-primary-hover", white, 4.5, "primary hover on white"),
    ]:
        col = resolve(f"var({var})", variables)
        if not col:
            check(f"{var} is defined", False, "not found in style.css")
            continue
        ratio = contrast(col, bg)
        check(f"{label} ({var}) >= {need}:1", ratio >= need, f"{ratio:.2f}:1")

    print("\nA visible keyboard focus indicator is defined")
    css = CSS.read_text(encoding="utf-8")
    # 2.4.7. Tested here by reading the stylesheet, because programmatic
    # focus in a headless browser does not trigger :focus-visible.
    has_focus_rule = ":focus-visible" in css
    check("a :focus-visible rule exists", has_focus_rule)
    block = re.search(r":focus-visible[^{]*\{([^}]*)\}", css)
    check("and it draws an outline",
          bool(block) and "outline" in block.group(1),
          (block.group(1).strip()[:60] if block else "no rule"))
    check("the outline is not suppressed anywhere",
          not re.search(r"outline\s*:\s*(none|0)\s*;", css),
          "an 'outline: none' would remove the only focus affordance")

    print("\nStudent pages: every input is labelled and typed correctly")
    valid_inputmode = {"none", "text", "tel", "url", "email",
                       "numeric", "decimal", "search"}
    for path in sorted((TPL / "student").glob("*.html")):
        html = path.read_text(encoding="utf-8")
        name = path.name
        # Strip CSS and Jinja comments before scanning. The comment explaining
        # why the old opacity was wrong contains the string "opacity: 0.35",
        # which the scan below matched, so this test failed on its own
        # explanation of the bug it was written to prevent.
        scan = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
        scan = re.sub(r"\{#.*?#\}", "", scan, flags=re.S)

        for m in re.finditer(r'inputmode="([^"]*)"', scan):
            # An invalid value is silently ignored, so the phone shows the
            # wrong keyboard and nothing anywhere reports an error.
            check(f"{name}: inputmode={m.group(1)!r} is a valid value",
                  m.group(1) in valid_inputmode,
                  f"not one of {sorted(valid_inputmode)}")

        for m in re.finditer(r"<input\b((?:[^>]|\n)*?)>", html):
            attrs = m.group(1)
            itype = re.search(r'type="([^"]+)"', attrs)
            if itype and itype.group(1) in ("hidden", "submit"):
                continue
            ident = re.search(r'id="([^"]+)"', attrs)
            labelled = bool(
                (ident and f'for="{ident.group(1)}"' in html)
                or "aria-label" in attrs or "aria-labelledby" in attrs)
            field = ident.group(1) if ident else attrs[:30]
            check(f"{name}: input {field!r} has a label", labelled)

        # 1.4.3 applies to placeholder text too, and a reduced opacity
        # multiplies straight through it.
        for m in re.finditer(r"::placeholder\s*\{([^}]*)\}", scan):
            body = m.group(1)
            op = re.search(r"opacity\s*:\s*([\d.]+)", body)
            colour = re.search(r"color\s*:\s*([^;]+);", body)
            if op:
                check(f"{name}: placeholder opacity does not sink contrast",
                      float(op.group(1)) >= 1.0,
                      f"opacity {op.group(1)} measured 1.56:1 once")
            if colour:
                col = resolve(colour.group(1), variables)
                if col:
                    ratio = contrast(over(col, white), white)
                    check(f"{name}: placeholder colour >= 4.5:1",
                          ratio >= 4.5, f"{ratio:.2f}:1")

    print("\nDecorative emoji are hidden from screen readers")
    emoji = re.compile("[\U0001F300-\U0001FAFF\u2700-\u27BF\u2600-\u26FF]")
    for path in sorted(TPL.rglob("*.html")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not emoji.search(raw):
                continue
            if "{#" in raw or raw.strip().startswith("#"):
                continue  # a template comment, never rendered
            # Announcing "graduation cap" before every heading is noise.
            ok = 'aria-hidden="true"' in raw or "sr-only" in raw
            if not ok:
                check(f"{path.name}: emoji is aria-hidden", False,
                      raw.strip()[:70])

    print("\nThe page shell carries the structure assistive tech relies on")
    base = (TPL / "base.html").read_text(encoding="utf-8")
    check("html has a lang attribute", re.search(r"<html[^>]*\blang=", base) is not None)
    check("a skip link is present", "skip-link" in base)
    check("the skip link targets the main landmark",
          'href="#main"' in base and 'id="main"' in base)
    check("there is a <main> landmark", "<main" in base)
    check("navigation is labelled", re.search(r"<nav[^>]*aria-label=", base) is not None)
    check("the skip link becomes visible on focus",
          ".skip-link:focus" in CSS.read_text(encoding="utf-8"),
          "a skip link that stays off-screen when focused helps nobody")

    print("\nThe typed-code field is not overridden by the cascade")
    # The typed code is the last fallback before a student gives up: they are
    # standing in a lecture theatre, their camera has failed, and they are
    # reading six characters off a projector. It is styled at 2rem monospace
    # for that reason.
    #
    # It rendered at 15.2px in the body sans-serif instead, for months,
    # because the rule was `.code-input` (0,1,0) while style.css carries
    # `.form-group input` (0,1,1) setting font-size and font-family. Source
    # order does not help: the more specific selector wins wherever it sits.
    # Nothing reported it because the field still looked tidy, just small, and
    # a human reading the template sees `font-size: 2rem` and believes it.
    #
    # Checked as a string rather than by rendering, because the whole failure
    # was that the declared value and the applied value disagreed.
    code_html = (TPL / "student" / "code.html").read_text(encoding="utf-8")
    css_text = CSS.read_text(encoding="utf-8")
    competing = re.search(r"\.form-group\s+input[^{]*\{[^}]*font-size", css_text)
    check("style.css still has a .form-group input rule that would win",
          competing is not None,
          "if this rule is gone the specificity workaround can be simplified")
    check("the code field selector out-specifies it",
          ".form-group input.code-input {" in code_html,
          "found a bare .code-input rule, which loses to .form-group input")
    check("the placeholder rule does too",
          ".form-group input.code-input::placeholder {" in code_html)
    check("and it still asks for 2rem monospace",
          bool(re.search(r"\.form-group input\.code-input\s*\{[^}]*font-size:\s*2rem",
                         code_html))
          and bool(re.search(r"\.form-group input\.code-input\s*\{[^}]*monospace",
                             code_html)))

    print("-" * 52)
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    print("Reminder: this covers the automatable third of WCAG. It is not a "
          "conformance claim. See docs/ACCESSIBILITY.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
