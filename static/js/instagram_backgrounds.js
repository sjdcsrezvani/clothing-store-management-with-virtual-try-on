/* Instagram Studio backgrounds: product field visibility, the one-line
   description, model lighting ownership, and the reference slots the chosen
   scene/pose actually needs. HTMX card swaps re-run sync. */
(function () {
    'use strict';

    var VIEW_LABELS = { main: 'اصلی', back: 'پشت', detail: 'جزئیات' };

    function syncReferences(form) {
        var scene = form.querySelector('select[name="garment_mode"]');
        var pose = form.querySelector('select[name="pose_key"]');
        var source = scene || pose;
        if (!source) return;

        var option = source.options[source.selectedIndex];
        var views = ((option && option.getAttribute('data-views')) || 'main')
            .split(',');
        var inputs = form.querySelectorAll('input[name="image_paths"]');

        Array.prototype.forEach.call(inputs, function (input) {
            var view = input.getAttribute('data-view') || 'main';
            var required = views.indexOf(view) !== -1;
            input.checked = required;
            input.disabled = !required;
            var label = input.closest('label');
            if (label) {
                label.classList.toggle('is-disabled', !required);
                label.style.cursor = required ? 'pointer' : 'not-allowed';
            }
        });

        var warning = form.querySelector('[data-reference-warning]');
        if (!warning) return;
        var supported = (form.getAttribute('data-supported-views') || '').split(',');
        var missing = views.filter(function (view) {
            return supported.indexOf(view) !== -1 &&
                !form.querySelector(
                    'input[name="image_paths"][data-view="' + view + '"]');
        });
        if (!missing.length) {
            warning.hidden = true;
            warning.textContent = '';
            return;
        }
        warning.hidden = false;
        if (missing.indexOf('main') !== -1) {
            warning.textContent =
                'عکس اصلی این تنوع ثبت نشده است؛ تا آپلود آن تولید ممکن نیست.';
        } else {
            warning.textContent = 'عکس ' + missing.map(function (view) {
                return VIEW_LABELS[view] || view;
            }).join(' و ') +
                ' برای این صحنه/ژست ثبت نشده؛ فقط تصویر اصلی ارسال می‌شود.';
        }
    }

    function sync(form) {
        syncReferences(form);

        var background = form.querySelector('select[name="background_key"]');
        if (!background) return;

        var scene = form.querySelector('select[name="garment_mode"]');
        var field = background.closest('[data-background-field]');
        if (scene && field) {
            var room = (form.getAttribute('data-room-background-scenes') || '')
                .split(',');
            var show = room.indexOf(scene.value) !== -1;
            field.hidden = !show;
            background.disabled = !show;
        }

        var option = background.options[background.selectedIndex];
        var description = field &&
            field.querySelector('[data-background-description]');
        if (description) {
            var text = option ?
                (option.getAttribute('data-description') || '') : '';
            description.textContent = text;
            description.hidden = !text;
        }

        var lighting = form.querySelector('[data-lighting-field]');
        if (lighting && option) {
            var fromBackground = option.getAttribute('data-source') === 'brand';
            lighting.hidden = fromBackground;
            var lightingSelect = lighting.querySelector('select');
            if (lightingSelect) lightingSelect.disabled = fromBackground;
        }
    }

    function syncAll() {
        Array.prototype.forEach.call(
            document.querySelectorAll(
                'form[action="/admin/instagram-studio/generate"]'),
            sync);
    }

    /* Capture phase: hide/disable fields before htmx serializes the form. */
    document.addEventListener('change', function (event) {
        if (event.target.form) sync(event.target.form);
    }, true);

    if (window.htmx && typeof window.htmx.onLoad === 'function') {
        window.htmx.onLoad(syncAll);
    } else {
        document.addEventListener('DOMContentLoaded', syncAll);
    }
    syncAll();
})();
