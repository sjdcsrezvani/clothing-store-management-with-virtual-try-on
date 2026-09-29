/* ============================================================
   Raykid Store — shared bulk-list behaviour
   One pattern for the review pages (birthdays, tier-up, downgrades):
   search narrows, all/none tick the visible, the submit button counts the
   checked, and a modal names the count before anything posts. Opt in with
   [data-bulk-form] on the form and data-confirm="...{n}..." on it; the
   dialog surface is #bulk-confirm with #bulk-confirm-text/#bulk-confirm-yes.
   A second click while the first POST is in flight is ignored.
   ============================================================ */
(function () {
    'use strict';

    var pendingForm = null;
    var submitting = false;

    function faNum(value) {
        var fa = '۰۱۲۳۴۵۶۷۸۹', ar = '٠١٢٣٤٥٦٧٨٩';
        return String(value == null ? '' : value)
            .replace(/[۰-۹]/g, function (d) { return fa.indexOf(d); })
            .replace(/[٠-٩]/g, function (d) { return ar.indexOf(d); });
    }

    function boxes(form) {
        return Array.prototype.slice.call(
            form.querySelectorAll('input[type="checkbox"][name]')
        ).filter(function (box) { return !box.disabled; });
    }

    function checked(form) {
        return boxes(form).filter(function (box) { return box.checked; });
    }

    document.querySelectorAll('[data-bulk-form]').forEach(function (form) {
        var send = form.querySelector('.bulk-send') || form.querySelector('[type="submit"]');
        var search = form.querySelector('.bulk-search');
        var match = form.querySelector('.bulk-match');
        var rows = Array.prototype.slice.call(form.querySelectorAll('.bulk-row'));

        function syncCount() {
            if (send && send.hasAttribute('data-count')) {
                var base = send.getAttribute('data-count');
                send.textContent = base + ' (' + checked(form).length + ')';
            }
        }

        function syncMatch() {
            if (!match) return;
            var visible = rows.filter(function (row) { return !row.hidden; }).length;
            match.textContent = (search && search.value.trim())
                ? (visible + ' از ' + rows.length) : '';
        }

        boxes(form).forEach(function (box) {
            box.addEventListener('change', syncCount);
        });

        var all = form.querySelector('.bulk-all');
        if (all) all.addEventListener('click', function () {
            boxes(form).forEach(function (box) {
                var row = box.closest('.bulk-row');
                if (!row || !row.hidden) box.checked = true;
            });
            syncCount();
        });

        var none = form.querySelector('.bulk-none');
        if (none) none.addEventListener('click', function () {
            boxes(form).forEach(function (box) { box.checked = false; });
            syncCount();
        });

        if (search) search.addEventListener('input', function () {
            var needle = faNum(search.value).trim().toLowerCase();
            rows.forEach(function (row) {
                var hay = faNum(row.getAttribute('data-search') || '').toLowerCase();
                row.hidden = needle.length > 0 && hay.indexOf(needle) === -1;
            });
            syncMatch();
        });

        // The base label lives in data-count so recounts never append to an
        // already-counted label («...(3) (5)»).
        if (send && !send.hasAttribute('data-count')) {
            send.setAttribute('data-count', send.textContent.trim());
        }
        syncCount();
    });

    document.addEventListener('submit', function (event) {
        var form = event.target.closest ? event.target.closest('[data-bulk-form]') : null;
        if (!form || form === pendingForm) return;
        if (submitting) { event.preventDefault(); return; }
        var pattern = form.getAttribute('data-confirm');
        if (!pattern || !window.RaykidDialog) return;
        var count = checked(form).length;
        if (!count) return; // the server refuses empty picks with its own error
        event.preventDefault();
        pendingForm = form;
        document.getElementById('bulk-confirm-text').textContent =
            pattern.split('{n}').join(String(count));
        window.RaykidDialog.open('bulk-confirm', document.activeElement);
    });

    document.getElementById('bulk-confirm-yes').addEventListener('click', function () {
        var form = pendingForm;
        pendingForm = null;
        window.RaykidDialog.close('bulk-confirm');
        if (!form) return;
        submitting = true;
        var send = form.querySelector('.bulk-send') || form.querySelector('[type="submit"]');
        if (send) send.setAttribute('disabled', '');
        form.submit();
    });

    var surface = document.getElementById('bulk-confirm');
    if (surface) surface.addEventListener('click', function (event) {
        if (event.target.closest('[data-dialog-close]')) pendingForm = null;
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') pendingForm = null;
    });
})();
