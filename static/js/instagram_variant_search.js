/* Live variant picker for Instagram Studio: htmx carries the search and the
   card swap; this file only does keyboard/ARIA and URL bookkeeping. */
(function () {
    'use strict';

    var search = document.getElementById('ig-variant-search');
    if (!search) return;
    var box = search.closest('.variant-search');
    var list = document.getElementById('ig-variant-results');
    var error = box.querySelector('.variant-search-error');
    var cardUrl = box.getAttribute('data-card-url');
    var scenario = box.getAttribute('data-scenario');
    var active = -1;

    function rows() {
        return Array.prototype.slice.call(
            list.querySelectorAll('li[data-variant-id]'));
    }

    function setError(message) {
        error.textContent = message || '';
        error.hidden = !message;
    }

    function hide() {
        list.hidden = true;
        search.setAttribute('aria-expanded', 'false');
        search.removeAttribute('aria-activedescendant');
        rows().forEach(function (row) {
            row.classList.remove('is-active');
            row.setAttribute('aria-selected', 'false');
        });
        active = -1;
    }

    function show() {
        if (!list.children.length) {
            list.hidden = true;
            search.setAttribute('aria-expanded', 'false');
            return;
        }
        list.hidden = false;
        search.setAttribute('aria-expanded',
                            rows().length ? 'true' : 'false');
    }

    function move(step) {
        var options = rows();
        if (!options.length) return;
        active = (active + step + options.length) % options.length;
        options.forEach(function (row, index) {
            row.classList.toggle('is-active', index === active);
            row.setAttribute('aria-selected', index === active ? 'true' : 'false');
        });
        var row = options[active];
        search.setAttribute('aria-activedescendant', row.id);
        row.scrollIntoView({ block: 'nearest' });
    }

    function choose(row) {
        if (!row) return;
        var item = {
            variant_id: row.getAttribute('data-variant-id'),
            product_id: row.getAttribute('data-product-id'),
            label: row.getAttribute('data-label')
        };
        var params = new URLSearchParams({
            scenario: scenario,
            product_id: item.product_id,
            variant_id: item.variant_id
        });
        hide();
        list.innerHTML = '';
        setError('');
        htmx.ajax('GET', cardUrl + '?' + params.toString(), {
            target: '#ig-variant-card',
            swap: 'outerHTML'
        }).then(function () {
            var card = document.getElementById('ig-variant-card');
            if (!card || card.getAttribute('data-variant-id') !== item.variant_id) {
                setError('انتخاب تنوع انجام نشد؛ دوباره تلاش کنید.');
                return;
            }
            var result = document.getElementById('instagram-result');
            if (result) result.remove();
            search.value = item.label;
            search.select();
            history.replaceState(null, '',
                '/admin/instagram-studio?' + params.toString());
            // The tabs live outside the swapped card; keep their ids current.
            var tabs = document.querySelectorAll('#ig-scenario-tabs a');
            Array.prototype.forEach.call(tabs, function (link) {
                var url = new URL(link.getAttribute('href'), window.location.origin);
                url.searchParams.set('product_id', item.product_id);
                url.searchParams.set('variant_id', item.variant_id);
                link.setAttribute('href', url.pathname + url.search);
            });
        }).catch(function () {
            setError('انتخاب تنوع انجام نشد؛ دوباره تلاش کنید.');
        });
    }

    list.addEventListener('htmx:afterSwap', function (event) {
        if (event.target !== list) return;
        if (search.value.trim().length < 2) {
            hide();
            return;
        }
        active = -1;
        show();
    });

    list.addEventListener('click', function (event) {
        var row = event.target.closest('li[data-variant-id]');
        if (!row) return;
        event.preventDefault();
        choose(row);
    });

    search.addEventListener('focus', function () {
        search.select();
        if (search.value.trim().length >= 2) show();
    });

    search.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            move(1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            move(-1);
        } else if (event.key === 'Escape') {
            hide();
        } else if (event.key === 'Enter') {
            event.preventDefault();
            var exact = list.querySelector('li[data-exact="1"]');
            choose(exact || rows()[active] || null);
        }
    });

    document.addEventListener('click', function (event) {
        if (!box.contains(event.target)) hide();
    });
})();
