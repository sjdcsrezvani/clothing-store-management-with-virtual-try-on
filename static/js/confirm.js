/* One app-wide confirmation dialog. Pages that ship their own confirm
   surface keep it; this covers every other form carrying data-confirm and
   the few scripted actions that need an answer (RaykidConfirm.ask). The
   submit gate is capture-phase so htmx never posts before the answer. */
(function () {
    'use strict';

    var pending = null; // {form} or {resolve}

    function surface() { return document.getElementById('app-confirm'); }

    function pageOwnsConfirms() {
        return !!document.querySelector('[data-dialog].confirm-scrim:not(#app-confirm)');
    }

    function fill(title, text, ok) {
        document.getElementById('app-confirm-title').textContent = title || 'مطمئن هستید؟';
        document.getElementById('app-confirm-text').textContent = text || '';
        document.getElementById('app-confirm-yes').textContent = ok || 'تأیید';
    }

    function settle(accepted) {
        var record = pending;
        pending = null;
        if (window.RaykidDialog) window.RaykidDialog.close('app-confirm');
        if (!record) return;
        if (record.form) {
            if (accepted) record.form.submit();
        } else if (record.resolve) {
            record.resolve(accepted);
        }
    }

    function open(record, title, text, ok) {
        if (!surface() || !window.RaykidDialog) return false;
        if (pending) settle(false); // never leave an earlier answer hanging
        pending = record;
        fill(title, text, ok);
        window.RaykidDialog.open('app-confirm', document.activeElement);
        return true;
    }

    // A page that already owns a confirm surface (variant, product, SMS, …)
    // answers first; the shared gate stays out of its way.
    document.addEventListener('submit', function (event) {
        if (event.defaultPrevented || pageOwnsConfirms()) return;
        var form = event.target.closest ? event.target.closest('form[data-confirm]') : null;
        if (!form || pending) return;
        var opened = open(
            {form: form},
            form.getAttribute('data-confirm-title'),
            form.getAttribute('data-confirm'),
            form.getAttribute('data-confirm-ok')
        );
        if (opened) event.preventDefault();
    }, true);

    document.addEventListener('click', function (event) {
        if (!event.target.closest) return;
        if (event.target.closest('#app-confirm-yes')) { settle(true); return; }
        if (event.target.closest('#app-confirm [data-dialog-close]')) settle(false);
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && pending) settle(false);
    });

    window.RaykidConfirm = {
        ask: function (message, options) {
            options = options || {};
            return new Promise(function (resolve) {
                var opened = open({resolve: resolve}, options.title, message, options.ok);
                if (!opened) resolve(true); // no helper: let the action run
            });
        }
    };
})();
