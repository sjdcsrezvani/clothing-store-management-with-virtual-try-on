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

    // A boosted page-load swaps the whole body in place, and htmx re-runs
    // every inline script it swaps in. A re-run must produce no second
    // document-level listener (the QR countdown restarts, that is fine —
    // but a second submit gate would open the confirm dialog twice).
    // Deduplicate by the handler's own source text: an identical script
    // re-registering the same listener on the same element is a re-entry,
    // not a new gate. Node-bound listeners on fresh nodes always land —
    // their elements are new — so every page arrives alive with nothing
    // rewritten. (A template whose embedded value changes a handler's
    // source between renders registers a fresh listener while the stale
    // one keeps working; state drifts in that rare corner, and the fresh
    // one wins the race that matters.)
    var seen = new Map();
    var addEventListener = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, handler, options) {
        if (handler && typeof handler === 'function') {
            var bag = seen.get(this);
            if (!bag) { bag = new Map(); seen.set(this, bag); }
            var key = type + '\u0000' + String(handler);
            if (bag.has(key)) return undefined;
            bag.set(key, true);
        }
        return addEventListener.call(this, type, handler, options);
    };

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

    /* List regions (the old list_nav, kept as one bridge): the region itself
       carries hx-boost + hx-select. What htmx cannot say in attributes lives
       here — a control that leaves the page keeps navigating fully, a POST
       never swaps, the region wears its veil while its rows fly, focus lands
       on the marked heading after the swap, and the scroll never moves. */
    document.body.addEventListener('htmx:configRequest', function (event) {
        var elt = event.detail.elt;
        if (!elt || !elt.closest || !elt.closest('[data-list-region]')) return;
        var isGet = (event.detail.verb || 'get').toLowerCase() === 'get';
        var target = null;
        try {
            target = new URL(event.detail.path, window.location.href);
        } catch (error) {
            return;
        }
        if (isGet && target.pathname === window.location.pathname) return;
        event.preventDefault();
        if (isGet) window.location.assign(target.href);
        else elt.submit(); // a POST inside a region acts like it always did
    });

    document.body.addEventListener('htmx:beforeRequest', function (event) {
        var elt = event.detail && event.detail.elt;
        var region = elt && elt.closest ? elt.closest('[data-list-region]') : null;
        if (!region) return;
        if (region.getAttribute('aria-busy') === 'true') {
            event.preventDefault(); // one flight per region, as before
            return;
        }
        region.classList.add('is-loading');
        region.setAttribute('aria-busy', 'true');
    });

    // The swap itself replaces the region node, veil and all; these only lift
    // it off a region that survived a flight which did not swap.
    var liftVeil = function (event) {
        var elt = event.detail && event.detail.elt;
        var region = elt && elt.closest ? elt.closest('[data-list-region]') : null;
        if (region) {
            region.classList.remove('is-loading');
            region.removeAttribute('aria-busy');
        }
    };
    document.body.addEventListener('htmx:responseError', liftVeil);
    document.body.addEventListener('htmx:sendError', liftVeil);

    document.body.addEventListener('htmx:load', function (event) {
        // A boosted nav answers with the server's flash row inside the new
        // body; the toaster already adopts those on a real load — adopt here.
        if (window.RaykidToast && window.RaykidToast.adopt) window.RaykidToast.adopt();
        var node = event.target;
        var region = node && node.matches ? node.closest('[data-list-region]') : null;
        if (!region) return;
        var head = region.querySelector('[data-list-heading]') || region;
        if (head === region && !region.hasAttribute('tabindex')) {
            region.setAttribute('tabindex', '-1');
        }
        head.focus({ preventScroll: true });
    });
})();
