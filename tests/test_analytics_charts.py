"""Every chart on سود و زیان says what it shows without colour.

Three readers were missing from this page. One cannot separate the chart's
accents from each other. One opens it on a phone in daylight. One prints it and
hands it to somebody who has never seen the screen. All three need the numbers
said in words and written on the marks, so these tests pin the sentences to the
figures they describe, pin them to the canvases they sit under, and drive the
canvas renderer itself to see the values it paints.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.chart_notes import CHART_IDS, NO_DATA, chart_notes

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

    A new chart is a new ``<canvas id>``, and this fails until the same list gains
    its sentence and the card shows it — as the visible caption *and* as the
    canvas's accessible name, because a screen reader reads the drawing no better
    than a printout does.
    """
    from tests.test_roles import _session_as, _staff

    user, password = _staff(db_session, "notes-owner", "owner")
    _session_as(client, user, password)
    html = client.get("/admin/analytics").text

    canvases = set(re.findall(r'<canvas id="([^"]+)"', html))
    captions = set(re.findall(r'data-chart="([^"]+)"', html))
    assert canvases, "the analytics page drew no charts at all"
    assert canvases == set(CHART_IDS)
    assert captions == canvases

    for canvas in sorted(canvases):
        pair = re.search(
            r'data-chart="%s">([^<]*)</p><canvas id="%s" role="img" aria-label="([^"]*)"' % (canvas, canvas),
            html,
        )
        assert pair, f"{canvas} has no sentence beside it"
        assert pair.group(1).strip() == pair.group(2).strip()
        assert pair.group(1).strip(), f"{canvas} has an empty sentence"

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
  '--ink': '#2d2d2d', '--ink-soft': '#6b7280',
};

const painted = [];
function context2d() {
  return {
    fillStyle: '', strokeStyle: '', lineWidth: 1, font: '', textAlign: '',
    direction: '', globalCompositeOperation: '',
    setTransform() {}, clearRect() {}, beginPath() {}, closePath() {}, moveTo() {},
    lineTo() {}, stroke() {}, fill() {}, arc() {}, fillRect() {},
    fillText(text) { painted.push(String(text)); },
    measureText(text) { return { width: String(text).length * 7 }; },
  };
}

const sandbox = {
  console,
  document: { querySelectorAll: () => [], documentElement: {} },
  getComputedStyle: () => ({ getPropertyValue: (name) => tokens[name] || '#333333' }),
};
sandbox.window = { devicePixelRatio: 1 };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

function paint(config) {
  painted.length = 0;
  const canvas = {
    parentElement: { clientWidth: 420, clientHeight: 240 },
    clientWidth: 420,
    style: {},
    getContext: () => context2d(),
  };
  new sandbox.window.Chart(canvas, config);
  return painted.slice();
}

const many = Array.from({ length: 30 }, (_, index) => (index === 7 ? 9000000 : 100000));

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
    assert "1.5م" in painted["bars"]
    assert "2.4م" in painted["bars"]
    assert "900,000" in painted["bars"]
    assert "الف" in painted["bars"]

    # Every point of a line that fits is labelled, the latest month included.
    assert "42٪" in painted["line"] and "33٪" in painted["line"]

    # A ring names every slice, counts it and shares it out — colour is the one
    # thing it does not need — and the hole says what the ring adds up to.
    assert "یک — 50 (50٪)" in painted["doughnut"]
    assert "چهار — 5 (5٪)" in painted["doughnut"]
    assert "100" in painted["doughnut"]

    # Thirty bars cannot carry thirty numbers: the highest keeps its label beside
    # the axis figure, and the caption carries the rest.
    assert [text for text in painted["dense"] if text.endswith("م")] == ["9م", "9م"]

    # A horizontal bar is read to its end, where its number is.
    assert "30" in painted["horizontal"] and "10" in painted["horizontal"]
