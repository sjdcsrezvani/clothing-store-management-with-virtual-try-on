/* One digit language for every money/phone/count field: Persian and Arabic
   digits are converted to English the moment they are typed, so what the
   field holds — and what the server parses — is always one script. The
   mapping is 1:1 per character, so the caret never jumps. Date-picker
   fields are excluded: Jalali dates live in Persian digits by design. */
function toEnglishDigits(value) {
    const fa = '۰۱۲۳۴۵۶۷۸۹', ar = '٠١٢٣٤٥٦٧٨٩';
    return String(value || '').replace(/[۰-۹]/g, d => fa.indexOf(d)).replace(/[٠-٩]/g, d => ar.indexOf(d));
}

function unifyFieldDigits(field) {
    const converted = toEnglishDigits(field.value);
    if (converted !== field.value) field.value = converted;
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('input[type="tel"]').forEach(input => {
        input.addEventListener('input', () => {
            unifyFieldDigits(input);
            input.value = input.value.replace(/[^0-9]/g, '');
        });
    });

    // Live conversion for every numeric field, including ones added later.
    document.addEventListener('input', (event) => {
        const target = event.target;
        if (!target.matches || !target.matches('input[inputmode="numeric"]')) return;
        unifyFieldDigits(target);
    });

    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', () => {
            const submit = form.querySelector('button[type="submit"]');
            if (!submit || submit.dataset.allowRepeat === 'true') return;
            submit.disabled = true;
            submit.setAttribute('aria-busy', 'true');
            submit.classList.add('is-loading');
        });
    });

    const cashReceived = document.getElementById('cash-received');
    const cashCalculator = document.getElementById('cash-calculator');
    const paymentMethods = document.querySelectorAll('input[name="payment_method"]');
    const totalElement = document.querySelector('.summary-row.total .tnum');
    const finalAmount = Number(totalElement?.dataset.amount || totalElement?.textContent.replace(/[^0-9]/g, '') || 0);
    function updateCashCalculator() {
        const selected = document.querySelector('input[name="payment_method"]:checked');
        const isCash = selected && selected.value === 'cash';
        if (cashCalculator) cashCalculator.hidden = !isCash;
        if (cashReceived && isCash) {
            const change = Number(toEnglishDigits(cashReceived.value).replace(/[^0-9]/g, '') || 0) - finalAmount;
            const label = document.getElementById('cash-change');
            if (label) label.textContent = change >= 0 ? 'باقی‌مانده: ' + change.toLocaleString('fa-IR') + ' تومان' : 'مبلغ دریافتی کافی نیست';
        }
    }
    paymentMethods.forEach(input => input.addEventListener('change', updateCashCalculator));
    if (cashReceived) cashReceived.addEventListener('input', updateCashCalculator);
    updateCashCalculator();

    initColorPickers();

    const barcode = document.getElementById('barcode-input');
    const scanForm = document.getElementById('scan-form');
    if (barcode && scanForm) {
        barcode.focus();
        barcode.addEventListener('keydown', event => {
            if (event.key === 'Enter' && barcode.value.trim()) {
                event.preventDefault();
                if (typeof scanForm.requestSubmit === 'function') scanForm.requestSubmit();
                else scanForm.submit();
            }
        });
    }
});

/* کد رنگ fields (marked with data-color-picker): clicking the field opens the
   native colour palette and the picked colour is written back as a hex code.
   Nothing is drawn, so every theme keeps its own field styling. */
function initColorPickers(scope) {
    const hexCode = /^#?(?:([0-9a-f]{6})|([0-9a-f])([0-9a-f])([0-9a-f]))$/i;
    (scope || document).querySelectorAll('input[data-color-picker]').forEach(field => {
        if (field.dataset.colorPickerReady) return;
        field.dataset.colorPickerReady = 'true';

        const host = field.parentElement || document.body;
        if (getComputedStyle(host).position === 'static') host.style.position = 'relative';

        const picker = document.createElement('input');
        picker.type = 'color';
        picker.tabIndex = -1;
        picker.setAttribute('aria-hidden', 'true');
        picker.style.cssText = 'position:absolute;width:1px;height:1px;min-height:0;padding:0;border:0;opacity:0;pointer-events:none;bottom:0;inset-inline-start:0;';
        host.appendChild(picker);

        picker.addEventListener('input', () => {
            field.value = picker.value.toUpperCase();
            field.dispatchEvent(new Event('change', { bubbles: true }));
        });

        field.addEventListener('click', () => {
            const match = hexCode.exec((field.value || '').trim());
            // Short #abc codes expand to aabbcc, the only form a colour input accepts.
            const full = match && (match[1] || (match[2] + match[2] + match[3] + match[3] + match[4] + match[4]));
            if (full) picker.value = '#' + full.toLowerCase();
            picker.click();
        });
    });
}

// ── «for whom» choice ───────────────────────────────────────────────────────
// Each customer chooses whether they buy for themselves or for their child, and
// that choice decides which birthday fields the form shows. Both groups stay in
// the form and the *server* writes only the chosen side, so hiding here is
// presentation only — a field that slips through can never be stored.
function syncBuysFor(scope) {
    const chosen = scope.querySelector('input[name="buys_for"]:checked');
    scope.querySelectorAll('[data-buys-for-panel]').forEach((panel) => {
        const hide = !!chosen && chosen.value !== panel.getAttribute('data-buys-for-panel');
        panel.classList.toggle('is-hidden', hide);
        // Keep the hidden side out of the accessibility tree and out of tab order.
        panel.hidden = hide;
        panel.querySelectorAll('input, select, textarea').forEach((field) => {
            field.disabled = hide;
        });
    });
}

function initBuysFor() {
    document.querySelectorAll('[data-buys-for-scope]').forEach(syncBuysFor);
}

document.addEventListener('change', (event) => {
    const target = event.target;
    if (!target.matches || !target.matches('input[name="buys_for"]')) return;
    const scope = target.closest('[data-buys-for-scope]');
    if (scope) syncBuysFor(scope);
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBuysFor);
} else {
    initBuysFor();
}

function normalizeServerUrl(value, defaultPort = 8000) {
    value = (value || '').trim().replace(/^https?:\/\//i, '').replace(/\/$/, '');
    if (!value) return '';
    if (!value.includes(':')) value += ':' + defaultPort;
    return 'http://' + value;
}
