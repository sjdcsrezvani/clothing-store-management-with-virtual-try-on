/* Sizing pickers — the size field follows the product's tag system and its
   garment ladder (embedded as JSON by the form). A ladder renders as a select
   with a «سایر…» escape that keeps free text; a value the changing ladder
   cannot name keeps its text too. The resolved band previews under the field
   through the server, so there is exactly one converter to trust. Re-entry
   safe: the shell re-runs this file on boosted arrivals. */
(function () {
    'use strict';

    function ladders() {
        var node = document.getElementById('sizing-ladders');
        if (!node) return {};
        try { return JSON.parse(node.textContent || '{}'); } catch (error) { return {}; }
    }

    function settingValue(id) {
        var node = document.getElementById(id);
        return node ? node.value : '';
    }

    function fieldGender(field) {
        return (field && field.dataset.sizeGender) || settingValue('gender') || '';
    }

    function optionsFor(system, field) {
        var table = ladders();
        if (!system || !table[system]) return null;
        if (system === 'adult') {
            var gender = fieldGender(field) === 'man' ? 'man' : 'woman';
            return (table.adult && table.adult[gender]) || null;
        }
        return table[system] || null;
    }

    function activeControl(field) {
        var other = field.querySelector('[data-size-other]');
        if (other && other.getAttribute('name')) return other;
        return field.querySelector('[data-size-select]');
    }

    function valueOf(field) {
        var control = activeControl(field);
        return control ? String(control.value || '').trim() : '';
    }

    function preview(field) {
        var node = field.querySelector('[data-size-preview]');
        if (!node) return;
        var system = field.dataset.sizeSystem || settingValue('size_system');
        var sizeType = field.dataset.sizeType || settingValue('size_type');
        var gender = fieldGender(field);
        var size = valueOf(field);
        if (!system || !size) { node.hidden = true; node.textContent = ''; return; }
        node.hidden = false;
        htmx.ajax('GET', '/admin/sizing/preview?system=' + encodeURIComponent(system)
            + '&type=' + encodeURIComponent(sizeType)
            + '&gender=' + encodeURIComponent(gender)
            + '&size=' + encodeURIComponent(size), { target: node });
    }

    function showOther(select, other, name) {
        select.removeAttribute('name');
        other.setAttribute('name', name || '');
        other.hidden = false;
    }

    function showSelect(select, other, name) {
        other.removeAttribute('name');
        other.hidden = true;
        other.value = '';
        select.setAttribute('name', name || '');
    }

    function rebuild(field) {
        var select = field.querySelector('[data-size-select]');
        var other = field.querySelector('[data-size-other]');
        if (!select || !other) return;
        var name = field.dataset.sizeName;
        if (!name) {
            name = select.getAttribute('name') || other.getAttribute('name') || '';
            field.dataset.sizeName = name;
        }
        var current = field.dataset.sizeValue || '';
        delete field.dataset.sizeValue;
        if (!current) current = valueOf(field);
        var system = field.dataset.sizeSystem || settingValue('size_system');
        var options = optionsFor(system, field);
        if (!options) {
            // No ladder for this combination: plain free text, as before.
            select.hidden = true;
            showOther(select, other, name);
            if (!other.value && current) other.value = current;
            preview(field);
            return;
        }
        var known = options.some(function (option) { return option.value === current; });
        select.innerHTML = '';
        var head = document.createElement('option');
        head.value = '';
        head.textContent = 'انتخاب سایز';
        select.appendChild(head);
        options.forEach(function (option) {
            var node = document.createElement('option');
            node.value = option.value;
            node.textContent = option.label;
            select.appendChild(node);
        });
        var escape = document.createElement('option');
        escape.value = '__other__';
        escape.textContent = 'سایر…';
        select.appendChild(escape);
        select.hidden = false;
        if (current && !known) {
            // A value this ladder cannot name keeps its own text («سایر»).
            select.value = '';
            showOther(select, other, name);
            other.value = current;
        } else {
            select.value = current || '';
            showSelect(select, other, name);
        }
        preview(field);
    }

    function setup(field) {
        if (field.dataset.sizingReady !== 'true') {
            field.dataset.sizingReady = 'true';
            var select = field.querySelector('[data-size-select]');
            var other = field.querySelector('[data-size-other]');
            var timer = null;
            if (select) {
                select.addEventListener('change', function () {
                    if (select.value === '__other__') {
                        showOther(select, other, field.dataset.sizeName);
                        other.value = '';
                        other.focus();
                        preview(field);
                    } else if (select.value) {
                        showSelect(select, other, field.dataset.sizeName);
                        preview(field);
                    }
                });
            }
            if (other) {
                other.addEventListener('input', function () {
                    clearTimeout(timer);
                    timer = setTimeout(function () { preview(field); }, 250);
                });
            }
        }
        rebuild(field);
    }

    window.RaykidSizing = {
        setup: function (scope) {
            (scope || document).querySelectorAll('[data-size-field]').forEach(setup);
        },
    };

    function bindSettings() {
        ['size_system', 'size_type', 'gender'].forEach(function (id) {
            var node = document.getElementById(id);
            if (node && node.dataset.sizingBound !== 'true') {
                node.dataset.sizingBound = 'true';
                node.addEventListener('change', function () { window.RaykidSizing.setup(); });
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        bindSettings();
        window.RaykidSizing.setup();
    });
    document.body.addEventListener('htmx:load', function (event) {
        bindSettings();
        window.RaykidSizing.setup(event.target);
    });
})();
