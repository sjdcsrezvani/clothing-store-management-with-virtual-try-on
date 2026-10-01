/* ============================================================
   Raykid Store — ECharts bridge (Phase A1)
   One place where the analytics page meets the vendored engine
   (static/vendor/echarts.min.js, offline by construction): theme tones
   read from the page, Vazirmatn type, Persian-digit figures, RTL axes
   (value axis right, time flowing right-to-left like the page), paper
   colours for print, and entrance motion that dies under reduced-motion.
   Charts register here so resize and before/after-print reach them all.
   ============================================================ */
(function () {
    'use strict';

    var FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';

    function faDigits(text) {
        return String(text).replace(/[0-9]/g, function (d) { return FA_DIGITS[+d]; });
    }

    function grouped(value) {
        var n = Math.round(Number(value) || 0);
        var sign = n < 0 ? '-' : '';
        var grouped = Math.abs(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return faDigits(sign + grouped);
    }

    function compact(value) {
        var n = Number(value) || 0;
        var abs = Math.abs(n);
        if (abs >= 1000000000) return faDigits((n / 1000000000).toString()) + ' م';
        if (abs >= 1000000) return faDigits((n / 1000000).toString()) + ' م';
        if (abs >= 1000) return faDigits((n / 1000).toString()) + ' ه';
        return grouped(n);
    }

    function readToken(name) {
        var value = getComputedStyle(document.documentElement).getPropertyValue(name);
        return value ? value.trim() : '';
    }

    function tones(paper) {
        if (!paper) {
            return {
                candy: readToken('--candy'), sky: readToken('--sky'),
                sunshine: readToken('--sunshine'), mint: readToken('--mint'),
                lavender: readToken('--lavender'), persimmon: readToken('--persimmon'),
                ink: readToken('--ink'), inkSoft: readToken('--ink-soft'),
                rule: readToken('--rule'),
            };
        }
        // Paper knows no theme: every colour derives from the paper pair the
        // palette declares, never from a literal — bars keep their hues so
        // series stay apart on a colour printer, text and rules go ink.
        var paperC = toChannels(readToken('--paper'));
        var inkC = toChannels(readToken('--paper-ink'));
        return {
            candy: readToken('--candy'), sky: readToken('--sky'),
            sunshine: readToken('--sunshine'), mint: readToken('--mint'),
            lavender: readToken('--lavender'), persimmon: readToken('--persimmon'),
            ink: toRgb(inkC),
            inkSoft: toRgb(mixChannels(paperC, inkC, 0.6)),
            rule: toRgb(mixChannels(paperC, inkC, 0.18)),
            paper: toRgb(paperC),
        };
    }

    function toChannels(value) {
        // Hex or rgb()/rgba() — the palette's tokens arrive as either.
        var text = String(value || '').trim();
        if (text.charAt(0) === '#') {
            var hex = text.slice(1);
            if (hex.length === 3) {
                hex = hex.split('').map(function (c) { return c + c; }).join('');
            }
            return [0, 2, 4].map(function (i) { return parseInt(hex.slice(i, i + 2), 16); });
        }
        return text.replace(/^rgba?\(|\)$/g, '').split(',')
            .slice(0, 3).map(function (part) { return parseFloat(part); });
    }

    function toRgb(channels) {
        return 'rgb(' + channels.map(Math.round).join(', ') + ')';
    }

    function mixChannels(first, second, amount) {
        // `first (1-amount)` over `second amount`, in sRGB channels.
        return [0, 1, 2].map(function (i) {
            return Math.round(first[i] * (1 - amount) + second[i] * amount);
        });
    }

    function reducedMotion() {
        return window.matchMedia &&
            window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    var registry = [];

    function textStyle(paper) {
        var t = tones(paper);
        return { color: t.ink, fontFamily: 'Vazirmatn, sans-serif', fontSize: 11 };
    }

    // Shared skeleton: bottom legend, axis-triggered tooltip, fast entrance
    // that never plays for reduced-motion readers.
    function base(paper) {
        var t = tones(paper);
        return {
            textStyle: textStyle(paper),
            tooltip: {
                trigger: 'axis',
                confine: true,
                backgroundColor: paper ? tones(paper).paper : undefined,
                borderColor: t.rule,
                textStyle: textStyle(paper),
            },
            legend: {
                bottom: 0,
                textStyle: textStyle(paper),
            },
            animationDuration: reducedMotion() ? 0 : 300,
            animationEasing: 'cubicOut',
        };
    }

    // Right-to-left axes: the value axis stands right, categories run
    // right-to-left, and ticks speak Persian groupings.
    function categoryAxis(data, paper) {
        var t = tones(paper);
        return {
            type: 'category',
            data: data,
            inverse: true,
            axisLine: { lineStyle: { color: t.rule } },
            axisTick: { lineStyle: { color: t.rule } },
            axisLabel: Object.assign({ interval: 'auto' }, textStyle(paper)),
        };
    }

    function valueAxis(paper, money) {
        var t = tones(paper);
        return {
            type: 'value',
            position: 'right',
            splitLine: { lineStyle: { color: t.rule } },
            axisLabel: Object.assign({
                formatter: function (v) { return money ? compact(v) : grouped(v); },
            }, textStyle(paper)),
        };
    }

    // Create, track, and keep responsive. `build` receives the paper flag
    // and returns the full option, so print swaps only colours, never data.
    function create(el, build) {
        if (!el || !window.echarts) return null;
        var chart = window.echarts.init(el);
        chart.setOption(build(false));
        registry.push({ chart: chart, build: build, el: el });
        return chart;
    }

    function repaint(paper) {
        registry.forEach(function (entry) {
            if (document.contains(entry.el)) entry.chart.setOption(entry.build(paper), true);
        });
    }

    function resizeAll() {
        registry.forEach(function (entry) {
            if (document.contains(entry.el)) entry.chart.resize();
        });
    }

    if (typeof window.addEventListener === 'function') {
        window.addEventListener('resize', resizeAll);
        window.addEventListener('beforeprint', function () { repaint(true); });
        window.addEventListener('afterprint', function () { repaint(false); });
    }

    // Click-through: a mark opens the list behind it. The builder decides
    // the destination from the clicked point; nothing happens without one.
    function link(chart, fn) {
        if (!chart || !chart.on) return;
        chart.on('click', function (point) {
            var url = null;
            try { url = fn(point); } catch (error) { url = null; }
            if (url) window.location.href = url;
        });
    }

    window.AnalyticsCharts = {
        tones: tones,
        grouped: grouped,
        compact: compact,
        money: function (v) { return grouped(v) + ' تومان'; },
        percent: function (v) { return faDigits(String(v)) + '٪'; },
        base: base,
        categoryAxis: categoryAxis,
        valueAxis: valueAxis,
        create: create,
        link: link,
    };
})();
