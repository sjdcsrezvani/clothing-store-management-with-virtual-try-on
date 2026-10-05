/* Settings tabs: one form per panel, pills to move, a dirty dot that says
   what is unsaved, and a confirm before switching away from it. Pills are
   plain links, so without JS every tab still opens — the script only adds
   the guard and the error focus. */
(function () {
    'use strict';
    var form = document.querySelector('form[data-settings-form]');
    if (!form) return;
    var currentTab = form.getAttribute('data-settings-form');
    var pendingHref = null;

    function pill() {
        return document.querySelector('[data-settings-tab="' + currentTab + '"]');
    }
    form.addEventListener('input', function () {
        form.dataset.dirty = '1';
        var active = pill();
        if (active) active.classList.add('is-dirty');
    });
    form.addEventListener('submit', function () {
        delete form.dataset.dirty;
    });

    // A failed save reopens its own tab server-side (?tab= + field); put the
    // caret where the toast points instead of leaving the owner hunting.
    var card = document.getElementById('settings-card');
    var bad = card ? card.getAttribute('data-error-field') : null;
    if (bad) {
        var field = form.querySelector('[name="' + bad + '"]');
        if (field) {
            field.focus();
            var group = field.closest('.form-group');
            if (group) group.classList.add('is-error');
        }
    }

    document.addEventListener('click', function (event) {
        var link = event.target.closest ? event.target.closest('[data-settings-tab]') : null;
        if (!link || link.getAttribute('data-settings-tab') === currentTab) return;
        if (form.dataset.dirty !== '1') return; // clean: follow the link
        if (!window.RaykidDialog) return; // no helper, no gate: follow it
        event.preventDefault();
        pendingHref = link.href;
        document.getElementById('settings-switch-text').textContent =
            'تغییرات این بخش ذخیره نشده. اول ذخیره می‌کنید یا بدون ذخیره می‌روید؟';
        window.RaykidDialog.open('settings-switch', link);
    });
    document.getElementById('settings-switch-save').addEventListener('click', function () {
        pendingHref = null;
        window.RaykidDialog.close('settings-switch');
        form.submit();
    });
    document.getElementById('settings-switch-go').addEventListener('click', function () {
        var href = pendingHref;
        pendingHref = null;
        window.RaykidDialog.close('settings-switch');
        if (href) window.location.href = href;
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') pendingHref = null;
    });
})();
