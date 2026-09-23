/* Opt-in custom dropdowns: a button + listbox exactly the field's width,
   wearing the theme. Native popups size to the longest option (OS-owned,
   unfixable in CSS); this replaces only the popup, never the data — the
   native select stays in the form and is what gets submitted.

   Coarse pointers keep the native control (a bottom sheet beats any popup).
   Applied per select with data-fancy; late-added rows initialise the same. */
(function () {
    'use strict';

    if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) return;

    var TYPE_DELAY = 600;

    function closeAll(except) {
        document.querySelectorAll('.fancy-list').forEach(function (list) {
            if (list === except) return;
            list.hidden = true;
            var btn = list.closest('.fancy-wrap');
            if (btn) btn.querySelector('.fancy-btn').setAttribute('aria-expanded', 'false');
        });
    }

    function pick(wrap, option, focusButton) {
        var select = wrap.querySelector('select');
        var btn = wrap.querySelector('.fancy-btn');
        select.value = option.dataset.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        btn.querySelector('.fancy-label').textContent = option.textContent;
        wrap.querySelectorAll('.fancy-option').forEach(function (each) {
            each.setAttribute('aria-selected', each === option ? 'true' : 'false');
        });
        closeAll();
        if (focusButton) btn.focus({ preventScroll: true });
    }

    function move(wrap, step) {
        var options = Array.prototype.slice.call(
            wrap.querySelectorAll('.fancy-option:not([aria-disabled="true"])'));
        if (!options.length) return null;
        var current = wrap.querySelector('.fancy-option.is-active');
        var index = current ? options.indexOf(current) : (step > 0 ? -1 : 0);
        index = (index + step + options.length) % options.length;
        options.forEach(function (each) { each.classList.remove('is-active'); });
        options[index].classList.add('is-active');
        options[index].scrollIntoView({ block: 'nearest' });
        return options[index];
    }

    function toggle(wrap, open) {
        var btn = wrap.querySelector('.fancy-btn');
        var list = wrap.querySelector('.fancy-list');
        var willOpen = open !== undefined ? open : list.hidden;
        closeAll(willOpen ? list : null);
        list.hidden = !willOpen;
        btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        if (willOpen) {
            var selected = wrap.querySelector('.fancy-option[aria-selected="true"]');
            wrap.querySelectorAll('.fancy-option').forEach(function (each) {
                each.classList.toggle('is-active', each === selected);
            });
            if (selected) selected.scrollIntoView({ block: 'nearest' });
        }
    }

    function build(select) {
        var wrap = document.createElement('span');
        wrap.className = 'fancy-wrap';
        select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(select);
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'fancy-btn';
        btn.id = select.id ? select.id + '-fancy' : '';
        btn.setAttribute('aria-haspopup', 'listbox');
        btn.setAttribute('aria-expanded', 'false');
        var label = select.options[select.selectedIndex];
        btn.innerHTML = '<span class="fancy-label"></span>';
        btn.querySelector('.fancy-label').textContent = label ? label.text : '';
        wrap.appendChild(btn);

        var list = document.createElement('ul');
        list.className = 'fancy-list';
        list.setAttribute('role', 'listbox');
        list.hidden = true;
        if (select.id) list.setAttribute('aria-labelledby', btn.id);
        Array.prototype.forEach.call(select.options, function (opt) {
            var item = document.createElement('li');
            item.className = 'fancy-option';
            item.setAttribute('role', 'option');
            item.dataset.value = opt.value;
            item.id = (select.id || 'fancy') + '-opt-' + opt.index;
            item.textContent = opt.text;
            item.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
            if (opt.disabled) item.setAttribute('aria-disabled', 'true');
            item.addEventListener('click', function () {
                if (opt.disabled) return;
                pick(wrap, item, true);
            });
            item.addEventListener('mousemove', function () {
                wrap.querySelectorAll('.fancy-option').forEach(function (each) {
                    each.classList.remove('is-active');
                });
                item.classList.add('is-active');
            });
            list.appendChild(item);
        });
        wrap.appendChild(list);

        var typed = '', typedAt = 0;
        btn.addEventListener('click', function () { toggle(wrap); });
        btn.addEventListener('keydown', function (event) {
            var now = Date.now();
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (list.hidden) { toggle(wrap, true); return; }
                move(wrap, event.key === 'ArrowDown' ? 1 : -1);
            } else if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                if (list.hidden) { toggle(wrap, true); return; }
                var active = wrap.querySelector('.fancy-option.is-active');
                if (active && active.getAttribute('aria-disabled') !== 'true') pick(wrap, active, true);
            } else if (event.key === 'Escape') {
                if (!list.hidden) { event.preventDefault(); toggle(wrap, false); btn.focus({ preventScroll: true }); }
            } else if (event.key.length === 1) {
                if (now - typedAt > TYPE_DELAY) typed = '';
                typed += event.key;
                typedAt = now;
                if (list.hidden) toggle(wrap, true);
                var hit = null;
                wrap.querySelectorAll('.fancy-option:not([aria-disabled="true"])').forEach(function (each) {
                    if (!hit && each.textContent.trim().toLowerCase().indexOf(typed.toLowerCase()) === 0) hit = each;
                });
                if (hit) {
                    wrap.querySelectorAll('.fancy-option').forEach(function (each) {
                        each.classList.toggle('is-active', each === hit);
                    });
                    hit.scrollIntoView({ block: 'nearest' });
                }
            }
        });

        // The server (or a form reset) owns the value; the button mirrors it.
        select.addEventListener('change', function () {
            var current = select.options[select.selectedIndex];
            btn.querySelector('.fancy-label').textContent = current ? current.text : '';
            wrap.querySelectorAll('.fancy-option').forEach(function (each) {
                each.setAttribute('aria-selected',
                    each.dataset.value === select.value ? 'true' : 'false');
            });
        });
    }

    function initFancy(scope) {
        (scope || document).querySelectorAll('select[data-fancy]').forEach(function (select) {
            if (select.dataset.fancyReady) return;
            select.dataset.fancyReady = 'true';
            build(select);
        });
    }

    document.addEventListener('click', function (event) {
        if (event.target.closest && event.target.closest('.fancy-wrap')) return;
        closeAll();
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closeAll();
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { initFancy(document); });
    } else {
        initFancy(document);
    }

    window.RaykidDropdown = { init: initFancy };
})();
