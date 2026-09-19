(function () {
    'use strict';

    // Every colour this renderer paints is read from the page rather than written
    // here, so a chart follows the active theme — light, dark and high-contrast
    // alike — instead of staying the palette the app happened to ship with.
    function readToken(name) {
        var value = getComputedStyle(document.documentElement).getPropertyValue(name);
        return value ? value.trim() : '';
    }

    function themeTones() {
        var tones = {
            candy: readToken('--candy'),
            sky: readToken('--sky'),
            sunshine: readToken('--sunshine'),
            mint: readToken('--mint'),
            lavender: readToken('--lavender'),
            persimmon: readToken('--persimmon'),
            rule: readToken('--rule'),
            ink: readToken('--ink'),
            inkSoft: readToken('--ink-soft'),
        };
        tones.palette = [tones.candy, tones.sky, tones.sunshine, tones.mint, tones.lavender, tones.persimmon];
        return tones;
    }

    // What the charts are painted in when the page is on paper. The screen's
    // palette follows the theme, but paper does not: printing a dark palette
    // as-is would spend a cartridge on a black background. Every hue the theme
    // owns is still named — laid onto white as a tint of itself — so a Midnight
    // chart keeps its identity on paper instead of becoming the default
    // palette's screenshot.
    var printTones = null;

    function toChannels(value) {
        // Hex or rgb()/rgba() — the palette's tokens arrive as either.
        var text = String(value || '').trim();
        if (text.charAt(0) === '#') {
            var hex = text.slice(1);
            if (hex.length === 3) hex = hex.split('').map(function (c) { return c + c; }).join('');
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

    function paperTones() {
        if (printTones) return printTones;
        // The paper pair is a declared token of every palette, like --ink:
        // no fallback here, so a missing token fails loudly instead of
        // printing a page that pretends a colour it never had.
        var paper = toChannels(readToken('--paper'));
        var ink = toChannels(readToken('--paper-ink'));
        var screen = themeTones();
        // Each accent as a tint of itself over the paper — colour enough to
        // tell the series apart, light enough to be kind to a printer.
        function tint(hex) {
            return toRgb(mixChannels(paper, toChannels(hex), 0.45));
        }
        printTones = {
            candy: tint(screen.candy),
            sky: tint(screen.sky),
            sunshine: tint(screen.sunshine),
            mint: tint(screen.mint),
            lavender: tint(screen.lavender),
            persimmon: tint(screen.persimmon),
            // The neutrals are mixed from the paper pair itself: a rule is a
            // quiet grey, the soft ink a readable one.
            rule: toRgb(mixChannels(paper, ink, 0.18)),
            ink: toRgb(ink),
            inkSoft: toRgb(mixChannels(paper, ink, 0.6)),
        };
        printTones.palette = [printTones.candy, printTones.sky, printTones.sunshine,
                              printTones.mint, printTones.lavender, printTones.persimmon];
        printTones._paper = paper;
        return printTones;
    }

    // A colour a chart's markup stated — the page passes its palette hexes
    // straight through — laid over paper when printing, so a Midnight bar and
    // a daylight bar print as the same kind of mark instead of the screen's
    // saturation. Unparseable colours come through untouched.
    function mapColour(colour) {
        if (!paperMode) return colour;
        var channels = toChannels(colour);
        for (var i = 0; i < 3; i++) {
            if (isNaN(channels[i])) return colour;
        }
        return toRgb(mixChannels(paperTones()._paper, channels, 0.45));
    }

    window.themeTones = themeTones;

    // A translucent fill for the area under a line: the canvas needs a real colour
    // string, and a token can be hex or the rgb() a computed style hands back.
    function tintColour(colour, alpha) {
        var hex = String(colour || '').replace(/^#/, '');
        if (hex.length === 3 || hex.length === 6) {
            if (hex.length === 3) {
                hex = hex.split('').map(function (character) { return character + character; }).join('');
            }
            var channels = [0, 2, 4].map(function (index) { return parseInt(hex.slice(index, index + 2), 16); });
            return 'rgba(' + channels.join(', ') + ', ' + alpha + ')';
        }
        return 'rgba(' + String(colour || '').replace(/^rgba?\(|\)$/g, '') + ', ' + alpha + ')';
    }

    window.tintColour = tintColour;

    // ===== How a number reads on the canvas ==================================
    // A chart that says what it shows only through its colours says nothing on a
    // printout and nothing to an owner who cannot separate the accents, so every
    // mark carries the number behind it. This is the single formatter both the
    // renderer and the page use, so an axis tick and a bar's value can never
    // disagree about the same number.

    function grouped(number) {
        return Math.round(number).toLocaleString('en-US');
    }

    function trimmed(number) {
        return String(Math.round(number * 10) / 10);
    }

    function valueText(value, format) {
        var number = Number(value);
        if (!Number.isFinite(number)) return '';
        if (format === 'percent') return trimmed(number) + '٪';
        // Millions on their own: twelve months of revenue does not fit over a bar
        // as seven digits, and «12.5م» is how the shop says it out loud.
        if (format === 'money' && Math.abs(number) >= 1000000) return trimmed(number / 1000000) + 'م';
        return grouped(number);
    }

    window.chartValueText = valueText;

    function formatFor(config) {
        return (config && config.options && config.options.valueFormat) || 'count';
    }

    function canvasFor(target) {
        return target && target.canvas ? target.canvas : target;
    }

    function setupCanvas(canvas) {
        if (!canvas || !canvas.getContext) return null;
        var parentWidth = canvas.parentElement ? canvas.parentElement.clientWidth : 0;
        var width = Math.max(parentWidth || canvas.clientWidth || 320, 260);
        var height = Math.max(Math.min(canvas.parentElement && canvas.parentElement.clientHeight || 240, 280), 200);
        var ratio = window.devicePixelRatio || 1;
        canvas.width = width * ratio;
        canvas.height = height * ratio;
        canvas.style.width = '100%';
        canvas.style.height = height + 'px';
        var context = canvas.getContext('2d');
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.clearRect(0, 0, width, height);
        context.direction = 'rtl';
        context.font = '12px sans-serif';
        return { context: context, width: width, height: height };
    }

    function valuesFor(config) {
        var datasets = config.data && config.data.datasets || [];
        var values = [];
        datasets.forEach(function (dataset) {
            (dataset.data || []).forEach(function (value) {
                var number = Number(value);
                if (Number.isFinite(number)) values.push(number);
            });
        });
        return values;
    }

    function rangeFor(values) {
        var min = Math.min.apply(Math, [0].concat(values));
        var max = Math.max.apply(Math, [0].concat(values));
        if (min === max) max = min + 1;
        return { min: min, max: max };
    }

    function textWidth(context, text) {
        context.font = '11px sans-serif';
        return context.measureText(String(text)).width;
    }

    function fits(context, text, room) {
        return textWidth(context, text) + 6 <= room;
    }

    // Which of a series' numbers are painted when its marks are too close together
    // to carry all of them: whatever fits, plus the highest and the lowest of the
    // series — the two a reader is looking for — and, for a line, the latest.
    //
    // A mark worth nothing is never labelled: a zero bar or a month with no trade
    // says itself by not being there, and a row of «0»s down an axis is noise on
    // top of the caption that already says the period was quiet.
    function keepsFor(values, room, context, format, alsoKeep) {
        var keep = {};
        var items = [];
        (values || []).forEach(function (raw, index) {
            var value = Number(raw);
            if (!Number.isFinite(value)) return;
            items.push({ index: index, value: value, text: valueText(value, format) });
        });
        if (!items.some(function (item) { return item.value !== 0; })) return keep;
        items.forEach(function (item) {
            if (item.value !== 0 && fits(context, item.text, room)) keep[item.index] = true;
        });
        if (items.length) {
            var highest = items.reduce(function (a, b) { return b.value > a.value ? b : a; });
            var lowest = items.reduce(function (a, b) { return b.value < a.value ? b : a; });
            if (highest.value !== 0) keep[highest.index] = true;
            if (lowest.value < 0) keep[lowest.index] = true;
            (alsoKeep || []).forEach(function (index) { keep[index] = true; });
        }
        return keep;
    }

    // ===== Axes and labels ===================================================

    function drawAxes(context, chart, range, padding) {
        padding = padding || {};
        var left = padding.left || 48;
        var right = padding.right || 16;
        var top = 18;
        // Room for the category names, and — under them — the legend, which used
        // to be drawn on top of the names it sat below.
        var bottom = padding.bottom || 42;
        var plotWidth = chart.width - left - right;
        var plotHeight = chart.height - top - bottom;
        var zero = top + (range.max / (range.max - range.min)) * plotHeight;
        var tones = activeTones();
        context.strokeStyle = tones.rule;
        context.lineWidth = 1;
        if (typeof context.recordStrokeStyle === 'function') context.recordStrokeStyle(tones.rule);
        context.beginPath();
        context.moveTo(left, top);
        context.lineTo(left, chart.height - bottom);
        context.lineTo(chart.width - right, chart.height - bottom);
        context.stroke();
        if (zero >= top && zero <= chart.height - bottom) {
            context.strokeStyle = tones.inkSoft;
            context.beginPath();
            context.moveTo(left, zero);
            context.lineTo(chart.width - right, zero);
            context.stroke();
        }
        return { left: left, right: right, top: top, bottom: bottom, plotWidth: plotWidth, plotHeight: plotHeight, zero: zero };
    }

    function yFor(value, range, plot) {
        return plot.top + (range.max - value) / (range.max - range.min) * plot.plotHeight;
    }

    function activeTones() {
        // Paper mode is a global toggle rather than an argument: every draw
        // helper reads the theme through here, so one flag repaints the page.
        return paperMode ? paperTones() : themeTones();
    }

    var paperMode = false;

    function label(context, text, x, y, align) {
        context.font = '11px sans-serif';
        context.fillStyle = activeTones().inkSoft;
        context.textAlign = align || 'center';
        context.fillText(String(text == null ? '' : text), x, y);
    }

    function valueLabel(context, text, x, y, align) {
        context.font = '11px sans-serif';
        context.fillStyle = activeTones().ink;
        context.textAlign = align || 'center';
        context.fillText(String(text), x, y);
    }

    // The top of the scale and its zero, so the size of a bar can be read without
    // counting pixels: with the numbers on the marks, this is what tells a reader
    // what the axis is worth.
    function drawScale(context, chart, plot, range, format, horizontal) {
        var baseline = chart.height - plot.bottom + 16;
        if (horizontal) {
            // A horizontal bar grows leftwards from the right edge, so that is
            // where its zero is and where its top sits.
            label(context, valueText(0, format), chart.width - plot.right, baseline, 'right');
            label(context, valueText(range.max, format), plot.left, baseline, 'left');
            return;
        }
        label(context, valueText(range.max, format), plot.left - 6, plot.top + 4, 'right');
        if (plot.zero > plot.top + 14 && plot.zero < chart.height - plot.bottom - 4) {
            label(context, valueText(0, format), plot.left - 6, plot.zero + 4, 'right');
        }
    }

    function hasLegend(config) {
        var options = config.options && config.options.plugins && config.options.plugins.legend;
        if (options && options.display === false) return false;
        return (config.data && config.data.datasets || []).some(function (dataset) { return !!dataset.label; });
    }

    function drawLegend(context, config, chart) {
        var datasets = config.data && config.data.datasets || [];
        var labels = datasets.map(function (dataset) { return dataset.label || ''; }).filter(Boolean);
        if (!labels.length) return;
        if (!hasLegend(config)) return;
        var x = chart.width - 12;
        var y = chart.height - 10;
        var tones = activeTones();
        context.font = '11px sans-serif';
        labels.slice().reverse().forEach(function (text, index) {
            var color = mapColour(datasets[datasets.length - 1 - index].backgroundColor || datasets[datasets.length - 1 - index].borderColor) || tones.palette[index % tones.palette.length];
            if (Array.isArray(color)) color = color[0];
            context.fillStyle = color;
            context.fillRect(x - 10, y - 9, 8, 8);
            context.fillStyle = tones.inkSoft;
            context.textAlign = 'right';
            context.fillText(text, x - 16, y);
            x -= Math.min(100, context.measureText(text).width + 30);
        });
    }

    function drawBars(context, chart, config) {
        var datasets = config.data && config.data.datasets || [];
        var labels = config.data && config.data.labels || [];
        var format = formatFor(config);
        var values = valuesFor(config);
        var range = rangeFor(values);
        var horizontal = config.options && config.options.indexAxis === 'y';
        var groupCount = Math.max(labels.length, 1);
        var datasetCount = Math.max(datasets.length, 1);
        var colors = activeTones().palette;
        var widestCategory = 0;
        var widestValue = 0;
        labels.forEach(function (text) {
            widestCategory = Math.max(widestCategory, textWidth(context, text));
        });
        datasets.forEach(function (dataset) {
            (dataset.data || []).forEach(function (raw) {
                widestValue = Math.max(widestValue, textWidth(context, valueText(raw, format)));
            });
        });
        // A horizontal chart carries its numbers past the ends of its bars, so the
        // plot keeps room for the widest of them and for the category names.
        var bottom = hasLegend(config) ? 58 : 42;
        // The page reads right to left, and the charts follow it: a horizontal
        // bar grows leftwards from the right edge, so its category names sit in
        // a right-hand gutter and the room past the ends of its bars — the
        // numbers — is what the left padding keeps.
        var padding = horizontal
            ? { left: widestValue + 12,
                right: Math.min(Math.max(48, widestCategory + 10), chart.width * 0.45), bottom: bottom }
            : { bottom: bottom };
        var plot = drawAxes(context, chart, range, padding);

        if (horizontal) {
            var rowHeight = plot.plotHeight / groupCount;
            var axis = { top: plot.left, plotHeight: plot.plotWidth, max: range.max, min: range.min };
            var baseline = yFor(0, range, axis);
            // Bars first, then the numbers: a bar drawn after them would paint over
            // whatever sits inside its own footprint.
            datasets.forEach(function (dataset, datasetIndex) {
                (dataset.data || []).forEach(function (raw, index) {
                    var value = Number(raw) || 0;
                    var y = plot.top + index * rowHeight + rowHeight * (datasetIndex + 0.12) / datasetCount;
                    var h = rowHeight * 0.76 / datasetCount;
                    var x = yFor(value, range, axis);
                    var color = mapColour(Array.isArray(dataset.backgroundColor) ? dataset.backgroundColor[index % dataset.backgroundColor.length] : dataset.backgroundColor);
                    context.fillStyle = color || colors[datasetIndex % colors.length];
                    context.fillRect(Math.min(x, baseline), y, Math.abs(baseline - x), h);
                });
            });
            datasets.forEach(function (dataset, datasetIndex) {
                var series = dataset.data || [];
                // The room a number has is the room past its own bar, so what is
                // kept is decided against the space it will actually be drawn in.
                var keep = keepsFor(series, 0, context, format);
                series.forEach(function (raw, index) {
                    var value = Number(raw) || 0;
                    var x = yFor(value, range, axis);
                    var room = value >= 0 ? (chart.width - plot.right) - x : x - plot.left;
                    if (fits(context, valueText(value, format), room)) keep[index] = true;
                });
                series.forEach(function (raw, index) {
                    if (!keep[index]) return;
                    var value = Number(raw) || 0;
                    var x = yFor(value, range, axis);
                    var y = plot.top + index * rowHeight + rowHeight * (datasetIndex + 0.12) / datasetCount + rowHeight * 0.76 / datasetCount / 2 + 4;
                    var text = valueText(value, format);
                    // Past the end of the bar when there is room for it there, and
                    // on the bar itself when the bar runs to the axis — a long bar
                    // has nowhere else to put its own number.
                    var room = value >= 0 ? x - plot.left : chart.width - plot.right - x;
                    if (fits(context, text, room)) {
                        if (value >= 0) valueLabel(context, text, x - 5, y, 'right');
                        else valueLabel(context, text, x + 5, y, 'left');
                    } else if (value >= 0) {
                        valueLabel(context, text, x + 5, y, 'left');
                    } else {
                        valueLabel(context, text, x - 5, y, 'right');
                    }
                });
            });
            labels.forEach(function (text, index) {
                label(context, text, chart.width - plot.right + 6, plot.top + index * rowHeight + rowHeight / 2 + 4, 'left');
            });
        } else {
            var groupWidth = plot.plotWidth / groupCount;
            var barWidth = groupWidth * 0.72 / datasetCount;
            // The first category sits at the right edge, the way the page is
            // read, and the first dataset is the rightmost bar of its group.
            datasets.forEach(function (dataset, datasetIndex) {
                (dataset.data || []).forEach(function (raw, index) {
                    var value = Number(raw) || 0;
                    var x = plot.left + (groupCount - 1 - index) * groupWidth + groupWidth * 0.86 - (datasetIndex + 1) * barWidth;
                    var y = yFor(value, range, plot);
                    var color = mapColour(Array.isArray(dataset.backgroundColor) ? dataset.backgroundColor[index % dataset.backgroundColor.length] : dataset.backgroundColor);
                    context.fillStyle = color || colors[datasetIndex % colors.length];
                    context.fillRect(x, Math.min(y, plot.zero), barWidth - 2, Math.abs(plot.zero - y));
                });
            });
            datasets.forEach(function (dataset) {
                var series = dataset.data || [];
                var keep = keepsFor(series, barWidth, context, format);
                series.forEach(function (raw, index) {
                    if (!keep[index]) return;
                    var value = Number(raw) || 0;
                    var x = plot.left + (groupCount - 1 - index) * groupWidth + groupWidth * 0.86 - barWidth / 2;
                    var y = yFor(value, range, plot);
                    if (value >= 0) {
                        valueLabel(context, valueText(value, format), x, Math.max(y - 5, plot.top + 9));
                    } else {
                        valueLabel(context, valueText(value, format), x, Math.min(y + 14, chart.height - plot.bottom - 4));
                    }
                });
            });
            // Hour and price axes carry more names than fit side by side, so they
            // are thinned to a readable step rather than overprinted.
            var stride = Math.max(1, Math.ceil((widestCategory + 8) / groupWidth));
            labels.forEach(function (text, index) {
                if (index % stride && index !== labels.length - 1) return;
                label(context, text, plot.left + (groupCount - 1 - index) * groupWidth + groupWidth / 2, chart.height - plot.bottom + 18);
            });
        }
        drawScale(context, chart, plot, range, format, horizontal);
        drawLegend(context, config, chart);
    }

    function drawLines(context, chart, config) {
        var datasets = config.data && config.data.datasets || [];
        var labels = config.data && config.data.labels || [];
        var format = formatFor(config);
        var range = rangeFor(valuesFor(config));
        var plot = drawAxes(context, chart, range, { bottom: hasLegend(config) ? 58 : 42 });
        var step = plot.plotWidth / Math.max(labels.length - 1, 1);
        var tones = activeTones();
        datasets.forEach(function (dataset, datasetIndex) {
            var color = mapColour(dataset.borderColor || tones.palette[datasetIndex % tones.palette.length]);
            context.strokeStyle = color;
            context.fillStyle = color;
            context.lineWidth = 2;
            context.beginPath();
            // Time reads the way the page does: the first point sits at the right
            // edge and the line walks leftwards, so «روند درآمد» ends — the month
            // the owner cares about — where a Persian reader's eye starts.
            (dataset.data || []).forEach(function (raw, index) {
                var x = plot.left + (dataset.data.length - 1 - index) * step;
                var y = yFor(Number(raw) || 0, range, plot);
                if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
            });
            context.stroke();
            (dataset.data || []).forEach(function (raw, index) {
                var x = plot.left + (dataset.data.length - 1 - index) * step;
                var y = yFor(Number(raw) || 0, range, plot);
                context.beginPath();
                context.arc(x, y, 3, 0, Math.PI * 2);
                context.fill();
            });
        });
        // The numbers go on the points, the latest month always among them: with a
        // dozen months the fitted ones are thinned, but the line still says where it
        // ended up without a reader having to compare colours.
        datasets.forEach(function (dataset, datasetIndex) {
            var series = dataset.data || [];
            var keep = keepsFor(series, step, context, format, [series.length - 1]);
            var above = datasetIndex % 2 === 0;
            Object.keys(keep).forEach(function (key) {
                var index = Number(key);
                var x = plot.left + (series.length - 1 - index) * step;
                var y = yFor(Number(series[index]) || 0, range, plot);
                valueLabel(context, valueText(series[index], format), x, above ? Math.max(y - 6, plot.top + 9) : Math.min(y + 14, chart.height - plot.bottom - 4));
            });
        });
        var widestCategory = 0;
        labels.forEach(function (text) { widestCategory = Math.max(widestCategory, textWidth(context, text)); });
        var stride = Math.max(1, Math.ceil((widestCategory + 8) / step));
        labels.forEach(function (text, index) {
            if (index % stride && index !== 0) return;
            label(context, text, plot.left + (labels.length - 1 - index) * step, chart.height - plot.bottom + 18);
        });
        drawScale(context, chart, plot, range, format, false);
        drawLegend(context, config, chart);
    }

    function drawDoughnut(context, chart, config) {
        var dataset = config.data && config.data.datasets && config.data.datasets[0] || { data: [] };
        var labels = config.data && config.data.labels || [];
        var format = formatFor(config);
        var values = (dataset.data || []).map(function (value) { return Math.max(Number(value) || 0, 0); });
        var total = values.reduce(function (sum, value) { return sum + value; }, 0);
        var radius = Math.min(chart.width, chart.height) * 0.28;
        var centerX = chart.width / 2;
        var centerY = chart.height / 2 - 8;
        // The page reads right to left, so the ring does too: the first slice
        // starts at the top and sweeps counter-clockwise — into the positions a
        // Persian reader reaches first — instead of clockwise like an LTR chart.
        var start = -Math.PI / 2;
        var tones = activeTones();
        if (!total) {
            context.fillStyle = tones.rule;
            context.beginPath();
            context.arc(centerX, centerY, radius, 0, Math.PI * 2);
            context.fill();
        } else {
            values.forEach(function (value, index) {
                var end = start - value / total * Math.PI * 2;
                var color = mapColour(Array.isArray(dataset.backgroundColor) ? dataset.backgroundColor[index % dataset.backgroundColor.length] : dataset.backgroundColor);
                context.fillStyle = color || tones.palette[index % tones.palette.length];
                if (typeof context.recordArcFill === 'function') context.recordArcFill(String(context.fillStyle));
                context.beginPath();
                context.moveTo(centerX, centerY);
                context.arc(centerX, centerY, radius, start, end, true);
                context.arc(centerX, centerY, radius, end, start, false);
                context.closePath();
                context.fill();
                start = end;
            });
            context.globalCompositeOperation = 'destination-out';
            context.beginPath();
            context.arc(centerX, centerY, radius * 0.54, 0, Math.PI * 2);
            context.fill();
            context.globalCompositeOperation = 'source-over';
            // What the ring adds up to, in the hole it leaves.
            context.font = '12px sans-serif';
            context.fillStyle = tones.ink;
            context.textAlign = 'center';
            context.fillText(valueText(total, format), centerX, centerY + 4);
        }
        // Every slice named, counted and shared: a ring whose legend carries only
        // colours is the one chart that cannot be read at all without them, so the
        // figure is beside every name. The list starts at the right edge and grows
        // leftwards, first entry where the eye lands.
        var entries = labels.slice(0, 8).map(function (text, index) {
            return { text: text, value: values[index] || 0 };
        });
        entries.forEach(function (entry, index) {
            var share = total ? Math.round(entry.value / total * 100) : 0;
            var y = chart.height - 12 - (entries.length - 1 - index) * 16;
            context.fillStyle = tones.palette[index % tones.palette.length];
            context.fillRect(chart.width - 12 - 8, y - 9, 8, 8);
            label(context, entry.text + ' — ' + valueText(entry.value, format) + ' (' + share + '٪)', chart.width - 26, y, 'right');
        });
    }

    function drawMessage(canvas) {
        var chart = setupCanvas(canvas);
        if (!chart) return;
        chart.context.fillStyle = activeTones().inkSoft;
        chart.context.textAlign = 'center';
        chart.context.fillText('نمودار در حالت آفلاین در دسترس نیست', chart.width / 2, chart.height / 2);
    }

    function renderChart(target, config) {
        var canvas = canvasFor(target);
        var chart = setupCanvas(canvas);
        if (!chart) return;
        var type = config && config.type;
        if (type === 'doughnut' || type === 'pie') drawDoughnut(chart.context, chart, config);
        else if (type === 'line') drawLines(chart.context, chart, config);
        else drawBars(chart.context, chart, config);
    }

    function Chart(target, config) {
        this.canvas = canvasFor(target);
        this.config = config || {};
        // Remembered so a beforeprint repaint can redraw this exact chart.
        this.canvas.__chart = this;
        renderChart(this.canvas, this.config);
    }

    Chart.prototype.destroy = function () {};
    Chart.prototype.update = function () { renderChart(this.canvas, this.config); };

    // A harness that drives the renderer with a real palette hands it one as the
    // second argument — the same catalogue a rendered page would expose to
    // getComputedStyle. Without it the renderer runs as it always has: every
    // token read comes back empty, the palette collapses, and nothing here may
    // take that as a third palette to assert against.
    if (typeof process !== 'undefined' && process.argv[3]) {
        var harnessTokens = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
        sandbox.getComputedStyle = function () {
            return { getPropertyValue: function (name) { return harnessTokens[name] || ''; } };
        };
        sandbox.document.documentElement = {};
    }

    window.Chart = Chart;
    window.renderOfflineCharts = function () {
        document.querySelectorAll('canvas').forEach(drawMessage);
    };

    // ── printing ────────────────────────────────────────────────────────────
    // A canvas is pixels fixed at screen time: the stylesheet cannot recolour
    // it for paper, so the page repaints every chart itself. On beforeprint
    // the charts are redrawn in the paper tones; on afterprint they go back
    // to the screen palette, so a theme switch never shows through a stale
    // canvas. The hooks are feature-detected because a test harness's window
    // is not a browser's.
    if (typeof window.addEventListener === 'function') {
        window.addEventListener('beforeprint', function () {
            paperMode = true;
            printTones = null;
            document.querySelectorAll('canvas').forEach(function (canvas) {
                var chart = canvas.__chart;
                if (chart && chart.config) renderChart(canvas, chart.config);
            });
        });
        window.addEventListener('afterprint', function () {
            paperMode = false;
            printTones = null;
            document.querySelectorAll('canvas').forEach(function (canvas) {
                var chart = canvas.__chart;
                if (chart && chart.config) renderChart(canvas, chart.config);
            });
        });
    }
})();
