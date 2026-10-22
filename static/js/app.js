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
        if (!target.matches) return;
        // Money/count fields convert and strip; size fields (marked
        // data-unify-digits) convert only — «۳ تا ۴ سال» keeps its words
        // while its digits settle into English. The mapping is 1:1 per
        // character, so the caret never jumps in either case.
        if (target.matches('input[inputmode="numeric"]')) unifyFieldDigits(target);
        else if (target.matches('input[data-unify-digits]')) unifyFieldDigits(target);
    });

    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', (event) => {
            // A form another handler refused owns its own button: locking it
            // after a validation refusal leaves the shop unable to try again.
            if (event.defaultPrevented) return;
            const submit = form.querySelector('button[type="submit"]');
            if (!submit || submit.dataset.allowRepeat === 'true') return;
            submit.disabled = true;
            submit.setAttribute('aria-busy', 'true');
            submit.classList.add('is-loading');
        });
    });

    const cashReceived = document.getElementById('cash-received');
    const paymentMethods = document.querySelectorAll('input[name="payment_method"]');
    const totalElement = document.querySelector('.summary-row.total .tnum');
    const finalAmount = Number(totalElement?.dataset.amount || totalElement?.textContent.replace(/[^0-9]/g, '') || 0);
    function updateCashCalculator() {
        // Always visible: for card it previews tendered-vs-final all the same.
        if (cashReceived) {
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
/* کد رنگ fields (marked with data-color-picker): a visible swatch plus a
   preset row, with the native palette kept for custom colours. The swatch
   shows the field's own value, so the colour is never invisible; presets are
   product colours (garment data), not theme tokens, so literal hex is
   correct here. Late-added rows (variant editor) initialise the same way. */
const COLOR_PRESETS = [
    ['سفید', '#FFFFFF'], ['مشکی', '#000000'],
    ['سرمه‌ای', '#1E3A8A'], ['آبی', '#2563EB'],
    ['قرمز', '#DC2626'], ['صورتی', '#EC4899'],
    ['سبز', '#16A34A'], ['کرم', '#E7D8B7'],
];

/* Persian names for the custom-palette picker («رنگ دلخواه»): every hex it
   hands back is named by its closest neighbour here, so a زرشکی pick reads
   «زرشکی», not the nearest of the eight presets. Garment data again, not
   theme tokens — literal hex is correct. */
const COLOR_NAMES = [
    ['سفید', '#FFFFFF'], ['شیری', '#FBF5E8'],
    ['صدفی', '#F4EDE4'], ['استخوانی', '#EFE5D2'],
    ['کرم', '#E7D8B7'], ['بژ', '#DCC9A8'],
    ['نود', '#E8C4A0'], ['نخودی', '#E4D5A5'],
    ['طوسی روشن', '#D8D8D8'], ['نقره‌ای', '#C0C0C0'],
    ['طوسی', '#A6A6A6'], ['طوسی تیره', '#757575'],
    ['دودی', '#5A5A5A'], ['ذغالی', '#363636'],
    ['مشکی', '#000000'], ['قرمز', '#DC2626'],
    ['قرمز تیره', '#AE1C1C'], ['جگری', '#7E1518'],
    ['آلبالویی', '#8C1B33'], ['زرشکی', '#801E3C'],
    ['شرابی', '#5E1526'], ['عنابی', '#6B2028'],
    ['آجری', '#B04A34'], ['گلبهی', '#E58C8C'],
    ['سرخابی', '#C2185B'], ['صورتی', '#EC4899'],
    ['صورتی روشن', '#F5B8CE'], ['صورتی پاستلی', '#FADCE8'],
    ['نارنجی', '#F97316'], ['نارنجی تیره', '#C2410C'],
    ['هلویی', '#FDB28A'], ['شتری', '#BE9A6A'],
    ['قهوه‌ای روشن', '#B08052'], ['قهوه‌ای', '#7E4E2B'],
    ['شکلاتی', '#563321'], ['نسکافه‌ای', '#4E3A2B'],
    ['قهوه‌ای سوخته', '#3B2417'], ['مسی', '#B87333'],
    ['عسلی', '#E0A428'], ['زرد', '#EAB308'],
    ['زرد روشن', '#F6E266'], ['لیمویی', '#BCD64A'],
    ['طلایی', '#D4AF37'], ['خردلی', '#C9A227'],
    ['سبز', '#16A34A'], ['سبز روشن', '#4ADE80'],
    ['سبز چمنی', '#4CAF50'], ['نعنایی', '#A8E6C4'],
    ['پسته‌ای', '#9CBF70'], ['یشمی', '#2E9E6B'],
    ['زیتونی', '#6F7B3E'], ['لجنی', '#566741'],
    ['سبز ارتشی', '#3F4B33'], ['سبز تیره', '#14532D'],
    ['سبز کله‌غازی', '#1D5A4A'], ['فیروزه‌ای', '#2BC4C2'],
    ['آبی آسمانی', '#6FB6E8'], ['آبی', '#2563EB'],
    ['آبی طوسی', '#6C7E96'], ['نفتی', '#274A5C'],
    ['نیلی', '#3B4A99'], ['آبی تیره', '#1E40AF'],
    ['سرمه‌ای', '#1E3A8A'], ['یاسی', '#C8A8E8'],
    ['بنفش', '#9333EA'], ['بنفش تیره', '#5B21B6'],
    ['ارغوانی', '#9B2B92'], ['بادمجانی', '#4C2E52'],
];

// Display families for the picker's grid: every table colour sits in exactly
// one row, in the words a shop groups colours by. The suite holds this list
// level with the table.
const COLOR_FAMILIES = [
    ['خنثی', ['سفید', 'شیری', 'صدفی', 'استخوانی', 'کرم', 'بژ', 'نود', 'نخودی']],
    ['خاکستری و مشکی', ['طوسی روشن', 'نقره‌ای', 'طوسی', 'طوسی تیره', 'دودی', 'ذغالی', 'مشکی']],
    ['قرمز', ['قرمز', 'قرمز تیره', 'جگری', 'آلبالویی', 'زرشکی', 'شرابی', 'عنابی', 'آجری', 'گلبهی', 'سرخابی']],
    ['صورتی', ['صورتی', 'صورتی روشن', 'صورتی پاستلی']],
    ['نارنجی و قهوه‌ای', ['نارنجی', 'نارنجی تیره', 'هلویی', 'شتری', 'قهوه‌ای روشن', 'قهوه‌ای', 'شکلاتی', 'نسکافه‌ای', 'قهوه‌ای سوخته', 'مسی', 'عسلی']],
    ['زرد', ['زرد', 'زرد روشن', 'لیمویی', 'طلایی', 'خردلی']],
    ['سبز', ['سبز', 'سبز روشن', 'سبز چمنی', 'نعنایی', 'پسته‌ای', 'یشمی', 'زیتونی', 'لجنی', 'سبز ارتشی', 'سبز تیره', 'سبز کله‌غازی']],
    ['آبی', ['فیروزه‌ای', 'آبی آسمانی', 'آبی', 'آبی طوسی', 'نفتی', 'نیلی', 'آبی تیره', 'سرمه‌ای']],
    ['بنفش', ['یاسی', 'بنفش', 'بنفش تیره', 'ارغوانی', 'بادمجانی']],
];
const COLOR_NAME_HEX = new Map(COLOR_NAMES);

// Short #abc codes expand to aabbcc, the only form a colour input accepts.
const hexCode = /^#?(?:([0-9a-f]{6})|([0-9a-f])([0-9a-f])([0-9a-f]))$/i;

function nearestColorName(hex) {
    const m = hexCode.exec((hex || '').trim());
    if (!m) return '';
    const full = (m[1] || (m[2] + m[2] + m[3] + m[3] + m[4] + m[4])).toLowerCase();
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    let best = '', bestDist = Infinity;
    COLOR_NAMES.forEach(([label, anchor]) => {
        const p = anchor.toLowerCase();
        const pr = parseInt(p.slice(1, 3), 16);
        const pg = parseInt(p.slice(3, 5), 16);
        const pb = parseInt(p.slice(5, 7), 16);
        // Weighted RGB: greens weigh most, then reds, then blues — plain RGB
        // distance names muted olives and wines wrong.
        const dr = r - pr, dg = g - pg, db = b - pb;
        const rmean = (r + pr) / 2;
        const dist = (2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db;
        if (dist < bestDist) { bestDist = dist; best = label; }
    });
    return best;
}

function initColorPickers(scope) {
    // The «رنگ» name field next to a «کد رنگ» code field: on the variant page
    // the pair is #color / #color_code, in product-form rows it is
    // variant_color_N / variant_color_code_N.
    // A hand on a «رنگ» name field ends the palette-sync for that field —
    // the same contract as the reorder-default rows. Programmatic fills
    // (palette picks) dispatch a flagged event so they never count as a hand.
    function armColorNameSync(nameField) {
        if (!nameField || nameField.dataset.colorNameReady) return;
        nameField.dataset.colorNameReady = 'true';
        nameField.addEventListener('input', (event) => {
            if (event && event.syntheticFill) return;
            nameField.dataset.touched = '1';
        });
    }
    function findColorNameField(codeField) {
        if (codeField.id === 'color_code') {
            const byId = document.getElementById('color');
            if (byId) { armColorNameSync(byId); return byId; }
        }
        const name = codeField.name || '';
        if (name.includes('color_code')) {
            const want = name.replace('color_code', 'color');
            const holder = codeField.closest('form, .variant-entry-body, .form-row') || document;
            const found = holder.querySelector(`input[name="${want}"]`);
            if (found && found !== codeField) { armColorNameSync(found); return found; }
        }
        return null;
    }
    (scope || document).querySelectorAll('input[data-color-picker]').forEach(field => {
        if (field.dataset.colorPickerReady) return;
        field.dataset.colorPickerReady = 'true';

        const host = field.parentElement || document.body;
        if (getComputedStyle(host).position === 'static') host.style.position = 'relative';

        const wrap = document.createElement('span');
        wrap.className = 'color-pick';
        host.insertBefore(wrap, field);
        wrap.appendChild(field);

        const swatch = document.createElement('button');
        swatch.type = 'button';
        swatch.className = 'color-swatch';
        swatch.setAttribute('aria-label', 'انتخاب رنگ');
        swatch.setAttribute('aria-haspopup', 'true');
        swatch.setAttribute('aria-expanded', 'false');
        wrap.insertBefore(swatch, field);

        const pop = document.createElement('div');
        pop.className = 'color-presets';
        pop.hidden = true;
        pop.setAttribute('role', 'group');
        pop.setAttribute('aria-label', 'رنگ‌های آماده');
        // The name the finger is over, so a dot is never a colour to guess at.
        const nameLine = document.createElement('p');
        nameLine.className = 'color-pick-name';
        nameLine.setAttribute('aria-hidden', 'true');
        pop.appendChild(nameLine);
        function showName(text) { nameLine.textContent = text || ''; }
        function attachName(button, label) {
            button.addEventListener('mouseenter', () => showName(label));
            button.addEventListener('mouseleave', () => showName(''));
            button.addEventListener('focus', () => showName(label));
            button.addEventListener('blur', () => showName(''));
        }
        function makeChip(hex, label) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'color-preset';
            chip.style.background = hex;
            chip.title = label;
            chip.setAttribute('aria-label', label);
            chip.dataset.hex = hex;
            chip.addEventListener('click', () => {
                setFieldColor(hex, label);
                closePop();
                field.focus({ preventScroll: true });
            });
            attachName(chip, label);
            return chip;
        }
        function addFamily(label, entries) {
            const familyLabel = document.createElement('p');
            familyLabel.className = 'color-family-label';
            familyLabel.textContent = label;
            pop.appendChild(familyLabel);
            const row = document.createElement('div');
            row.className = 'color-family-row';
            row.setAttribute('role', 'group');
            row.setAttribute('aria-label', label);
            entries.forEach(([hex, name]) => row.appendChild(makeChip(hex, name)));
            pop.appendChild(row);
        }
        // The quick row keeps the old contract exactly: the eight garment
        // presets, first, one tap each.
        addFamily('پرکاربرد', COLOR_PRESETS.map(([name, hex]) => [hex, name]));
        // Then the whole table under its family: anything the shop sells is one
        // look away, and a pick carries its name into «رنگ» with the hex.
        COLOR_FAMILIES.forEach(([family, names]) => {
            addFamily(family, names.filter(name => COLOR_NAME_HEX.has(name))
                                  .map(name => [COLOR_NAME_HEX.get(name), name]));
        });
        const actions = document.createElement('div');
        actions.className = 'color-actions';
        const clear = document.createElement('button');
        clear.type = 'button';
        clear.className = 'color-preset is-clear';
        clear.textContent = '×';
        clear.title = 'بدون رنگ';
        clear.setAttribute('aria-label', 'بدون رنگ');
        clear.addEventListener('click', () => {
            field.value = '';
            field.dispatchEvent(new Event('change', { bubbles: true }));
            paintSwatch();
            // «بدون رنگ» releases the name field back to following picks.
            const nameField = findColorNameField(field);
            if (nameField) delete nameField.dataset.touched;
            closePop();
        });
        actions.appendChild(clear);
        const custom = document.createElement('button');
        custom.type = 'button';
        custom.className = 'color-custom';
        custom.textContent = 'رنگ دلخواه…';
        custom.addEventListener('click', () => { picker.click(); });
        actions.appendChild(custom);
        pop.appendChild(actions);
        // A panel fixed to the viewport, not a child of the card: the variant
        // row clips its children (`overflow: clip` for the accordion), so the
        // palette must live on the body and be placed by script.
        document.body.appendChild(pop);

        const picker = document.createElement('input');
        picker.type = 'color';
        picker.tabIndex = -1;
        picker.setAttribute('aria-hidden', 'true');
        picker.style.cssText = 'position:absolute;width:1px;height:1px;min-height:0;padding:0;border:0;opacity:0;pointer-events:none;bottom:0;inset-inline-start:0;';
        host.appendChild(picker);

        function paintSwatch() {
            const match = hexCode.exec((field.value || '').trim());
            const full = match && (match[1] || (match[2] + match[2] + match[3] + match[3] + match[4] + match[4]));
            swatch.style.background = full ? '#' + full.toLowerCase() : 'transparent';
            swatch.classList.toggle('is-empty', !full);
            pop.querySelectorAll('.color-preset[data-hex]').forEach(preset => {
                preset.classList.toggle('is-active',
                    !!full && preset.dataset.hex.toLowerCase() === ('#' + full.toLowerCase()));
            });
        }

        function setFieldColor(hex, label) {
            field.value = hex.toUpperCase();
            field.dispatchEvent(new Event('change', { bubbles: true }));
            paintSwatch();
            // A palette pick suggests the Persian name in the sibling «رنگ»
            // field — until a hand on that field ends the sync, the same
            // contract as the reorder-default rows.
            const nameField = findColorNameField(field);
            if (nameField && !nameField.dataset.touched) {
                const suggestion = label || nearestColorName(hex);
                if (suggestion) {
                    nameField.value = suggestion;
                    // Flagged as synthetic: armColorNameSync ignores it, so the
                    // field keeps following later picks until a real keystroke.
                    const fill = new Event('input', { bubbles: true });
                    fill.syntheticFill = true;
                    nameField.dispatchEvent(fill);
                }
            }
        }

        function closePop() {
            pop.hidden = true;
            swatch.setAttribute('aria-expanded', 'false');
        }

        picker.addEventListener('input', () => {
            setFieldColor(picker.value);
        });

        // Anchored to the swatch on the viewport, then clamped to it: the
        // panel floats anywhere but leaves the screen on neither axis. It
        // grows to the roomier side and scrolls inside itself rather than
        // stretching to a page edge.
        function placePop() {
            const rect = swatch.getBoundingClientRect();
            const vw = document.documentElement.clientWidth;
            const vh = document.documentElement.clientHeight;
            const below = vh - rect.bottom - 8;
            const above = rect.top - 8;
            const flipAbove = below < 240 && above > below;
            const room = (flipAbove ? above : below) - 6;
            pop.style.maxHeight = Math.max(180, Math.min(room, Math.round(vh * 0.6))) + 'px';
            const height = pop.offsetHeight;
            pop.style.top = (flipAbove ? Math.max(8, rect.top - height - 6) : rect.bottom + 6) + 'px';
            const width = pop.offsetWidth;
            pop.style.right = Math.max(8, Math.min(vw - rect.right, vw - width - 8)) + 'px';
        }

        swatch.addEventListener('click', event => {
            event.stopPropagation();
            const open = pop.hidden;
            document.querySelectorAll('.color-presets').forEach(other => { other.hidden = true; });
            document.querySelectorAll('.color-swatch').forEach(other => { other.setAttribute('aria-expanded', 'false'); });
            pop.hidden = !open;
            swatch.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) placePop();
        });

        field.addEventListener('input', paintSwatch);
        field.addEventListener('click', () => {
            const match = hexCode.exec((field.value || '').trim());
            const full = match && (match[1] || (match[2] + match[2] + match[3] + match[3] + match[4] + match[4]));
            if (full) picker.value = '#' + full.toLowerCase();
        });
        paintSwatch();
    });
}

document.addEventListener('click', event => {
    if (event.target.closest && (event.target.closest('.color-pick') || event.target.closest('.color-presets'))) return;
    document.querySelectorAll('.color-presets').forEach(pop => { pop.hidden = true; });
    document.querySelectorAll('.color-swatch').forEach(swatch => { swatch.setAttribute('aria-expanded', 'false'); });
});

// The palette is fixed to the viewport while the field scrolls away with the
// page: close it rather than let it hang over whatever scrolled under it. Its
// own scrolling is not a scroll away.
document.addEventListener('scroll', event => {
    if (event.target && event.target.closest && event.target.closest('.color-presets')) return;
    document.querySelectorAll('.color-presets').forEach(pop => { pop.hidden = true; });
    document.querySelectorAll('.color-swatch').forEach(swatch => { swatch.setAttribute('aria-expanded', 'false'); });
}, true);

document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.color-presets').forEach(pop => { pop.hidden = true; });
    document.querySelectorAll('.color-swatch').forEach(swatch => { swatch.setAttribute('aria-expanded', 'false'); });
});

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
