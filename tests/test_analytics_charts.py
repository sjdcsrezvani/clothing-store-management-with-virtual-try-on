"""Every chart on سود و زیان says what it shows without colour.

Three readers were missing from this page. One cannot separate the chart's
accents from each other. One opens it on a phone in daylight. One prints it and
hands it to somebody who has never seen the screen. All three need the numbers
said in words and written on the marks, so these tests pin the sentences to the
figures they describe, pin them to the canvases they sit under, and drive the
canvas renderer itself to see the values it paints.
"""

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.chart_notes import CHART_IDS, NO_DATA, chart_notes
from services.themes import THEMES

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static" / "js" / "charts.js"


def _loaded_period() -> dict:
    """One period of a shop that has something to say, used by several tests."""
    return chart_notes(
        price_stats={"avg_price": 500000, "avg_cost": 300000, "avg_profit": 200000},
        color_stats=[{"label": "مشکی", "quantity": 30}, {"label": "سفید", "quantity": 10}],
        size_stats=[{"label": "2", "quantity": 5}],
        daily=[{"date": "۰۱/۰۱", "revenue": 1000, "profit": 400},
               {"date": "۰۱/۰۲", "revenue": 3000, "profit": 900}],
        categories=[{"category": "پسرانه", "revenue": 8000},
                    {"category": "دخترانه", "revenue": 2000}],
        tier_revenue=[{"tier": "diamond", "label": "الماس", "revenue": 6000},
                      {"tier": "silver", "label": "نقره‌ای", "revenue": 4000}],
        revenue_trend=[{"month": "1/1404", "revenue": 1000000},
                       {"month": "2/1404", "revenue": 2000000},
                       {"month": "3/1404", "revenue": 3000000}],
        sales_pattern={"weekdays": [{"day": "شنبه", "revenue": 100}, {"day": "جمعه", "revenue": 10}],
                       "hours": [{"hour": 10, "label": "10:00", "revenue": 100},
                                 {"hour": 20, "label": "20:00", "revenue": 900}]},
        price_dist=[{"price": 300000, "quantity": 6}, {"price": 600000, "quantity": 4}],
        margin_by_cat=[{"category": "پسرانه", "margin": 40}, {"category": "دخترانه", "margin": -10}],
        customer_health={"repeat_rate": 30, "one_timer_pct": 70, "avg_orders": 1.4,
                         "segments": [{"label": "تا 500 هزار", "count": 7},
                                      {"label": "1 تا 2 میلیون", "count": 3}]},
    )


def test_every_chart_has_a_sentence_even_when_the_period_is_empty():
    """A quiet day still gets a caption — and it says the day was quiet.

    Nothing here may raise or come back blank: the page is opened on days with no
    sales at all, and a chart with an empty caption over it reads as a chart whose
    data failed to arrive.
    """
    notes = chart_notes()
    assert set(notes) == set(CHART_IDS)
    assert set(notes.values()) == {NO_DATA}
    assert NO_DATA.strip().endswith(".")
    for canvas, sentence in notes.items():
        assert sentence.strip() == sentence and sentence, canvas


def test_each_sentence_names_the_figure_its_chart_draws():
    notes = _loaded_period()
    assert "200,000 ت سود دارد" in notes["pricingChart"]
    assert "«مشکی» با 30 واحد" in notes["colorChart"] and "75٪" in notes["colorChart"]
    assert "«2» با 5 واحد" in notes["sizeChart"]
    assert "بالاترین درآمد در 3/1404 با 3,000,000 ت" in notes["trendChart"]
    # The line ends on the current, unfinished month, so the comparison is made
    # between the two finished ones.
    assert "2/1404 نسبت به 1/1404 100٪ بیشتر بود." in notes["trendChart"]
    assert "بیشترین 3,000 ت در ۰۱/۰۲" in notes["dailyChart"]
    assert "جمع 4,000 ت و سود 1,300 ت" in notes["dailyChart"]
    assert "«پسرانه» با 80٪ بیشترین سهم فروش" in notes["categoryChart"]
    assert "بیش از نیمی از فروش به یک دسته وابسته است." in notes["categoryChart"]
    assert "«الماس» با 60٪" in notes["tierChart"]
    assert "شلوغترین روز «شنبه» با 100 ت" in notes["weekdayChart"]
    assert "اوج فروش ساعت 20:00 با 900 ت" in notes["hourChart"]
    assert "بازه قیمتی 300,000 ت" in notes["priceDistChart"] and "60٪" in notes["priceDistChart"]
    assert "«پسرانه» با 40٪ و پایینترین «دخترانه» با -10٪" in notes["marginCatChart"]
    assert "1 دسته از 2 دسته زیاندهاند." in notes["marginCatChart"]
    assert "30٪ مشتریان این بازه بیش از یکبار" in notes["customerSegChart"]
    assert "پرحجمترین گروه «تا 500 هزار» با 7 مشتری" in notes["customerSegChart"]


def test_the_page_sentences_can_say_a_named_month_without_changing_words():
    """The page's sentences are read twice more: on the dashboard and by SMS.

    The builders take the period as a word — «این بازه» here, «این ماه» or a
    month's name elsewhere — and nothing else about a sentence may move with
    it. A sentence edited only for the page would silently stop being true of
    the phone, which is why the substitution is guarded rather than trusted.
    """
    from services.chart_notes import chart_notes, DEFAULT_SPAN, NO_DATA

    assert DEFAULT_SPAN == "این بازه"  # the word the page's captions carry
    kwargs = dict(
        price_stats={"avg_price": 500000, "avg_cost": 300000, "avg_profit": 200000},
        daily=[{"date": "۰۱/۰۱", "revenue": 1000, "profit": 400}],
        categories=[{"category": "پسرانه", "revenue": 8000}],
        tier_revenue=[{"tier": "gold", "label": "طلایی", "revenue": 6000}],
        sales_pattern={"weekdays": [{"day": "شنبه", "revenue": 100}],
                       "hours": [{"hour": 10, "label": "10:00", "revenue": 100}]},
        price_dist=[{"price": 300000, "quantity": 6}],
        margin_by_cat=[{"category": "پسرانه", "margin": 40},
                       {"category": "دخترانه", "margin": -10}],
        customer_health={"repeat_rate": 30, "one_timer_pct": 70, "avg_orders": 1.4,
                         "segments": [{"label": "تا 500 هزار", "count": 7}]},
    )
    page = chart_notes(**kwargs)
    month = chart_notes(**kwargs, span="این ماه")
    for key, page_sentence in page.items():
        month_sentence = month[key]
        if page_sentence == NO_DATA:
            # The empty sentence is *rewritten* for a named span, not spliced:
            # it must still say the period the reader is holding.
            assert NO_DATA.replace(DEFAULT_SPAN, "این ماه") == month_sentence
            continue
        assert month_sentence == page_sentence.replace(DEFAULT_SPAN, "این ماه"), key


def test_a_loss_is_said_in_words_rather_than_left_to_a_colour():
    """The one figure nobody may have to work out from a red bar."""
    notes = chart_notes(price_stats={"avg_price": 400000, "avg_cost": 450000, "avg_profit": -50000})
    assert "50,000 ت زیان میدهد" in notes["pricingChart"]
    assert "400,000 ت فروش در برابر 450,000 ت هزینه کالا" in notes["pricingChart"]
    assert "زیانده" in chart_notes(margin_by_cat=[{"category": "دخترانه", "margin": -5}])["marginCatChart"]


def test_every_sentence_is_stated_in_one_line():
    """The caption sits in a paragraph: a newline in it would break the markup."""
    for canvas, sentence in _loaded_period().items():
        assert "\n" not in sentence, canvas
        assert sentence.count(".") >= 1, canvas


def test_the_sentences_and_the_canvases_are_the_same_set(client, db_session):
    """The page's own markup is what proves no chart can arrive without a caption.

    A new chart is a new host ``div`` (SVG mounts into a container; a canvas
    element cannot take children), and this fails until the same list gains
    its sentence and the card shows it — as the visible caption *and* as the
    host's accessible name, because a screen reader reads the drawing no
    better than a printout does.
    """
    from tests.test_roles import _session_as, _staff

    user, password = _staff(db_session, "notes-owner", "owner")
    _session_as(client, user, password)
    html = client.get("/admin/analytics").text

    hosts = set(re.findall(r'<div class="chart-host" id="([^"]+)"', html))
    captions = set(re.findall(r'data-chart="([^"]+)"', html))
    assert hosts, "the analytics page drew no charts at all"
    assert hosts == set(CHART_IDS)
    assert captions == hosts

    for host in sorted(hosts):
        pair = re.search(
            r'data-chart="%s">([^<]*)</p><div class="chart-host" id="%s" role="img" aria-label="([^"]*)"' % (host, host),
            html,
        )
        assert pair, f"{host} has no sentence beside it"
        assert pair.group(1).strip() == pair.group(2).strip()
        assert pair.group(1).strip(), f"{host} has an empty sentence"

    # This shop's database is empty in this test, so every caption says so rather
    # than leaving the owner a chart with no explanation under it.
    assert set(re.findall(r'data-chart="[^"]+">([^<]*)</p>', html)) == {NO_DATA}


# ===== The renderer, driven for real =======================================
# The numbers have to be painted on the marks, which is a claim about a canvas
# drawing call. So the real renderer is loaded into a real JavaScript engine with
# a canvas that records instead of painting, and what it painted is the assertion.

RENDER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const tokens = {
  '--candy': '#f07a91', '--sky': '#45b7d1', '--sunshine': '#ffc857', '--mint': '#55c878',
  '--lavender': '#a66cff', '--persimmon': '#ad3100', '--rule': '#e8ddd5',
  '--ink': '#2d2d2d', '--ink-soft': '#6b7280', '--paper': '#FFFFFF', '--paper-ink': '#000000',
};

const texts = [];
const rects = [];
const arcs = [];
const strokes = [];
const strokeStyles = [];
const lineStrokes = [];
const arcFills = [];
const canvases = [];
const printListeners = {};
function context2d() {
  let strokeStyleValue = '';
  return {
    fillStyle: '', lineWidth: 1, font: '', textAlign: '',
    direction: '', globalCompositeOperation: '',
    get strokeStyle() { return strokeStyleValue; },
    set strokeStyle(value) { strokeStyleValue = String(value); lineStrokes.push(strokeStyleValue); },
    setTransform() {}, clearRect() {}, beginPath() {}, closePath() {},
    moveTo(x, y) { strokes.push({ op: 'move', x, y }); },
    lineTo(x, y) { strokes.push({ op: 'line', x, y }); },
    stroke() {}, fill() {},
    arc(x, y, r, start, end, ccw) { arcs.push({ x, y, r, start, end, ccw: !!ccw }); },
    fillRect(x, y, w, h) { rects.push({ x, y, w, h, fill: String(this.fillStyle) }); },
    fillText(text, x, y) { texts.push({ text: String(text), x, y, align: this.textAlign, fill: String(this.fillStyle) }); },
    measureText(text) { return { width: String(text).length * 7 }; },
    recordStrokeStyle(style) { strokeStyles.push(String(style)); },
    recordArcFill(fill) { arcFills.push(String(fill)); },
  };
}

const GlobalMath = globalThis.Math;
const Math = { PI: GlobalMath.PI, abs: GlobalMath.abs, ceil: GlobalMath.ceil, floor: GlobalMath.floor,
  max: GlobalMath.max, min: GlobalMath.min, round: GlobalMath.round, pow: GlobalMath.pow,
  hypot: GlobalMath.hypot, sign: GlobalMath.sign, trunc: GlobalMath.trunc };

const sandbox = {
  console,
  document: { querySelectorAll: () => canvases, documentElement: {} },
  getComputedStyle: () => ({ getPropertyValue: (name) => tokens[name] || '#333333' }),
};
sandbox.window = {
  devicePixelRatio: 1,
  addEventListener(name, fn) { printListeners[name] = fn; },
};
vm.createContext(sandbox);
// A palette file may be passed as argv[3]: its JSON replaces the default token
// set, so the renderer is driven with the catalogue of one real theme rather
// than this file's own tokens.
if (process.argv[3]) Object.assign(tokens, JSON.parse(fs.readFileSync(process.argv[3], 'utf8')));
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

function paint(config) {
  texts.length = 0;
  rects.length = 0;
  arcs.length = 0;
  strokes.length = 0;
  strokeStyles.length = 0;
  lineStrokes.length = 0;
  arcFills.length = 0;
  const canvas = {
    parentElement: { clientWidth: 420, clientHeight: 240 },
    clientWidth: 420,
    style: {},
    getContext: () => context2d(),
  };
  canvases.push(canvas);
  new sandbox.window.Chart(canvas, config);
  return { text: texts.map((call) => call.text), texts: texts.slice(), rects: rects.slice(), arcs: arcs.slice(), strokes: strokes.slice(), strokeStyles: strokeStyles.slice(), lineStrokes: lineStrokes.slice(), arcFills: arcFills.slice() };
}

const many = Array.from({ length: 30 }, (_, index) => (index === 7 ? 9000000 : 100000));

// The page builds its charts from a named `tone` map, so the harness mirrors
// that name — the reading shapes below can be lifted straight from the page's
// own builders and stay true to what the page draws.
const tone = {
  candy: tokens['--candy'], sky: tokens['--sky'], sunshine: tokens['--sunshine'],
  mint: tokens['--mint'], lavender: tokens['--lavender'], persimmon: tokens['--persimmon'],
};
tone.palette = [tone.candy, tone.sky, tone.sunshine, tone.mint, tone.lavender, tone.persimmon];
const baseOpts = (extra={}) => Object.assign({ responsive: true, plugins: { legend: { position: 'bottom', labels: { font: { family: 'Vazirmatn' } } } }, scales: { y: { beginAtZero: true } } }, extra);

// An empty chart is a state the theme matrix never renders: a shop with no
// sales drives drawBars through `datasets` empty, and its neutrals are what
// the eye sees then.
const paletteCharts = {
  bare: paint({ type: 'bar', options: { valueFormat: 'money' }, data: {} }),
  bars: paint({ type: 'bar', options: { valueFormat: 'money' },
    data: { labels: ['الف', 'ب', 'پ'], datasets: [{ label: 'فروش', data: [1500000, 2400000, 900000], backgroundColor: tokens['--candy'] }] } }),
  doughnut: paint({ type: 'doughnut', options: { valueFormat: 'count' },
    data: { labels: ['یک', 'دو'], datasets: [{ data: [75, 25], backgroundColor: [tokens['--sky'], tokens['--mint']] }] } }),
  lineSeries: paint({ type: 'line', valueFormat: 'money',
    data: { labels: ['1/1403', '2/1403', '3/1403'], datasets: [{ label: 'درآمد', data: [100, 300, 200], borderColor: tokens['--candy'] }] } }),
  // ── the dashboard's monthly reading, by canvas id ───────────────────────
  // The reading strip quotes four charts of سود و زیان (the same four ids the
  // strip's builder reads, in the same order). These shapes are lifted from
  // the page's own builders — a drive per palette exercises the *real*
  // geometry, including the grouped two-dataset path nothing generic below
  // draws. Each is judged in the test by the hues its own page line names.
  readingDaily: paint({ type: 'bar',
    data: { labels: ['۰۱/۰۱', '۰۱/۰۲', '۰۱/۰۳'], datasets: [
        { label: 'فروش', data: [1000, 3000, 2000], backgroundColor: tone.candy },
        { label: 'سود', data: [400, 900, 700], backgroundColor: tone.mint }
    ]},
    options: baseOpts({ valueFormat: 'money' })
  }),
  readingCategory: paint({ type: 'doughnut',
    data: { labels: ['پسرانه', 'دخترانه'], datasets: [{ data: [8000, 2000], backgroundColor: tone.palette }]},
    options: { responsive: true, valueFormat: 'money', plugins: { legend: { position: 'bottom', labels: { font: { family: 'Vazirmatn' } } } } }
  }),
  readingTier: paint({ type: 'bar',
    data: { labels: ['الماس', 'طلایی', 'نقره‌ای'], datasets: [{ label: 'فروش', data: [6000, 4000, 2000], backgroundColor: [tone.lavender, tone.sunshine, tone.sky] }]},
    options: baseOpts({ valueFormat: 'money', plugins: { legend: { display: false } } })
  }),
  readingSegments: paint({ type: 'doughnut',
    data: { labels: ['تا 500 هزار', '1 تا 2 میلیون'], datasets: [{ data: [7, 3], backgroundColor: [tone.lavender, tone.sunshine, tone.sky, tone.candy] }]},
    options: { responsive: true, valueFormat: 'count', plugins: { legend: { position: 'bottom', labels: { font: { family: 'Vazirmatn', size: 11 } } } } }
  }),
  // ── the page's remaining canvases, in the shapes their builders draw ────
  // The reading strip quotes four of سود و زیان's twelve canvases; these are
  // the other eight, lifted from the same builders (pricing/color/size/hour/
  // weekday/priceDist/marginCat/trend), so every chart id the page renders is
  // driven per palette and judged by the hue its own builder line names.
  pricing: paint({ type: 'bar',
    data: { labels: ['میانگین قیمت', 'میانگین هزینه', 'میانگین سود'], datasets: [
        { label: 'تومان', data: [480000, 300000, 180000], backgroundColor: [tone.sunshine, tone.sky, tone.mint] }]},
    options: baseOpts({ valueFormat: 'money', plugins: { legend: { display: false } } })
  }),
  colors: paint({ type: 'bar',
    data: { labels: ['مشکی', 'سفید'], datasets: [{ label: 'تعداد', data: [30, 12], backgroundColor: tone.lavender }]},
    options: baseOpts({ valueFormat: 'count', plugins: { legend: { display: false } } })
  }),
  sizes: paint({ type: 'bar',
    data: { labels: ['۸ سال', '۱۰ سال'], datasets: [{ label: 'تعداد', data: [18, 9], backgroundColor: tone.persimmon }]},
    options: baseOpts({ valueFormat: 'count', plugins: { legend: { display: false } } })
  }),
  hours: paint({ type: 'bar',
    data: { labels: ['۸', '۱۲', '۲۰'], datasets: [{ label: 'فروش', data: [1000, 4000, 2500], backgroundColor: tone.sunshine }]},
    options: baseOpts({ valueFormat: 'money', plugins: { legend: { display: false } } })
  }),
  weekdays: paint({ type: 'bar',
    data: { labels: ['شنبه', 'جمعه'], datasets: [{ label: 'فروش', data: [3000, 1500], backgroundColor: tone.sky }]},
    options: baseOpts({ valueFormat: 'money', plugins: { legend: { display: false } } })
  }),
  priceDist: paint({ type: 'bar',
    data: { labels: ['زیر ۵۰۰ هزار', 'بالای ۲ میلیون'], datasets: [{ label: 'تعداد', data: [40, 10], backgroundColor: tone.lavender }]},
    options: baseOpts({ valueFormat: 'count', plugins: { legend: { display: false } } })
  }),
  marginCat: paint({ type: 'bar',
    data: { labels: ['پسرانه', 'دخترانه'], datasets: [{ label: 'حاشیه سود ٪', data: [42, -8], backgroundColor: [tone.mint, tone.candy] }]},
    options: baseOpts({ valueFormat: 'percent', plugins: { legend: { display: false } } })
  }),
  trend: (function () {
    // tintColour as the builder reaches it (window.tintColour on the page);
    // the harness scope has no window, so the same rgba is spelled here.
    const tint = (hex, alpha) => {
      const h = hex.replace('#', '');
      const c = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
      return 'rgba(' + c.join(', ') + ', ' + alpha + ')';
    };
    return paint({ type: 'line',
      data: { labels: ['1/1403', '2/1403'], datasets: [
          { label: 'درآمد', data: [5000, 7000], borderColor: tone.candy, backgroundColor: tint(tone.candy, 0.1), fill: true, tension: 0.3 },
          { label: 'سود', data: [2000, 2600], borderColor: tone.mint, backgroundColor: tint(tone.mint, 0.1), fill: true, tension: 0.3 }
      ]},
      options: baseOpts({ valueFormat: 'money' })
    });
  })(),
};

process.stdout.write(JSON.stringify({
  bars: paint({ type: 'bar', options: { valueFormat: 'money' },
    data: { labels: ['الف', 'ب', 'پ'], datasets: [{ label: 'فروش', data: [1500000, 2400000, 900000], backgroundColor: tokens['--candy'] }] } }),
  line: paint({ type: 'line', options: { valueFormat: 'percent' },
    data: { labels: ['a', 'b', 'c', 'd'], datasets: [{ label: 'حاشیه', data: [10, 25, 42, 33], borderColor: tokens['--mint'] }] } }),
  doughnut: paint({ type: 'doughnut', options: { valueFormat: 'count' },
    data: { labels: ['یک', 'دو', 'سه', 'چهار'], datasets: [{ data: [50, 30, 15, 5], backgroundColor: tokens['--sky'] }] } }),
  dense: paint({ type: 'bar', options: { valueFormat: 'money' },
    data: { labels: Array.from({ length: 30 }, (_, index) => String(index + 1)), datasets: [{ label: 'فروش', data: many, backgroundColor: tokens['--mint'] }] } }),
  horizontal: paint({ type: 'bar', options: { indexAxis: 'y', valueFormat: 'count' },
    data: { labels: ['مشکی', 'سفید'], datasets: [{ label: 'تعداد', data: [30, 10], backgroundColor: tokens['--lavender'] }] } }),
  trendLine: paint({ type: 'line', valueFormat: 'money',
    data: { labels: ['1/1403', '2/1403', '3/1403'], datasets: [{ label: 'درآمد', data: [100, 300, 200], borderColor: tokens['--candy'] }] } }),
  doughnutGeom: paint({ type: 'doughnut', options: { valueFormat: 'count' },
    data: { labels: ['الف', 'ب'], datasets: [{ data: [75, 25], backgroundColor: tokens['--sky'] }] } }),
  // ── the print cycle, driven on its own canvas ──────────────────────────
  // The chart is built once (one canvas, one remembered config); then the
  // renderer's own listeners repaint it for paper and put the screen palette
  // back. The three fills of the same bar are the whole evidence.
  tone,
  printCycle: (function () {
    const canvas = {
      parentElement: { clientWidth: 420, clientHeight: 240 },
      clientWidth: 420,
      style: {},
      getContext: () => context2d(),
    };
    canvases.length = 0;
    canvases.push(canvas);
    const config = { type: 'bar', options: { valueFormat: 'money' },
      data: { labels: ['الف', 'ب'], datasets: [{ label: 'فروش', data: [100, 200], backgroundColor: tokens['--candy'] }] } };

    rects.length = 0;
    new sandbox.window.Chart(canvas, config);
    const fill = () => {
      const bars = rects.filter((rect) => rect.w > 20);
      return bars.length ? bars[0].fill : null;
    };
    const screenFill = fill();

    rects.length = 0;
    printListeners['beforeprint']();
    const printFill = fill();

    rects.length = 0;
    printListeners['afterprint']();
    const restoredFill = fill();

    return { screenFill, printFill, restoredFill,
             hooked: 'beforeprint' in printListeners };
  })(),
  paletteCharts,
}));
"""


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="a JavaScript engine is needed to drive the renderer")


@needs_node
def test_the_renderer_paints_the_numbers_on_the_marks(tmp_path):
    script = tmp_path / "paint.js"
    script.write_text(RENDER_HARNESS, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(script), str(RENDERER)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    painted = json.loads(result.stdout)

    # Three wide bars get all three of their numbers, and millions read as the
    # shop says them out loud.
    assert "1.5م" in painted["bars"]["text"]
    assert "2.4م" in painted["bars"]["text"]
    assert "900,000" in painted["bars"]["text"]
    assert "الف" in painted["bars"]["text"]

    # Every point of a line that fits is labelled, the latest month included.
    assert "42٪" in painted["line"]["text"] and "33٪" in painted["line"]["text"]

    # A ring names every slice, counts it and shares it out — colour is the one
    # thing it does not need — and the hole says what the ring adds up to.
    assert "یک — 50 (50٪)" in painted["doughnut"]["text"]
    assert "چهار — 5 (5٪)" in painted["doughnut"]["text"]
    assert "100" in painted["doughnut"]["text"]

    # Thirty bars cannot carry thirty numbers: the highest keeps its label beside
    # the axis figure, and the caption carries the rest.
    assert [text for text in painted["dense"]["text"] if text.endswith("م")] == ["9م", "9م"]

    # A horizontal bar is read to its end, where its number is.
    assert "30" in painted["horizontal"]["text"] and "10" in painted["horizontal"]["text"]


@needs_node
def test_the_bar_axes_read_right_to_left_like_the_page(tmp_path):
    """The first category sits at the right, the way a Persian reader scans.

    The page is RTL from ``<html dir="rtl">`` down, but a canvas inherits none
    of that: the bars laid category 0 at the left edge the way an LTR chart
    does, so «پرفروش‌ترین رنگ» opened with its best seller in the position a
    Persian reader reaches last. The flip is a claim about geometry, so the
    proof is geometry — the renderer is driven with known categories and the
    painted rectangles are located, not the words it wrote.
    """
    script = tmp_path / "geometry.js"
    script.write_text(RENDER_HARNESS, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(script), str(RENDERER)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    painted = json.loads(result.stdout)

    # A vertical bar per category, coloured with the first palette tone: the
    # bar rects are told apart by their order in the paint stream, which is
    # dataset order — category 0 first. The legend's 8×8 swatch carries the
    # same colour, so only rects of bar width count.
    bars = [rect for rect in painted["bars"]["rects"]
            if rect["fill"] == "#f07a91" and rect["w"] > 20]
    assert len(bars) == 3, bars
    first_category, second_category = bars[0], bars[1]
    assert first_category["x"] > second_category["x"], (
        "category 0 must sit to the right of category 1 on a vertical bar chart")

    # The value labels follow their bars: «1.5م» belongs to category 0, so its
    # text must be painted right of «2.4م»'s.
    label_positions = {call["text"]: call["x"] for call in painted["bars"]["texts"]}
    assert label_positions["1.5م"] > label_positions["2.4م"], label_positions

    # Category names sit under their own groups, mirrored with the bars.
    assert label_positions["الف"] > label_positions["ب"] > label_positions["پ"], label_positions

    # A horizontal chart grows leftwards from the right edge with the names in a
    # right-hand gutter: the category names sit right of the plot's bars.
    horizontal = painted["horizontal"]
    name_calls = [call for call in horizontal["texts"] if call["text"] in ("مشکی", "سفید")]
    bar_rects = [rect for rect in horizontal["rects"] if rect["fill"] == "#a66cff" and rect["w"] > 20]
    assert len(name_calls) == 2 and len(bar_rects) == 2
    assert min(call["x"] for call in name_calls) > max(rect["x"] + rect["w"] for rect in bar_rects), (
        "horizontal category names must sit in a right-hand gutter beside the bars")
    # The gutter is wide enough for the names it holds: every name lies fully
    # inside the canvas (the harness's fake width is 7px per character on a
    # 420px canvas), so reserving room on the wrong side cannot hide here.
    for call in name_calls:
        assert call["x"] + len(call["text"]) * 7 <= 420, name_calls
    # Both bars share one baseline at their right end — the zero the axis sits
    # on, at the plot's right edge — and the longer bar is the one reaching
    # further left.
    bars_by_length = sorted(bar_rects, key=lambda rect: rect["w"], reverse=True)
    baseline = bars_by_length[0]["x"] + bars_by_length[0]["w"]
    assert abs(baseline - (bars_by_length[1]["x"] + bars_by_length[1]["w"])) <= 1, bar_rects
    assert baseline > 300, bar_rects
    assert bars_by_length[1]["x"] > bars_by_length[0]["x"], bar_rects


@needs_node
def test_the_time_axis_and_the_ring_read_right_to_left_like_the_page(tmp_path):
    """The two remaining LTR habits, proven by geometry like the bars were.

    The bar arc turned the category axes round but left two charts drawing the
    way an LTR chart does. A line laid its first — oldest — point at the left
    edge, so «روند درآمد» ran time leftwards-to-right and the newest month, the
    one the owner reads the chart for, landed where a Persian reader's eye
    finishes. A ring swept clockwise from the top, putting its first slice into
    the positions a Persian reader reaches last, and its legend anchored at the
    canvas's left edge while every other legend on the page anchors right.
    """
    script = tmp_path / "geometry.js"
    script.write_text(RENDER_HARNESS, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(script), str(RENDERER)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    painted = json.loads(result.stdout)

    # The line: three points, data 100 → 300 → 200, named 1/1403, 2/1403,
    # 3/1403. Direction is read through the *names*: the oldest month must sit
    # right of the middle one. A max/min reading of the coordinates alone is
    # symmetric — it cannot tell left-flowing time from right-flowing — so the
    # words carry the direction, the coordinates carry the geometry.
    positions = label_positions(painted["trendLine"])
    assert positions["1/1403"] > positions["2/1403"], positions
    assert positions["2/1403"] > positions["3/1403"], positions
    # The polyline itself runs the same way, not just its names: the first
    # point drawn — data index 0, the oldest month — sits right of the last
    # point the stroke reaches. A revert of only the stroke loops (leaving the
    # names mirrored) would split the line from its labels; this sees it.
    # The axes stroke first, so the polyline is the run from its own moveTo on.
    moves = [i for i, point in enumerate(painted["trendLine"]["strokes"]) if point["op"] == "move"]
    polyline = painted["trendLine"]["strokes"][moves[-1]:]
    stroke_xs = [point["x"] for point in polyline]
    assert stroke_xs[0] > stroke_xs[-1], stroke_xs

    # The ring: two slices, 75/25. Each slice's boundary is drawn with the
    # counterclockwise flag set, and where the bulk of the first slice lands is
    # decided by its midpoint angle: counter-clockwise out of the top puts it in
    # the left half — the half a Persian reader reaches first — while the old
    # clockwise sweep put it in the right.
    slice_arcs = [arc for arc in painted["doughnutGeom"]["arcs"] if arc["r"] > 50]
    assert len(slice_arcs) == 4, painted["doughnutGeom"]["arcs"]  # two boundaries per slice
    first_boundary = slice_arcs[0]
    assert first_boundary["ccw"], first_boundary
    mid = (first_boundary["start"] + first_boundary["end"]) / 2
    assert math.cos(mid) < 0, (first_boundary, mid)
    # …and the second slice fills the right half, so the ring really did flip.
    second_boundary = slice_arcs[2]
    mid2 = (second_boundary["start"] + second_boundary["end"]) / 2
    assert math.cos(mid2) > 0, (second_boundary, mid2)

    # The legend: swatches anchor at the right edge, mirroring drawLegend, and
    # the first entry is the top row — the row the eye lands on.
    swatches = [rect for rect in painted["doughnutGeom"]["rects"] if rect["w"] == 8 and rect["h"] == 8]
    assert swatches and all(rect["x"] + rect["w"] > 400 for rect in swatches), swatches
    tops = sorted(swatches, key=lambda rect: rect["y"])
    first_texts = [call for call in painted["doughnutGeom"]["texts"] if call["text"].startswith("الف")]
    # A row's text baseline sits 9px below its swatch's top; the next row is
    # 16px away, so 9 identifies the same row and nothing else.
    assert first_texts and abs(first_texts[0]["y"] - (tops[0]["y"] + 9)) < 1.5, (first_texts, tops)


# The tokens the renderer reads off the page — the full set, paper pair
# included. A palette sample that misses one would hand that read to the
# harness's #333333 fallback and the paint below would be the harness's
# colour wearing the theme's name.
RENDERER_TOKENS = (
    "--candy", "--sky", "--sunshine", "--mint", "--lavender", "--persimmon",
    "--rule", "--ink", "--ink-soft", "--paper", "--paper-ink",
)

# Every palette the shell ships, the whole matrix: the theme walk has checked
# token *names* on rendered pages since the harness existed, but the renderer
# was driven per palette from a two-theme sample — enough to catch a
# constant-coloured renderer, not enough to catch one palette's accent being
# the only hue a shape ever gets. So every palette drives the doughnut and the
# line now, and a ring fill or a series stroke that does not come from the
# palette driving it fails here by name.
PALETTE_SAMPLES = tuple(THEMES)


def _drive_renderer(tmp_path, with_palette=True):
    """Run the harness once, optionally with the first sample palette's tokens.

    Returns the JSON the harness painted. One place so the default-token runs
    (geometry, print cycle) and the per-palette runs share the load path.
    """
    script = tmp_path / "palette_paint.js"
    script.write_text(RENDER_HARNESS, encoding="utf-8")
    command = [shutil.which("node"), str(script), str(RENDERER)]
    if with_palette:
        tokens = THEMES[PALETTE_SAMPLES[0]]["tokens"]
        missing = [token for token in RENDERER_TOKENS if not tokens.get(token)]
        assert not missing, (PALETTE_SAMPLES[0], missing)
        palette_file = tmp_path / f"{PALETTE_SAMPLES[0]}.json"
        palette_file.write_text(json.dumps({token: tokens[token] for token in RENDERER_TOKENS}), encoding="utf-8")
        command.append(str(palette_file))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_the_charts_paint_each_palette_s_own_colours_and_read_rtl_in_both(tmp_path):
    """The theme matrix walks templates; it has never drawn a chart.

    The renderer reads every colour through ``getComputedStyle``, and the
    harness answers those reads from one hard-coded token set — so a chart
    could have gone theme-blind and every test here would stay green. This
    drives the same renderer with every catalogue's own tokens, light and dark
    and high-contrast alike, and asks two independent things of each run: the
    paint is the palette's own (shading), and the geometry is the page's own
    (bars, months and ring sweeping the way a Persian reader scans) —
    direction proved per palette, so it cannot be a light-theme accident the
    dark palette never took.
    """
    palettes = {}
    for theme_id in PALETTE_SAMPLES:
        tokens = THEMES[theme_id]["tokens"]
        missing = [token for token in RENDERER_TOKENS if not tokens.get(token)]
        assert not missing, (theme_id, missing)
        palettes[theme_id] = {token: tokens[token] for token in RENDERER_TOKENS}

    # Every run is judged against its own palette's exact hues below, so a
    # constant-coloured renderer fails in every run whose candy is not that
    # constant — no cross-palette uniqueness premise needed, and palettes
    # that legitimately share a hue (pos-focus and operations-light do) cost
    # nothing.

    outputs = {}
    for theme_id, tokens in palettes.items():
        palette_file = tmp_path / f"{theme_id}.json"
        palette_file.write_text(json.dumps(tokens), encoding="utf-8")
        script = tmp_path / "palette_paint.js"
        script.write_text(RENDER_HARNESS, encoding="utf-8")
        result = subprocess.run(
            [shutil.which("node"), str(script), str(RENDERER), str(palette_file)],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        outputs[theme_id] = json.loads(result.stdout)

    default_candy = "#f07a91"  # the harness's own set: also midnight's real candy
    for theme_id, tokens in palettes.items():
        painted = outputs[theme_id]

        # Shading — bars carry the palette's own first accent, exactly, and
        # nothing else: not a neighbouring token, not a fallback.
        bar_fills = {rect["fill"] for rect in painted["bars"]["rects"] if rect["w"] > 20}
        assert bar_fills == {tokens["--candy"]}, (theme_id, bar_fills)

        # The ring fills only from the tokens its dataset named.
        ring_fills = set(painted["paletteCharts"]["doughnut"]["arcFills"])
        assert ring_fills and ring_fills <= {tokens["--sky"], tokens["--mint"]}, (
            theme_id, ring_fills)

        # The single-accent ring and the trend line name their palette too:
        # the doughnut's every slice and the line's every stroke come from the
        # catalogue driving the run, never from another theme or the harness
        # default. (Slice boundaries share the fill via strokeStyle; strokes
        # 0–1 are the axes, so the series strokes start at index 2.)
        single_ring = set(painted["doughnutGeom"]["arcFills"])
        assert single_ring == {tokens["--sky"]}, (theme_id, single_ring)
        series_strokes = {s.upper() for s in painted["trendLine"]["lineStrokes"][2:]}
        assert series_strokes == {tokens["--candy"].upper()}, (theme_id, series_strokes)

        # The neutrals follow the palette too — in the empty state most of
        # all, where the axis rule is all there is to see.
        assert painted["paletteCharts"]["bare"]["strokeStyles"] == [tokens["--rule"]], (
            theme_id, painted["paletteCharts"]["bare"]["strokeStyles"])

        # Direction — category 0 at the right on a vertical chart, the oldest
        # month at the right edge of the trend, the ring's first slice in the
        # half a Persian reader reaches first. Each proven per palette.
        bars = [rect for rect in painted["bars"]["rects"]
                if rect["fill"] == tokens["--candy"] and rect["w"] > 20]
        assert len(bars) == 3, bars
        assert bars[0]["x"] > bars[-1]["x"], (theme_id, bars)

        positions = label_positions(painted["trendLine"])
        assert positions["1/1403"] > positions["3/1403"], (theme_id, positions)

        slice_arcs = [arc for arc in painted["doughnutGeom"]["arcs"]
                      if arc["r"] > 50]
        assert slice_arcs and slice_arcs[0]["ccw"], (theme_id, slice_arcs)
        mid = (slice_arcs[0]["start"] + slice_arcs[0]["end"]) / 2
        assert math.cos(mid) < 0, (theme_id, slice_arcs[0], mid)

        # ── the dashboard's monthly reading, chart by chart ────────────────
        # The strip quotes four real charts of سود و زیان (dailyChart,
        # categoryChart, tierChart, customerSegChart — the ids its builder
        # reads). Each is driven here in the shape the page builds it, and
        # judged by the hues the page's own builder line names — exactly, per
        # palette, so a hue that stopped following its theme fails by name.
        charts = painted["paletteCharts"]
        daily_bars = {rect["fill"] for rect in charts["readingDaily"]["rects"] if rect["w"] > 2}
        assert daily_bars == {tokens["--candy"], tokens["--mint"]}, (theme_id, daily_bars)
        assert set(charts["readingCategory"]["arcFills"]) <= set(tone_palette(tokens)), (
            theme_id, charts["readingCategory"]["arcFills"])
        assert {tokens["--candy"], tokens["--sky"]} <= set(charts["readingCategory"]["arcFills"]), (
            theme_id, charts["readingCategory"]["arcFills"])
        tier_fills = {rect["fill"] for rect in charts["readingTier"]["rects"] if rect["w"] > 2}
        assert tier_fills == {tokens["--lavender"], tokens["--sunshine"], tokens["--sky"]}, (
            theme_id, tier_fills)
        assert set(charts["readingSegments"]["arcFills"]) <= set(tone_palette(tokens)), (
            theme_id, charts["readingSegments"]["arcFills"])
        assert {tokens["--lavender"], tokens["--sunshine"]} <= set(charts["readingSegments"]["arcFills"]), (
            theme_id, charts["readingSegments"]["arcFills"])

        # ── the page's remaining canvases, judged the same way ───────────────
        # Each is held to exactly the hue its own builder line names on
        # analytics.html, so a builder that reached for a stranger's token —
        # or a palette that couldn't supply one — fails by name here.
        pricing_fills = {rect["fill"] for rect in charts["pricing"]["rects"] if rect["w"] > 2}
        assert pricing_fills == {tokens["--sunshine"], tokens["--sky"], tokens["--mint"]}, (
            theme_id, pricing_fills)
        for key, token in (("colors", "--lavender"), ("sizes", "--persimmon"),
                           ("hours", "--sunshine"), ("weekdays", "--sky"),
                           ("priceDist", "--lavender")):
            fills = {rect["fill"] for rect in charts[key]["rects"] if rect["w"] > 2}
            assert fills == {tokens[token]}, (theme_id, key, fills)
        margin_fills = {rect["fill"] for rect in charts["marginCat"]["rects"] if rect["w"] > 2}
        assert margin_fills == {tokens["--mint"], tokens["--candy"]}, (theme_id, margin_fills)
        # The trend line's strokes come from the palette. (The offline renderer
        # paints a line from borderColor alone — its fill/tension options are
        # inert here — so the stroke hues are the whole judgment.)
        trend_strokes = {s.upper() for s in charts["trend"]["lineStrokes"][2:]}
        assert trend_strokes == {tokens["--candy"].upper(), tokens["--mint"].upper()}, (
            theme_id, trend_strokes)


def label_positions(painted):
    """Text call positions by content, for assertions that reason in words."""
    return {call["text"]: call["x"] for call in painted["texts"]}


def tone_palette(tokens):
    """The page's six accents in the order ``themeTones`` lays them out — the
    palette a doughnut built with ``tone.palette`` fills from."""
    return [tokens["--candy"], tokens["--sky"], tokens["--sunshine"],
            tokens["--mint"], tokens["--lavender"], tokens["--persimmon"]]


def test_the_colour_size_matrix_is_a_matrix_and_adds_up(db_session):
    """The heatmap is read to decide what to restock, so it has to be the data.

    The matrix is one query over the sales of a period, and a query that groups
    nothing is answered by SQLite with a *single synthesised row*: an arbitrary
    colour, an arbitrary size, and the total quantity of everything. The table
    still drew — one column, one row, a number in the cell — so it read as a
    matrix with a clear answer, and the answer was wrong for every shop that had
    ever sold two colours of anything. On a shop that had sold nothing it printed
    «None» where the labels go, which is how the empty-shop pass found it.

    So this pins the readings to the sales they claim to summarise: every colour
    and size that sold is in the table, every cell is that variant's own quantity,
    the cells add up to the units sold, and both margins agree with the other
    reading of the same sales — the colour and size breakdowns the page draws
    beside it.
    """
    from datetime import datetime, timedelta, timezone

    from models import Product, ProductVariant, Sale, SaleItem
    from services.analytics import get_color_size_matrix, get_variant_stats

    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=1), now + timedelta(days=1)
    product = Product(name="تیشرت تست", category="تست")
    db_session.add(product)
    db_session.flush()

    sold = {("مشکی", "S"): 3, ("مشکی", "M"): 1, ("سفید", "S"): 2, ("سفید", "M"): 4}
    for (colour, size), quantity in sold.items():
        variant = ProductVariant(product_id=product.id, price=100_000, cost_price=50_000,
                                 stock_quantity=10, size=size, color=colour,
                                 barcode=f"MATRIX-{colour}-{size}", is_active=True)
        db_session.add(variant)
        db_session.flush()
        sale = Sale(total_amount=100_000 * quantity, final_amount=100_000 * quantity,
                    payment_method="cash", payment_confirmed=True, created_at=now)
        db_session.add(sale)
        db_session.flush()
        db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant.id,
                                quantity=quantity, unit_price=100_000, unit_cost=50_000,
                                total_price=100_000 * quantity))
    db_session.commit()

    matrix = get_color_size_matrix(db_session, start, end)
    assert set(matrix["colors"]) == {"مشکی", "سفید"}, matrix["colors"]
    assert {row["size"] for row in matrix["rows"]} == {"S", "M"}, matrix["rows"]

    cell = {(colour, row["size"]): row["cells"][matrix["colors"].index(colour)]["qty"]
            for row in matrix["rows"] for colour in matrix["colors"]}
    assert cell == sold, cell
    assert sum(cell.values()) == sum(sold.values()) == 10
    assert matrix["max"] == 4

    # The two readings of the same sales cannot disagree: the matrix's margins are
    # the breakdowns drawn beside it.
    for attribute, axis in (("color", "column"), ("size", "row")):
        totals = {row["label"]: row["quantity"]
                  for row in get_variant_stats(db_session, start, end, attribute)}
        if attribute == "color":
            drawn = {colour: sum(row["cells"][matrix["colors"].index(colour)]["qty"]
                                 for row in matrix["rows"]) for colour in matrix["colors"]}
        else:
            drawn = {row["size"]: sum(c["qty"] for c in row["cells"]) for row in matrix["rows"]}
        assert drawn == totals, (axis, drawn, totals)

    # …and a period with no sales has no matrix at all, rather than one cell of
    # nothing: the page says the range has no colour or size recorded.
    empty = get_color_size_matrix(db_session, now + timedelta(days=10), now + timedelta(days=11))
    assert empty == {"colors": [], "rows": [], "max": 0, "max_revenue": 0}


@needs_node
def test_a_printed_chart_is_repainted_for_paper_and_restored_after(tmp_path):
    """The canvas is pixels fixed at screen time — so the page repaints it.

    A stylesheet can recolour the page for paper, but not a canvas: printing a
    dark palette as painted would hand the owner a page-sized black rectangle
    that costs a cartridge and reads as nothing. So the renderer listens for
    the print events, repaints every chart it drew in the paper tones — the
    theme's own hues laid onto paper, never the default palette's colours —
    and puts the screen palette back afterwards, so a theme switch never
    shows through a stale canvas.
    """
    script = tmp_path / "print.js"
    script.write_text(RENDER_HARNESS, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(script), str(RENDERER)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    painted = json.loads(result.stdout)
    cycle = painted["printCycle"]

    assert cycle["hooked"], "the renderer registered no print hooks"
    # The same bar, three moments: the theme's hex on screen, a paper tint of
    # that same hue while printing, and the theme's hex back after.
    assert cycle["screenFill"] == "#f07a91", cycle
    assert cycle["printFill"] != cycle["screenFill"], cycle
    assert cycle["printFill"].startswith("rgb("), cycle
    channels = [int(part) for part in cycle["printFill"][4:-1].split(",")]
    # A tint of #f07a91 over white: red-dominant and unmistakably the theme's
    # hue on paper — (248, 195, 206) exactly, computed the same way here —
    # never a fallback palette's colour, never near-black.
    assert channels == [248, 195, 206], cycle
    assert cycle["restoredFill"] == cycle["screenFill"], cycle


def test_the_report_can_be_printed_from_the_page_itself():
    """A report nobody can trigger prints for no one.

    The print affordances are part of the page, not a browser-menu accident:
    the button sits in the page header, the paper carries a heading naming the
    period (a staple travels without a URL), and the stylesheet ships the
    report's own print rules.
    """
    html = (ROOT / "templates" / "admin" / "analytics.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

    # The print action is the header partial's — one button for every page —
    # so the page no longer carries its own.
    assert 'onclick="window.print()"' not in html
    # The paper heading is the shared one: the page names its period into
    # `print_heading`, and the page-header partial draws it.
    assert "print_heading" in html
    assert "{% if print_heading %}" in (ROOT / "templates" / "partials" / "page_header.html").read_text(encoding="utf-8")
    # The heading is hidden on screen and shown on paper — it exists for print.
    assert ".print-heading { display: none; }" in css
    assert ".print-heading { display: block !important; }" in css
    # The screen-only controls card is excluded from paper: the analytics
    # block's own hide-list names it (chrome — breadcrumb, admin-nav — is the
    # shell block's business now, so it no longer appears here).
    assert ".filter-bar, .search-form, .tip-icon, .screen-only" in css


def test_chart_engine_is_vendored_and_never_a_cdn(client, db_session, authed):
    """The shop renders charts with no internet: the engine ships with the app.

    A CDN script tag would make every analytics load depend on sanctions,
    outages and upstream renames — so the page may reference only files the
    shop itself serves, and the vendored bundle must actually be served.
    """
    page = client.get("/admin/analytics")
    assert page.status_code == 200
    assert "/static/vendor/echarts.min.js" in page.text
    assert "/static/js/analytics-charts.js" in page.text
    assert "cdn.jsdelivr" not in page.text
    assert "unpkg.com" not in page.text
    assert "cdn.jsdelivr.net/npm/echarts" not in page.text

    vendor = client.get("/static/vendor/echarts.min.js")
    assert vendor.status_code == 200
    assert "echarts" in vendor.text.lower()


def test_pilot_charts_mount_through_the_bridge_with_a_fallback(client, db_session, authed):
    """Daily + trend build ECharts options via the bridge, or fall back to the
    custom renderer when the vendored script is blocked — never a blank chart.
    The fallback grows its own canvas inside the host div, carrying the same
    accessible name, because SVG mounts into a container and a canvas element
    cannot take children."""
    page = client.get("/admin/analytics").text
    assert "AC.create(document.getElementById('dailyChart')" in page
    assert "AC.create(document.getElementById('trendChart')" in page
    assert "valueFormatter" in page  # hover values, the pilot's whole point
    # The custom-renderer fallback stays inside the else branch for both,
    # painting canvases it grows itself.
    assert "function hostCanvas(id)" in page
    assert page.count("new Chart(hostCanvas(") == 12


@needs_node
def test_the_bridge_formats_speaks_paper_and_stays_still_on_request(tmp_path):
    """The ECharts bridge is executed, not just shipped: Persian groupings,
    RTL axes, a fast entrance that vanishes under reduced-motion, and a paper
    rebuild that swaps colours without touching data."""
    script = tmp_path / "bridge.js"
    script.write_text(
        "global.window = { addEventListener() {},"
        " matchMedia: (q) => ({ matches: global.__REDUCED || false }) };\n"
        "global.document = { documentElement: {} };\n"
        "global.getComputedStyle = () => ({ getPropertyValue: (n) => ({"
        " '--candy': '#C65D3A', '--sky': '#3E7CB1', '--sunshine': '#D9A441',"
        " '--mint': '#3E9B6B', '--lavender': '#7C6BB0', '--persimmon': '#C65D3A',"
        " '--ink': '#222', '--ink-soft': '#666', '--rule': '#ddd',"
        " '--paper': '#ffffff', '--paper-ink': '#1a1a1a',"
        "}[n] || '') });\n"
        + (ROOT / "static/js/analytics-charts.js").read_text() + "\n"
        "const AC = global.window.AnalyticsCharts;\n"
        "const out = { money: AC.money(2500000), compact: AC.compact(2500000), };\n"
        "const base = AC.base(false);\n"
        "out.tooltip = base.tooltip.trigger; out.legend = base.legend.bottom;\n"
        "out.motion = base.animationDuration;\n"
        "out.xInverse = AC.categoryAxis(['a'], false).inverse;\n"
        "out.yPosition = AC.valueAxis(false, true).position;\n"
        "const tones = AC.tones(true);\n"
        "out.paperInk = tones.ink; out.paperBar = tones.candy;\n"
        "global.__REDUCED = true;\n"
        "out.stillMotion = AC.base(false).animationDuration;\n"
        "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    result = subprocess.run([shutil.which("node"), str(script)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    bridge = json.loads(result.stdout)
    assert bridge["money"] == "۲,۵۰۰,۰۰۰ تومان"
    assert bridge["compact"] == "۲.۵ م"
    assert bridge["tooltip"] == "axis"
    assert bridge["legend"] == 0
    assert bridge["motion"] == 300
    assert bridge["stillMotion"] == 0
    assert bridge["xInverse"] is True
    assert bridge["yPosition"] == "right"
    assert bridge["paperInk"] == "rgb(26, 26, 26)"
    assert bridge["paperBar"] == "#C65D3A"


@needs_node
def test_the_digit_switch_is_one_choke_point_for_every_formatter(tmp_path):
    """Latin axes on demand: the same formatters, the other script."""
    script = tmp_path / "digits.js"
    script.write_text(
        "global.window = { addEventListener() {}, CHART_DIGITS: 'latin',"
        " matchMedia: () => ({ matches: false }) };\n"
        "global.document = { documentElement: {} };\n"
        "global.getComputedStyle = () => ({ getPropertyValue: () => '' });\n"
        + (ROOT / "static/js/analytics-charts.js").read_text() + "\n"
        "const AC = global.window.AnalyticsCharts;\n"
        "console.log(JSON.stringify({ money: AC.money(2500000),"
        " grouped: AC.grouped(1234567), percent: AC.percent(42.5) }));\n",
        encoding="utf-8",
    )
    result = subprocess.run([shutil.which("node"), str(script)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    latin = json.loads(result.stdout)
    assert latin["money"] == "2,500,000 تومان"
    assert latin["grouped"] == "1,234,567"
    assert latin["percent"] == "42.5٪"


@needs_node
def test_the_rendered_config_script_parses(client, db_session, tmp_path):
    """The ECharts builders the page ships must parse: twelve builders plus
    twelve fallbacks in one inline script is exactly where a dropped brace
    would hide, and no eye reviews generated commas."""
    from tests.test_roles import _session_as, _staff

    user, password = _staff(db_session, "config-owner", "owner")
    _session_as(client, user, password)
    html = client.get("/admin/analytics").text
    blocks = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    config = next(b for b in blocks if "AC.create(document.getElementById" in b)
    assert config.count("AC.create(document.getElementById") == 16
    assert config.count("new Chart(hostCanvas(") == 12
    assert "function hostCanvas(id)" in config
    for canvas in ("mixChart", "discountChart", "heatChart", "staffChart"):
        assert f"getElementById('{canvas}')" in config, canvas
    script = tmp_path / "analytics-config.js"
    script.write_text(config, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), "--check", str(script)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_goal_is_set_cleared_and_read_on_the_sales_tab(client, db_session, authed):
    """A monthly target with nowhere to set it is a wish; the tab states it."""
    from urllib.parse import unquote_plus
    from tests.conftest import csrf_token
    from models import Settings
    token = csrf_token(client, "/admin/analytics")
    assert client.post("/admin/analytics/goal", data={"csrf_token": token, "amount": "10000000"},
                       follow_redirects=False).status_code == 303
    assert db_session.query(Settings).filter(Settings.key == "sales_goal_amount").one().value == "10000000"
    page = client.get("/admin/analytics").text
    assert "هدف ماه" in page
    assert "10,000,000 تومان" in page

    bad = client.post("/admin/analytics/goal", data={"csrf_token": token, "amount": "-5"},
                      follow_redirects=False)
    assert "صفر یا بیشتر" in unquote_plus(bad.headers["location"])
    assert client.post("/admin/analytics/goal", data={"csrf_token": token, "amount": ""},
                       follow_redirects=False).status_code == 303
    assert db_session.query(Settings).filter(Settings.key == "sales_goal_amount").one().value == "0"
    cleared = client.get("/admin/analytics").text
    assert "<h4>هدف ماه</h4>" not in cleared
    assert "ثبت هدف ماه" in cleared


def _seed_sale(db_session, amount, days_ago=0):
    from datetime import datetime, timedelta, timezone
    from models import Sale
    sale = Sale(total_amount=amount, final_amount=amount, payment_method="cash",
                payment_confirmed=True,
                created_at=datetime.now(timezone.utc) - timedelta(days=days_ago))
    db_session.add(sale)
    db_session.commit()
    return sale


def test_new_builders_overlays_and_heatmap_ride_on_real_figures(client, db_session, authed):
    """Average, overlay, moving average, mix, heat and goal all read the shop."""
    _seed_sale(db_session, 4_000_000, days_ago=1)
    _seed_sale(db_session, 6_000_000, days_ago=40)
    from tests.conftest import csrf_token
    token = csrf_token(client, "/admin/analytics")
    client.post("/admin/analytics/goal", data={"csrf_token": token, "amount": "100000000"},
                follow_redirects=False)
    page = client.get("/admin/analytics").text
    assert "markLine" in page and "میانگین" in page
    assert "دوره قبل" in page
    assert "میانگین ۳ ماهه" in page
    assert "ترکیب روش پرداخت" in page
    assert "سهم انواع تخفیف" in page
    assert "تقویم فروش سال" in page
    assert "AC.link(dailyChart" in page
    assert "AC.link(catChart" in page and "AC.link(tierChart" in page
    assert "data-matrix-mode" in page
    assert "هدف ماه" in page
    # Hour axis spans live hours only; matrix cells carry both readings.
    assert "heatChart" in page and "heatWeeks" in page


def test_heatmap_weeks_open_on_saturday_and_carry_revenue(db_session):
    """Columns read like the wall calendar: شنبه first, every cell honest."""
    from services.analytics import get_year_heatmap
    heat = get_year_heatmap(db_session)
    assert heat["weeks"] >= 52
    assert heat["max"] >= 0
    first_week = [c for c in heat["cells"] if c["week"] == 0]
    assert first_week and first_week[0]["dow"] == 0
    assert first_week[0]["day_name"] == "شنبه"
    assert all(set(c) == {"date", "dow", "day_name", "week", "revenue"} for c in heat["cells"])


def test_inventory_tab_holds_turnover_abc_and_dead(client, db_session, authed):
    """The sixth tab answers «what runs out, what never moves, what matters»."""
    from models import Product, ProductVariant, Sale, SaleItem
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    product = Product(name="کالای انبار")
    db_session.add(product)
    db_session.flush()
    fast = ProductVariant(product_id=product.id, price=200_000, cost_price=100_000,
                          stock_quantity=2, size="M", color="مشکی",
                          barcode="TURN-1", is_active=True)
    slow = ProductVariant(product_id=product.id, price=200_000, cost_price=100_000,
                          stock_quantity=60, size="L", color="سفید",
                          barcode="TURN-2", is_active=True)
    db_session.add_all([fast, slow])
    db_session.flush()
    sale = Sale(total_amount=2_000_000, final_amount=2_000_000, payment_method="cash",
                payment_confirmed=True, created_at=now)
    db_session.add(sale)
    db_session.flush()
    db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, variant_id=fast.id,
                            quantity=10, unit_price=200_000, unit_cost=100_000,
                            total_price=2_000_000))
    db_session.commit()

    page = client.get("/admin/analytics?tab=inventory").text
    assert 'data-tab="inventory"' in page
    assert "چقدر مانده" in page
    assert '<span class="badge badge-danger">سفارش</span>' in page
    assert "تحلیل ABC" in page
    assert "کالای مرده" in page
    # …while the product tab no longer carries Pareto or the dead.
    product_page = client.get("/admin/analytics?tab=product").text
    assert product_page.split('data-tab="inventory"')[0].count("تحلیل ABC") == 0


def test_turnover_pace_and_reorder_flag(db_session):
    """Ten a month with sixty on the shelf is not urgent; one left is."""
    from datetime import datetime, timedelta, timezone
    from models import Product, ProductVariant, Sale, SaleItem
    from services.analytics import get_turnover
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=30), now
    product = Product(name="کالای چرخش")
    db_session.add(product)
    db_session.flush()
    urgent = ProductVariant(product_id=product.id, price=100_000, cost_price=50_000,
                            stock_quantity=2, barcode="PACE-1", is_active=True)
    calm = ProductVariant(product_id=product.id, price=100_000, cost_price=50_000,
                          stock_quantity=60, barcode="PACE-2", is_active=True)
    db_session.add_all([urgent, calm])
    db_session.flush()
    for variant, qty in ((urgent, 10), (calm, 10)):
        sale = Sale(total_amount=100_000 * qty, final_amount=100_000 * qty,
                    payment_method="cash", payment_confirmed=True, created_at=now)
        db_session.add(sale)
        db_session.flush()
        db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant.id,
                                quantity=qty, unit_price=100_000, unit_cost=50_000,
                                total_price=100_000 * qty))
    db_session.commit()

    turnover = get_turnover(db_session, start, end)
    by_barcode = {r["variant_id"]: r for r in turnover["rows"]}
    assert by_barcode[urgent.id]["reorder"] is True
    assert by_barcode[calm.id]["reorder"] is False
    assert by_barcode[urgent.id]["days_left"] == 6.0
    assert turnover["low_count"] == 1
    assert turnover["rows"][0]["variant_id"] == urgent.id  # most urgent first


def test_each_tab_reads_only_its_own_queries(client, db_session, authed, monkeypatch):
    """Twenty queries for one visible tab was the waste; a tab switch is a
    reload, so nothing inactive is ever missed — just read on its own visit."""
    import services.analytics as analytics_module
    import routers.analytics as analytics_router

    def _refuse(*args, **kwargs):
        raise AssertionError("inactive tab query ran")

    monkeypatch.setattr(analytics_module, "get_color_size_matrix", _refuse)
    monkeypatch.setattr(analytics_module, "get_year_heatmap", _refuse)
    assert client.get("/admin/analytics?tab=sales").status_code == 200

    monkeypatch.setattr(analytics_module, "get_daily_revenue", _refuse)
    assert client.get("/admin/analytics?tab=product").status_code == 200


def test_staff_names_cashiers_and_parks_the_rest(client, db_session, authed):
    """Cashiers read off their shifts; the shift-less stand apart, unnamed by guess."""
    from datetime import datetime, timezone
    from models import CashSession, Sale, StaffUser
    from services.analytics import get_staff_performance
    from tests.conftest import csrf_token
    from tests.test_roles import _session_as, _staff
    cashier, password = _session_as_cashier(db_session)
    _session_as(client, cashier, password)
    token = csrf_token(client, "/admin/cashbox")
    client.post("/admin/cashbox/open", data={"opening": "500000", "csrf_token": token},
                follow_redirects=False)
    _seed_sale(db_session, 1_000_000)
    shift = db_session.query(CashSession).filter(CashSession.status == "open").one()
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    sale.cash_session_id = shift.id
    db_session.commit()
    _seed_sale(db_session, 2_000_000)

    performance = get_staff_performance(db_session,
                                        datetime.now(timezone.utc).replace(hour=0, minute=0),
                                        datetime.now(timezone.utc))
    assert len(performance["rows"]) == 1
    assert performance["rows"][0]["revenue"] == 1_000_000
    assert performance["unattributed"]["revenue"] == 2_000_000
    owner_token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": owner_token}, follow_redirects=False)
    page = client.get("/admin/analytics?tab=sales").text
    assert "عملکرد فروشندگان" in page
    assert "نامشخص (بدون شیفت)" in page


def _session_as_cashier(db_session):
    from tests.test_roles import _staff
    return _staff(db_session, "analytics-cashier", "cashier")


def test_returns_name_variants_reasons_and_rate(client, db_session, authed):
    """What came back, how much, and why — or an honest empty section."""
    from models import Product, ProductVariant, Sale, SaleItem
    from datetime import datetime, timezone
    from tests.conftest import csrf_token
    now = datetime.now(timezone.utc)
    product = Product(name="کالای برگشتی")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=300_000, cost_price=150_000,
                             stock_quantity=5, barcode="RET-1", is_active=True)
    db_session.add(variant)
    db_session.flush()
    db_session.flush()
    sale = Sale(total_amount=600_000, final_amount=600_000, payment_method="cash",
                payment_confirmed=True, is_refunded=True, refund_amount=600_000,
                refund_reason="سایز نشد", created_at=now)
    db_session.add(sale)
    db_session.flush()
    db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant.id,
                            quantity=2, unit_price=300_000, unit_cost=150_000,
                            total_price=600_000))
    db_session.commit()

    page = client.get("/admin/analytics?tab=sales").text
    assert "مرجوعی‌ها" in page
    assert "سایز نشد" in page
    assert "کالای برگشتی" in page

    for kind in ("hourly", "weekday", "trend", "margin", "basket", "staff", "returns"):
        response = client.get(f"/admin/analytics/export?kind={kind}")
        assert response.status_code == 200, kind
        assert "text/csv" in response.headers["content-type"], kind


def test_chart_digits_toggle_is_charts_only_and_sticks(client, db_session, authed):
    """Latin axes on demand; Persian everywhere else, untouched."""
    from tests.conftest import csrf_token
    from models import Settings
    token = csrf_token(client, "/admin/analytics")
    assert client.post("/admin/analytics/digits", data={"csrf_token": token, "mode": "latin"},
                       follow_redirects=False).status_code == 303
    assert db_session.query(Settings).filter(Settings.key == "chart_digits_latin").one().value == "1"
    assert "window.CHART_DIGITS = 'latin'" in client.get("/admin/analytics").text
    assert client.post("/admin/analytics/digits", data={"csrf_token": token, "mode": "fa"},
                       follow_redirects=False).status_code == 303
    assert "window.CHART_DIGITS = 'fa'" in client.get("/admin/analytics").text


def test_export_doors_are_buttons_and_tips_open_for_everyone(client, db_session, authed):
    """Six labelled buttons, not six bare links — and «?» works by mouse,
    touch and keyboard, not by hover alone."""
    import re
    page = client.get("/admin/analytics").text
    for kind in ("daily", "hourly", "weekday", "basket", "staff", "returns"):
        assert re.search(
            r'<a class="btn btn-sm btn-ghost" href="/admin/analytics/export\?kind='
            + kind + r'[^"]*"><svg', page), kind
    tips = re.findall(r'<span class="tip-icon"[^>]*>', page)
    assert len(tips) >= 20
    assert all('tabindex="0"' in tag and 'role="button"' in tag for tag in tips)
    assert "aria-expanded" in page and "pinned" in page
    # The bubble every trigger writes into: deleted once by a template edit
    # while 48 triggers kept pointing at it, killing every tip silently.
    assert 'id="globalTip"' in page


# Fake DOM + assertions wrapping the real tip script (see test below).
_TIP_FAKE_DOM = """
function __makeTipEl(tag, cls, tipText) {
    var listeners = {};
    return {
        tagName: tag, className: cls || '',
        _tip: tipText || null, _expanded: 'false',
        style: {},
        getAttribute: function (n) {
            if (n === 'data-tip') return this._tip;
            if (n === 'aria-expanded') return this._expanded;
            return null;
        },
        setAttribute: function (n, v) { if (n === 'aria-expanded') this._expanded = v; },
        addEventListener: function (t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
        blur: function () { (listeners['blur'] || []).forEach(function (fn) { fn({}); }); },
        getBoundingClientRect: function () { return { left: 100, top: 200, width: 22, height: 22 }; },
        _fire: function (t, e) { (listeners[t] || []).forEach(function (fn) { fn(e || {}); }); },
        closest: function () { return null; },
    };
}
var __tipIcon = __makeTipEl('span', 'tip-icon', 'متن راهنما');
var __tipBox = { style: {}, textContent: '', offsetWidth: 200, offsetHeight: 60 };
var __docListeners = {};
var document = {
    querySelectorAll: function (sel) {
        if (sel === '.tip-icon') return [__tipIcon];
        if (sel === '.tip-icon[aria-expanded="true"]') return __tipIcon._expanded === 'true' ? [__tipIcon] : [];
        return [];
    },
    querySelector: function () { return null; },
    getElementById: function (id) { return id === 'globalTip' ? __tipBox : null; },
    addEventListener: function (t, fn) { (__docListeners[t] = __docListeners[t] || []).push(fn); },
};
var window = { innerWidth: 1200, innerHeight: 800, addEventListener: function () {} };
function __fireDoc(t, e) { (__docListeners[t] || []).forEach(function (fn) { fn(e); }); }
"""

_TIP_ASSERTIONS = """
var __results = [];
function __check(name, ok) { __results.push([(ok ? 'PASS' : 'FAIL') + ' ' + name, ok]); }
__tipIcon._fire('mouseenter', { clientX: 300, clientY: 300 });
__check('hover shows', __tipBox.style.display === 'block' && __tipBox.textContent === 'متن راهنما');
__tipIcon._fire('mouseleave', {});
__check('leave hides', __tipBox.style.display === 'none');
__tipIcon._fire('click', { stopPropagation: function () {}, target: __tipIcon });
var __pinnedOpen = __tipBox.style.display === 'block';
__tipIcon._fire('click', { stopPropagation: function () {}, target: __tipIcon });
__check('tap toggles', __pinnedOpen && __tipBox.style.display === 'none');
__tipIcon._fire('click', { stopPropagation: function () {}, target: __tipIcon });
__fireDoc('click', { target: { closest: function () { return null; } } });
__check('outside click closes', __tipBox.style.display === 'none');
__tipIcon._fire('click', { stopPropagation: function () {}, target: __tipIcon });
__fireDoc('keydown', { key: 'Escape' });
__check('escape closes', __tipBox.style.display === 'none');
__tipIcon._fire('focus', {});
__check('focus shows', __tipBox.style.display === 'block');
var __failed = 0;
__results.forEach(function (r) { console.log(r[0]); if (!r[1]) __failed++; });
if (__failed) throw new Error(__failed + ' tip behaviour(s) broken');
"""


@needs_node
def test_tip_state_machine_shows_hides_and_toggles(tmp_path):
    """The shipped explainer script is executed, not trusted: hover shows,
    leave hides, tap pins, anywhere-else/Escape closes, focus shows."""
    import re as _re
    src = (ROOT / "templates/admin/analytics.html").read_text(encoding="utf-8")
    script = _re.search(r"<script>\n// One explainer bubble.*?</script>", src, _re.DOTALL).group(0)
    harness = tmp_path / "tip-harness.js"
    harness.write_text(
        _TIP_FAKE_DOM + script[len("<script>"):-len("</script>")] + _TIP_ASSERTIONS,
        encoding="utf-8",
    )
    result = subprocess.run([shutil.which("node"), str(harness)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "FAIL" not in result.stdout


def test_junk_category_spellings_read_as_uncategorised():
    """The string "None" typed once must never print as a category called None."""
    from services.analytics import _clean_category
    assert _clean_category(None) == "بدون دسته"
    assert _clean_category("") == "بدون دسته"
    assert _clean_category("None") == "بدون دسته"
    assert _clean_category("  null  ") == "بدون دسته"
    assert _clean_category("—") == "بدون دسته"
    assert _clean_category("پیراهن") == "پیراهن"
    assert _clean_category("  پیراهن  ") == "پیراهن"


def test_bridge_names_jalali_months_and_hides_crowded_ticks(tmp_path):
    """"5/1405" reads «مرداد ۱۴۰۵» on the axis; crowded ticks yield."""
    script = tmp_path / "months.js"
    script.write_text(
        "global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };\n"
        "global.document = { documentElement: {} };\n"
        "global.getComputedStyle = () => ({ getPropertyValue: () => '' });\n"
        + (ROOT / "static/js/analytics-charts.js").read_text() + "\n"
        "const AC = global.window.AnalyticsCharts;\n"
        "console.log(JSON.stringify({ month: AC.monthName('5/1405'),"
        " plain: AC.monthName('مرداد'),"
        " overlap: AC.categoryAxis(['a'], false).axisLabel.hideOverlap }));\n",
        encoding="utf-8",
    )
    result = subprocess.run([shutil.which("node"), str(script)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    months = json.loads(result.stdout)
    assert months["month"] == "مرداد ۱۴۰۵"
    assert months["plain"] == "مرداد"
    assert months["overlap"] is True


def test_charts_render_vector_not_canvas():
    """Sharpness by construction: the bridge asks for SVG, and the vendored
    bundle actually ships the SVG painter — canvas needed DPR bookkeeping
    that blurred on Retina, and zoom reopened it every time."""
    bridge = (ROOT / "static/js/analytics-charts.js").read_text(encoding="utf-8")
    assert "{ renderer: 'svg' }" in bridge
    vendor = (ROOT / "static/vendor/echarts.min.js").read_text(encoding="utf-8")
    assert "renderToSVGString" in vendor and "CanvasRenderer" not in vendor
    assert "containLabel: true" in bridge


def test_matrix_toggle_sits_in_the_heading_row(client, db_session, authed):
    """The qty/مبلغ switch belongs to the card's heading, not floating
    between title and table."""
    import re
    page = client.get("/admin/analytics?tab=product").text
    heading = re.search(
        r'<div class="section-heading-row">\s*<h3>ماتریس.*?</div>\s*</div>', page, re.DOTALL)
    assert heading, "matrix heading row with its toggle"
    assert "data-matrix-mode" in heading.group(0)
