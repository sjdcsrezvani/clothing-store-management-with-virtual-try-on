/* Soft actions — a plain POST that lands back on the page it came from is
   sent through htmx and swapped in place: no white flash, no scroll jump, and
   the filters/tab the owner was looking at stay put. The server still owns the
   answer and its redirect; this file only watches where the redirect landed.

   Forms that need a real document (login, setup, checkout, the theme's <html>
   class, database restore, a chosen upload file) opt out with native-actions
   on <body> or data-native on the form.

   The gate sits on window in the bubble phase: every page handler — client
   validation, the shared confirm dialog, a page's own dialog — has already
   run and may have prevented the submit. Only an unanswered POST gets here. */
(function () {
    'use strict';

    var busy = false;
    var preSubmit = new WeakMap();

    // A submit control can live outside its form (`form="product-form"`, the
    // product page's sticky summary). The busy lock must reach both kinds.
    function submitControls(form) {
        var controls = Array.prototype.slice.call(
            form.querySelectorAll('button[type="submit"], input[type="submit"]')
        );
        if (form.id) {
            controls = controls.concat(Array.prototype.filter.call(
                document.querySelectorAll('[form="' + form.id + '"]'),
                function (control) { return control.type === 'submit'; }
            ));
        }
        return controls;
    }

    // Snapshot the buttons before the app's own submit listener (app.js)
    // disables them, so a failed soft request can put the form back exactly
    // as it was. Capture runs ahead of every bubble handler.
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || form.tagName !== 'FORM') return;
        preSubmit.set(form, submitControls(form).map(
            function (control) { return control.disabled; }
        ));
    }, true);

    function nativeForm(form) {
        if (document.body.classList.contains('native-actions') ||
            document.body.classList.contains('auth-shell')) return true;
        if (form.closest('[data-native], [data-soft-skip]')) return true;
        if (form.matches('[hx-post],[hx-put],[hx-patch],[hx-delete]')) return true;
        var target = form.getAttribute('target');
        if (target && target !== '_self') return true;
        var file = form.querySelector('input[type="file"]');
        return !!(file && file.files && file.files.length);
    }

    function targetSelector(form) {
        var box = form.closest('[data-soft-target]');
        if (box) return box.getAttribute('data-soft-target');
        var region = form.closest('[data-list-region]');
        if (region) {
            return '[data-list-region="' + region.getAttribute('data-list-region') + '"]';
        }
        return '#app-main';
    }

    function parse(html) {
        return new DOMParser().parseFromString(html || '', 'text/html');
    }

    function swap(html, selector) {
        var live = document.querySelector(selector);
        var incoming = parse(html).querySelector(selector);
        if (!live || !incoming) return false;
        htmx.swap(live, incoming.outerHTML, { swapStyle: 'outerHTML' });
        return true;
    }

    function flash(url) {
        var out = { type: 'success', text: '' };
        try {
            var q = new URL(url, location.href).searchParams;
            if (q.get('err')) { out.type = 'error'; out.text = q.get('err'); }
            else if (q.get('msg')) { out.text = q.get('msg'); }
        } catch (error) { /* keep the empty flash */ }
        return out;
    }

    function toast(doc, url) {
        var hit = flash(url);
        if (!hit.text && doc) {
            var row = doc.querySelector('[data-toast]');
            if (row) {
                hit.text = row.textContent.trim();
                hit.type = row.getAttribute('data-toast-type') || 'success';
            }
        }
        if (!hit.text || !window.RaykidToast) return;
        if (hit.type === 'error') window.RaykidToast.error(hit.text);
        else window.RaykidToast.success(hit.text);
    }

    function sameView(a, b) {
        function bare(url) {
            var u = new URL(url, location.href);
            u.searchParams.delete('msg');
            u.searchParams.delete('err');
            return u.pathname + u.search;
        }
        return bare(a) === bare(b);
    }

    // A redirect cannot carry the page's tab in XHR's responseURL; a same-page
    // swap keeps the hash the owner was already reading.
    function keepHash(url) {
        var u = new URL(url, location.href);
        if (!u.hash && location.hash) u.hash = location.hash;
        return u.href;
    }

    // Keep the search/filter/page the owner was looking at; the redirect's own
    // params (e.g. settings tab) win over the old ones.
    function keepView(finalUrl) {
        var here = new URL(location.href);
        var there = new URL(finalUrl, location.href);
        var kept = new URL(here.pathname, here.origin);
        here.searchParams.forEach(function (value, key) {
            if (key !== 'msg' && key !== 'err') kept.searchParams.append(key, value);
        });
        there.searchParams.forEach(function (value, key) {
            if (key !== 'msg' && key !== 'err') kept.searchParams.set(key, value);
        });
        return kept.href;
    }

    function restore(scroll) {
        setTimeout(function () {
            window.scrollTo(0, scroll);
            if (document.activeElement === document.body) {
                var main = document.getElementById('app-main');
                if (main) main.focus({ preventScroll: true });
            }
        }, 0);
    }

    function send(form, submitter) {
        if (busy) return;
        if (nativeForm(form)) { form.submit(); return; }

        var action = (submitter && submitter.hasAttribute('formaction') &&
                      submitter.formAction) || form.getAttribute('action') || location.href;
        var verb = ((submitter && submitter.getAttribute('formmethod')) ||
                    form.getAttribute('method') || 'get').toUpperCase();
        if (verb !== 'POST') { form.submit(); return; } // safety, never expected

        var selector = targetSelector(form);
        var scroll = window.scrollY;
        var values = null;
        if (submitter && submitter.name) {
            values = {};
            values[submitter.name] = submitter.value;
        }
        var locks = submitControls(form);
        var disabled = preSubmit.get(form) || locks.map(function (control) { return control.disabled; });

        busy = true;
        document.body.classList.add('is-soft-busy');
        locks.forEach(function (control) {
            control.setAttribute('aria-busy', 'true');
            control.disabled = true;
        });
        if (window.ensureCsrf) window.ensureCsrf(form);

        function finish() {
            busy = false;
            document.body.classList.remove('is-soft-busy');
            locks.forEach(function (control, index) {
                control.disabled = disabled[index];
                control.removeAttribute('aria-busy');
            });
        }

        function answer(event) {
            var xhr = event.detail && event.detail.xhr;
            if (!xhr) { finish(); return; }
            var finalUrl = xhr.responseURL || action;
            var asked = new URL(action, location.href).href;
            var landed;
            try { landed = new URL(finalUrl, location.href); } catch (error) { finish(); return; }
            if (!xhr.status || xhr.status >= 400) { finish(); return; }

            var direct = landed.href === asked;
            // A different page is a real destination (create, detail, login).
            if (!direct && landed.pathname !== location.pathname) {
                location.assign(landed.href);
                return;
            }

            var isMain = selector === '#app-main';
            var reuse = direct || sameView(landed.href, location.href);
            if (reuse && xhr.responseText && swap(xhr.responseText, selector)) {
                if (!isMain) toast(parse(xhr.responseText), landed.href);
                if (!direct) history.replaceState(null, '', keepHash(landed.href));
                finish();
                restore(scroll);
                return;
            }

            // The redirect resets filters: fetch the view the owner actually
            // had, with the server's intended params merged in.
            var url = direct ? location.href : keepView(landed.href);
            toast(null, landed.href);
            history.replaceState(null, '', keepHash(url));
            var pending = htmx.ajax('GET', url, {
                target: selector, select: selector, swap: 'outerHTML'
            });
            Promise.resolve(pending).then(function () {
                finish();
                restore(scroll);
            }, function () {
                finish();
                restore(scroll);
            });
        }

        form.addEventListener('htmx:afterRequest', answer, { once: true });
        try {
            htmx.ajax('POST', action, { source: form, swap: 'none', values: values });
        } catch (error) {
            finish();
        }
    }

    window.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || form.tagName !== 'FORM' || event.defaultPrevented) return;
        var verb = ((event.submitter && event.submitter.getAttribute('formmethod')) ||
                    form.getAttribute('method') || 'get').toUpperCase();
        if (verb !== 'POST') return;
        if (nativeForm(form)) return;
        event.preventDefault();
        send(form, event.submitter);
    }, false);

    // Confirm dialogs that disabled their submit control before asking hand
    // the form back here when the answer is «no».
    function reset(form) {
        if (!form || busy) return;
        var was = preSubmit.get(form);
        submitControls(form).forEach(function (control, index) {
            control.disabled = was ? !!was[index] : false;
            control.removeAttribute('aria-busy');
        });
    }

    window.RaykidSoft = { submit: send, reset: reset };
})();
