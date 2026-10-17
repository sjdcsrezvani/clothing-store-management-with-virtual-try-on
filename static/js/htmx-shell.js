/* HTMX shell — the bridge between htmx and the shop's own conventions.
   Loaded after htmx.min.js in base.html; inert until a page uses hx- attributes.

   Two jobs:

   * Every htmx request carries the app's own markers: the CSRF token (the
     server already accepts it as X-CSRF-Token, services/security.py) and
     X-Requested-With: XMLHttpRequest, so server branches — existing and new —
     can ask one question about who is calling. This file is the one
     sanctioned place for that second header; the migration guard test
     tolerates it here and nowhere else.
   * A response that is not the fragment it claims to be never lands in the
     page. An expired session answers with the login page — a 303 the fetch
     chain follows to a 200, so it does not look like an error — and would
     otherwise be pasted into a table cell; the browser navigates instead,
     the same guarantee the till's hand-rolled transport kept. Error
     responses never swap either, and a failed boosted page-load falls back
     to a real navigation so a click is never a dead end. Fragment requests
     stay quiet on error — their phases own their fallbacks. */
(function () {
    'use strict';

    function loginPath(url) {
        try {
            return new URL(url, window.location.href).pathname === '/admin/login';
        } catch (error) {
            return false;
        }
    }

    document.addEventListener('htmx:configRequest', function (event) {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) event.detail.headers['X-CSRF-Token'] = meta.content;
        event.detail.headers['X-Requested-With'] = 'XMLHttpRequest';
    });

    document.addEventListener('htmx:beforeSwap', function (event) {
        var xhr = event.detail.xhr;
        var config = event.detail.requestConfig || {};
        var verb = (config.verb || 'get').toLowerCase();

        var landedOnLogin = xhr.responseURL && loginPath(xhr.responseURL)
            && !loginPath(window.location.href);
        if (landedOnLogin) {
            event.detail.shouldSwap = false;
            window.location.assign('/admin/login');
            return;
        }
        if (!event.detail.isError) return;
        event.detail.shouldSwap = false;
        // A boosted GET is a page-load: its error answer (a refusal page, a
        // 500) is a real page, so the browser goes there rather than leaving
        // the click unanswered.
        if (config.boosted && verb === 'get') {
            window.location.assign(xhr.responseURL || window.location.href);
        }
    });
})();
