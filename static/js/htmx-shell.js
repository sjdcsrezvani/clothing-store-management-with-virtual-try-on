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

    /* A boosted swap inserts the page's <script src> tags async, so the inline
       script after them can run first and find its library still missing.
       Its own DOMContentLoaded listener would never fire either — that event
       is long gone. Wait for the dependency, then run once.
       shortcut: gives up after 5s if the script never arrives; switch to
       script load events if a slow network makes that visible. */
    window.RaykidWhen = function (ready, run) {
        var tries = 100;
        (function wait() {
            if (ready()) return run();
            if (--tries) setTimeout(wait, 50);
        })();
    };

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

    /* Plain POST forms belong to soft.js: htmx must not turn one into a boosted
       swap before its own validation or confirm dialog has spoken. Forms with
       an explicit hx verb keep htmx; everything else is unboosted right before
       htmx reads its attributes (initial visit and every region swap). */
    document.addEventListener('htmx:beforeProcessNode', function (event) {
        var node = event.target;
        if (!node || node.tagName !== 'FORM') return;
        if (node.hasAttribute('hx-post') || node.hasAttribute('hx-put') ||
            node.hasAttribute('hx-patch') || node.hasAttribute('hx-delete')) return;
        node.setAttribute('hx-boost', 'false');
    });

    function loginPath(url) {
        try {
            return new URL(url, window.location.href).pathname === '/admin/login';
        } catch (error) {
            return false;
        }
    }

    /* Boost belongs to list regions, not the whole shell: a boosted link that
       leaves the page (CSV, download, new tab) must keep the browser's own
       behaviour instead of swapping a file into the body. */
    function unboostNativeLinks(root) {
        var scope = root || document;
        if (!scope.querySelectorAll) scope = document.body;
        if (!scope || !scope.querySelectorAll) return;
        scope.querySelectorAll(
            'a[target], a[download], a[href*="/export"], a[href*="/download"]'
        ).forEach(function (link) {
            link.setAttribute('hx-boost', 'false');
        });
    }

    document.addEventListener('htmx:configRequest', function (event) {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) event.detail.headers['X-CSRF-Token'] = meta.content;
        event.detail.headers['X-Requested-With'] = 'XMLHttpRequest';
    });

    // The session can rotate its CSRF token mid-flight (login and logout clear
    // it) and a boosted swap never replaces the <head>: the meta would keep a
    // token the server no longer accepts, and every meta-fed request — the
    // header above, a form whose field was injected before the rotation —
    // would answer 403 in silence. The server names the current token on every
    // HTML answer; adopt it.
    document.addEventListener('htmx:afterOnLoad', function (event) {
        var xhr = event.detail && event.detail.xhr;
        if (!xhr || !xhr.getResponseHeader) return;
        var fresh = xhr.getResponseHeader('X-CSRF-Token');
        if (!fresh) return;
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) meta.content = fresh;
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
       here — a control that leaves the page keeps navigating fully, the region
       wears its veil while its rows fly, focus lands on the marked heading
       after the swap, and the scroll never moves. Plain POSTs were unboosted
       above and are soft.js's business. */
    function bindBody() {
        unboostNativeLinks(document);
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
            if (isGet) {
                event.preventDefault();
                window.location.assign(target.href);
            }
        });

        document.body.addEventListener('htmx:beforeRequest', function (event) {
            var elt = event.detail && event.detail.elt;
            var region = elt && elt.closest ? elt.closest('[data-list-region]') : null;
            if (!region) return;
            // The one-flight rule is the list's GET controls; a soft POST owns
            // its own busy gate and must never be refused here.
            var verb = (event.detail.requestConfig && event.detail.requestConfig.verb) ||
                       event.detail.verb || 'get';
            if (verb.toLowerCase() !== 'get') return;
            if (region.getAttribute('aria-busy') === 'true') {
                event.preventDefault(); // one flight per region, as before
                return;
            }
            region.classList.add('is-loading');
            region.setAttribute('aria-busy', 'true');
        });

        // The swap itself replaces the region node, veil and all; these only
        // lift it off a region that survived a flight which did not swap.
        var liftVeil = function (event) {
            var elt = event.detail && event.detail.elt;
            var region = elt && elt.closest ? elt.closest('[data-list-region]') : null;
            if (region) {
                region.classList.remove('is-loading');
                region.removeAttribute('aria-busy');
            }
        };
        // Any terminal outcome lifts the veil — an aborted, timed-out or
        // erroring flight must not leave the region dimmed and refusing the
        // next request forever.
        document.body.addEventListener('htmx:afterRequest', liftVeil);
        document.body.addEventListener('htmx:responseError', liftVeil);
        document.body.addEventListener('htmx:sendError', liftVeil);
        document.body.addEventListener('htmx:timeout', liftVeil);
        document.body.addEventListener('htmx:abort', liftVeil);

        document.body.addEventListener('htmx:load', function (event) {
            unboostNativeLinks(event.target);
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
    }

    // This file loads in <head>, where the body does not exist yet. Binding
    // it there threw and every page lost the bridge above: a «ویرایش» click
    // inside a list region pushed the URL and swapped nothing.
    if (document.body) bindBody();
    else document.addEventListener('DOMContentLoaded', bindBody);
})();
