/* ============================================================
   Raykid Store — shared dialog behaviour (Phase C)
   One pattern for every overlay surface: open moves focus inside,
   Escape closes the topmost surface and returns focus to its opener,
   Tab cycles inside while open. The sidebar drawer keeps its own
   tested script in base.html; this helper serves the lightbox, the
   mobile capture overlay, and future bottom-sheets.
   Surfaces opt in with [data-dialog] on the container and
   [data-dialog-close] on dismiss buttons; the opener passes itself.
   ============================================================ */
(function () {
    'use strict';

    var stack = []; // open records, topmost last

    function focusables(root) {
        return Array.prototype.filter.call(
            root.querySelectorAll(
                'a[href], button:not([disabled]), input:not([disabled]), ' +
                'select:not([disabled]), textarea:not([disabled]), ' +
                '[tabindex]:not([tabindex="-1"])'
            ),
            function (el) { return el.offsetParent !== null; }
        );
    }

    function show(record) {
        record.node.classList.add('is-open');
        record.node.setAttribute('aria-hidden', 'false');
        var items = focusables(record.node);
        // The keyboard goes where the eye went: Tab lands inside the open
        // surface rather than continuing through the page behind it.
        (items[0] || record.node).focus();
    }

    function hide(record) {
        record.node.classList.remove('is-open');
        record.node.setAttribute('aria-hidden', 'true');
    }

    function open(id, opener) {
        var node = typeof id === 'string' ? document.getElementById(id) : id;
        if (!node || node.classList.contains('is-open')) return null;
        var record = { node: node, opener: opener || document.activeElement || null };
        stack.push(record);
        show(record);
        return record;
    }

    function close(recordOrId) {
        var index;
        if (typeof recordOrId === 'string' || (recordOrId && recordOrId.nodeType === 1)) {
            var node = typeof recordOrId === 'string'
                ? document.getElementById(recordOrId) : recordOrId;
            index = stack.map(function (r) { return r.node; }).lastIndexOf(node);
        } else if (recordOrId) {
            index = stack.indexOf(recordOrId);
        } else {
            index = stack.length - 1;
        }
        if (index < 0) return false;
        var record = stack.splice(index, 1)[0];
        hide(record);
        // Focus returns to whoever opened the surface, never left on air.
        if (record.opener && document.contains(record.opener)) record.opener.focus();
        return true;
    }

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && stack.length) {
            event.preventDefault();
            close();
            return;
        }
        if (event.key !== 'Tab' || !stack.length) return;
        var top = stack[stack.length - 1];
        var items = focusables(top.node);
        if (!items.length) { event.preventDefault(); top.node.focus(); return; }
        var first = items[0], last = items[items.length - 1];
        var current = document.activeElement;
        if (!current || !top.node.contains(current)) {
            event.preventDefault(); first.focus(); return;
        }
        if (event.shiftKey && current === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && current === last) { event.preventDefault(); first.focus(); }
    });

    // Declarative wiring: [data-dialog-open="id"] opens, [data-dialog-close]
    // closes its own surface. Zero inline handlers for new surfaces.
    document.addEventListener('click', function (event) {
        var opener = event.target.closest ? event.target.closest('[data-dialog-open]') : null;
        if (opener) { open(opener.getAttribute('data-dialog-open'), opener); return; }
        var closer = event.target.closest ? event.target.closest('[data-dialog-close]') : null;
        if (closer) {
            var host = closer.closest('[data-dialog]');
            if (host) close(host);
        }
    });

    window.RaykidDialog = { open: open, close: close };
})();
