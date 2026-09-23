/* ============================================================
   Raykid Store — toast queue (Phase B)
   Vanilla Sonner-style patterns adapted from emilkowalski/sonner:
   one #toaster container, id-merge updates, max 3 visible with a
   FIFO waiting room, loading → result by id, action buttons,
   timers that pause while the tab is hidden.
   Colours come only from the theme tokens the shell already owns
   (see motion.css .toast-item rules); this file owns behaviour.
   Enter/exit motion is motion.css `.t-toast` + `.is-open`.
   ============================================================ */
(function () {
    'use strict';

    var MAX_VISIBLE = 3;
    var DEFAULT_DURATION = 4000;
    var counter = 1;
    var visible = [];   // toast records on screen, newest last
    var waiting = [];   // records queued while 3 are showing
    var byId = {};      // id -> record (visible or waiting)

    function container() { return document.getElementById('toaster'); }

    function closeMs() {
        try {
            var v = parseFloat(getComputedStyle(document.documentElement)
                .getPropertyValue('--toast-close'));
            return Number.isFinite(v) ? v : 250;
        } catch (e) { return 250; }
    }

    function buildEl(record) {
        var el = document.createElement('div');
        el.className = 'toast-item t-toast toast-' + record.type;
        el.setAttribute('role', record.type === 'error' ? 'alert' : 'status');
        el.dataset.toastId = String(record.id);

        var icon = document.createElement('span');
        icon.className = 'toast-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = record.type === 'error' ? '⚠️'
            : record.type === 'success' ? '✅'
            : record.type === 'loading' ? '⏳' : 'ℹ️';
        el.appendChild(icon);

        var body = document.createElement('div');
        body.className = 'toast-body';
        var title = document.createElement('div');
        title.className = 'toast-title';
        title.textContent = record.title;
        body.appendChild(title);
        if (record.description) {
            var desc = document.createElement('div');
            desc.className = 'toast-desc';
            desc.textContent = record.description;
            body.appendChild(desc);
        }
        el.appendChild(body);

        if (record.action) {
            var btn = document.createElement('a');
            btn.className = 'toast-action';
            btn.textContent = record.action.label;
            btn.href = record.action.href || '#';
            if (record.action.href) {
                btn.addEventListener('click', function () { dismiss(record.id); });
            } else if (record.action.onClick) {
                btn.href = '#';
                btn.addEventListener('click', function (ev) {
                    ev.preventDefault();
                    record.action.onClick();
                    dismiss(record.id);
                });
            }
            el.appendChild(btn);
        }

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'toast-close';
        close.setAttribute('aria-label', 'بستن اعلان');
        close.textContent = '✕';
        close.addEventListener('click', function () { dismiss(record.id); });
        el.appendChild(close);

        return el;
    }

    function paint(record) {
        // Re-render an adopted or updated toast in place.
        var fresh = buildEl(record);
        record.el.replaceWith(fresh);
        record.el = fresh;
        if (!fresh.classList.contains('is-open')) {
            void fresh.offsetWidth;
            fresh.classList.add('is-open');
        }
    }

    function armTimer(record) {
        disarmTimer(record);
        if (!record.duration || record.duration === Infinity) return;
        record.deadline = Date.now() + record.remaining;
        record.timer = setTimeout(function () { dismiss(record.id); }, record.remaining);
    }

    function disarmTimer(record) {
        if (record.timer) { clearTimeout(record.timer); record.timer = null; }
    }

    // Timers pause while the tab is hidden: a toast the cashier never saw
    // must not expire behind the inventory tab.
    document.addEventListener('visibilitychange', function () {
        var now = Date.now();
        Object.keys(byId).forEach(function (id) {
            var record = byId[id];
            if (!record.el || !record.el.isConnected) return;
            if (document.hidden) {
                disarmTimer(record);
                if (record.duration && record.duration !== Infinity) {
                    record.remaining = Math.max(0, (record.deadline || now) - now);
                }
            } else if (record.duration && record.duration !== Infinity && !record.timer) {
                armTimer(record);
            }
        });
    });

    function mount(record) {
        var box = container();
        if (!box) return;
        record.el = buildEl(record);
        box.appendChild(record.el);
        // Flush the pre-open rest state, then release in the same task so
        // the entrance always animates (a rAF hop is skipped when the frame
        // clock is throttled and the toast would land with no motion).
        void record.el.offsetWidth;
        record.el.classList.add('is-open');
        visible.push(record);
        armTimer(record);
    }

    function unmount(record) {
        disarmTimer(record);
        var el = record.el;
        delete byId[record.id];
        visible = visible.filter(function (r) { return r.id !== record.id; });
        if (el && el.isConnected) {
            el.classList.remove('is-open');
            setTimeout(function () { el.remove(); }, closeMs() + 60);
        }
        pump();
    }

    function pump() {
        while (visible.length < MAX_VISIBLE && waiting.length) {
            mount(waiting.shift());
        }
    }

    function upsert(title, opts) {
        opts = opts || {};
        var id = (opts.id !== undefined && opts.id !== null) ? opts.id : counter++;
        var type = opts.type || 'info';
        var existing = byId[id];
        if (existing) {
            // Same id merges: a loading toast becomes its own result instead
            // of stacking a second row (StrictMode-style double calls safe).
            existing.title = title;
            existing.description = opts.description || '';
            existing.type = type;
            existing.action = opts.action || null;
            existing.duration = (opts.duration !== undefined)
                ? opts.duration
                : (type === 'error' || existing.action ? Infinity : DEFAULT_DURATION);
            existing.remaining = existing.duration;
            if (existing.el && existing.el.isConnected) {
                paint(existing);
                armTimer(existing);
            }
            return id;
        }
        var record = {
            id: id,
            title: title,
            description: opts.description || '',
            type: type,
            action: opts.action || null,
            duration: (opts.duration !== undefined)
                ? opts.duration
                : (type === 'error' || opts.action ? Infinity : DEFAULT_DURATION),
            remaining: 0,
            el: null,
            timer: null,
            deadline: 0,
        };
        record.remaining = record.duration;
        byId[id] = record;
        if (visible.length < MAX_VISIBLE) mount(record);
        else waiting.push(record);
        return id;
    }

    function dismiss(id) {
        if (id === undefined || id === null) {
            Object.keys(byId).forEach(function (key) { unmount(byId[key]); });
            waiting = [];
            return;
        }
        var record = byId[id];
        if (!record) return;
        waiting = waiting.filter(function (r) { return r.id !== id; });
        if (!record.el || !record.el.isConnected) { delete byId[id]; pump(); return; }
        unmount(record);
    }

    // Adopt server-rendered flash: page_header renders ?msg=/?err= as
    // [data-toast] rows so the text survives with no JS; here they move
    // into the stack and gain timers + dismissal.
    function adoptServerFlash() {
        var box = container();
        if (!box) return;
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-toast]:not([data-toast-adopted])'),
            function (node) {
                node.setAttribute('data-toast-adopted', 'true');
                var type = node.getAttribute('data-toast-type') || 'info';
                var id = upsert(node.textContent.trim(), { type: type });
                var record = byId[id];
                if (record && record.el) {
                    // Keep the server row's accessible role on the toast.
                    record.el.setAttribute('role', node.getAttribute('role') || record.el.getAttribute('role'));
                }
                node.remove();
            }
        );
    }

    var api = {
        show: function (title, opts) { return upsert(title, opts); },
        success: function (title, opts) { return upsert(title, Object.assign({}, opts, { type: 'success' })); },
        error: function (title, opts) { return upsert(title, Object.assign({}, opts, { type: 'error' })); },
        info: function (title, opts) { return upsert(title, Object.assign({}, opts, { type: 'info' })); },
        loading: function (title, opts) { return upsert(title, Object.assign({}, opts, { type: 'loading' })); },
        promise: function (promise, msgs) {
            var id = upsert(msgs.loading || 'در حال انجام…', { type: 'loading', duration: Infinity });
            Promise.resolve(promise).then(function (value) {
                var ok = (typeof msgs.success === 'function') ? msgs.success(value) : msgs.success;
                upsert(ok || 'انجام شد', { id: id, type: 'success' });
            }, function (err) {
                var bad = (typeof msgs.error === 'function') ? msgs.error(err) : (msgs.error || 'ناموفق بود');
                upsert(bad, { id: id, type: 'error' });
            });
            return id;
        },
        dismiss: dismiss,
    };

    window.RaykidToast = api;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', adoptServerFlash);
    } else {
        adoptServerFlash();
    }
})();
