document.addEventListener('DOMContentLoaded', () => {
    const phoneInputs = document.querySelectorAll('input[type="tel"]');
    phoneInputs.forEach(input => {
        input.addEventListener('input', () => {
            input.value = input.value.replace(/[^0-9]/g, '');
        });
    });
});

// Accept either 192.168.1.10, 192.168.1.10:8000, or a full URL.
function normalizeServerUrl(value, defaultPort = 8000) {
    value = (value || '').trim().replace(/^https?:\/\//i, '').replace(/\/$/, '');
    if (!value) return '';
    if (!value.includes(':')) value += ':' + defaultPort;
    return 'http://' + value;
}
