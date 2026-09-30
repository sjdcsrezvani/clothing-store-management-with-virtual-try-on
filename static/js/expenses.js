/* ============================================================
   Raykid Store — expenses behaviour (Phase A/C)
   Money fields group digits as typed (1,500,000) so a missing zero is
   visible before it posts; separators are stripped back to raw digits on
   submit because the server parses digits, not punctuation. Adding echoes
   amount, method, type and category back through a shared dialog.js surface
   before anything posts. Voiding passes its own surface carrying a reason
   field, which lands in the row form's hidden reason input, and stopping a
   monthly rule passes a third surface with the rule's own words — without
   JS all forms post directly and the server still validates, with the void
   defaulting to its standing reason. A second click while the first POST is
   in flight is ignored.
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

    var amountField = document.getElementById('expense-amount');
    // Every money field on the page groups alike — the add form's and the
    // monthly rule's — because two faces for one kind of figure is how a
    // missing zero hides.
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

    function selectedText(select) {
        if (!select || select.selectedIndex < 0) return '';
        return select.options[select.selectedIndex].textContent.trim();
    }

    // Adding: read the figure back with its method, type and category, so
    // the person recording hears what they are about to lock in.
    var addForm = document.getElementById('expense-add-form');
    if (addForm) {
        addForm.addEventListener('submit', function (event) {
            if (submitting || pendingForm === addForm) return;
            if (!window.RaykidDialog || !amountField) return;
            var raw = rawDigits(amountField.value);
            // An empty figure belongs to the browser's required prompt, and
            // a bad one to the server's itemised refusal — the modal only
            // echoes a figure that is actually there.
            if (!raw || raw === '0') return;
            event.preventDefault();
            event.stopImmediatePropagation();
            pendingForm = addForm;
            var category = document.getElementById('expense-category');
            var catText = (category && category.value.trim()) || 'بدون دسته';
            document.getElementById('expense-add-confirm-text').textContent =
                'هزینه ' + grouped(raw) + ' تومان (' + catText + '، ' +
                selectedText(document.getElementById('expense-type')) + '، ' +
                selectedText(document.getElementById('expense-method')) +
                ') ثبت شود؟';
            window.RaykidDialog.open('expense-add-confirm', document.activeElement);
        });
    }

    // Voiding: one surface for every row, the row's own words, and a reason
    // field whose content travels in the form's hidden reason input.
    var reasonField = document.getElementById('expense-void-reason');
    document.querySelectorAll('[data-void-form]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (submitting || pendingForm === form) return;
            if (!window.RaykidDialog) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            pendingForm = form;
            if (reasonField) reasonField.value = '';
            document.getElementById('expense-void-confirm-text').textContent =
                form.getAttribute('data-confirm') || '';
            window.RaykidDialog.open('expense-void-confirm', document.activeElement);
            if (reasonField) reasonField.focus();
        });
    });

    // A rule promise moves no money today, so it posts without a modal —
    // but never twice from one double click.
    var ruleForm = document.getElementById('recurring-add-form');
    if (ruleForm) {
        ruleForm.addEventListener('submit', function (event) {
            if (submitting) { event.preventDefault(); return; }
            submitting = true;
            var send = ruleForm.querySelector('[type="submit"]');
            if (send) send.setAttribute('disabled', '');
        });
    }

    // Stopping a rule is forever, so the rule's own words come back first.
    document.querySelectorAll('[data-stop-form]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (submitting || pendingForm === form) return;
            if (!window.RaykidDialog) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            pendingForm = form;
            document.getElementById('recurring-stop-confirm-text').textContent =
                form.getAttribute('data-confirm') || '';
            window.RaykidDialog.open('recurring-stop-confirm', document.activeElement);
        });
    });

    function confirmYes(surfaceId, buttonId, onConfirm) {
        var yes = document.getElementById(buttonId);
        if (!yes) return;
        yes.addEventListener('click', function () {
            var form = pendingForm;
            pendingForm = null;
            window.RaykidDialog.close(surfaceId);
            if (!form) return;
            if (onConfirm) onConfirm(form);
            submitting = true;
            var send = form.querySelector('[type="submit"]');
            if (send) send.setAttribute('disabled', '');
            moneyFields.forEach(function (field) {
                if (form.contains(field)) field.value = rawDigits(field.value);
            });
            form.submit();
        });
    }

    confirmYes('expense-add-confirm', 'expense-add-confirm-yes');
    confirmYes('expense-void-confirm', 'expense-void-confirm-yes', function (form) {
        var hidden = form.querySelector('[name="reason"]');
        if (hidden && reasonField) hidden.value = reasonField.value.trim();
    });
    confirmYes('recurring-stop-confirm', 'recurring-stop-confirm-yes');

    // A dismissal is a change of mind: the form stays exactly as typed.
    document.addEventListener('click', function (event) {
        if (event.target.closest && event.target.closest('[data-dialog-close]')) pendingForm = null;
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') pendingForm = null;
    });
})();
