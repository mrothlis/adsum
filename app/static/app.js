/* app.js — Global helpers for Attendance Check-In */

// Auto-dismiss flash messages after 5s
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.alert').forEach(el => {
        if (el.closest('.alert-card')) return; // Don't dismiss alert panels
        setTimeout(() => {
            el.style.transition = 'opacity 0.3s';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 300);
        }, 5000);
    });
});
