/* ============================================================
   Raykid Store — cashbox behaviour (Phase A)
   Two kindnesses for the person at the drawer, one guard for the
   records. Money fields group digits as typed (1,250,000) so a
   missing zero is visible before it posts; separators are stripped
   back to raw digits on submit because the server parses digits,
   not punctuation. Closing and withdrawal-reversal both pass a
   shared dialog.js surface that echoes the figure before anything
   posts. Without JS the forms post directly and the server still
   refuses mismatched counts. A second click while the first POST
   is in flight is ignored.
   ============================================================ */
(function () {
    'use strict';

    var FA = '۰۱۲۳۴۵۶۷۸۹', AR = '٠١٢٣٤٥٦٧٨٩';

    function enDigits(value) {
        return String(value == null ? '' : value)
            .replace(/[۰-۹]/g, function (d) { return FA.indexOf(d); })
            .replace(/[٠-٩]/g, function (d) { return AR.indexOf(d); });
    }

    function rawDigits(value) {
        return enDigits(value).replace(/[^0-9]/g, '');
    }

    function grouped(value) {
        var raw = rawDigits(value).replace(/^0+(?=\d)/, '');
        if (!raw) return '';
        return raw.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    // Group as typed; the caret stays at the end, which is where a
    // left-to-right money figure is always being extended.
    document.querySelectorAll('input[inputmode="numeric"]').forEach(function (field) {
        field.addEventListener('input', function () {
            field.value = grouped(field.value);
        });
    });

    // Separators never reach the server: int() would choke on them and
    // the refusal would blame the figure instead of the punctuation.
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || form.nodeName !== 'FORM') return;
        form.querySelectorAll('input[inputmode="numeric"]').forEach(function (field) {
            field.value = rawDigits(field.value);
        });
    });

    var pendingForm = null;
    var submitting = false;

    function openConfirm(surfaceId, textId, text, opener) {
        document.getElementById(textId).textContent = text;
        window.RaykidDialog.open(surfaceId, opener);
    }

    // Closing: echo the counted figure and the opener back, so the person
    // closing reads what they are about to lock into an immutable shift.
    var closeForm = document.getElementById('cashbox-close-form');
    if (closeForm) {
        closeForm.addEventListener('submit', function (event) {
            if (submitting || pendingForm === closeForm) return;
            if (!window.RaykidDialog) return;
            var counted = closeForm.querySelector('[name="counted"]');
            var repeat = closeForm.querySelector('[name="counted2"]');
            if (!counted || !repeat) return;
            var first = rawDigits(counted.value), second = rawDigits(repeat.value);
            // Empty fields belong to the browser's required prompt, and a
            // mismatch belongs to the server's refusal — the modal only
            // echoes a figure that is actually there twice.
            if (!first || !second || first !== second) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            pendingForm = closeForm;
            openConfirm(
                'cashbox-confirm', 'cashbox-confirm-text',
                'شمارش ' + grouped(first) + ' تومان به نام «' +
                (closeForm.getAttribute('data-opener') || '') +
                '» ثبت و صندوق بسته می‌شود؟ پس از بستن، عدد مورد انتظار و اختلاف نمایش داده می‌شود.',
                document.activeElement
            );
        });
    }

    // Withdrawal reversal: one surface for every row, the row's own words.
    document.querySelectorAll('[data-reverse-form]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (submitting || pendingForm === form) return;
            if (!window.RaykidDialog) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            pendingForm = form;
            openConfirm(
                'cashbox-reverse-confirm', 'cashbox-reverse-confirm-text',
                form.getAttribute('data-confirm') || '',
                document.activeElement
            );
        });
    });

    function confirmYes(surfaceId, buttonId) {
        var yes = document.getElementById(buttonId);
        if (!yes) return;
        yes.addEventListener('click', function () {
            var form = pendingForm;
            pendingForm = null;
            window.RaykidDialog.close(surfaceId);
            if (!form) return;
            submitting = true;
            var send = form.querySelector('[type="submit"]');
            if (send) send.setAttribute('disabled', '');
            form.querySelectorAll('input[inputmode="numeric"]').forEach(function (field) {
                field.value = rawDigits(field.value);
            });
            form.submit();
        });
    }

    confirmYes('cashbox-confirm', 'cashbox-confirm-yes');
    confirmYes('cashbox-reverse-confirm', 'cashbox-reverse-confirm-yes');

    // A dismissal is a change of mind: the form stays exactly as typed.
    document.addEventListener('click', function (event) {
        if (event.target.closest && event.target.closest('[data-dialog-close]')) pendingForm = null;
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') pendingForm = null;
    });
})();
