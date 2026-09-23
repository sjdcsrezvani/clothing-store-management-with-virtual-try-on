/* Reload-free list controls: sort headers, pagination and filter links inside a
   [data-list-region] fetch their URL and swap only the region. The document
   never reloads, so the scroll stays exactly where the owner left it.

   Same path only — a link that leaves the page (edit forms, exports, tabs)
   is not a list control and navigates normally. Anything the fetch cannot
   do (network down, region missing from the answer) falls back to a plain
   navigation, never a dead click. No-JS browsers never run this at all. */
(function () {
    'use strict';

    var REGION = 'data-list-region';
    var lastUrl = window.location.href;

    // Widgets a page builds inside its region (disclosures, bulk bars) wither
    // on swap, so pages register a re-initializer once: RaykidListNav.register.
    var initializers = {};
    function initRegion(region) {
        var init = initializers[region.getAttribute(REGION)];
        if (init) {
            try { init(region); } catch (err) { /* widgets stay inert, links still work */ }
        }
    }

    function focusRegion(region) {
        // The scroll does not move; focus tells assistive tech the rows did.
        // A marked heading takes it, otherwise the region itself is enlisted.
        var target = region.querySelector('[data-list-heading]') || region;
        if (target === region && !region.hasAttribute('tabindex')) {
            region.setAttribute('tabindex', '-1');
        }
        target.focus({ preventScroll: true });
    }

    function swapRegion(region, url, push) {
        if (region.hasAttribute('aria-busy')) return null; // one flight per region
        region.setAttribute('aria-busy', 'true');
        region.classList.add('is-loading');
        return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (response) {
                if (!response.ok) throw new Error('bad status ' + response.status);
                return response.text();
            })
            .then(function (html) {
                var name = region.getAttribute(REGION);
                var fresh = new DOMParser().parseFromString(html, 'text/html')
                    .querySelector('[' + REGION + '="' + name + '"]');
                if (!fresh) throw new Error('no region in answer');
                region.innerHTML = fresh.innerHTML;
                if (push) {
                    history.pushState({ listNav: true }, '', url);
                    lastUrl = url;
                }
                initRegion(region);
                focusRegion(region);
            })
            .catch(function () {
                window.location.href = url;
            })
            .finally(function () {
                region.removeAttribute('aria-busy');
                region.classList.remove('is-loading');
            });
    }

    function regionOf(node) {
        return node && node.closest ? node.closest('[' + REGION + ']') : null;
    }

    function samePage(url) {
        return url.origin === window.location.origin
            && url.pathname === window.location.pathname;
    }

    document.addEventListener('click', function (event) {
        if (event.defaultPrevented || event.button !== 0
                || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        var anchor = event.target && event.target.closest
            ? event.target.closest('a[href]') : null;
        if (!anchor) return;
        var region = regionOf(anchor);
        if (!region || anchor.hasAttribute('download') || anchor.target === '_blank') return;
        var url = new URL(anchor.getAttribute('href'), window.location.href);
        if (!samePage(url)) return;
        event.preventDefault();
        swapRegion(region, url.toString(), true);
    });

    // A GET filter form that ever moves inside a region behaves like its links.
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || form.method.toLowerCase() !== 'get') return;
        var region = regionOf(form);
        if (!region) return;
        var url = new URL(form.action || window.location.href, window.location.href);
        if (!samePage(url)) return;
        event.preventDefault();
        var params = new URLSearchParams(new FormData(form));
        url.search = params.toString();
        swapRegion(region, url.toString(), true);
    });

    // Back and forward replay through the regions instead of reloading the page.
    // lastUrl guards the spurious popstate some shells fire on load.
    window.addEventListener('popstate', function () {
        if (window.location.href === lastUrl) return;
        lastUrl = window.location.href;
        document.querySelectorAll('[' + REGION + ']').forEach(function (region) {
            swapRegion(region, window.location.href, false);
        });
    });

    window.RaykidListNav = {
        register: function (name, init) { initializers[name] = init; },
    };
})();
