/* ============================================================
   Raykid Store — checks behaviour (Phase A)
   The rial field groups digits as typed (1,000,000,000) so a missing
   zero is visible before it posts; separators are stripped back to raw
   digits on submit because the server parses digits, not punctuation.
   Recording echoes amount, payee, due date and bank back through a
   shared dialog.js surface before anything posts — a wrong cheque is a
   promise to pay wrong. Without JS the form posts directly and the
   server still validates, beside its two-minute identical-recording
   guard. A second click while the first POST is in flight is ignored.
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

    var moneyFields = Array.prototype.slice.call(
        document.querySelectorAll('input[inputmode="numeric"]'));
    moneyFields.forEach(function (field) {
        field.addEventListener('input', function () {
            field.value = grouped(field.value);
        });
    });

    // Separators never reach the server: the parser reads digits, and a
    // refusal should blame the figure, never the punctuation.
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || form.nodeName !== 'FORM') return;
        moneyFields.forEach(function (field) {
            if (form.contains(field)) field.value = rawDigits(field.value);
        });
    });

    var pendingForm = null;
    var submitting = false;

    // Recording: read the promise back — figure, payee, due date, bank —
    // so the person signing hears what they are about to lock in.
    var addForm = document.getElementById('check-add-form');
    if (addForm) {
        addForm.addEventListener('submit', function (event) {
            if (submitting || pendingForm === addForm) return;
            if (!window.RaykidDialog) return;
            var amount = addForm.querySelector('[name="amount_rials"]');
            var payee = addForm.querySelector('[name="provider_name"]');
            var due = addForm.querySelector('[name="due_date"]');
            var bank = addForm.querySelector('[name="bank_name"]');
            var number = addForm.querySelector('[name="check_number"]');
            if (!amount || !rawDigits(amount.value) || rawDigits(amount.value) === '0') return;
            if (!payee || !payee.value.trim()) return;
            if (!number || !number.value.trim()) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            pendingForm = addForm;
            var dueText = (due && due.value.trim()) || '—';
            var bankText = (bank && bank.value.trim()) || '—';
            document.getElementById('check-add-confirm-text').textContent =
                'چک ' + grouped(amount.value) + ' ریال در وجه «' + payee.value.trim() +
                '» با سررسید ' + dueText + ' (' + bankText + ') ثبت شود؟';
            window.RaykidDialog.open('check-add-confirm', document.activeElement);
        });
    }

    var yes = document.getElementById('check-add-confirm-yes');
    if (yes) yes.addEventListener('click', function () {
        var form = pendingForm;
        pendingForm = null;
        window.RaykidDialog.close('check-add-confirm');
        if (!form) return;
        submitting = true;
        var send = form.querySelector('[type="submit"]');
        if (send) send.setAttribute('disabled', '');
        moneyFields.forEach(function (field) {
            if (form.contains(field)) field.value = rawDigits(field.value);
        });
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
