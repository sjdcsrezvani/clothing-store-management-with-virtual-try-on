## 2.5.7 — 2026-09-17

### پوسته: an ink for everything a person reads, a fill for everything a label sits on

- **A link was the brand colour — and the brand colour is not a colour chosen to be read.** `--candy` as text measured 3.36:1 on the card in Kids Boutique and 4.12:1 as `--candy-dark` in Midnight; a white label on the same hue did no better, 3.34:1 on the light palettes and 2.10:1 on the dark one. Every position a person reads now takes a **derivative each palette computes for its own surfaces**: `--link` and `--link-hover` carry the hue with its lightness moved until 4.5:1 holds on the four surfaces text is read on, on the strongest tint of the brand a chip paints beneath it, and on the danger alert — and `--brand-fill`, `--brand-fill-dark`, `--success-fill` and `--success-fill-dark` are derived against the label they carry, both ends of the gradient included. A shop's custom brand re-derives all of them from the two colours it picked. The hue keeps its place in borders, tints, gradients and bars.
- **The same treatment covers the other voices the app speaks in.** A positive figure, a neutral note and a warning were printed in `--mint-dark`, `--sky-dark` and `--sunshine` as they came out of the palette — 3.64:1, 3.77:1 and, on the pale gold of its own banner, 1.84:1. Each is a derived ink now, measured where it lands.
- **The shell is opened without a mouse.** The drawer is *hidden* when closed rather than only slid off screen — a transform leaves every entry of a menu nobody can see in the tab order. Escape closes it and the photo lightbox, handing focus back to the control that opened each; the toggle says what pressing it does next; a skip link is the first thing Tab reaches and lands on a focusable `<main>`; and nothing clickable is a `<div>` or an `<img>` — the lightbox's ✕ and the picture that opens it are buttons.
- **The guards fail rather than let any of this regress.** The state floors are measured palette by palette in all ten, and re-measured for a custom brand; no rule may paint a hue as text or put a label on a hue — the stylesheet, a template's own `<style>` block and a `style="…"` attribute are all read for that pair; an ink may not paint a boundary; an interaction rule may not name a colour of its own; and the link rule is pinned by name. Each rule was sabotaged and caught before it was trusted.

### اعداد: a figure the shop never recorded reads «—», not a nought

- **Ten printed figures answered a column that was never filled with «۰».** The customer list and the customer's own page read «۰ تومان · ۰ خرید» for a customer whose spending counters were never computed; the credit page and the debt column the payment is taken against read «۰ ت» owed; the inventory ledger valued items whose cost was never entered at «۰ ت». A nought is a statement — the opposite of what the data says. A real zero is still a figure and still prints as one; only a missing one says «—», through the new `figure`/`percent`/`pct` filters, and `or 0` on a column that can be empty fails the suite unless the empty column means the nought it prints.
- **A share of a whole that traded nothing is no share.** «حاشیه ۰٪» on the first dashboard of an empty shop was a margin nobody measured; `share` returns `None` where there is no whole, and every reader of it has to say so. The analytics KPIs, the category table that divided by a revenue it had just checked for zero, the day card, the accounting breakdown and the campaign percentages all print the number or the dash.
- **A period that was asked for and could not be read says so.** `?period=banana` used to come back as the whole ledger — five years of trade under a filter that said something else — and one bound plus an empty field read as «everything». `period_range` refuses an unreadable bound, the page shows the notice, the filter's first option no longer pretends it was applied, the credit statement prints the range it was actually drawn for, and the export refuses to widen a range it cannot read rather than handing over a spreadsheet of the wrong period that looks right afterwards.
- **The renderer's vocabulary stopped leaking into the database.** Every optional field of an existing product printed `value="None"` — the guard covered the missing record, not the NULL column — and saving that form stored `None` as the brand, SKU, barcode, garment type, material, season and collection; the variant form fed a NULL cost into the value *and* the `data-real` its calculator reads as a number. Both draw what the column holds now: blank where nothing was recorded.
- **A name a page was never given no longer prints as nothing.** `WatchedUndefined` behaves exactly like Jinja's default — falsy, prints nothing, `is defined` still answers no — but records every name a page *renders* rather than finds missing, and the suite fails on a page that printed one. Asking a question (`{% if error %}`) is not a bug; a gap where a figure belongs is.

### بررسی: the seeded shop's blind spots, walked

- **Two companion passes open every page in the states the seeded matrix never sees.** `test_every_page_survives_an_empty_shop` renders every address, as every role, in all ten palettes, with nothing in the database — the empty branch of every table, ids that match no record. `test_every_page_survives_a_brand_new_install` replays the boot in order — schema, migrations, the additive pass, the backfills, the owner account, the seeded templates — and opens the first page as the owner the boot created, before any palette has been chosen. Both hold every document to the same rule as the seeded matrix.
- **A boot step added later cannot leave the pass describing a first run the app no longer performs.** A guard reads `main.py`'s `lifespan`, resolves its import aliases, and requires every call the boot makes to be replayed by the pass or excused with a reason — mentions do not count, only calls.
- **The walks found live defects on their first run.** The colour × size matrix had no `group_by`, so SQLite answered the aggregate with one synthesised row: four variants sold drew a matrix with one cell holding the total of everything, and an empty shop printed «None» where the colours and sizes go. A page whose job is to list records must now draw 200 with none of them, and no document may print the renderer's own vocabulary — `None`, `undefined`, `nan`, `Infinity` — anywhere the shop can read it, a field's `value` included.

**No schema change.** The database stays on revision 19.

## 2.5.6 — 2026-09-16

### تحلیل فروش: every chart says what it shows, in words and on the marks

- **The charts were read by colour and nothing else.** Twelve canvases whose datasets were told apart by their accents alone, in a panel that changes palette with the shop's theme, and a legend that named the series without naming a single number. A reader who cannot separate those accents, and a page printed in one colour, lost the charts entirely. Each one now carries a **sentence** saying what its marks show, built from the same figures the chart draws so the two cannot disagree, and shown twice: beside the title, and as the canvas's accessible name — a screen reader reads a drawing no better than a printout does.
- **The sentence names the figure it is about, and states a quiet period instead of leaving it blank.** The peak month and how the last *finished* month compared to the one before it (the line ends on the current, incomplete one); the category that carries the sales, with a warning when more than half of them are one category; the busiest and the quietest weekday; the price band most units sold in; a margin chart that says how many categories are losing money. A loss is said in words, because a negative bar is the one figure nobody should have to work out from a red.
- **The renderer writes the number on the marks.** Above each bar and past the end of a horizontal one, on the points of a line, on the axis as its maximum and its zero, and on a ring as a legend that gives every slice its value and its share — with the sum of the ring written in the hole. One formatter serves both the renderer and the labels the page writes itself, so an axis tick and a bar's value cannot say different things. Where the marks are too close together to carry all of them — ninety days of trade, thirty bars — the highest, the lowest and the latest of each series keep their labels and the caption carries the rest, and a mark worth nothing is never labelled: a month with no trade no longer adds a rung of «0» to a flat line.
- **Four figures on this page were mislabelled.** The hour axis called both the morning and the evening «8:00», so a peak at eight was two bars with one name between them. The price distribution divided its buckets by ten thousand and labelled a 300,000 bucket «30ت». The tier axis printed the database's own words — silver, gold, diamond — in a Persian chart, and takes them from the module that owns that vocabulary. And the customer ring's bands read «1m-2m» and «2m+», which is not how the shop says them. The legend was also drawn on top of the category names it sat under.
- **One dead chart went with them.** The profit-by-product block pointed at a canvas the template has not contained for several releases, and the two queries feeding it ran on every page load for a result nothing could reach.
- **The tests pin both halves.** The sentences against the figures they describe, including an empty period and a loss; the page's canvases against the sentences, so a new chart fails until its card shows one and the caption and the canvas's accessible name must be the same string; and the renderer driven for real in a JavaScript engine, with a canvas that records instead of painting, so what it puts on the marks is asserted rather than assumed.

**No schema change.** The database stays on revision 19.

## 2.5.5 — 2026-09-16

### پوسته: every page, as every role, in every palette — checked on what the shop receives

- **The palette is verified on the rendered page now, not on the sources it came from.** The suite opens every address the shell serves — sixty-two of them, read from the app's own route table so a page added tomorrow is in the matrix without anyone remembering to add it — as each of the three roles, with each of the ten palettes active: 1,860 requests and 1,710 rendered documents, each one's own `style` attributes and `<style>` blocks resolved against the palette it was loaded with. It fails on a 5xx for any role, on a page that loads without the theme it was asked for, and on a page that answers differently depending on the palette. The till's long poll is the one address not opened — two seconds of waiting, and JSON when it answers — named in the test with that reason so the gap cannot grow quietly.
- **A stylesheet's `:root` answers only for a document that links the stylesheet.** That is the rule the page below needed, and it is checked rather than assumed: `:root` is deliberately not allowed to answer on a palette's behalf, because letting it do so is exactly how `--persimmon` went missing from nine themes while every page kept rendering a plausible colour — the light theme's value, on a dark card, chosen by nobody. A property only the stylesheet declares, read by a page that loads no stylesheet, fails the suite for all three roles, which was verified by falsification; on every other page it still resolves from `:root` as it always did.
- **The seed holds the pages in the states that carry the attention colour.** The invoice line naming the نسیه debt is drawn only for an unpaid credit sale, the category bar on سود و زیان only when expenses are categorised: a calm shop would have rendered neither, and the missing colour would have shipped with the matrix passing. So the seed carries a نسیه past its due date, a debt over the customer's limit, a failed message, an overdue check, a drawer counted short and a wage paid on the card.

### دست گوشی: the capture page takes the shop's palette, and the two colours it needed

- **`/admin/mobile` was the last screen carrying a palette of its own.** A private `:root` and twenty hex literals meant that a shop on the dark or the high-contrast palette met a daylight page at the counter. It now carries `data-theme`, `data-theme-mode` and the theme's tokens on `<html>` exactly as the shell does; the phone's browser chrome takes the theme's background, the colour scheme follows the dark palette, and every colour in it is one of twenty-one tokens: the header is the shop's own top bar, the capture overlay dims with the `--scrim` the photo lightbox already used, and the log badges take the app's own shapes — ink on an 18–20% tint, and a failure in the attention colour on its own tint. The two colours the script used to set inline are classes.
- **One colour it read was not the theme's to give.** `--button-text` lived only in the stylesheet's `:root`, and a document that does not link the stylesheet cannot resolve it — so `color: var(--button-text)` was invalid at computed-value time and every label on the page inherited the ink instead: measured as ink on the candy button in the light palette, and white on light pink at 2.7:1 in the dark one. Every palette supplies it now, and Custom Brand still derives its own from its primary colour. The bar's text was hard-coded white in three places, so it is named once as `--topbar-text`, and the shell's brand and its two top-bar buttons read it as well — a theme with a light bar answers it in one line and both surfaces follow.
- **The last exemptions are retired.** The phone capture page was the one page excused from «a page may not write a colour of its own», the exemption 2.5.4 had to grant it, and the one page excused from carrying the theme it was loaded with. Both allowlists are gone: no page of the app is exempt from the hex rule any more, and this one is checked in the matrix like the rest.

**No schema change.** The database stays on revision 19.

## 2.5.4 — 2026-09-15

### پوسته: the charts and the photo panels follow the palette the shop chose

- **Every page reads its colours from the theme now, and nothing is left painting the one palette the app shipped with.** تحلیل فروش named all twelve of its chart datasets by hand — the same pink, green, blue, violet, gold and orange written into the bars, the doughnuts, the trend line and the margin chart — and its tooltip was a hard-coded navy, so a shop on the dark or high-contrast preset got dark cards with a daylight chart. Each series now takes the accent that means what it means: sales in candy, profit in mint, the category breakdown in the theme's own accents, a negative margin in candy and a positive one in mint, the size histogram in persimmon. The translucent fill under a line is derived from its own token instead of sitting beside it as a pink-tinted rgba.
- **The renderer behind those charts was the same defect one layer down.** The canvas fallback draws them when the charting library is missing — and in this build it is what draws them — and it carried its own eight-colour palette, its own greys for the axes and the labels, and a grey ring for a doughnut with nothing in it. That is why a page could follow the theme while the pictures on it did not.
- **پرو مجازی no longer keeps a warm cream column down the middle of the dark workspace.** Five beige panels, six beige field borders, a magenta-to-cyan spinner gradient and a white tile on the saved images were all fixed colours; they are `--surface-soft`, `--rule`, `--candy`, `--lavender`, `--sky`, `--card` and `--shadow` now. The photo lightbox needed something the catalogue did not have — a surface that dims rather than brightens, because a light palette's card colour would wash the picture out and a dark one's would disappear into it — so `--scrim` and `--scrim-text` are defined for every theme.
- **The last single colours went with them, including one no stylesheet can reach.** The tag editor's swatch fallback, the till's terminal dot (offline, connected and unreachable in the panel's own soft, success and danger colours), its basket-count pill — which now takes the token that exists for text on the brand colour — the invoice's discount panel and refund field, the customer's avatar circle and the period filter's stale comment. And the browser colour a phone shows above an installed shortcut, the one thing a theme cannot set from CSS, is rendered from the theme's own background, so it stops being the shop's first impression and the last stale colour. The Custom Brand pickers start on the defaults the service itself uses, one source instead of a literal in the page and another in the route.
- **The suite refuses the class of defect rather than the instance.** No page of the shell may write a colour of its own: a hex literal anywhere in a template, or in the scripts beside them, fails with the file, the line and the text. Four exemptions are named with their reasons and re-checked, so an exemption cannot outlive what it was written for — the A4 sheet handed to the printer is paper and stays white in every theme, the example colour code a shop types into a colour-code field is not a style, a comment explaining how a short code expands is not a colour, and the phone capture page, served to a customer's browser with no stylesheet and no theme, is the one page whose palette genuinely cannot come from the shop's.

**No schema change.** The database stays on revision 19.

## 2.5.3 — 2026-09-15

### صندوق: the drawer is counted open, counted closed, and its difference is shown

- **The cash box is a drawer now, not a second profit report.** One page mixed two ideas — a shift, which is a drawer opened with a count and closed with a count, and a period, which is money in and out over a range — into a register whose headline figure could be neither, because it measured a month of movement off the drawer's own float whenever a shift happened to be open. The shift is the page; the period figures stay on سود و زیان.
- **The count is taken blind, and the expected figure is shown to the verifier, not to the counter.** The closing form never displays what the register expects, because a count copied off a number the register printed can never disagree with it; the counter sees the amount and the difference the moment they save, in the result message, and the shift statement is refused with the reason while the shift is still open.
- **The variance was recorded on every close and shown nowhere.** No template mentioned it. A shift now lists its difference in the shop's words (مطابق، کسری، اضافه), opens a **statement** at `/admin/cashbox/sessions/{id}` listing every sale, نسیه receipt, refund, expense, supplier payment and withdrawal behind its number, each linked to the record that produced it, and raises a dashboard card when it is not zero. A shift left open into a later day says so on the page.
- **Money taken out mid-shift is real.** `CashSessionEntry` had existed unused since the schema was written — referenced by a business event, subtracted by nothing. A **برداشت** now needs an amount and a reason, lowers what should be counted without touching profit and loss, can be reversed only while its shift is open, and has its own event. The opener can close their own shift, a manager or the owner can close and verify any, set the default float and see the period register on the same Jalali picker as سود و زیان, and only one drawer can be open at a time — the database refuses a second, not just the route.
- **Four defects came out with it.** A card-paid expense drained the till: supplier payments were filtered by method and expenses were not, so a salary paid by card left the drawer in the period view and not in the shift view — the same money, two answers, on one page. `expected_closing_balance` was computed, put in the event payload and left NULL, so the history and the statement had nothing to read. `Sale.cash_session_id` was assigned only when a sale was voided, so session scoping used time windows for sales and equality for everything else. And the default float accepted a negative number while the open route refused one.

### پوسته: the attention colour every theme was missing, and the breakdown سود و زیان promised

- **A custom property no theme defines does not fall back to a sibling token.** The declaration is dropped at computed-value time and the element inherits its parent's colour, so the page looks deliberate while showing the wrong thing. `--persimmon` was read by eleven declarations — the debt figure, the over-limit line, the 61–90 ageing card, the overdue, expired and failed badges, the SMS error, failed and over-counter lines, the replay difference, the empty value — plus eight inline styles on the invoice, the till, سود و زیان and first-run setup, and **no theme declared it**: every one of them rendered in the ordinary ink colour. It is defined in every preset now, the dark workspace and the high-contrast palette each giving their own value for their own surface, and it is chosen for both roles it plays — text on a card and text on the 20% tint the state badges mix from itself — clearing 4.5:1 in all three modes. It is deliberately not the brand colour, so a debt never reads as something clickable.
- **Two guards, because this one left nothing broken enough to notice.** A theme now fails the suite if it omits any property the stylesheet reads bare, and a second test applies the same rule to the markup; three properties no theme varies are exempt by name rather than excused by the presence of a `:root` fallback. The markup half found the same hole twice more — `analytics.html` read `var(--line)` and `var(--surface)`, defined nowhere, so its table borders and filter select were inheriting their colour.
- **سود و زیان told the owner there were no expenses while showing an expense total.** The route handed the page an empty breakdown, so «هزینهها بر اساس دسته» said «هزینهای در این بازه ثبت نشده است» directly under a real figure and the card above it counted «۰ دسته هزینه». The canonical report carries the categories now, computed under the same filters as the total they sit beside so the breakdown always adds up to it, an expense saved without a category is reported under «بدون دسته» instead of dropped, and the share bar's track is a theme token rather than a hard-coded beige that stayed beige on a dark card.

**One schema change: revision 19.** The migration closes any duplicate open shift as abandoned — without inventing a count it never had — and adds a partial unique index so a second drawer cannot be opened. An existing installation upgrades by starting the app.

## 2.5.2 — 2026-09-15

### پوسته: five categories, a trail on every page, and one name per destination

- **The sidebar, the tab, the heading and the breadcrumb are one registry now.** They were four answers to the same question kept in four places, so they disagreed: «تحلیل فروش» in the menu and «تحلیل مالی» over the page itself, a page carrying two titles at once, six English eyebrows («Financial commitments», «Reminder settings», «Appearance») above Persian pages, and eight English words in a Persian UI. `services/navigation.py` now answers all four — which item owns this path (`active_key_for`), what the page is called (`page_title_for`, also the tab and the `<h1>`), where it sits (`trail_for`) — so the heading and the last crumb print the same variable and a page cannot be named two things. `ROLE_LABELS["cashier"]` was «کارمند» while the staff form assigns «صندوقدار»; it is one word now, and no emoji survives in any title or heading.
- **Which menu item is lit is decided in Python, not by prefix-matching in the browser.** The shell ran `path.indexOf(href) === 0`, so `/admin/settings/appearance` lit up تنظیمات **and** ظاهر فروشگاه at once, ten pages filed under a section lit up nothing at all, and `/admin/` claimed every address beneath it. Longest-prefix matching over declared parents replaced it, destinations match exactly, and the topbar's own pages (the till, the dashboard) can no longer act as prefixes for the whole admin panel. The three measured highlight defects are gone, and a test walks every destination a role can open and asserts exactly one item is current.
- **The categories read the way a shopkeeper reads a day.** Four sections became five — فروش، کالا و انبار، مشتریان و باشگاه، مالی، مدیریت — and the old «ابزارها» drawer, which had collected nine unrelated pages and grew with every addition, is gone: تأمین‌کنندگان now sits with the خرید that uses them, پرو مجازی with selling, and داشبورد left «گزارش و مالی» because the topbar owns it. The till and the dashboard are topbar destinations with their roles declared there, so no category holds a page that is not of its kind, the largest section is six items, and nothing needs hiding behind a dropdown.
- **Every page draws a breadcrumb, and every parent is a page the same role can open.** `templates/partials/page_header.html` prints the trail, the heading and the page's own actions from the registry, so the 42 hand-written headers collapse onto one; a page showing a record — a customer, an invoice, a draft purchase, a staff contract — overrides `page_title` above the include and the crumb follows it. A crumb is a link, so `PARENTS` only ever points at a page the child's roles can open: `/admin/settings/tags` is a manager route and therefore parents to محصولات و موجودی rather than to the owner-only تنظیمات, which would have put a refusing link in a manager's breadcrumb — the defect 2.5.1 removed, reintroduced by the obvious way of doing this. Thirteen footer «بازگشت به داشبورد» buttons are removed with it, the header's crumb being the way back, and the one real action among them (a supplier's نسیه list) was promoted to the header it belonged in.
- **The route guard is the authority on the viewer's role, and now says so.** `require_role` resolved the role from the database on every guarded request but left the session holding whatever it was set at login, so an account promoted or demoted mid-session saw one page drawn for two different viewers — the sidebar for one role, the refusal page naming another. It writes the confirmed role back to the session, so the menu, the cards and the refusal agree with the guard without signing the user out.
- **Two defects came out of walking the real pages rather than reading them.** A purchase list and its draft editor share one address, and collapsing the headers made the editor's title unconditional — so the list was titled «ویرایش پیش‌نویس فاکتور #» with an empty id; title and description follow the job now, with their own trails. And the staff contract page had no trail at all, in the one place nobody had looked because it prints instead of displaying. One assertion was retired rather than kept: «تعهدات مالی» was an English eyebrow over the checks title, and the trail says «مالی / چک‌ها» instead — replaced by a stronger guard that no Latin name survives in anything the registry prints.

**No schema change.** Nothing here adds or alters a column, so an existing installation upgrades by replacing the files — the migration runner has nothing pending for it.

## 2.5.1 — 2026-09-15

### پوسته: only the doors a role may open, and a refusal that reads like a page

- **The sidebar is chosen in Python, and every entry names the role it is for.** It was twenty-five hand-written links with three inline owner checks and nothing at all on the rest, so a cashier was shown تحلیل فروش، تنظیمات، پشتیبان‌ها and گزارش عملیات — and clicking any of them answered with FastAPI's raw `{"detail": …}` in the browser, because the app had no exception handler. The destinations now live in `services/navigation.py` with a `min_role` on each, and the shell draws only what the viewer may open: a cashier is left with «فروش جدید» and «تاریخچه فروش», a manager loses the owner-only tools, and a section whose every item is hidden goes with them rather than leaving a heading over nothing. The check is the same `role_allows` the dashboard's cards use, so the menu cannot offer a door the route will refuse.
- **A refusal is a page, not a payload.** A 403 or a wrong address under `/admin` or `/sales` now renders a Persian card — what happened, **which role is asking** («شما با نقش مدیر وارد شده‌اید.») and a way back to a page that role can use. The handler is registered on Starlette's `HTTPException` rather than FastAPI's, so one definition covers an owner-only route (403) and a URL that matches nothing (404) alike; the framework's own English «Not Found» and the app's generic refusal are recognised and not printed beneath prose that already says both. `/api/*` keeps its JSON contract, so the phone app and any script are unaffected.
- **The invoice and the sales list carried the same defect where the sidebar cannot reach.** Each had a «داشبورد» button in its own footer nav pointing at `/admin`, on a page a cashier can open — the role was offered a link that only refused them. Both are gated like the topbar's button now, and a test opens **every page the till grants** and fails if any of them offers an admin door, which is the one assertion a walk of the sidebar alone could not make.
- **A login lands on a page that role can open.** Everybody used to be sent to `/admin`, so a cashier's first page was a refusal; the cashier now arrives at the till. `role_allows` is the single comparison behind the route guards, the sidebar and the dashboard's cards, so the three cannot drift apart on who may see what.

**No schema change.** Nothing here adds or alters a column, so an existing installation upgrades by replacing the files — the migration runner has nothing pending for it.

## 2.5.0 — 2026-09-15

### باشگاه مشتریان: کاهش سطح becomes a decision, never a sweep

- **Nobody is demoted while the shop is closed.** A 01:00 pass used to take a level away from customers with nobody watching and tell nobody who it had touched — it redirected with a bare count and logged nothing at all. That pass is gone, and the button that ran the same check by hand went with it. `/admin/tier-downgrades` is now the only way a level is ever lost: it lists everyone the rule reaches, longest-quiet first, with the level they are on, the level they would fall to, when they last bought, how long ago that was and a sentence saying why they are on the list. Reading it is a plain GET that demotes nobody; nothing moves until the owner ticks the people they mean and confirms.
- **The rule is one sentence again: «no purchase in N months».** The old rule also read a lifetime «حداقل مبلغ خرید», and it let a recent purchase protect a level only when that *lifetime* figure cleared the bar — so a customer who had bought yesterday could still be demoted. That gate is gone, and the field that fed a value nothing reads is removed from تنظیمات: a field that changes nothing is worse than no field at all. A window of zero now means «I do not want this rule», and both the page and the dashboard card say the rule is off rather than reporting an empty list as if they had checked and found nobody.
- **The form's ids are the only thing trusted.** A page can sit open while the shop keeps trading, so who still qualifies is decided again at the moment of the click: somebody who bought in the meantime is refused with a reason instead of demoted anyway, and every demotion is written to گزارش عملیات with the names — the route it replaces recorded nothing at all. Nothing is pre-ticked and «انتخاب همه» is a separate move from the rows, because this page takes something away rather than giving it.
- **The level vocabulary has one home.** The سطوح, their order and what one step down means now live in `services/tier.py` alone, so the SMS and campaign modules import them instead of each keeping a copy that can drift.

### داشبورد: built for the role that opens it, counted from the pages it links to

- **The role decides what exists, and the decision is in Python.** `/admin` was a manager's page carrying owner-only buttons — its backup, level and reset actions POSTed to owner-only routes, so a manager who pressed one got FastAPI's raw `{"detail": …}` instead of a page — and it counted its own rows, so its figures disagreed with the pages it linked to. The card set now lives in `services/dashboard.py` with a minimum role on each card, and `dashboard_overview(db, role=…)` returns only what that viewer may see, already formatted. A card a manager may not see is never rendered **and never computed**, and the template holds no branch on the role, so no template edit can reveal a margin.
- **What each role sees.** A manager gets «نیازمند توجه», today's and this month's takings, the till, نسیه in circulation, the club's numbers and the month's best sellers. An owner additionally gets today's gross profit, this month's net profit and expenses, the stock's value at cost, the backup state, the periodic work whose own pages are owner-only (پیامک تولد، پیگیری مشتریان، ارتقای سطح، کاهش سطح) and the profit column of «برترین‌ها» — that column is chosen where the row is built, so it does not exist for anyone else.
- **«نیازمند توجه» carries amounts, not just doors.** Four decorative links became cards with real counts — low and out-of-stock variants, card-terminal attempts with no confirmed outcome, credit past its سررسید, checks due and the SMS queue with whether the phone can actually send it, plus the newest backup for an owner — each saying in words when there is nothing to do. The reassuring style is reserved for that row (`is-clear` means «nothing here needs you»), so a day with no sales can never borrow it.
- **A number never wears another period's clothes.** Money is grouped by the span it measures — «امروز» and «این ماه» — so the eye cannot compare a day against a month, and every headline that has a fair comparison states what it is compared against («همین بازه دیروز»، «همین بازه ماه گذشته») and computes its own verdict, because spending less is good news too. A change measured against zero, against a loss, or against a period that had none is reported as no comparison rather than as a four-figure percentage that never happened. «برترین‌ها» is a ranked list instead of a five-column table, the lighter way to show the page's least glanceable information.
- **A count and its list are one number now.** The reconciliation total, the low-stock alert, the follow-up due count, the SMS queue and the newest backup each come from a helper the destination page also calls, so a dashboard figure and the page behind it cannot drift. The reconciliation page was corrected with it: its caption read «از ۲۰۰ رکورد اخیر», true of the windowed list and a lie once the dashboard needed the shop's real outstanding total — the count is now the whole table, and the page says so when it can only list the newest 200. `tier_up_candidates` asks the database once instead of once per customer. Four helpers stay deliberately out of reach (`reconciliation_checks`, `list_backups`, `follow_up_plans`, `get_revenue_summary`), because they verify files, walk every sale or render every message — right for their own pages, wrong for a page opened all day.
- **The danger zone moved to where it is allowed to exist.** Database reset now sits on `/admin/settings`, owner-only like the route it posts to; on the dashboard it was a trap that answered a manager with raw JSON.

**No schema change.** Nothing here adds or alters a column, so an existing installation upgrades by replacing the files — the migration runner has nothing pending for it.

## 2.4.4 — 2026-09-14

### پیامک: a message that can be replayed, a page that shows who is due, and a list you can slice

- **Every message now records what it was built from, not only what it said.** The frozen body says what a customer received; it never said what it was built out of, so an old entry could be read but not audited. `sms_messages.values_json` holds the customer values behind each text — every placeholder the sentence *actually uses*, with the shop's own label and the value of that moment («نام مشتری: سارا»، «بدهی نسیه: ۴۵۰,۰۰۰») — and the history page states the verdict beside it: that replaying the record through the template still produces exactly this text, that the template has been edited since and now yields something else, or that the template is gone while its values remain. The record is self-describing, so it outlives a template being renamed, re-bound or deleted — the same reason the body is frozen in the first place.
- **An empty record and an empty message are two different facts.** `"[]"` says the question was asked and the sentence had no placeholders; an empty string says the row predates the column. The page keeps them apart, so a message sent before the record existed reads «مقادیر این پیامک ثبت نشده» rather than being shown as one that never had any, and a row whose JSON cannot be parsed is logged and labelled instead of blamed on the message — reporting a broken record as «no placeholders» would have been a lie about the message.
- **The senders that render their own body now hand their values over.** `queue_sms` already holds the values when it renders a pattern, so the welcome, birthday, tier-up, campaign and نسیه senders needed no change; the ones that pre-render a string — the manual blast, the purchase trigger, the test send and the pattern send — now pass theirs explicitly, because a record that looked present and was empty would be worse than none. A test drives the real ارسال پیامک form to pin exactly that line.
- **پیگیری مشتریان is a review page now** (`/admin/follow-ups`), the sibling of پیامک تولد and ارتقای سطح. For each follow-up template it lists who is due with the text they would actually receive, how long they have been quiet (longest first) and their tier, and states how many people were left out and why — counted by the same audience test the ارسال پیامک page applies, so the page cannot become a way around consent. `follow_up_candidates` stopped capping its own query at the sweep's limit: a page built on that would have hidden the people it exists to surface and reported a half-scanned shop's skip counts as if they were the whole of it, so the cap moved to the sweep, per template and per pass.
- **The page says out loud that it is not a gate.** A follow-up still leaves on its own, so the review page is offered as an «همچنین» and never as a precondition, and its banner states that each sweep sends at most the سقف and that the list is a snapshot rather than a queue nothing leaves without. Both paths write the same per-purchase reference, so whichever arrives first the customer is asked about one buy once, and the reviewed send re-checks the template and the customers on POST — a form left open while the sweep went past is refused with a reason rather than quietly messaging somebody twice.
- **The پیامک list can be sliced and ordered by how each template has been used.** A row already said when a template last sent something and how many times; the list can now be filtered to هرگز فرستاده‌نشده / فرستاده‌شده / دارای ارسال ناموفق and ordered by last-sent date, newest or oldest first, with the ones that have never spoken at the old end. The filter, the badge on the row and the date order all read the same figure — a message still queued on the phone or refused by the gateway is not a send — so the page cannot contradict itself. The catalogue keeps its category groups until a filter or an order is applied, because a heading would otherwise undo the sort that was asked for; a filter stays applied across a row's own toggle and delete; and a value the page does not understand falls back to «همه» instead of emptying the list.
- **Schema revision 18** adds `sms_messages.values_json` as a purely additive column, with the same entry in the boot-time additive pass for databases that never ran a versioned migration. An existing shop upgrades into exactly the behaviour it had: every past row reads «مقادیر این پیامک ثبت نشده» until new messages carry the record, and no historical text is touched.

## 2.4.3 — 2026-09-14

### پیامک: chips that write the sentence, and a hole the save refuses at the door

- **The editor's chips insert a sentence now, not a token.** Clicking used to leave a bare «%var1%» in the text — a blank to fill in rather than something to say. Each chip now carries the whole sentence a shop actually writes: «%var1% عزیز، سلام! امیدواریم حالتان خوب باشد.»، a birthday wish, a نسیه reminder that names the amount, a tier-up congratulation, an order-ready note and a come-back invitation — plus wording for every built-in, written with the tokens that built-in's own sender really hands over (a campaign's code and percentage, the birthday relationship, the amount owed). The vocabulary lives in `sms_templates.SMS_SENTENCES` with `sentences_for(category)`, so it sits with the rest of the page's language instead of inside the template.
- **A chip cannot hand you a template the save would refuse.** A sentence carrying `%var2%` would be a trap now that an unbound slot is rejected, so each one declares what its tokens read and choosing it fills those dropdowns itself. It never overwrites a value the owner already chose, a built-in has nothing to fill and is simply skipped, and two sentences in a row get the space between them instead of running together.
- **The unbound-slot refusal now covers every custom template, hand-sent ones included.** It used to apply only to a template that had been given a trigger, which left the larger hole open: a text written by hand could be saved with `%var2%` in it and sent to every customer as « عزیز، سفارش شما آماده است» — the owner only ever sees a preview, and the customer is who reads the gap. Saving one is now refused with the reason naming the tokens, and an automatic template gets the extra sentence it has earned: nobody reads it at all before it leaves. A slot the text does *not* use may stay unbound, so the rule cannot creep into a demand to fill all three every time, and the built-ins are untouched because their own senders fill their slots.
- **The refusal writes nothing and describes what was rejected.** Two bugs came out with it. `admin_sms_template_update` assigned the template's name and body *before* validating, so a refused save left a half-edited object in the session for any later autoflush to land — the check now runs before a single field is written and the stored row is provably untouched. And on a refusal the page described the *stored* template, so the warning listed holes the owner had already fixed; the form context now takes the posted bindings and describes the text that was actually turned away.
- **The editor's warning is live rather than frozen.** The unbound tokens are read from the form's own dropdowns and filtered by what the text uses, so typing a token into the body lights the warning immediately — previously the list was baked in server-side from the last saved text, so a newly typed one produced no warning until a reload. The server-seeded duplicate array is gone with it. The rule is also stated in words above the slots, the third dropdown option reads «بدون مقدار (تنها اگر در متن نباشد)» rather than claiming it sends empty, and the warning beside the preview says the template will not be saved.
- **CI refuses a commit message that credits a tool.** A commit message is public the moment it lands, so the `test` job gained a step that walks *every* commit reachable from the push — not just the tip — and fails on a generated-by stamp, a co-author trailer aimed at a bot, or the robot emoji. The `release` job already needs `test`, so a bad message blocks the release as well. The rule is written without naming any product on purpose: a policy that listed the tools it bans would itself record what wrote the code.

## 2.4.2 — 2026-09-14

### پیامک: messages that leave on their own, and a row that says whether one ever has

- **A custom template can now fire by itself.** The editor gained «چه زمانی خودش فرستاده شود؟» with three answers — **فقط دستی** (the default, unchanged), **پس از هر خرید** and **پیگیری پس از چند روز** — so a thank-you after checkout or a nudge to a lapsed customer no longer has to be typed by hand. The manager row, the manager help card, the editor and the send page all read one `send_info()` function, so a template's «خودکار/دستی» badge and the sentence beside it cannot drift from what actually happens.
- **Three rules hold the automatic sends**, because a message that spends money cannot be recalled. *Consent:* the audience page's own test was pulled out into `message_block_reason` and both call it, so an automatic send can never reach somebody the manual page would have skipped. *One message per event:* every automatic message records what it was about in `sms_messages.ref` (`sale:12` for a purchase, `purchase:2026-09-01` for a follow-up), so a retried checkout, a double-click or the five-minute sweep cannot repeat itself — and buying again re-arms the follow-up on its own. *A sale never fails because of SMS:* the purchase trigger runs after the commit and swallows its own errors.
- **The follow-up sweep is throttled, never truncated.** «سقف ارسال خودکار در هر بررسی» (default 50, owner-only on the پیامک page) caps each pass, and whoever is left over goes out on the next one. Only customers who have actually bought before qualify — «پیگیری پس از آخرین خرید» is about a relationship the shop already has, so someone who never bought is reported as «بدون خرید» rather than guessed at. The purchase trigger is transactional (it thanks the customer for their own order) and the follow-up is marketing, so consent, the archive flag and the «بلاک» tag all apply to it.
- **An automatic template may not carry a hole.** A slot the text uses but nothing fills would reach a phone with nobody watching it, so the editor refuses to switch that combination on with the reason spelled out, and the engine re-checks as a last gate for a row that reached the table another way.
- **The row now says whether a template has ever spoken.** Each one shows its total count, the failed subset and the **last-sent date**, and a template that has never sent anything carries a dashed «هنوز ارسال نشده» badge — a forgotten template is visible at a glance instead of reading like a healthy one. The counts are honest: «ارسال‌شده» no longer flatters queued and failed rows, and the date falls back to the row's own creation time for messages recorded before `sent_at` existed.
- **Placeholders are bound by name, not by position.** Moving `%var1%` to the end of a sentence always worked; what did not was *saving* a custom template, which rebuilt its variable list from scratch and silently dropped every slot's binding — the customer's name vanished from a template on its first edit while the preview kept showing a sample. Saving now keeps each slot's source, and each custom slot became a choice rather than a guess: a «مقدار» dropdown over real customer fields (نام، نام خانوادگی، نام و نام خانوادگی، شماره موبایل، سطح باشگاه، امتیاز، تعداد خرید، مجموع خرید، بدهی نسیه، کد معرف، نام فرزند), with a source auto-filling that slot's label and sample. Values arrive formatted the way a message should read — the level as «طلایی» rather than `gold`, money with separators — and the editor says in words that only the token's *name* matters, not its place.
- **The preview stopped lying.** A custom slot bound to nothing no longer previews with a fake sample: it renders blank and the editor names the token that will arrive empty. The same warning now sits on the send page right above the ارسال button, because that is the last place a hole can slip out. Built-in slots keep their samples, since their own senders really do fill them.
- **The send page states how the chosen template fires**, from the same function as the manager: «همین صفحه جای فرستادن آن است» for a hand-sent custom text, a link to the page a manual built-in is normally sent from, and — for one that is already automatic — a warning that sending it here is a separate act that can duplicate a message the shop already sent.
- **پیامک تولد is a review page now** (`/admin/birthdays`). The dashboard's one-click `POST /admin/check-birthdays` queued every wish sight-unseen; the page lists who is due, whose birthday it is, how soon, and who already received this year's wish, and nothing is queued until the owner ticks and confirms. A stale form cannot message someone whose window has passed — they are refused with a reason instead of silently counted — the per-customer, per-occasion marker still guards a double-send, and the send is audit-logged.
- **Schema revision 17** rebuilds `sms_messages`. A database created before these triggers existed carried `ck_sms_message_source` frozen at the senders of that day, so «پس از خرید» and «پیگیری» rows were **rejected outright** at INSERT — an automatic trigger that silently could not record anything it sent. That is the same trap revision 15 rebuilt `business_events` to escape, so the table is rebuilt the same way with the history preserved and every other constraint kept, and the vocabulary moved into code: `log_message` files a source it does not recognise as «دستی» and logs the bug. `sms_messages.ref`, `sms_templates.trigger_key` and `sms_templates.trigger_days` arrive as additive columns, so an existing shop upgrades into exactly the behaviour it already had — every template starts hand-sent.

## 2.4.1 — 2026-09-14

### درگاه پیامک روی همان دستگاه: the VPS is gone, the phone pairs with the shop

- The rented VPS that relayed SMS to the shop's phone is **gone**. The gateway the APK already speaks — `GET /api/v1/device/poll`, `POST /api/v1/device/heartbeat`, `POST /api/v1/device/sms/{id}/result` and `.../delivery` with the `X-Device-API-Key` header — is now served **by the store app itself**, from the same database. One process, one truth about what was sent; nothing to rent, nothing to reach over the internet. The phone only needs to be on the same Wi-Fi as the shop computer (or forwarded to it), and the APK needs **no rebuild**: `vm_url` and `api_key` were always read from the phone's settings at runtime, never baked into the build.
- The desktop launcher now starts a **second listener on port 8101** for the device endpoints. The phone's world stays on its own port on purpose: a firewall rule or port-forward can expose only the gateway, never a single admin page, and a slow device request can never queue behind a sale. Inside the app the endpoints are additionally mounted under `/gateway` so tests and previews exercise the exact same routes.
- **Pairing is one QR.** «جفت‌کردن گوشی» on the پیامک page issues a 43-character key — shown exactly once, as a QR the phone's app scans — and stores only its SHA-256. Re-pairing rotates the key and invalidates the old one; «قطع اتصال» removes the device while leaving queued messages waiting. The gateway card shows the phone's live state (آنلاین / آفلاین / در انتظار اتصال), its last contact, battery level, and the queue's numbers (در صف / دست گوشی / تحویل‌شده) beside the blast ceiling.
- **An offline phone no longer loses messages.** The old VPS API answered 400 when the device was offline, so a birthday wish queued overnight was written ناموفق after five retries and never sent. Now the queue *is* the log: a queued message simply waits, the phone's 15-second poll is the only latency, and the verdict comes from the phone itself. The history page now tells the whole journey — «دست گوشی» (claimed, not yet reported), «تحویل شد» / «نرسید» (the carrier's own report, where the device sends one) — beside the familiar در صف / ارسال‌شده / ناموفق.
- **A crashed phone self-heals.** A claim older than five minutes is released back to the queue by the scheduler, so a phone that died mid-send hands its message to the next poll instead of wedging it forever. Claims are handed out once (a second poll claims nothing), and the phone's own failure text (e.g. «Radio off») is stored on the row.
- The **5-minute scheduler batch no longer gates SMS.** Messages used to wait up to five minutes in `BackgroundJob` before the worker even looked at them, plus the phone's poll. The log row is the queue item now; the remaining `BackgroundJob("sms")` rows from older databases are completed as no-ops, and the other jobs keep their batching.
- The پیامک page's old gateway form (کلید API، آیدی دستگاه) is gone with the VPS; the blast ceiling stays, owner-only, and `qrcode` joins the requirements for the pairing image.

## 2.4.0 — 2026-09-13

### پیامک: the texts on one page, the sends on another, and a log of everything

- The six patterns (خوش‌آمدگویی، تولد، ارتقای سطح gold و diamond، کمپین، یادآوری بدهی) and the gateway credentials left the middle of the general settings form for a dedicated **پیامک** page (`/admin/sms`). The page states which templates are on, what each one carries (length, segment count, how many it has sent and how many failed), and which automatic event fires it. The gateway key, device and blast cap moved there too — the credentials stay owner-only.
- Added **`SmsTemplate`** as the single source of a message. Deleting a built-in is refused (it has a job to do), but it can be switched off — and switching a built-in off writes an **empty** legacy pattern, which is exactly the «off» every existing sender already understood. Nothing could be switched on behind the owner's back either: a built-in whose old settings row was empty is seeded switched off, so an upgrade cannot start sending a birthday wish nobody ever wrote.
- The editor now understands **variables**: every placeholder is a chip you click to insert, with its own label and sample, and the right-hand panel is a live preview that redraws as you type and re-measures the message in characters and segments (70 per segment in Persian). A declared placeholder nobody filled is removed rather than left as a literal `%var2%` on a customer's phone.
- Added **custom templates** — write your own message, name the placeholders, duplicate any existing template as a starting point, and send from it in the manual sender. Deleting is refused once a template has actually sent something, with the count, because the log has to keep pointing at the text that went out.
- Added **`/admin/sms/history`**, the log of every message the shop has sent: automatic (خوش‌آمدگویی، تولد، ارتقای سطح، کمپین، یادآوری نسیه) and manual, with the recipient, the template, the source, the text and the queue's own verdict (در صف / ارسال‌شده / ناموفق) — filterable by status, source, text or recipient and paginated. The body is frozen **when the message is queued**, so editing a template later can never rewrite what a customer already received, and the background worker now writes its result back onto the row instead of leaving it invisible (a retry keeps the row «در صف» until the last attempt fails).
- Added **`/admin/sms/send`**, a manual blast with an audience that is counted before anything is queued: everyone consented, one tier, one tag, a hand-picked list with its own search, or numbers pasted from a contact card. Archived customers, explicit opt-outs and anyone tagged «بلاک» are skipped and reported; a message about the customer's own account (a نسیه reminder, a test) can be sent transactionally without marketing consent, and the whole blast is capped by the store's ceiling.
- The campaign send now reads its template from this page instead of the settings row, so a campaign text that is switched off refuses to send with a sentence in Persian, and every campaign message appears in the log like any other.
- Nothing anywhere sends without being recorded: `queue_sms` writes the rendered body, template, recipient, source and kind to the log for every caller — and the settings page now points at the new page instead of holding six textareas and the gateway key.

## 2.3.5 — 2026-09-13

### کمپین‌ها: an audience, a code, and a discount that lands

- Campaigns now **affect money**. The code always claimed «کدی که مشتریان برای استفاده از تخفیف وارد می‌کنند», but nothing in the sales flow ever read it and `SaleCampaign` was only ever deleted — a campaign could not change a single invoice. The counter now takes a «کد کمپین» beside the referral code, `finalize_basket` computes the discount from the campaign row (never from the client), and every redemption writes `SaleCampaign` and a `CampaignRedeemed` business event, so the report can show what a campaign actually earned.
- A customer who holds a campaign gets it **automatically**: the customer card in checkout names it with the amount it saves, the counter panel shows «کمپین فعال», and the customers list carries a «کمپین» badge with a matching «کمپیندار» filter. Nothing has to be typed for those customers, and a typed code works for everyone else — a bad code is explained and still falls back to the campaign the customer does hold.
- Added **`CampaignAssignment`**, a real per-customer membership row with a state (دعوت‌شده / استفاده‌شده / برداشته‌شده) and the date the SMS went out. It is what the badge, the counter and the campaign report all read, and it makes a double-send impossible: a blast skips anyone this campaign already messaged. A customer can be added by hand from the profile or the campaign page and taken off again without losing the history.
- The send now has a **real audience** instead of a hardcoded tier. It was pinned to `tier == "diamond"`, which reached nobody in a shop of silver customers; the send panel offers everyone consented, one tier, one tag or the hand-picked list, and each option states the number of people it would actually reach — consented, not archived, not already messaged — before anything is queued. Opt-outs, archived customers and re-sends are counted in the result instead of being silently skipped, and the queue result is recorded rather than assumed.
- Added a per-campaign **«چندباره»** mode: the default spends the campaign on the first invoice, and a reusable campaign is a standing promo that applies while it is live. Refunding an invoice no longer burns the customer's campaign — the redemption is reversed and the campaign goes back on offer.
- Rebuilt both pages in the catalogue design language: the list has a heading with the nav merged in, a KPI row (کل، در جریان، پیامک‌شده، تخفیف اعمال‌شده), filters for search/status/sort, a scrollable table with per-row «پیامک‌شده» and «استفاده» ratios, row actions and distinct empty states; the campaign page now opens on a **report** (status, invites, redemptions, revenue, the send panel, the holders with their state and the invoices that used it) with the form moved to `/admin/campaigns/{id}/edit`. The form gained sections, a reusable switch, a live summary of what the campaign will do, and real validation: a duplicate code and a backwards date range are sentences in Persian instead of a 500.
- Deleting a campaign that already sold is refused with the count, because removing it would orphan the invoices it discounted — the page points at deactivating it instead.
- Every colour on these pages comes from theme tokens, with high-contrast and print rules of their own, and the campaign discount follows the rule the rest of the app already had: it stacks with the loyalty discounts up to the basket total, and is refused on نسیه like every other discount.

## 2.3.4 — 2026-09-13

### حساب نسیه: collection and the ageing report are one page

- Merged `/admin/credit` and وصول مطالبات into one screen: the debtors list, the ageing summary, the open invoices and every collection action now live together. The list was rebuilt in the catalogue design language (heading with the nav merged in, a KPI row whose cards link to their own filter, filters for search/status/ageing bucket/sort, 25-per-page pagination and separate empty states for "no debt at all" and "nothing matched"), and it gained an **invoice-level view** the old dashboard could never show — every open فاکتور with its own customer, remainder, سررسید and status. `/admin/collections` is a redirect into the page and has left the sidebar.
- Added a real **سررسید** per نسیه invoice (`sales.credit_due_date`), filled from the store's new «مهلت پرداخت نسیه» setting when the sale is confirmed and shown on the checkout form so the cashier can say the date out loud. Ageing is measured from that date, so an invoice that is late now reads as late even if it is young; invoices the shop never agreed a term for keep ageing by their own date, so no existing debtor jumped buckets when the column appeared, and one opt-in action («ثبت سررسید … روزه برای فاکتورهای باز») stamps a term on the open ones when the owner is ready. The KPI row, the ageing strip, the filters and the statement all read that single rule and cannot disagree about who is late.
- Receipts can now close **one invoice**: the per-invoice «دریافت» form targets that فاکتور (the `Payment.sale_id` column existed but was never honoured), and paying more than that invoice's remainder is refused instead of spilling onto another one. The round «ثبت دریافت» form still settles oldest-first, and a reversal rebuilds every allocation while keeping tagged receipts on their own invoice.
- The receipt record now says **who took the money** (`payments.received_by_id`), where it previously existed only in the event journal.
- Reversing a receipt now asks for a **reason**, which is what the immutable `payment_reversals.reason` stores; it used to be filled with the English constant "Payment reversal" because nothing asked. The reason is shown beside the reversed receipt, and the legacy delete path still works while recording where it came from.
- Added a printable **صورت‌حساب** per customer — invoices, receipts and reversals in one running balance, for the whole period or a date range with a «مانده از قبل» opening line — plus a one-click 🖨️ چاپ view and a footer that states the balance the customer actually owes.
- Added **یادآوری پیامکی** for debt: the owner writes the message pattern in Settings (`var1` name, `var2` amount, `var3` سررسید), sends it per customer or to every overdue debtor in one confirm-guarded action that is capped like a campaign, and a configurable cool-down refuses a second reminder to the same customer too soon. The button says why it is disabled, and transactional reminders are not gated on marketing consent.
- Removed the N+1 behind the old debtors list: it ran a query per debtor (one of them for payment history the page never rendered) and is now a fixed number of queries for the whole page, with the stored debt shown beside the total recomputed from the open invoices and any disagreement flagged as **نامطابق**.
- Every hardcoded colour on these pages is gone — the نسیه styles come from theme tokens, with print and high-contrast rules of their own, so the statement prints clean and the dark and high-contrast themes stay readable.

### Layout fixes

- Fixed the page heading's actions being squeezed by a long description until its buttons wrapped onto a second line — on حساب نسیه «داشبورد» dropped below the other two. The nav is now sized to its own buttons and the description gives up the width, and short nav labels share one width so a set of them reads as a set instead of four differently sized pills.
- Fixed a page that could be scrolled sideways for no visible reason: a card holding a wide table stretched to the table's width instead of letting the table scroll inside it (a 1020px table pushed the whole document to 1081px), and screen-reader-only labels sat at their static position — which inside a horizontally scrolled table is off the page — widening the document by a further 77px.
- Buttons now reserve the same border box whether they draw a border or not: a primary or success button was 4px shorter than a ghost one, so a row mixing them (چاپ next to two link buttons, «اعمال فیلتر» next to «پاک‌کردن فیلتر») sat out of line.
- Stacked form fields no longer leave a double gap: a `.form-row`'s own row gap added to each field's bottom margin, so fields that stacked had 2rem between them where every other field has 1rem.

## 2.3.3 — 2026-09-13

### Who a customer buys for is now their choice, not the store's

- Added a **«این مشتری برای چه کسی خرید می‌کند؟»** choice to the customer record (`buys_for`: «برای خودم» / «برای فرزندم»), asked at signup and editable afterwards. It is the customer's own decision, so one children's shop can serve a parent who records a child's name and birthday and an adult who records their own — on the same form, without a store-wide setting deciding for them.
- The fields follow the choice: «برای خودم» asks for the customer's birthday, «برای فرزندم» asks for the child's name and birthday and takes no customer birthday. Registration, the counter's new-customer step, the counter panel and the admin profile all render one shared partial, so the four cannot drift apart.
- The choice is enforced server-side, not by hiding fields: whichever side a form posts, only the chosen one is written, so a hand-crafted post cannot put a birthday into a profile that does not use it. Switching sides writes the new side and **keeps** the old, so switching back loses nothing.
- The birthday discount, its sale line and its SMS now follow the customer's own choice rather than the store's target — a self-buyer in a kids' shop is wished on their own birthday («تخفیف تولد شما»), and a child-buyer on the child's. Rows that never chose keep following the store default, and a one-time additive backfill pins the ones already holding a child profile to «فرزند» so changing that default can never silently move an existing customer's discount.
- A shop with `child_profile_enabled` off is never asked, because it has only one possible answer; posting the child option there is refused outright.
- The counter panel now shows the birthday the customer is actually wished on, resolved per customer, and the admin customer list's «تولد» heading stays neutral instead of claiming every customer is a child.

## 2.3.2 — 2026-09-12

### Customer club: a real customer record, and a page to open

- Rebuilt `/admin/customers` in the catalogue design language: a page heading with the nav merged in, a KPI row where each card links to the matching filter, real filters (search across name, phone, referral code and child name; tier; tag; status), ten sort orders, 25-per-page pagination with a total, an empty state, and page links that URL-encode their query instead of breaking on a search term with a space or `&`.
- Added the page that was missing: `/admin/customers/{id}` shows a customer's purchase history, the discounts actually applied, who referred whom, the نسیه summary, and a card for editing birthday, tags, a free-text note and SMS consent. Counters stored on the row are shown beside totals recomputed from the sales, and any disagreement is flagged as **نامطابق** instead of being silently trusted.
- Made the customer record describe the customer: their own birthday (`birth_month_day` and `birth_year`, Jalali) is now the primary field, so the birthday discount works for any clothing shop rather than only one selling to children. Child details are an optional module controlled by `birthday_target` (مشتری / فرزند / هر دو) and `child_profile_enabled` in Owner → Settings, defaulting to the previous children's-shop behaviour so nothing changes until a toggle is flipped.
- The birthday discount and its SMS now follow `birthday_target` — previously they read the child's birthday unconditionally, so a shop that celebrates the customer's own birthday got no discount at all, and the sale line always read «تخفیف تولد فرزند» regardless of whose birthday it was.
- Added a child birth year alongside the stored month/day, so a child's age — the size-guide input for a children's shop — can finally be computed.
- Replaced the bare delete with a lifecycle: a customer who has recorded sales is **archived** rather than deleted (deleting nulls the sale's customer and silently detaches purchase history), archived customers leave the list, the counts and the marketing sends, and reappear only under `?status=archived`. Marketing SMS skips archived customers and anyone who opted out.
- Added customer columns for tags, a note, SMS consent, archive state and the customer's own birthday, applied as additive migrations.

## 2.3.1 — 2026-09-12

### Inventory ledger, and a date picker that follows every theme

- Rebuilt `/admin/inventory-movements` in the catalogue design language (heading with the nav merged in, badge pills instead of emoji, theme tokens throughout, a real empty state) and turned it into an audit trail: filters for text/id/barcode, type, direction, product, user and date range, real pagination with a count instead of a silent 200-row cap, and new columns for the balance *after* each movement, the user who recorded it, cost before → new, and links to the sale or purchase behind the row. Pre-2.3.0 purchase rows are marked as historical so old behaviour can't be misread as current.
- The ledger page reports stock it has no rows for (**موجودی بدون سابقه**) and variants whose ledger total disagrees with the balance, and offers one opt-in «ثبت موجودی اولیه دفتر» action that writes an opening row per variant without touching any stock quantity.
- Replaced the jQuery date picker with a dependency-free one drawn entirely from theme tokens, so it follows light, dark and high-contrast instead of a hardcoded purple and white.
- Fixed the picker's defaults and value handling: it opened on a fixed 1403/1 and read Persian digits as `NaN`, so every field holding a date reopened on the wrong month. It now opens on the field's own value (Jalali, dashed or ISO Gregorian), dates are typeable again, selecting fires `input`/`change`, the calendar is fully keyboard-operable with dialog semantics, and it becomes a bottom sheet with comfortable tap targets on narrow screens.
- Added constraints across every date field: start/end pairs cannot cross, birth dates cannot be in the future, and typed input obeys the same limits the calendar enforces. The year list is a rolling window (120 years back, 10 forward, with a 1300 typo floor) so it keeps up as the years pass, and typing a four-digit year anywhere in the popup jumps straight to it.
- Fixed campaigns storing a date 621 years out: their native Gregorian inputs were parsed as Jalali (`2026-09-12` → `2647-12-03`). They now use the shared picker and a parser that reads Jalali and ISO correctly, and their list shows Persian dates.
- Removed roughly 140KB of dead assets: jQuery (whose only consumer was the old picker), its orphaned stylesheet, and the unused `persian-date.min.js`.

## 2.3.0 — 2026-09-12

### Supplier invoices: draft first, with a scoped product picker

- Rebuilt `/admin/purchases` in the catalog design language (page heading, AJAX-free KPI cards, numbered sections, filters, pagination, empty states, a full detail page and a printable receipt), with theme tokens instead of hardcoded colours.
- One invoice covers one supplier: choosing a product is now a searchable picker that lists only the chosen supplier's products, and every product's supplier is set next to its brand on the product form. The server rejects lines belonging to another supplier.
- An invoice is assembled as a **draft** and only finalising it applies the landed cost, books the payable and records an optional first payment. Drafts stay out of the cash register, supplier balances, P&L periods, CSV exports and the reverse-event backfill.
- Quantity and unit price are optional on a line and default to the product's own stock and cost basis, so an invoice can be recorded by picking products alone.
- A purchase records money and cost only: it never changes stock quantities, applies landed cost through an auditable zero-quantity `cost_adjustment` movement, and reversing it restores the previous cost basis and frees its payments.
- The invoice date now drives reporting periods while the recorded date keeps the real entry time, and shipping can be spread into each variant's cost basis or left on the invoice total.
- Fixed the cash register double-counting purchases and supplier payments as the same money: only money that actually left the till counts as cash out.
- Paying a wholesaler is money out however it is handed over, so purchase payments are recorded as till movements instead of asking for cash-versus-card.

## 2.2.3 — 2026-09-12

### Variant page sidebar fix

- The «خلاصه تنوع» card no longer slides underneath the «غیرفعال‌سازی» card while scrolling: the whole side column now sticks to the top as one unit, and returns to normal flow on narrow screens.

## 2.2.2 — 2026-09-12

### Variant colour palette, demand counter, and cost display

- Clicking a کد رنگ field now opens the native colour palette and writes the chosen colour back as a hex code, on the variant edit page and on script-added variant blocks.
- Added a demand counter to the variant page so repeated “do you have this size?” requests can be recorded, logged, and reset.
- Prevented the second (display-only) purchase price from overwriting the real cost basis, including across restarts.

## 2.2.1 — 2026-09-12

### Maintenance release

- Published the completed product catalog and tag-template workflows as the 2.2.1 maintenance release.
- Kept this release focused on packaging and release metadata; no private databases, environment files, or generated runtime data are included.

## 2.2.0 — 2026-09-08

### Product tag templates and catalog workflow

- Added reusable product-level tag templates so each product can use the appropriate tag detail and size.
- Added default-first tag editing with a visible field palette, independent barcode image and code fields, live preview, and saved-template editing.
- Applied assigned product templates to catalog views, product forms, barcode printing, mixed-template A4 output, and print-history snapshots.
- Added safer product and variant validation, duplicate-barcode protection, CSRF coverage, stock/reservation display, and printable tag quantity controls.

## 2.1.0 — 2026-09-08

### Tag editor and barcode reliability

- Added configurable compact or standard barcode density for printable product tags.
- Kept barcode bars and human-readable barcode numbers as independently editable fields.
- Added live barcode preview synchronization, layout diagnostics, overlap warnings, and sticky preview guidance.
- Ensured generated barcode images contain only scanner-readable Code 128 bars and are keyed to the exact variant code.

## 2.0.0 — 2026-09-03

### Switchable store themes

- Added ten store-wide visual themes for operations, POS, boutique, children’s, dark, high-contrast, and custom-brand workflows.
- Added Owner-only Appearance settings with live previews and persistent theme selection.
- Added validated custom brand colors.
- Kept the generated-image logo workflow limited to virtual try-on output; no store logo is rendered in the application shell or theme settings.
- Added theme-aware shared surfaces, navigation, forms, tables, alerts, focus states, and dark-mode styling.
- Replaced professional navigation emoji dependence with local labeled control icons while preserving the Kids Boutique identity.
- Kept receipts and invoices on a clean light print layout.

## 1.1.11

- Unified dashboard totals with canonical financial reporting.
- Corrected period-scoped refunds and inventory reconciliation details.

# Changelog

## v1.1.14 — 2026-09-02

### Transaction-centered business events

- Added an append-only business event journal for checkout, inventory, payments, refunds, credit, purchasing, expenses, cash sessions, POS reconciliation, and database reset actions.
- Added idempotent event appends with redacted payloads, actor/request attribution, legacy-record backfill, and owner-only event history.
- Kept event writes in the same database transaction as the business mutation so failed operations do not leave false history.
- Added server-owned checkout and terminal event coverage for payment requests, outcomes, reservation changes, and completed sales.

## v1.1.13 — 2026-09-02

### Frontend workflows and responsive checkout

- Added a workflow-focused action center to the admin dashboard.
- Added touch-friendly controls, visible keyboard focus, responsive basket behavior, and duplicate-submit protection.
- Added checkout payment status feedback, cash change calculation, and accessible status announcements.
- Added a local offline chart renderer and removed the analytics CDN dependency.
- Added frontend smoke checks for local assets, checkout behavior, accessibility hooks, and offline analytics.

## v1.1.10 — 2026-09-01

### Unified reporting

- Added canonical financial reporting definitions for sales, refunds, cost, profit, cash flow, credit, and inventory value.
- Added owner-facing reconciliation checks for sales, inventory, and customer debt discrepancies.
- Shared the canonical report with accounting and analytics dashboards.


## v1.1.9 — 2026-09-01

### Cash-session ledger ownership

- Added explicit cash-session ownership for new cash sales, refunds, collections, expenses, and supplier payments.
- Added migration support for the new financial links.
- Added immutable expense reversal handling and supplier payment balance validation.
- Added the explicit payment reversal endpoint while retaining the legacy route for compatibility.


## v1.1.8 — 2026-09-01

### Financial ledgers

- Added immutable refund and refund-line records with operator and payment references.
- Added immutable payment reversals without deleting original receipts.
- Added financial ledger entries for refund and reversal events.
- Added cash-session and supplier-payment foundations.


## v1.1.7 — 2026-09-01

### POS reconciliation

- Added provider, terminal, retrieval, masked-card, and timestamp metadata.
- Added operator identity, evidence, and explicit reconciliation resolutions.
- Prevented uncertain payments from being marked paid through the review form.
- Added controlled resolution handling for cancellation, reversal, duplicates, terminal errors, and provider investigations.

## v1.1.6 — 2026-09-01

### Checkout consistency hardening

- Made reservation acquisition conditional on available unreserved stock.
- Prevented normal completion of uncertain terminal payments.
- Froze the persisted basket once terminal payment begins.
- Connected completed checkout refunds to checkout history.
- Added regression coverage for reservation and payment state rules.

## v1.1.4 — 2026-09-01

### Concurrency-safe checkout

- Added server-owned checkout drafts with explicit lifecycle states and history.
- Added temporary stock reservations with automatic expiration and release.
- Replaced checkout stock writes with conditional atomic decrements.
- Bound POS requests to persisted checkout totals and baskets.
- Added idempotent POS retries and rejected completed-checkout replays.
- Added concurrency, reservation, state-transition, and payment-integrity tests.


## v1.1.3 — 2026-09-01

### Database integrity and migrations

- Enabled SQLite foreign-key enforcement for every connection.
- Added database checks for payment methods, monetary values, quantities, POS states, and stock movement types.
- Added versioned schema tracking with upgrade, downgrade, status, and backup helpers.
- Added migration compatibility, backup, foreign-key, relationship, and constraint tests.
- Updated upgrade documentation for the versioned database flow.


## v1.1.2 — 2026-09-01

### Phase 1 hardening

- Completed route authorization coverage for staff roles.
- Disabled sessions are rejected immediately, and role changes apply to active sessions.
- Added legacy-schema migration coverage using an isolated temporary database.
- Refused anonymous audit records for business mutations.
- Removed the transitional administrator flag from authorization and staff models.
- Cleaned public documentation and source comments of internal development references.


## v1.1.1 — 2026-09-01

### Security and authorization

- Added explicit `cashier`, `manager`, and `owner` staff roles.
- Migrated the legacy admin password to the owner staff account.
- Protected checkout, POS, invoices, refunds, inventory, credit, campaigns,
  settings, reports, backups, reset, and virtual try-on routes by role.
- Added owner-only staff management with account disabling.
- Extended audit logs with staff identity, target, IP address, request ID, and
  before/after metadata.
- Added role and authorization regression tests.


## v0.1.0-beta.1 — 2026-09-01

First public beta release of RaiKids Store Management.

### Included

- Barcode-based clothing checkout with cash, card, and credit sales
- Parsian POS terminal integration with approval and cancellation handling
- Durable POS transaction records and reconciliation workflow
- Append-only inventory movement ledger and safe purchase reversals
- Customer profiles, referrals, loyalty points, and tier discounts
- Credit-sales ledger with customer limits and payment tracking
- Expenses, suppliers, cashbox, accounting, analytics, and CSV exports
- Persian invoices in HTML and PDF formats
- Optional SMS gateway integration
- Optional virtual try-on workflow
- macOS and Windows desktop packaging scripts

### Beta limitations

- Role-based staff permissions are not yet implemented; the current release is intended for a trusted local store environment.
- SQLite is intended for a single-store deployment.
- POS provider reversal and automatic reconciliation depend on the terminal/provider protocol.
- Desktop packages are not code-signed in this beta.
