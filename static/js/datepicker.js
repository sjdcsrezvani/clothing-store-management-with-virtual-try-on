/* Persian (Jalali) date picker for every `.persian-date-input` field.
 *
 * Dependency-free: the previous implementation needed jQuery purely to drive
 * this one widget. Presentation lives entirely in style.css (.pdp-*) and is
 * built from theme tokens, so the popup follows whichever theme is active
 * (including dark and high-contrast) instead of hardcoding purple and white.
 *
 * Value contract: reads and writes the same `YYYY/MM/DD` Jalali format that
 * `jalali_str` renders and that the server already parses (parse_jalali_input,
 * parse_check_date, _parse_staff_date, parse_persian_birthday), using Persian
 * digits. Latin digits and `-` separators are accepted when typing.
 *
 * Optional attributes on the field:
 *   data-pdp-pair="#endId"  this field opens a range: it can never be after the
 *                          named field, and that field can never be before this
 *                          one (declared once, enforced both ways)
 *   data-pdp-min="today"    earliest selectable date ("today" or a date)
 *   data-pdp-max="today"    latest selectable date ("today" or a date)
 *
 * Typing four digits anywhere inside the popup walks the calendar straight to
 * that year (1380 or ۱۳۸۰ both work), which is the fast path through a year list
 * that spans a century. An unfinished year is shown in the popup's hint line and
 * dropped after a pause, Backspace takes a digit back, and Escape discards it
 * before falling through to closing the popup.
 *
 * The year list is a rolling window measured from today — 120 years back and 10
 * forward — so it keeps up with the calendar as the years pass instead of being
 * pinned to a fixed range. `FLOOR_YEAR` (1300) stays underneath as an anti-typo
 * floor: it is the bound you actually meet today (it is about 105 years back, so
 * a birth date from the 1920s still selects), and once the rolling window has
 * moved past it the window becomes the only limit.
 */
(function () {
    'use strict';

    var MONTHS = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
                  'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
    var WEEKDAY_INITIALS = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج'];
    var WEEKDAYS = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه'];

    // Day 0 is 1350/01/01 Jalali, which was a Sunday (verified against jdatetime).
    var EPOCH_YEAR = 1350;
    var EPOCH_WEEKDAY_COLUMN = 1; // Saturday = 0 … Friday = 6
    var FLOOR_YEAR = 1300;   // nothing older can be a real date — guard against typo'd years
    var YEARS_BACK = 120;    // covers a lifetime of birthdays, and rolls forward with today
    var YEARS_FORWARD = 10;  // planning date fields never look further ahead than this
    var YEAR_DIGITS = 4;         // a Jalali year is always four digits
    var YEAR_BUFFER_IDLE = 1400; // pause after which a half-typed year is dropped
    var YEAR_HINT = 'سال را تایپ کنید؛ مثال: ۱۳۸۰';
    var SHEET_QUERY = '(max-width: 560px)';
    var DIGITS = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];

    var active = null; // { input, popup, view, focusDay } — one popup at a time
    var yearBuffer = ''; // digits typed towards a year, reset when it commits or goes idle
    var yearBufferTimer = null;

    // ── digits ────────────────────────────────────────────────────────────────

    function toLatinDigits(value) {
        return String(value == null ? '' : value).replace(/[۰-۹]/g, function (digit) {
            return String(DIGITS.indexOf(digit));
        });
    }

    function toPersianDigits(value) {
        return toLatinDigits(value).replace(/[0-9]/g, function (digit) {
            return DIGITS[Number(digit)];
        });
    }

    // ── Jalali calendar ───────────────────────────────────────────────────────
    // Verified against Python's jdatetime for 1390–1444 (no mismatches).

    function isLeapYear(jy) {
        var m = ((jy % 33) + 33) % 33;
        return m === 1 || m === 5 || m === 9 || m === 13 || m === 17 || m === 22 || m === 26 || m === 30;
    }

    function daysInMonth(jy, jm) {
        if (jm <= 6) return 31;
        if (jm <= 11) return 30;
        return isLeapYear(jy) ? 30 : 29;
    }

    /** Days since 1350/01/01; negative for earlier years. */
    function dayNumber(jy, jm, jd) {
        var n = 0, y;
        if (jy >= EPOCH_YEAR) {
            for (y = EPOCH_YEAR; y < jy; y++) n += isLeapYear(y) ? 366 : 365;
        } else {
            for (y = jy; y < EPOCH_YEAR; y++) n -= isLeapYear(y) ? 366 : 365;
        }
        for (var m = 1; m < jm; m++) n += daysInMonth(jy, m);
        return n + jd - 1;
    }

    function fromDayNumber(n) {
        var y = EPOCH_YEAR;
        while (n >= (isLeapYear(y) ? 366 : 365)) {
            n -= isLeapYear(y) ? 366 : 365;
            y++;
        }
        while (n < 0) {
            y--;
            n += isLeapYear(y) ? 366 : 365;
        }
        var m = 1;
        while (n >= daysInMonth(y, m)) {
            n -= daysInMonth(y, m);
            m++;
        }
        return { y: y, m: m, d: n + 1 };
    }

    /** Column of the month's first day, Saturday = 0. */
    function firstColumn(jy, jm) {
        return ((dayNumber(jy, jm, 1) + EPOCH_WEEKDAY_COLUMN) % 7 + 7) % 7;
    }

    function weekdayIndex(jy, jm, jd) {
        return ((dayNumber(jy, jm, jd) + EPOCH_WEEKDAY_COLUMN) % 7 + 7) % 7;
    }

    function gregorianToJalali(gy, gm, gd) {
        var offset = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
        var gy2 = gm > 2 ? gy + 1 : gy;
        var days = 355666 + (365 * gy) + Math.floor((gy2 + 3) / 4)
            - Math.floor((gy2 + 99) / 100) + Math.floor((gy2 + 399) / 400)
            + gd + offset[gm - 1];
        var jy = -1595 + (33 * Math.floor(days / 12053));
        days %= 12053;
        jy += 4 * Math.floor(days / 1461);
        days %= 1461;
        if (days > 365) {
            jy += Math.floor((days - 1) / 365);
            days = (days - 1) % 365;
        }
        if (days < 186) return { y: jy, m: 1 + Math.floor(days / 31), d: 1 + (days % 31) };
        return { y: jy, m: 7 + Math.floor((days - 186) / 30), d: 1 + ((days - 186) % 30) };
    }

    function today() {
        var now = new Date();
        return gregorianToJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
    }

    function isValid(jy, jm, jd) {
        if (!jy || !jm || !jd) return false;
        if (jm < 1 || jm > 12 || jd < 1) return false;
        return jd <= daysInMonth(jy, jm);
    }

    // ── value helpers ─────────────────────────────────────────────────────────

    /**
     * Parse a date written as `YYYY/MM/DD` (Persian or Latin digits, `/`, `-` or
     * `.`). Jalali years never reach 1900, so a bigger year can only be a
     * Gregorian ISO value — the same rule the server applies — and gets
     * converted instead of being read as a Jalali year.
     */
    function parseDate(raw) {
        var cleaned = toLatinDigits(raw).trim().replace(/[.\-\s]+/g, '/');
        var parts = cleaned.split('/');
        if (parts.length !== 3) return null;
        var y = parseInt(parts[0], 10);
        var m = parseInt(parts[1], 10);
        var d = parseInt(parts[2], 10);
        if (y >= 1900) {
            if (m < 1 || m > 12 || d < 1 || d > 31 || y > 2200) return null;
            return gregorianToJalali(y, m, d);
        }
        if (!isValid(y, m, d)) return null;
        return { y: y, m: m, d: d };
    }

    function formatDate(y, m, d) {
        return toPersianDigits(y) + '/' + toPersianDigits(pad(m)) + '/' + toPersianDigits(pad(d));
    }

    function pad(value) {
        return String(value).length < 2 ? '0' + value : String(value);
    }

    function dateLabel(y, m, d) {
        return WEEKDAYS[weekdayIndex(y, m, d)] + ' ' + toPersianDigits(d) + ' ' +
            MONTHS[m - 1] + ' ' + toPersianDigits(y);
    }

    function monthLabel(state) {
        return MONTHS[state.view.m - 1] + ' ' + toPersianDigits(state.view.y);
    }

    // ── limits ────────────────────────────────────────────────────────────────

    function parseLimit(raw) {
        var value = String(raw || '').trim();
        if (!value) return null;
        if (value === 'today') return dayNumber.apply(null, jalaliArgs(today()));
        var parsed = parseDate(value); // Jalali or ISO, same as a field's own value
        return parsed ? dayNumber(parsed.y, parsed.m, parsed.d) : null;
    }

    function jalaliArgs(date) {
        return [date.y, date.m, date.d];
    }

    /** The field this one points at with data-pdp-pair (its range partner). */
    function partnerFor(input) {
        var selector = input.dataset.pdpPair;
        if (!selector) return null;
        var partner = query(selector);
        return partner && partner !== input ? partner : null;
    }

    /** The field that declares *this* one as its partner (this is the range end). */
    function starterFor(input) {
        var selector = selectorOf(input);
        if (!selector) return null;
        var starter = query('[data-pdp-pair="' + selector + '"]');
        return starter && starter !== input ? starter : null;
    }

    function selectorOf(element) {
        return element.id ? '#' + element.id : null;
    }

    function query(selector) {
        try {
            return document.querySelector(selector);
        } catch (error) {
            return null; // malformed selector in markup must not break the page
        }
    }

    function dayOf(input) {
        var parsed = input && parseDate(input.value);
        return parsed ? dayNumber(parsed.y, parsed.m, parsed.d) : null;
    }

    /** Only what the markup declared, so the built-in floor can't widen the calendar. */
    function declaredLimits(input) {
        return { min: parseLimit(input.dataset.pdpMin), max: parseLimit(input.dataset.pdpMax) };
    }

    /** { min, max } as day numbers for this field, range pairs included. */
    function limitsFor(input) {
        var declared = declaredLimits(input);
        var min = declared.min;
        var max = declared.max;
        if (min == null) min = dayNumber(FLOOR_YEAR, 1, 1);

        // Declaring a pair makes this field the range start, so it may not pass
        // its partner. The reverse (partner may not precede this one) is derived
        // automatically, so a pair is only ever declared once in the markup.
        var partnerDay = dayOf(partnerFor(input));
        if (partnerDay != null && (max == null || partnerDay < max)) max = partnerDay;

        var starterDay = dayOf(starterFor(input));
        if (starterDay != null && starterDay > min) min = starterDay;

        return { min: min, max: max };
    }

    function withinRange(y, m, d) {
        if (!active) return true;
        var n = dayNumber(y, m, d);
        var limits = limitsFor(active.input);
        if (limits.min != null && n < limits.min) return false;
        if (limits.max != null && n > limits.max) return false;
        return true;
    }

    // ── rendering ─────────────────────────────────────────────────────────────

    function buildPopup() {
        var popup = document.createElement('div');
        popup.className = 'pdp-popup';
        popup.setAttribute('role', 'dialog');
        popup.setAttribute('aria-modal', 'false');
        popup.setAttribute('aria-label', 'انتخاب تاریخ');
        popup.innerHTML =
            '<div class="pdp-head">' +
            '  <button type="button" class="pdp-nav pdp-prev" aria-label="ماه قبل">‹</button>' +
            '  <div class="pdp-selects">' +
            '    <select class="pdp-month" aria-label="ماه"></select>' +
            '    <select class="pdp-year" aria-label="سال"></select>' +
            '  </div>' +
            '  <button type="button" class="pdp-nav pdp-next" aria-label="ماه بعد">›</button>' +
            '</div>' +
            '<div class="pdp-weekdays" aria-hidden="true"></div>' +
            '<div class="pdp-grid" role="grid"></div>' +
            '<div class="pdp-foot">' +
            '  <button type="button" class="pdp-btn pdp-today">امروز</button>' +
            '  <button type="button" class="pdp-btn pdp-clear">پاک کردن</button>' +
            '</div>' +
            '<p class="pdp-hint"></p>' +
            '<p class="pdp-status sr-only" role="status" aria-live="polite"></p>';
        WEEKDAY_INITIALS.forEach(function (initial) {
            var cell = document.createElement('span');
            cell.textContent = initial;
            popup.querySelector('.pdp-weekdays').appendChild(cell);
        });
        return popup;
    }

    function render() {
        if (!active) return;
        var popup = active.popup;
        var view = active.view;
        var selected = parseDate(active.input.value);
        var selectedDay = selected ? dayNumber(selected.y, selected.m, selected.d) : null;
        var todayDate = today();
        var todayDay = dayNumber(todayDate.y, todayDate.m, todayDate.d);
        var limits = limitsFor(active.input);

        var window_ = yearWindow(active.input, view.y);
        var monthSelect = popup.querySelector('.pdp-month');
        var yearSelect = popup.querySelector('.pdp-year');
        fillSelect(monthSelect, MONTHS.map(function (name, index) { return [index + 1, name]; }), view.m);
        fillSelect(yearSelect, yearOptions(active.input, view.y), view.y);
        popup.querySelector('.pdp-prev').disabled = view.y <= window_.startYear && view.m <= 1;
        popup.querySelector('.pdp-next').disabled = view.y >= window_.endYear && view.m >= 12;

        var grid = popup.querySelector('.pdp-grid');
        grid.innerHTML = '';
        var startColumn = firstColumn(view.y, view.m);
        var monthLength = daysInMonth(view.y, view.m);
        var cells = Math.ceil((startColumn + monthLength) / 7) * 7;

        for (var index = 0; index < cells; index++) {
            var offset = index - startColumn;
            var date = offset < 0 || offset >= monthLength
                ? fromDayNumber(dayNumber(view.y, view.m, 1) + offset)
                : { y: view.y, m: view.m, d: offset + 1 };
            var day = dayNumber(date.y, date.m, date.d);
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'pdp-day';
            button.dataset.dayNumber = String(day);
            button.textContent = toPersianDigits(date.d);
            button.setAttribute('aria-label', dateLabel(date.y, date.m, date.d));
            if (date.m !== view.m) button.classList.add('is-outside');
            if (day === todayDay) {
                button.classList.add('is-today');
                button.setAttribute('aria-current', 'date');
            }
            if (selectedDay === day) {
                button.classList.add('is-selected');
                button.setAttribute('aria-selected', 'true');
            }
            if ((limits.min != null && day < limits.min) || (limits.max != null && day > limits.max)) {
                button.disabled = true;
                button.setAttribute('aria-disabled', 'true');
                if (date.m !== view.m) button.classList.add('is-outside');
            }
            grid.appendChild(button);
        }

        var status = popup.querySelector('.pdp-status');
        if (status) status.textContent = monthLabel(active);
    }

    function fillSelect(select, options, current) {
        select.innerHTML = '';
        options.forEach(function (option) {
            var element = document.createElement('option');
            element.value = String(option[0]);
            element.textContent = option[1];
            if (option[0] === current) element.selected = true;
            select.appendChild(element);
        });
    }

    /**
     * Years the calendar may travel through: a rolling window around today,
     * narrowed by anything the markup declared and always wide enough to show
     * the year currently on screen (or the field's own year).
     *
     * Both ends are measured from `today()` on every render, so nothing here is
     * pinned to a fixed year: seven years from now the window has moved seven
     * years forward, and the floor stops the earliest year drifting below the
     * anti-typo limit.
     */
    function yearWindow(input, viewYear) {
        var todayYear = today().y;
        var declared = declaredLimits(input);
        var startYear = Math.max(FLOOR_YEAR, todayYear - YEARS_BACK);
        var endYear = todayYear + YEARS_FORWARD;
        if (declared.min != null) startYear = Math.max(startYear, fromDayNumber(declared.min).y);
        if (declared.max != null) endYear = Math.min(endYear, fromDayNumber(declared.max).y);
        if (viewYear) {
            startYear = Math.min(startYear, viewYear);
            endYear = Math.max(endYear, viewYear);
        }
        return { startYear: startYear, endYear: endYear };
    }

    function yearOptions(input, viewYear) {
        var window_ = yearWindow(input, viewYear);
        var options = [];
        for (var year = window_.startYear; year <= window_.endYear; year++) {
            options.push([year, toPersianDigits(year)]);
        }
        return options;
    }

    // ── positioning ───────────────────────────────────────────────────────────

    function isSheet() {
        return window.matchMedia(SHEET_QUERY).matches;
    }

    function position() {
        if (!active) return;
        var popup = active.popup;
        var input = active.input;
        popup.classList.toggle('is-sheet', isSheet());
        if (isSheet()) {
            popup.style.left = '';
            popup.style.top = '';
            return;
        }
        var rect = input.getBoundingClientRect();
        var width = popup.offsetWidth;
        var height = popup.offsetHeight;
        var viewportWidth = document.documentElement.clientWidth;
        var viewportHeight = document.documentElement.clientHeight;
        var above = rect.bottom + height + 12 > viewportHeight && rect.top > viewportHeight - rect.bottom;
        var top = above ? rect.top - height - 8 : rect.bottom + 8;
        var left = rect.right - width; // RTL: keep the popup's right edge on the field
        if (left < 8) left = 8;
        if (left + width > viewportWidth - 8) left = Math.max(8, viewportWidth - width - 8);
        if (top < 8) top = 8;
        if (top + height > viewportHeight - 8) top = Math.max(8, viewportHeight - height - 8);
        popup.classList.toggle('is-above', above);
        popup.style.left = Math.round(left) + 'px';
        popup.style.top = Math.round(top) + 'px';
    }

    var repositionFrame = null;

    function onViewportChange() {
        if (!active || repositionFrame) return;
        // Scroll fires in bursts; measure once per frame instead of per event.
        repositionFrame = window.requestAnimationFrame(function () {
            repositionFrame = null;
            if (active) position();
        });
    }

    // ── open / close ──────────────────────────────────────────────────────────

    function open(input) {
        close();
        var popup = buildPopup();
        document.body.appendChild(popup);
        var selected = parseDate(input.value) || today();
        active = {
            input: input,
            popup: popup,
            view: { y: selected.y, m: selected.m },
            focusDay: dayNumber(selected.y, selected.m, selected.d),
        };
        setHint('');
        render();
        position();
        input.setAttribute('aria-expanded', 'true');
        bindPopup();
        focusDayButton();
    }

    function close() {
        if (!active) return;
        active.popup.remove();
        active.input.setAttribute('aria-expanded', 'false');
        active = null;
        flushYearBuffer();
    }

    function focusDayButton() {
        if (!active) return;
        var target = active.popup.querySelector('[data-day-number="' + active.focusDay + '"]');
        if (target && !target.disabled) {
            target.focus();
            return;
        }
        var fallback = active.popup.querySelector('.pdp-day:not(:disabled)');
        if (fallback) fallback.focus();
    }

    function moveFocus(days) {
        if (!active) return;
        var next = active.focusDay + days;
        var date = fromDayNumber(next);
        if (!withinRange(date.y, date.m, date.d)) return;
        active.focusDay = next;
        if (date.y !== active.view.y || date.m !== active.view.m) {
            active.view = { y: date.y, m: date.m };
            render();
        }
        focusDayButton();
    }

    function changeMonth(delta) {
        if (!active) return;
        var month = active.view.m + delta;
        var year = active.view.y;
        if (month < 1) { month = 12; year--; }
        if (month > 12) { month = 1; year++; }
        var window_ = yearWindow(active.input, active.view.y);
        if (year < window_.startYear || year > window_.endYear) return;
        active.view = { y: year, m: month };
        render();
        focusDayButton();
    }

    function pick(y, m, d) {
        if (!active) return;
        var input = active.input;
        setValue(input, y, m, d);
        close();
        input.focus();
    }

    /** Mark a field the picker believes is wrong (the server still has the last word). */
    function markInvalid(input, invalid) {
        input.classList.toggle('is-invalid', !!invalid);
        if (invalid) input.setAttribute('aria-invalid', 'true');
        else input.removeAttribute('aria-invalid');
    }

    function setValue(input, y, m, d) {
        input.value = formatDate(y, m, d);
        markInvalid(input, false);
        // Pages and native validation only react to real events.
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // ── type-ahead: four digits go straight to that year ─────────────────────

    function setHint(text) {
        if (!active) return;
        var hint = active.popup.querySelector('.pdp-hint');
        if (hint) hint.textContent = text || YEAR_HINT;
    }

    function announce(text) {
        if (!active) return;
        var status = active.popup.querySelector('.pdp-status');
        if (status) status.textContent = text;
    }

    function flushYearBuffer() {
        yearBuffer = '';
        if (yearBufferTimer) {
            window.clearTimeout(yearBufferTimer);
            yearBufferTimer = null;
        }
    }

    function clearYearBuffer() {
        flushYearBuffer();
        setHint('');
    }

    /** Drop a half-typed year once the typing stops, so decades don't merge. */
    function expireYearBuffer() {
        if (yearBufferTimer) window.clearTimeout(yearBufferTimer);
        yearBufferTimer = window.setTimeout(function () {
            yearBufferTimer = null;
            yearBuffer = '';
            setHint('');
        }, YEAR_BUFFER_IDLE);
    }

    /** Keep a typed year inside the range the calendar can actually show. */
    function clampYear(year) {
        if (!active) return year;
        var window_ = yearWindow(active.input, active.view.y);
        if (year < window_.startYear) return window_.startYear;
        if (year > window_.endYear) return window_.endYear;
        return year;
    }

    /** Move the calendar to `year`, keeping the month and day of month in view. */
    function jumpToYear(year) {
        if (!active) return;
        var month = active.view.m;
        var day = Math.min(fromDayNumber(active.focusDay).d, daysInMonth(year, month));
        active.view = { y: year, m: month };
        active.focusDay = dayNumber(year, month, day);
        render();
        focusDayButton();
    }

    function showYearBuffer() {
        setHint('سال: ' + toPersianDigits(yearBuffer));
        announce('سال ' + toPersianDigits(yearBuffer));
        expireYearBuffer();
    }

    /** A digit towards a year; the fourth one commits and moves the calendar. */
    function pushYearDigit(digit) {
        if (!active) return;
        if (yearBuffer.length >= YEAR_DIGITS) yearBuffer = '';
        yearBuffer += digit;
        if (yearBuffer.length < YEAR_DIGITS) {
            showYearBuffer();
            return;
        }
        var year = clampYear(Number(yearBuffer));
        flushYearBuffer();
        jumpToYear(year);
        setHint('سال ' + toPersianDigits(year));
        expireYearBuffer();
    }

    function popYearDigit() {
        if (!active || !yearBuffer) return false;
        yearBuffer = yearBuffer.slice(0, -1);
        showYearBuffer();
        return true;
    }

    function isDigitKey(event) {
        if (event.ctrlKey || event.metaKey || event.altKey) return false;
        if (event.key.length !== 1) return false;
        return /^[0-9]$/.test(toLatinDigits(event.key));
    }

    // ── popup interactions ────────────────────────────────────────────────────

    function bindPopup() {
        var popup = active.popup;
        popup.addEventListener('click', function (event) {
            var target = event.target;
            if (target.classList.contains('pdp-day') && !target.disabled) {
                var date = fromDayNumber(Number(target.dataset.dayNumber));
                pick(date.y, date.m, date.d);
                return;
            }
            if (target.classList.contains('pdp-today')) {
                var now = today();
                if (withinRange(now.y, now.m, now.d)) pick(now.y, now.m, now.d);
                return;
            }
            if (target.classList.contains('pdp-clear')) {
                var input = active.input;
                input.value = '';
                markInvalid(input, false);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                close();
                input.focus();
                return;
            }
            if (target.classList.contains('pdp-prev')) changeMonth(-1);
            if (target.classList.contains('pdp-next')) changeMonth(1);
        });

        popup.querySelector('.pdp-month').addEventListener('change', function () {
            active.view = { y: active.view.y, m: Number(this.value) };
            render();
            focusDayButton();
        });
        popup.querySelector('.pdp-year').addEventListener('change', function () {
            active.view = { y: Number(this.value), m: active.view.m };
            render();
            focusDayButton();
        });

        popup.addEventListener('keydown', onPopupKeydown);
    }

    function onPopupKeydown(event) {
        if (!active) return;
        switch (event.key) {
            case 'ArrowLeft': event.preventDefault(); moveFocus(-1); return;
            case 'ArrowRight': event.preventDefault(); moveFocus(1); return;
            case 'ArrowUp': event.preventDefault(); moveFocus(-7); return;
            case 'ArrowDown': event.preventDefault(); moveFocus(7); return;
            case 'PageUp': event.preventDefault(); changeMonth(event.shiftKey ? -12 : -1); return;
            case 'PageDown': event.preventDefault(); changeMonth(event.shiftKey ? 12 : 1); return;
            case 'Home': event.preventDefault(); moveFocus(-weekdayIndex.apply(null, jalaliArgs(fromDayNumber(active.focusDay)))); return;
            case 'End': event.preventDefault(); moveFocus(6 - weekdayIndex.apply(null, jalaliArgs(fromDayNumber(active.focusDay)))); return;
            case 'Enter':
            case ' ':
                if (event.target.classList.contains('pdp-day') && !event.target.disabled) {
                    var date = fromDayNumber(Number(event.target.dataset.dayNumber));
                    event.preventDefault();
                    pick(date.y, date.m, date.d);
                }
                return;
            case 'Escape':
                event.preventDefault();
                // A half-typed year is discarded first; a second Escape closes.
                if (yearBuffer) {
                    clearYearBuffer();
                    announce('');
                    return;
                }
                var input = active.input;
                close();
                input.focus();
                return;
            case 'Backspace':
                if (popYearDigit()) event.preventDefault();
                return;
            case 'Tab':
                trapFocus(event);
                return;
            default:
                if (isDigitKey(event)) {
                    event.preventDefault();
                    pushYearDigit(toLatinDigits(event.key));
                }
                return;
        }
    }

    function trapFocus(event) {
        var focusable = active.popup.querySelectorAll(
            'button:not(:disabled), select, [href], input, [tabindex]:not([tabindex="-1"])'
        );
        if (!focusable.length) return;
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    // ── field binding ─────────────────────────────────────────────────────────

    function bindField(input) {
        if (input.dataset.pdpReady === 'true') return;
        input.dataset.pdpReady = 'true';
        input.setAttribute('aria-haspopup', 'dialog');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('autocomplete', 'off');
        input.setAttribute('spellcheck', 'false');
        input.removeAttribute('readonly');

        var typed = false;

        input.addEventListener('click', function (event) {
            event.stopPropagation();
            typed = false;
            if (active && active.input === input) close();
            else open(input);
        });

        input.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowDown' || (event.key === 'Enter' && !active)) {
                event.preventDefault();
                open(input);
            } else if (event.key === 'Escape' && active && active.input === input) {
                event.preventDefault();
                close();
            } else if (event.key.length === 1 || event.key === 'Backspace' || event.key === 'Delete') {
                typed = true;
                if (active && active.input === input) close();
            }
        });

        input.addEventListener('input', function () {
            // Keep the field to digits and separators so a paste can't smuggle junk in.
            var cleaned = toLatinDigits(input.value).replace(/[^0-9/\-]/g, '');
            if (cleaned.length > 10) cleaned = cleaned.slice(0, 10);
            if (toLatinDigits(input.value) !== cleaned) {
                var caret = input.selectionStart;
                input.value = toPersianDigits(cleaned);
                try { input.setSelectionRange(caret, caret); } catch (error) { /* not focusable yet */ }
            }
        });

        input.addEventListener('blur', function () {
            if (!input.value.trim()) {
                markInvalid(input, false);
                return;
            }
            var parsed = parseDate(input.value);
            if (!parsed) {
                // Leave the text for the server, but say it doesn't parse.
                markInvalid(input, typed);
                return;
            }
            input.value = formatDate(parsed.y, parsed.m, parsed.d);
            // Typing must obey the same limits the calendar enforces, otherwise
            // a ceiling could be bypassed by hand. The value is kept (never
            // rewritten) so the server decides what to do with it.
            markInvalid(input, !withinLimits(input, parsed));
        });

        function withinLimits(field, date) {
            var limits = limitsFor(field);
            var day = dayNumber(date.y, date.m, date.d);
            if (limits.min != null && day < limits.min) return false;
            if (limits.max != null && day > limits.max) return false;
            return true;
        }
    }

    function init(root) {
        var scope = root || document;
        var fields = scope.querySelectorAll ? scope.querySelectorAll('.persian-date-input') : [];
        Array.prototype.forEach.call(fields, bindField);
    }

    // Single shared listeners instead of one per field.
    document.addEventListener('pointerdown', function (event) {
        if (!active) return;
        if (active.popup.contains(event.target) || active.input === event.target) return;
        close();
    }, true);

    window.addEventListener('resize', onViewportChange);
    window.addEventListener('scroll', onViewportChange, true);

    // Fields added after load (e.g. by the purchases item editor) work too.
    document.addEventListener('focusin', function (event) {
        var field = event.target;
        if (field && field.classList && field.classList.contains('persian-date-input')) bindField(field);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { init(); });
    } else {
        init();
    }

    window.PersianDatePicker = {
        init: init,
        open: open,
        close: close,
        parse: parseDate,
        format: formatDate,
        toPersianDigits: toPersianDigits,
        toLatinDigits: toLatinDigits,
    };
})();
