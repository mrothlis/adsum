/* app.js: global helpers for Adsum */

/* Alerts.
   This file used to delete every element with class .alert five seconds after
   the page loaded. That took out things that were never meant to go away: the
   standing D2L warning on /admin, the permanent "no sessions recorded,
   exporting is blocked" notice on a course page, and the result of a roster
   import ("32 new students, 4 updated") which exists nowhere else, so a
   professor who looked away lost the only record of it. Removing content on a
   timer is also a WCAG 2.2.1 failure.

   So the rule is now the other way round: nothing disappears unless the
   template explicitly says it is a passing message, by adding the class
   "flash" (or a data-dismiss-after in milliseconds). Everything else stays
   until the reader closes it with the X. */
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.alert').forEach((el) => {
        // Announce it. Errors interrupt; a success message waits its turn.
        if (!el.hasAttribute('role')) {
            const isError = el.classList.contains('alert-error')
                || el.classList.contains('alert-danger')
                || el.classList.contains('alert-warning');
            el.setAttribute('role', isError ? 'alert' : 'status');
        }

        // A visible way to dismiss, which is what the timer was standing in for.
        if (!el.querySelector('.alert-close') && el.dataset.noClose !== '1') {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'alert-close';
            btn.setAttribute('aria-label', 'Close this message');
            btn.textContent = '\u00d7';
            btn.addEventListener('click', () => el.remove());
            el.appendChild(btn);
        }

        // Opt in, never opt out. A warning or a standing notice is never
        // auto-dismissed, whatever else it is marked with.
        const transient = el.classList.contains('flash') || el.dataset.dismissAfter;
        const permanent = el.classList.contains('alert-warning')
            || el.classList.contains('alert-permanent')
            || el.closest('.alert-card');
        if (!transient || permanent) return;

        const delay = parseInt(el.dataset.dismissAfter, 10) || 8000;
        setTimeout(() => {
            el.style.transition = 'opacity 0.3s';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 300);
        }, delay);
    });
});

/* Roster tables carry nine columns, which does not fit a phone. Two of them
   are folded away under 700px; this button brings them back. */
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-toggle-columns]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const on = document.body.classList.toggle('show-optional-cols');
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            btn.textContent = on ? 'Hide ID and D2L username' : 'Show ID and D2L username';
        });
    });
});
