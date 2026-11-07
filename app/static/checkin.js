/* checkin.js — one-tap check-in.
   When the course enforces a geofence, grab the phone's location first
   and attach it to the POST; otherwise submit as-is. */
(() => {
    const form = document.getElementById('checkin-form');
    if (!form || form.dataset.geofence !== '1') return;

    form.addEventListener('submit', (e) => {
        if (form.dataset.fix === '1') return;  // location already attached, let it submit
        e.preventDefault();
        const btn = document.getElementById('checkin-btn');
        const err = document.getElementById('geo-error');
        if (!navigator.geolocation) { err.style.display = 'block'; return; }
        // Remember the label. On the retry screen the button says Try again,
        // not Check In, and putting the wrong word back is confusing.
        const label = btn.innerHTML;
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
        btn.textContent = 'Getting your location...';
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                document.getElementById('gps_lat').value = pos.coords.latitude;
                document.getElementById('gps_lon').value = pos.coords.longitude;
                form.dataset.fix = '1';
                form.submit();
            },
            () => {
                err.style.display = 'block';
                btn.disabled = false;
                btn.removeAttribute('aria-busy');
                btn.innerHTML = label;
            },
            { timeout: 10000, maximumAge: 30000, enableHighAccuracy: true }
        );
    });
})();
