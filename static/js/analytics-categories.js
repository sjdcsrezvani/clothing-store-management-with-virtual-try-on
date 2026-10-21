/* Category/size tab charts. The page owns the data and the sentences; this
   file only draws the four ECharts views and keeps their captions honest when
   a metric toggle changes what the marks mean. */
(function () {
    'use strict';

    // Both matrices live below the scripts that used to bind them; one pass at
    // the bottom of the page covers the existing colour matrix and the new
    // category matrix without caring whether ECharts loaded.
    document.querySelectorAll('[data-matrix-mode]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var table = btn.closest('.chart-card').querySelector('table.matrix');
            if (!table) return;
            var mode = btn.getAttribute('data-matrix-mode');
            btn.parentElement.querySelectorAll('[data-matrix-mode]').forEach(function (other) {
                other.setAttribute('aria-pressed', other === btn ? 'true' : 'false');
            });
            table.querySelectorAll('td[data-qty]').forEach(function (cell) {
                var raw = parseInt(mode === 'revenue' ? cell.getAttribute('data-revenue') : cell.getAttribute('data-qty'), 10) || 0;
                cell.textContent = raw.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')
                    .replace(/[0-9]/g, function (d) { return '۰۱۲۳۴۵۶۷۸۹'[+d]; }) + (mode === 'revenue' ? ' تومان' : '');
            });
        });
    });

    var AC = window.AnalyticsCharts;
    var data = window.ANALYTICS_SIZES;
    if (!AC || !window.echarts || !data) return;

    function ranked(rows, metric) {
        return (rows || []).slice().sort(function (a, b) {
            return (Number(b[metric]) || 0) - (Number(a[metric]) || 0);
        });
    }

    function setNote(hostId, mode) {
        var notes = window.CHART_TOGGLE_NOTES && window.CHART_TOGGLE_NOTES[hostId];
        var text = notes && notes[mode];
        if (!text) return;
        var caption = document.querySelector('.chart-note[data-chart="' + hostId + '"]');
        var host = document.getElementById(hostId);
        if (caption) caption.textContent = text;
        if (host) host.setAttribute('aria-label', text);
    }

    function setPressed(buttons, active) {
        buttons.forEach(function (button) {
            button.setAttribute('aria-pressed', button === active ? 'true' : 'false');
        });
    }

    var groupState = { dimension: 'group', metric: 'revenue' };
    function buildGroupType(paper) {
        var T = AC.tones(paper);
        var opt = AC.base(paper);
        var rows = ranked((data.groupType || {})[groupState.dimension],
                          groupState.metric).slice(0, 8);
        var money = groupState.metric === 'revenue';
        opt.legend = { show: false };
        opt.tooltip.valueFormatter = money ? AC.money : AC.grouped;
        opt.xAxis = AC.categoryAxis(rows.map(function (row) { return row.label; }), paper);
        opt.yAxis = AC.valueAxis(paper, money);
        opt.series = [{
            name: money ? 'فروش' : 'تعداد',
            type: 'bar',
            barMaxWidth: 36,
            data: rows.map(function (row) { return Number(row[groupState.metric]) || 0; }),
            itemStyle: { color: T.candy, borderRadius: [3, 3, 0] },
        }];
        return opt;
    }

    var groupChart = AC.create(document.getElementById('groupTypeChart'), buildGroupType);
    if (groupChart) {
        document.querySelectorAll('[data-group-type-dimension]').forEach(function (button) {
            button.addEventListener('click', function () {
                groupState.dimension = button.getAttribute('data-group-type-dimension');
                setPressed(Array.prototype.slice.call(
                    document.querySelectorAll('[data-group-type-dimension]')), button);
                groupChart.setOption(buildGroupType(false), true);
            });
        });
        document.querySelectorAll('[data-group-type-metric]').forEach(function (button) {
            button.addEventListener('click', function () {
                groupState.metric = button.getAttribute('data-group-type-metric');
                setPressed(Array.prototype.slice.call(
                    document.querySelectorAll('[data-group-type-metric]')), button);
                groupChart.setOption(buildGroupType(false), true);
                setNote('groupTypeChart', groupState.metric);
            });
        });
    }

    var ageState = { metric: 'revenue' };
    function buildCategoryAge(paper) {
        var T = AC.tones(paper);
        var opt = AC.base(paper);
        var budget = data.categoryAge || {};
        var categories = budget.categories || [];
        var source = budget.series || [];
        var metric = ageState.metric;
        var rows = categories.map(function (label, index) {
            var total = source.reduce(function (sum, row) {
                return sum + (Number((row[metric] || [])[index]) || 0);
            }, 0);
            return { label: label, index: index, total: total };
        }).sort(function (a, b) { return b.total - a.total; }).slice(0, 10);
        var palette = [T.candy, T.sky, T.sunshine, T.mint, T.lavender];
        var money = metric === 'revenue';
        opt.legend = {
            bottom: 0, type: 'scroll', itemGap: 6, itemWidth: 10, itemHeight: 10,
            textStyle: { fontSize: 10 },
        };
        opt.grid = Object.assign({}, opt.grid, { bottom: 58, left: 80 });
        opt.tooltip.valueFormatter = money ? AC.money : AC.grouped;
        opt.xAxis = AC.valueAxis(paper, money);
        var ageAxis = AC.categoryAxis(rows.map(function (row) { return row.label; }), paper);
        ageAxis.axisLabel.align = 'left';
        opt.yAxis = ageAxis;
        opt.series = source.map(function (row, index) {
            return {
                name: row.label,
                type: 'bar',
                stack: 'age',
                barMaxWidth: 32,
                data: rows.map(function (item) {
                    return Number((row[metric] || [])[item.index]) || 0;
                }),
                itemStyle: { color: palette[index % palette.length] },
            };
        });
        return opt;
    }

    var ageChart = AC.create(document.getElementById('categoryAgeChart'), buildCategoryAge);
    if (ageChart) {
        document.querySelectorAll('[data-age-metric]').forEach(function (button) {
            button.addEventListener('click', function () {
                ageState.metric = button.getAttribute('data-age-metric');
                setPressed(Array.prototype.slice.call(
                    document.querySelectorAll('[data-age-metric]')), button);
                ageChart.setOption(buildCategoryAge(false), true);
                setNote('categoryAgeChart', ageState.metric);
            });
        });
    }

    (function () {
        var rows = ranked(data.returnsBand, 'returned_units').slice(0, 10);
        AC.create(document.getElementById('returnsBandChart'), function (paper) {
            var T = AC.tones(paper);
            var opt = AC.base(paper);
            var rateAxis = AC.valueAxis(paper, false);
            rateAxis.position = 'left';
            rateAxis.splitLine = { show: false };
            rateAxis.axisLabel.formatter = function (value) {
                return value === null || value === undefined ? '—' : AC.percent(value);
            };
            opt.grid = Object.assign({}, opt.grid, { bottom: 58 });
            opt.xAxis = AC.categoryAxis(rows.map(function (row) { return row.label; }), paper);
            opt.yAxis = [AC.valueAxis(paper, false), rateAxis];
            opt.tooltip.formatter = function (points) {
                if (!points || !points.length) return '';
                var row = rows[points[0].dataIndex] || {};
                return row.label + ': ' + AC.grouped(row.returned_units) + ' واحد مرجوعی · ' +
                    (row.rate === null || row.rate === undefined ? 'نرخ —' : AC.percent(row.rate));
            };
            opt.series = [
                {
                    name: 'مرجوعی (واحد)', type: 'bar', barMaxWidth: 48,
                    data: rows.map(function (row) { return row.returned_units; }),
                    itemStyle: { color: T.candy, borderRadius: [3, 3, 0] },
                },
                {
                    name: 'نرخ مرجوعی', type: 'line', yAxisIndex: 1,
                    data: rows.map(function (row) { return row.rate; }),
                    symbol: 'circle', symbolSize: 7,
                    lineStyle: { color: T.persimmon, width: 2 },
                    itemStyle: { color: T.persimmon },
                },
            ];
            return opt;
        });
    })();

    (function () {
        var rows = ranked(data.systemMix, 'units').slice(0, 12);
        AC.create(document.getElementById('systemMixChart'), function (paper) {
            var T = AC.tones(paper);
            var opt = AC.base(paper);
            opt.legend = { show: false };
            opt.tooltip.valueFormatter = AC.grouped;
            opt.grid = Object.assign({}, opt.grid, { left: 100 });
            opt.xAxis = AC.valueAxis(paper, false);
            var systemAxis = AC.categoryAxis(rows.map(function (row) { return row.label; }), paper);
            systemAxis.axisLabel.align = 'left';
            opt.yAxis = systemAxis;
            opt.series = [{
                name: 'تعداد', type: 'bar', barMaxWidth: 22,
                data: rows.map(function (row) { return row.units; }),
                itemStyle: { color: T.lavender, borderRadius: [0, 3, 3, 0] },
            }];
            return opt;
        });
    })();
})();
