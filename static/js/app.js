document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('input[type="tel"]').forEach(input => {
        input.addEventListener('input', () => {
            input.value = input.value.replace(/[^0-9]/g, '');
        });
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
            const change = Number(cashReceived.value || 0) - finalAmount;
            const label = document.getElementById('cash-change');
            if (label) label.textContent = change >= 0 ? 'باقی‌مانده: ' + change.toLocaleString('fa-IR') + ' تومان' : 'مبلغ دریافتی کافی نیست';
        }
    }
    paymentMethods.forEach(input => input.addEventListener('change', updateCashCalculator));
    if (cashReceived) cashReceived.addEventListener('input', updateCashCalculator);
    updateCashCalculator();

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

function normalizeServerUrl(value, defaultPort = 8000) {
    value = (value || '').trim().replace(/^https?:\/\//i, '').replace(/\/$/, '');
    if (!value) return '';
    if (!value.includes(':')) value += ':' + defaultPort;
    return 'http://' + value;
}
