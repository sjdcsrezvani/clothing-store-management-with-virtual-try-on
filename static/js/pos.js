/* ============================================================
   Raykid Store — terminal reconciliation behaviour (Phase A)
   Each review echoes its own chosen outcome before anything posts: the
   figure comes from the row, the words from the selected option, so the
   modal can never confirm a choice the reader did not make. Without JS
   the forms post directly and the server still guards every rule.
   A second click while the first POST is in flight is ignored.
   ============================================================ */
(function () {
    'use strict';

    var pendingForm = null;
    var submitting = false;

    function grouped(value) {
        var raw = String(value == null ? '' : value).replace(/[^0-9]/g, '');
        if (!raw) return '۰';
        return raw.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
            .replace(/[0-9]/g, function (d) { return '۰۱۲۳۴۵۶۷۸۹'[+d]; });
    }

    document.querySelectorAll('[data-pos-form]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (submitting || pendingForm === form) return;
            if (!window.RaykidDialog) return;
            var select = form.querySelector('[name="resolution_type"]');
            var choice = select && select.selectedIndex > 0
                ? select.options[select.selectedIndex].textContent.trim() : '';
            // An unchosen outcome belongs to the browser's required prompt —
            // the modal only echoes a decision that is actually there.
            if (!choice) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            pendingForm = form;
            document.getElementById('pos-confirm-text').textContent =
                'نتیجه «' + choice + '» برای تراکنش ' +
                grouped(form.getAttribute('data-amount')) + ' تومان ثبت می‌شود؟';
            window.RaykidDialog.open('pos-confirm', document.activeElement);
        });
    });

    var yes = document.getElementById('pos-confirm-yes');
    if (yes) yes.addEventListener('click', function () {
        var form = pendingForm;
        pendingForm = null;
        window.RaykidDialog.close('pos-confirm');
        if (!form) return;
        submitting = true;
        var send = form.querySelector('[type="submit"]');
        if (send) send.setAttribute('disabled', '');
        form.submit();
    });

    // A dismissal is a change of mind: the form stays exactly as typed.
    document.addEventListener('click', function (event) {
        if (event.target.closest && event.target.closest('[data-dialog-close]')) pendingForm = null;
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') pendingForm = null;
    });
})();
