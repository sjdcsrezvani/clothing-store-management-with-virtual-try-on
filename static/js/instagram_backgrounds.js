/* Instagram Studio backgrounds: product field visibility, the one-line
   description, and model lighting ownership. HTMX card swaps re-run sync. */
(function () {
    'use strict';

    function sync(form) {
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
