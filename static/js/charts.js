(function () {
    'use strict';

    var palette = ['#FF6B8A', '#45B7D1', '#FFC857', '#51CF66', '#A66CFF', '#FF8A65', '#FFB347', '#6C5CE7'];

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

    function drawAxes(context, chart, range) {
        var left = 48;
        var right = 16;
        var top = 18;
        var bottom = 42;
        var plotWidth = chart.width - left - right;
        var plotHeight = chart.height - top - bottom;
        var zero = top + (range.max / (range.max - range.min)) * plotHeight;
        context.strokeStyle = '#d9d9d9';
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(left, top);
        context.lineTo(left, chart.height - bottom);
        context.lineTo(chart.width - right, chart.height - bottom);
        context.stroke();
        if (zero >= top && zero <= chart.height - bottom) {
            context.strokeStyle = '#bcbcbc';
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

    function label(context, text, x, y, align) {
        context.fillStyle = '#666';
        context.textAlign = align || 'center';
        context.fillText(String(text == null ? '' : text), x, y);
    }

    function drawLegend(context, config, chart) {
        var options = config.options && config.options.plugins && config.options.plugins.legend;
        if (options && options.display === false) return;
        var datasets = config.data && config.data.datasets || [];
        var labels = datasets.map(function (dataset) { return dataset.label || ''; }).filter(Boolean);
        if (!labels.length) return;
        var x = chart.width - 12;
        var y = chart.height - 12;
        context.font = '11px sans-serif';
        labels.slice().reverse().forEach(function (text, index) {
            var color = datasets[datasets.length - 1 - index].backgroundColor || datasets[datasets.length - 1 - index].borderColor || palette[index % palette.length];
            if (Array.isArray(color)) color = color[0];
            context.fillStyle = color;
            context.fillRect(x - 10, y - 9, 8, 8);
            context.fillStyle = '#666';
            context.textAlign = 'right';
            context.fillText(text, x - 16, y);
            x -= Math.min(100, context.measureText(text).width + 30);
        });
    }

    function drawBars(context, chart, config) {
        var datasets = config.data && config.data.datasets || [];
        var labels = config.data && config.data.labels || [];
        var values = valuesFor(config);
        var range = rangeFor(values);
        var plot = drawAxes(context, chart, range);
        var horizontal = config.options && config.options.indexAxis === 'y';
        var groupCount = Math.max(labels.length, 1);
        var datasetCount = Math.max(datasets.length, 1);
        var colors = palette;

        if (horizontal) {
            var rowHeight = plot.plotHeight / groupCount;
            datasets.forEach(function (dataset, datasetIndex) {
                (dataset.data || []).forEach(function (raw, index) {
                    var value = Number(raw) || 0;
                    var y = plot.top + index * rowHeight + rowHeight * (datasetIndex + 0.12) / datasetCount;
                    var h = rowHeight * 0.76 / datasetCount;
                    var x = yFor(value, range, { top: plot.left, plotHeight: plot.plotWidth, max: range.max, min: range.min });
                    var baseline = yFor(0, range, { top: plot.left, plotHeight: plot.plotWidth, max: range.max, min: range.min });
                    var color = Array.isArray(dataset.backgroundColor) ? dataset.backgroundColor[index % dataset.backgroundColor.length] : dataset.backgroundColor;
                    context.fillStyle = color || colors[datasetIndex % colors.length];
                    context.fillRect(Math.min(x, baseline), y, Math.abs(baseline - x), h);
                });
                labels.forEach(function (text, index) {
                    label(context, text, plot.left - 6, plot.top + index * rowHeight + rowHeight / 2 + 4, 'right');
                });
            });
        } else {
            var groupWidth = plot.plotWidth / groupCount;
            datasets.forEach(function (dataset, datasetIndex) {
                (dataset.data || []).forEach(function (raw, index) {
                    var value = Number(raw) || 0;
                    var barWidth = groupWidth * 0.72 / datasetCount;
                    var x = plot.left + index * groupWidth + groupWidth * 0.14 + datasetIndex * barWidth;
                    var y = yFor(value, range, plot);
                    var color = Array.isArray(dataset.backgroundColor) ? dataset.backgroundColor[index % dataset.backgroundColor.length] : dataset.backgroundColor;
                    context.fillStyle = color || colors[datasetIndex % colors.length];
                    context.fillRect(x, Math.min(y, plot.zero), barWidth - 2, Math.abs(plot.zero - y));
                });
            });
            labels.forEach(function (text, index) {
                label(context, text, plot.left + index * groupWidth + groupWidth / 2, chart.height - plot.bottom + 18);
            });
        }
        drawLegend(context, config, chart);
    }

    function drawLines(context, chart, config) {
        var datasets = config.data && config.data.datasets || [];
        var labels = config.data && config.data.labels || [];
        var range = rangeFor(valuesFor(config));
        var plot = drawAxes(context, chart, range);
        var step = plot.plotWidth / Math.max(labels.length - 1, 1);
        datasets.forEach(function (dataset, datasetIndex) {
            var color = dataset.borderColor || palette[datasetIndex % palette.length];
            context.strokeStyle = color;
            context.fillStyle = color;
            context.lineWidth = 2;
            context.beginPath();
            (dataset.data || []).forEach(function (raw, index) {
                var x = plot.left + index * step;
                var y = yFor(Number(raw) || 0, range, plot);
                if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
            });
            context.stroke();
            (dataset.data || []).forEach(function (raw, index) {
                var x = plot.left + index * step;
                var y = yFor(Number(raw) || 0, range, plot);
                context.beginPath();
                context.arc(x, y, 3, 0, Math.PI * 2);
                context.fill();
            });
        });
        labels.forEach(function (text, index) {
            label(context, text, plot.left + index * step, chart.height - plot.bottom + 18);
        });
        drawLegend(context, config, chart);
    }

    function drawDoughnut(context, chart, config) {
        var dataset = config.data && config.data.datasets && config.data.datasets[0] || { data: [] };
        var labels = config.data && config.data.labels || [];
        var values = (dataset.data || []).map(function (value) { return Math.max(Number(value) || 0, 0); });
        var total = values.reduce(function (sum, value) { return sum + value; }, 0);
        var radius = Math.min(chart.width, chart.height) * 0.28;
        var centerX = chart.width / 2;
        var centerY = chart.height / 2 - 8;
        var start = -Math.PI / 2;
        if (!total) {
            context.fillStyle = '#eeeeee';
            context.beginPath();
            context.arc(centerX, centerY, radius, 0, Math.PI * 2);
            context.fill();
        } else {
            values.forEach(function (value, index) {
                var end = start + value / total * Math.PI * 2;
                var color = Array.isArray(dataset.backgroundColor) ? dataset.backgroundColor[index % dataset.backgroundColor.length] : dataset.backgroundColor;
                context.fillStyle = color || palette[index % palette.length];
                context.beginPath();
                context.moveTo(centerX, centerY);
                context.arc(centerX, centerY, radius, start, end);
                context.closePath();
                context.fill();
                start = end;
            });
            context.globalCompositeOperation = 'destination-out';
            context.beginPath();
            context.arc(centerX, centerY, radius * 0.54, 0, Math.PI * 2);
            context.fill();
            context.globalCompositeOperation = 'source-over';
        }
        labels.slice(0, 8).forEach(function (text, index) {
            var y = chart.height - 12 - index * 16;
            context.fillStyle = palette[index % palette.length];
            context.fillRect(12, y - 9, 8, 8);
            label(context, text, 26, y, 'left');
        });
    }

    function drawMessage(canvas) {
        var chart = setupCanvas(canvas);
        if (!chart) return;
        chart.context.fillStyle = '#777';
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
        renderChart(this.canvas, this.config);
    }

    Chart.prototype.destroy = function () {};
    Chart.prototype.update = function () { renderChart(this.canvas, this.config); };

    window.Chart = Chart;
    window.renderOfflineCharts = function () {
        document.querySelectorAll('canvas').forEach(drawMessage);
    };
})();
