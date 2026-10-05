/* Audit-trail fresh-rows poller: every 30s ask /admin/logs/latest under the
   active filters; when a stream moved, offer a reload pill — never reload
   behind the owner's back, never steal scroll or focus. Skips hidden tabs
   and open dialogs, fails silent: a dead check leaves the page untouched. */
(function () {
    'use strict';
    var card = document.getElementById('logs-card');
    if (!card || !card.dataset.pollUrl) return;
    var pill = document.getElementById('logs-fresh');
    var btn = document.getElementById('logs-fresh-btn');
    if (!pill || !btn) return;
    var firstAction = parseInt(card.dataset.firstAction || '0', 10);
    var firstEvent = parseInt(card.dataset.firstEvent || '0', 10);
    btn.addEventListener('click', function () {
        window.location.reload();
    });
    window.setInterval(function () {
        if (document.hidden) return;
        if (document.querySelector('[data-dialog].is-open')) return;
        if (!pill.hidden) return;
        fetch(card.dataset.pollUrl, { headers: { Accept: 'application/json' } })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (data) {
                if (!data) return;
                if ((data.actions_max || 0) > firstAction ||
                    (data.events_max || 0) > firstEvent) {
                    pill.hidden = false;
                }
            })
            .catch(function () { /* quiet by design */ });
    }, 30000);
})();
