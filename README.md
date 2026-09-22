# RaiKids POS — سیستم فروش و باشگاه مشتریان

FastAPI + SQLite point-of-sale for a local kids' & teens' clothing shop:
products & variants with barcode tags, checkout with referral / tier / birthday
discounts, customer loyalty (points, gold/diamond tiers, and a shell built for the
role that opens it — a role-aware sidebar and topbar, one name per page with a
trail back to where it lives, a refusal that reads like a page rather than a
payload, and ten palettes every page takes its colours from, down to the charts,
the photo panels and the lightbox), an SMS suite (template manager, manual sends,
welcome / birthday / tier-up / campaign messages, and a send log whose entries
can be replayed), virtual try-on previews on the child's picture, invoices
(HTML + Persian PDF), a deep analytics suite whose charts say what they show in
words and on the marks rather than by colour alone, links and filled button
labels that read at 4.5:1 in every palette — each filled surface deriving its
own label, the loyalty metals and the printed page included, with a stylesheet
that names no colour the theme should own, every native control taking the theme's
accent, form bounds that share one table with the server rules, pages that answer
garbage query parameters with their list instead of a bare 422 (and a bad path
integer with the shop's error page, never raw JSON), CSV exports that honour the
filters the view shows, supplier payments that name the invoice they settle and
are bounded by it server-side, a mobile drawer that traps focus like a modal,
document pages that print as paper in every palette, and loyalty counters
computed from the invoices they came from — with a drift worklist, a bulk view
and reconcile actions when a stored counter disagrees — and accounting-lite: credit sales
(نسیه) with a debt ledger, supplier purchases that update stock & cost,
expenses, a cash drawer counted open and counted closed with every difference
recorded, a net profit & loss report that breaks the expenses down by category,
and CSV exports.

## Security and staff roles

The application uses authenticated staff accounts with explicit roles:

- **Cashier** — create sales, use the POS checkout, and view limited invoices.
- **Manager** — all cashier capabilities plus refunds, customer/loyalty changes,
  inventory, purchases, expenses, credit collections, campaigns, and POS review.
- **Owner** — all manager capabilities plus settings, analytics and exports,
  backups, database reset, and staff account management.

The legacy single-admin password is migrated to the `owner` account on startup.
Create additional accounts from **Admin → Staff**. Disabled accounts cannot log
in. Sensitive actions are recorded with the staff member, target, IP address,
request ID when supplied, and before/after summaries where applicable.

## The dashboard (داشبورد)

`/admin` is built for the role that opens it. The gating happens in Python —
`services.dashboard.dashboard_overview(db, role=…)` returns only the cards that
role may see, already formatted — so a card a manager may not see is never
rendered **and never computed**, and no template edit can reveal a figure to the
wrong person.

- **Manager and owner** — a «نیازمند توجه» row that carries real counts instead
  of decorative links (low and out-of-stock variants, card-terminal attempts
  with no confirmed outcome, credit past its سررسید, checks due, and the SMS
  phone only when it is *not* connected), today's and this month's takings, the
  cash register, outstanding نسیه, the club's numbers, and the month's
  best-selling products.
- **Owner only** — gross profit today, net profit and expenses for the month,
  stock value at cost, the backup state, the periodic work whose review pages
  are owner-only (پیامک تولد, پیگیری مشتریان, ارتقای سطح), and the manual
  tier-downgrade review, which never runs on its own. Only owners receive the profit column of «برترینها».

Each headline comes from the same helper as the page it links to, so a count and
its list cannot disagree — including the reconciliation count, which is the
shop's real outstanding total rather than the newest 200 rows the table below it
can list. Four helpers are deliberately out of the dashboard's reach
(`reconciliation_checks`, `list_backups`, `follow_up_plans`,
`get_revenue_summary`): they verify files, walk every sale or render every
message, which is right for their own pages and wrong for a page opened all day.

The danger zone (database reset) lives on `/admin/settings`, owner-only like the
route it posts to; on the dashboard it was a trap that answered a manager with
raw JSON.

## The shell around the pages

The sidebar is data, not markup. `services.navigation.NAV_SECTIONS` is one list
of destinations, each naming the `min_role` of the page behind it, and the shell
draws only what the viewer may open — a cashier sees «فروش جدید» and «تاریخچه
فروش» and nothing else, a manager loses the owner-only tools, and a section whose
every item is hidden disappears with them rather than leaving a bare heading.
The check is the same `role_allows` the dashboard's cards use, so the sidebar
cannot offer a door the route will refuse. The pages a cashier can open (the
invoice, the sales list, a staff contract) carry no داشبورد button either,
because it would be a refusal drawn for the role reading them.

Five categories, read the way a shopkeeper reads a day: فروش, کالا و انبار,
مشتریان و باشگاه, مالی, مدیریت. They are flat on purpose — the old «ابزارها»
drawer held nine unrelated things and grew every time a page was added, and it
put تأمین‌کنندگان away from the خرید that uses them. The till (فروش جدید) and the
dashboard (داشبورد) belong to no category, so they live in the topbar, which is
also where their roles are declared.

**One name per destination.** The menu, the browser tab, the `<h1>` and the last
breadcrumb crumb are the same string read from the same registry, so «تحلیل فروش»
can no longer be «تحلیل مالی» on its own page. Which item is current is decided in
Python (`active_key_for`), not by matching prefixes in the browser — that is what
used to light up two items at once on `/admin/settings/appearance`, and nothing at
all on the pages filed under a section.

Every page draws `templates/partials/page_header.html`, which prints the trail,
the heading and the page's own actions. A page showing a record — a customer, an
invoice, a draft purchase, a contract — overrides `page_title` above the include
and the crumb follows it. `PARENTS` gives every address the sidebar cannot reach
an owner; a crumb is a link, so `PARENTS` only ever points at a page the child's
own roles can open. `/admin/settings/tags` is a manager route and therefore
parents to محصولات و موجودی, not to the owner-only تنظیمات page, which would have
put a refusing link in a manager's breadcrumb.

The route guard is the authority on the viewer's role: `require_role` writes the
role back to the session when the database disagrees, so a promoted or demoted
account stops seeing one page drawn for two different viewers without being
signed out of it.

Icons and words come from the same place. `icon` in the registry names a symbol in
`base.html`'s sprite, so the menu, the heading and the topbar draw from one set;
no emoji appears in a title or a heading, and no page keeps an English eyebrow
over a Persian one.

A refusal is a page. A 403 or a missing address under `/admin` or `/sales` now
renders `templates/admin/error.html` in Persian — what happened, which role the
viewer holds, and a way back to a page that role can use — instead of FastAPI's
raw `{"detail": …}`. The `/api/*` routes keep their JSON contract, so the phone
app and any script are unaffected.

The shell moves like one app. `static/css/motion.css` owns the motion vocabulary
— duration and ease tokens plus a family of `.t-*` transitions re-authored from
transitions.dev's patterns under house rules (transform and opacity only, exact
properties, asymmetric open/close, a `prefers-reduced-motion` guard per
snippet) — and `static/js/toast.js` upgrades the server flash into a stacked
toast queue adapted from sonner's contract: one live region per shell, timers
that pause while the tab is hidden, and colours taken only from the theme
tokens. `static/js/dialog.js` gives every overlay the same focus, Escape and
Tab-cycle behaviour. Ten palettes ship in the box — «کاشی» (Kashi Tile) leads
them as the default — and a retired palette is never silently swapped: the
appearance page names where its shop's colours moved.

Lists never lose the scroll. `static/js/list_nav.js` gives every list zone
(sort headers, page links, filter chips and in-zone filter forms on eleven
pages) the same behaviour: fetch the URL, swap only the zone, keep the
document — and the scroll — exactly where it was, with the address bar, back
button and focus kept honest and a plain navigation as the fallback. The
products list manages the catalogue from one page: sortable columns on the
shared sorting convention, page-size picker, stock chips reading the same
definition as the KPIs, bulk archive, a filter-honouring CSV export,
search-term highlighting, and expandable variant rows.

## Accounting features (admin panel)

- **📒 حساب نسیه** (`/admin/credit`) — sell on credit at checkout and collect
it here: the debtors list, the ageing report, the open invoices and the
collection actions are one page (the old وصول مطالبات dashboard is a redirect
into it). A نسیه invoice carries its own **سررسید**, prefilled from the store's
payment term (مهلت پرداخت); ageing is measured from that date, and invoices the
shop never agreed a term for keep ageing by their own date — one opt-in action
stamps a term on the open ones when you are ready. Receipts (cash/card) settle
the oldest unpaid invoice first, or you can close **one invoice** directly, which
is refunded back if it exceeds that invoice's remainder. Every receipt shows who
took the money, and a reversal asks for a **reason** that lands in the immutable
ledger. Each customer has a printable **صورت‌حساب** (whole period or a date
range, with an opening balance) and a manual **یادآوری پیامکی** whose text you
write on the **پیامک** page, with a cool-down so nobody is reminded twice in a row. A
credit limit (سقف اعتبار) blocks new نسیه sales once a customer's debt exceeds
it — set a store-wide default in Settings and per-customer overrides on each
customer's credit page.
- **📦 خرید از عمده‌فروش** (`/admin/purchases`) — record what goods cost and what
you owe. Pick a supplier, then choose its products with a searchable picker
(the catalogue is scoped to that supplier, so a product's supplier on its own
page decides where it can be bought). An invoice is assembled as a **draft**
first — quantities and unit prices are optional because each product already
knows its stock and its cost basis — and finalising it is the moment that
applies the landed cost, books the payable and (optionally) records the first
payment. Buying stock never changes stock quantities — inventory is counted on
the product and variant screens — and reversing a purchase puts the cost basis
back and frees its payments.
- **📚 دفتر انبار** (`/admin/inventory-movements`) — the append-only inventory
ledger: every stock change (opening stock, sale, refund, manual adjustment, cost
adjustment) with what the balance *became* at that moment, who recorded it, and
a link to the invoice behind it. Filter by type, direction, product, user and
date; paginated. Because purchases only record money and cost, the ledger is the
single authoritative account of stock movement.
  Stock that existed before the ledger (or that was entered outside a recorded
  flow) has no rows, so the page reports it as **موجودی بدون سابقه** and,
on request, writes one explanatory opening row per variant. That action never
changes a stock quantity — those units are already on the shelf; it only gives
the ledger a row that accounts for them. Any variant whose ledger total
disagrees with its current balance is listed separately and is never corrected
automatically.
- **🏭 تأمین‌کنندگان** (`/admin/suppliers`) — supplier list with total purchased.
- **💸 هزینه‌ها** (`/admin/expenses`) — rent, utilities, wages… with categories and separate one-time/monthly types.
- **🧾 صندوق** (`/admin/cashbox`) — the cash drawer, run as a **shift**. The
person at the counter opens it by counting the float and closes it by counting
what is there, and the difference is recorded — so the page answers the one
question accounting cannot: does the money in the drawer match the money the
till rang up. Cash taken out mid-shift (a bank deposit, a small purchase) is
recorded as a **برداشت** with a reason and lowers what should be counted without
touching profit and loss. The expected figure is deliberately **kept off the
closing form** until the count is in: a count copied off a number the register
printed can never disagree with it, so the verifier sees the figure and the
counter counts blind, seeing it the moment they save. Every shift stays listed
with its difference (مطابق، کسری، اضافه) and opens a **statement** showing the
sale, receipt, refund, expense, supplier payment and withdrawal behind its
number — each linked to the record that produced it. The opener can close their
own shift; a manager or the owner can close and verify any, set the default
float, and see the period register (money in and out for a range, on the same
Jalali picker as سود و زیان). Card and نسیه money is deliberately absent: this
register counts cash, and only one drawer can be open at a time.
- **🧮 سود و زیان** (`/admin/accounting`) — revenue − COGS − expenses = net
profit for any period (today/week/month/year/custom, Persian dates), the expenses
then broken down by the shop's own categories with each one's share of the total
(an expense saved without a category is counted under «بدون دسته» rather than
left out), plus CSV exports of sales, customers, purchases and expenses
(Excel-friendly).

Every date field in the panel shares one Jalali picker: dates are shown and
saved as `YYYY/MM/DD` in Persian digits, a birth date can never be later than
today, a start/end pair can never cross, and typing a four-digit year jumps
straight to it. It carries no JavaScript dependency and is drawn from the active
theme, so it matches light, dark and high-contrast alike.

## Customer club

- **👥 مشتریان** (`/admin/customers`) — one row per customer with their tier,
  points, lifetime spend, debt, tags, birthday and last purchase, filtered by
  search (name, phone, referral code, child name), tier, tag and status
  (فعال، کم‌فعال، بدهکار، تولد نزدیک، تخفیف استفاده‌نشده، بایگانی), sorted ten
  ways, paginated 25 to a page. The KPI cards above the table each match a
  filter below it, so a number is never a dead end.
- **پرونده مشتری** (`/admin/customers/{id}`) — the page the list points at:
  purchase history, the discounts actually applied, who referred whom, the نسیه
  summary, and a card for editing the birthday, tags, a note and SMS consent.
  Stored counters are shown beside totals recomputed from the sales, and a
  disagreement is flagged as **نامطابق** rather than silently trusted.
- **Store-agnostic birthdays.** The record describes *the customer* — their own
  birthday is the primary field. Child details are a module for a children's
  shop: **Owner → Settings** holds `birthday_target` (مشتری / فرزند / هر دو) and
  `child_profile_enabled`. With the module off, no child field appears on any
  form (registration, checkout, profile) and a hand-crafted post cannot store
  one. Turning it off never deletes data already on file. Defaults reproduce a
  kids' shop exactly, so nothing changes until a setting is flipped.
- **«این مشتری برای چه کسی خرید می‌کند؟»** — who a customer buys for is *their*
  own choice, not a store setting, so one children's shop can serve a parent
  (`buys_for='child'`: child name + child birthday, and no birthday of their own)
  and someone shopping for themselves (`buys_for='self'`: their own birthday) on
  the same page. Asked once at signup — registration, the counter's new-customer
  step and the counter panel all render the same partial — and changeable later
  on the panel and the admin profile. The choice decides whose birthday the
  discount and the wish use, which is why a self-buyer in a kids' shop is still
  wished on their own birthday. Switching sides writes the new one and *keeps*
  the old, so switching back loses nothing. A shop with the child module off is
  never asked, because it has only one possible answer. Rows created before the
  choice existed keep following the store's target, and the migration pins the
  ones already holding a child profile to «فرزند» so no existing customer's
  discount moves when the default is changed.
- **Live campaign badge.** The list gained a کمپین column (and a «کمپیندار»
  filter) that names the campaign the counter would honour today, so you can see
  who is entitled to a discount without opening each file; already-used
  campaigns are summarised instead, and the badge lookup is one bulk query for
  the whole page.
- **Lifecycle.** A customer with recorded sales is **archived**, never deleted —
  deleting one nulls the sale's customer and silently detaches purchase history.
  Archived customers leave the list, the counts and the marketing sends, and
  re-appear only under `?status=archived`. Marketing (birthday and campaign SMS)
  skips archived customers and anyone who opted out.
- **📉 کاهش سطح مشتریان** (`/admin/tier-downgrades`) — the only way a customer is
  ever demoted, and it is deliberate. The rule is «no purchase in N months»
  (N is a single setting, now read only by this page); the page lists everyone it
  matches — longest quiet first, with the reason on each row — and nothing moves
  until you tick who and confirm. A nightly sweep used to demote on its own while
  nobody watched; it is gone, so the rule no longer fires behind the owner's back
  (and the old lifetime-spend gate, which could demote a customer who had bought
  *yesterday*, went with it). The list is re-checked at the moment of the click,
  so a customer who bought while the form was open is not touched, and every
  demotion is written to **گزارش عملیات**.

## Campaigns (کمپین پیامکی)

- **📢 کمپین‌ها** (`/admin/campaigns`) — a campaign is a code, a window and an
  audience. The list shows status (در جریان / زمان‌بندی‌شده / منقضی / غیرفعال),
  the window in Jalali dates, how many customers were messaged, how many bought
  with it and what the campaign sold, all filterable and sortable.
- **The code actually works.** Entering it at the counter applies the discount
  immediately — the code is checked against the campaign's window and minimum
  purchase, and the amount is computed server-side, so the figure the cashier
  approves is the figure sent to the terminal. A code that is refusable is
  refused with a sentence in Persian instead of a silent no-op.
- **Assigned customers are automatic.** A customer holding a campaign gets the
  discount without typing anything: the checkout card names the campaign and the
  amount, the customer panel shows «کمپین فعال», and the customers list carries
  the badge. Each redemption is written to the invoice (`SaleCampaign`) and to
  the customer's assignment, which is what the report counts.
- **One-use by default, «چندباره» on request.** A campaign is spent by the first
  invoice unless it is flagged reusable, where it becomes a standing promo. A
  refund returns the campaign to the customer instead of burning it.
- **Sending is explicit.** The send panel offers everyone consented, one tier
  (silver included — the old send was hardcoded to diamond), one tag or the
  hand-picked list, and shows how many people each option would reach before you
  confirm. Everyone archived, opted out or already messaged is skipped and
  counted, so a double-click can never message the same customer twice; each
  holder records the date their SMS went out. The blast is capped by the
  «سقف هر ارسال» on the پیامک page, and the campaign page shows who was
  invited, who used it, and which invoices it discounted.
- **Deleting is limited on purpose.** A campaign that already discounted an
  invoice cannot be deleted — the count is refused with the reason, because
  removing it would orphan the sales it explains; deactivate it instead.

## SMS (پیامک)

- **📱 پیامک** (`/admin/sms`) — every message the shop can send, in one place.
  Each template states whether it is on, its **total** count, how many failed and
  the date it **last went out**, and a template that has never sent anything is
  flagged «هنوز ارسال نشده» so a forgotten one is visible at a glance. Each row
  also says which event fires it — a new signup, a birthday, a tier upgrade, a
  campaign send, a نسیه reminder, a trigger you gave it yourself, or nothing at
  all (a text you always send by hand). The list itself can be **sliced by how
  each template has been used** — هرگز فرستاده‌نشده / فرستاده‌شده / دارای ارسال
  ناموفق — and **ordered by the date it last went out**, newest or oldest first,
  with the ones that have never spoken at the old end. «فرستاده شده» means a
  message actually went out: still queued on the phone or refused by the gateway
  does not count, and the row's own line says which it was. The gateway key,
  device and blast ceiling live here too, and stay owner-only.
- **Editing is safe.** Each chip is a whole sentence you click to insert —
  «... عزیز، سلام! امیدواریم حالتان خوب باشد.»، a birthday wish, a نسیه reminder
  that names the amount, a tier-up congratulation — rather than a bare `%var1%`,
  and choosing one fills that sentence's own placeholders without overwriting a
  value you already picked, so a chip can never hand you a text the save refuses.
  The preview beside it redraws as you type and re-measures the message in
  characters and segments, and «ارسال آزمایشی» queues one real message to your
  own number so you see exactly what the customer will. Switching a built-in
  off stops its automatic send **everywhere** — the counter, the profile and the
  senders all read the same switch — without deleting its text.
- **Custom templates.** Write your own message and name its placeholders — each
  slot picks the customer field it is filled from (نام، نام خانوادگی، شماره،
  سطح، امتیاز، مجموع خرید، بدهی نسیه، کد معرف، نام فرزند) or is deliberately left
  blank, and only its **name** matters, never where it sits in the sentence. The
  preview shows a value only where one will really arrive, so an unfilled slot is
  visibly blank rather than flattered by a sample. A placeholder the text *uses*
  while nothing fills it is refused on save, with the tokens named — you only ever
  see a preview, and the customer is who reads that gap — while a slot your text
  never uses may stay blank. You can also duplicate an existing template and edit
  the copy.
- **Texts that send themselves.** A custom template can be given a trigger:
  **پس از هر خرید** (a thank-you for that customer's own order, the moment the
  sale is confirmed) or **پیگیری پس از چند روز** (a sweep over customers whose
  last purchase is further back than the wait you choose). Both are guarded —
  consent, the archive flag and the «بلاک» tag apply, each purchase is only ever
  asked about once, a template whose text uses a slot nothing fills is refused,
  and the sweep stops at «سقف ارسال خودکار در هر بررسی» and picks up the rest on
  the next pass.
- **📮 پیگیری مشتریان** (`/admin/follow-ups`) — everyone the follow-up templates
  are due to message: the text each one would actually receive, how long they
  have been quiet, and how many people were left out and why. Tick who to send
  and confirm, or leave it to the sweep. It sits **beside** the sweep rather than
  in front of it and the page says so out loud — a follow-up still leaves on its
  own, so this is a way to get there first, not a gate. Both paths record the
  same per-purchase reference, so whichever arrives first, nobody is asked about
  one purchase twice.
- **🎂 پیامک تولد** (`/admin/birthdays`) — the customers whose birthday falls in
  the next N days, with whose birthday it is, how soon, and who already received
  this year's wish. You tick who to wish and confirm; nothing is queued before
  that, a stale form cannot reach someone whose window has passed, and nobody is
  wished twice in the same year.
- **📤 ارسال پیامک** (`/admin/sms/send`) — a manual blast with an honest
  audience: everyone who consented, one tier, one tag, a hand-picked list with
  its own search, or numbers pasted in. The count is shown before anything is
  queued, and archived customers, opt-outs, «بلاک» tags, duplicates and bad
  numbers are skipped and reported. A message about the customer's own account
  (a نسیه reminder, a test) can be sent transactionally without marketing
  consent; the blast stops at the store's ceiling and says how many were left.
- **🧾 تاریخچه پیامک** (`/admin/sms/history`) — every message the shop has ever
  sent, automatic or manual, with recipient, template, source, text and the
  queue's verdict (در صف / ارسال‌شده / ناموفق) plus the gateway journey where
  known: «دست گوشی» once the phone claims it, «تحویل شد» / «نرسید» from the
  carrier's own report. The text is frozen when the message is queued, so
  editing a template later never rewrites what a customer actually received.
  Filtered to the خلاصه ماهانه sender, each digest row carries its month
  («مرداد ۱۴۰۵») and a form lets the owner **re-send a past month by hand** —
  the recorded text goes out again verbatim, under the same month ref; the
  scheduler's once-per-month rule never blocks the owner's own decision.
- **📊 خلاصه ماهانه** — the month's reading, texted to the owner when the
  Persian month turns: takings and profit, the busiest day, what sold, who
  bought, the best seller — the same sentences سود و زیان writes, condensed to
  one message, describing the month that *finished*. The owner's number in the
  settings is the opt-in (clearing it switches the digest off), the day it
  fires is configurable, one ref per month keeps retries and restarts from
  double-sending, and a month with no sales says so instead of inventing
  zeros. The پیامک page **previews the exact text** from last month's real
  figures before any phone is saved — composed by the same composer the send
  uses, so what is shown is what would arrive.
- **An entry says what it was built from, not only what it said.** The customer
  values behind each message are recorded beside it — every placeholder the
  sentence used, with the shop's own label and the value of that moment («نام
  مشتری: سارا»، «بدهی نسیه: ۴۵۰,۰۰۰») — so a row can be replayed through its
  template and audited long afterwards. The page states the verdict: that a
  replay still produces exactly this text, or that the template has been edited
  since and now yields something else. A message sent before the record existed
  says so plainly rather than being shown as «no placeholders».

### The SMS gateway (درگاه پیامک)

- The shop **is** the gateway. The Android app (`free-sms-gateway`) polls the
  store app directly — same paths and headers it has always used — so there is
  no VPS to rent and no middleman to reach over the internet. The phone only
  needs to share the shop's Wi-Fi (or a forwarded port); the desktop launcher
  serves the device endpoints on **port 8101**, separate from the admin UI, so
  only the gateway is ever exposed to the network.
- **Pairing is one QR.** «جفت‌کردن گوشی» on the پیامک page issues a key shown
  exactly once as a QR; the phone's app scans it and stores the address and
  key. Re-pairing rotates the key, «قطع اتصال» removes the phone, and queued
  messages wait — an offline phone never loses a message, it just sends later.
  A claim whose phone died is released back to the queue after five minutes,
  so nothing wedges when a handset is switched off mid-send.

## Requirements

- Python 3.11+ (tested on 3.12)
- A barcode scanner that acts as a keyboard (any USB scanner works)
- Optional: an Android phone with the free-sms-gateway app for SMS (pairs by
  QR over the local network), and a try-on API key

## Install

```bash
# 1. Create a virtual environment and install dependencies (pinned in requirements.txt)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Create the config file from the template
cp .env.example .env             # Windows: copy .env.example .env

# 3. Edit .env — at minimum set ADMIN_PASSWORD and SESSION_SECRET
#    SESSION_SECRET:  python -c "import secrets; print(secrets.token_hex(32))"
```

## Run

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000 — the app redirects to the POS checkout
(`/sales/new`). The admin panel is at `/admin`.

Or use the launcher scripts:

- **Linux/macOS:** `./start.sh`
- **Windows:** `start.bat`

## Deployment modes

This project has two intentionally separate modes, controlled only by the
source file `deployment.py`:

### Owner mode (default: `OWNER_MODE = True`)

This is the private RaiKids installation. The desktop app:

- opens directly to the POS checkout;
- never shows the setup wizard;
- returns 404 for `/admin/setup`;
- refuses to create a new empty owner database when packaged without data;
- preserves the existing owner database and `.env`.

A customer cannot switch this mode from the UI. Changing it requires modifying
source code and rebuilding the application.

### Sales/demo mode (`OWNER_MODE = False`)

Use this only when you, the developer, intentionally prepare a build for a
new shop. In that build the first-run wizard is available and can collect that
shop's name, password, and optional integration keys. Build it separately and
do not distribute your private owner `.env` or database with it.

### Provisioning the private owner build

The developer can pre-load the packaged app with the existing owner's data:

```bash
python provision_owner.py --source-db referral.db --source-env .env --with-uploads
```

This copies the private DB and secrets (and with `--with-uploads`, the product
and invoice images) to the platform data directory and marks it as
owner-provisioned. It uses SQLite's online backup API so recent WAL
transactions are preserved. The target directory contains real secrets and
must never be committed or sent to another shop.

To make the packaged app fully self-contained — double-click on any machine
and the store is simply there, with no setup page at all — stage an embeddable
bundle before building:

```bash
python provision_owner.py --source-db referral.db --source-env .env --stage
./build_mac.sh   # or the Windows equivalent
```

`owner_bundle/` (DB + `.env`) is embedded into the build; a fresh data dir is
seeded from it automatically on first launch. The bundle is gitignored and
contains real secrets — never share it or commit it.

### First-run setup (sales/demo builds only)

When `OWNER_MODE = False`, the demo build opens `/admin/setup` on first launch.
The wizard collects store branding, an admin password, and optional SMS/try-on
keys. It is deliberately disabled in the default owner build.

## Configuration

All configuration lives in `.env` (secrets) and the `settings` table (per-shop
values editable in the admin panel). See `.env.example` for every variable.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite path (default `sqlite:///./referral.db`) |
| `ADMIN_PASSWORD` | First-run admin password (seeded as a hash; changeable in Settings) |
| `SESSION_SECRET` | Signs login sessions; set once, keep it secret |
| `API_TOKEN` | Unlocks `/api/*` for the phone app (photo upload / try-on) |
| `RAYKID_GATEWAY_PORT` | Port for the SMS device gateway (default 8101) |
| `TRYON_API_URL` / `TRYON_API_KEY` | Virtual try-on image API |

> **Never commit `.env`** — it contains secrets. It is already in `.gitignore`.

## The phone app (photo capture)

Pair the phone from the admin panel — **پوشاک مجازی → گوشی ضبط عکس** (owner only):
a QR appears once; scan it with the phone's camera and the capture page opens
with the server's address and a per-device key already held. No IP is typed, and
the shared `API_TOKEN` is no longer part of this flow — the key is stored only
as its SHA-256 on the server, rides in the QR's URL fragment (which browsers
never send to any server), and pairing again invalidates the key a lost phone
holds. The page wears the shop's own palette, so the phone at the counter
matches whichever theme the panel is set to, dark and high-contrast included.

## Backups

- A backup is created automatically every night at 02:00 UTC and kept in
  `backups/` (last 30).
- **پشتیبان‌ها** in the admin panel: back up now, download any backup.
- **Restore:** stop the app, copy the backup file over `referral.db` (keep a
  copy of the current file first), start the app again.

## Development from a clean checkout

The repository intentionally excludes local customer data and generated files.
After cloning, create a virtual environment, install `requirements.txt`, copy
`.env.example` to `.env`, and configure local development secrets. The app
creates a new local SQLite database when run in development. Never copy a real
shop database into the repository.

Run the isolated test suite with:

```bash
PYTHONPATH=. python -m pytest -q
```

Tests use a temporary database directory and do not modify `referral.db`, `.env`,
uploads, backups, or any other local shop data.

## Database migrations

The database now records a schema version in `schema_version`. Before upgrading
a real installation, stop the app and create a verified backup. Run the
migration status check, apply the upgrade, and confirm the application and
reports work before deleting the old backup. A failed migration must stop the
upgrade so the backup can be restored and the issue investigated.

## Updating

1. Stop the app.
2. Create a verified backup of `referral.db`.
3. Pull the new code (`git pull` or replace files).
4. `pip install -r requirements.txt`.
5. Start the app; the versioned migration runner applies pending changes.
6. Verify the schema and application before removing the backup.

## Tests

```bash
PYTHONPATH=. pytest -q
```

Tests cover the money logic and authorization: server-side price recomputation at checkout,
stock clamping, refund reversal, referral settlement, CSRF protection, and the
API-token gate, plus the inventory ledger's reconciliation contract, the Jalali
date picker's calendar maths and constraints, the customer club's filters,
birthday targets and archive-instead-of-delete rule, the SMS log's record of what
each message was built from and the verdict a replay gives it, the follow-up
review page's rule that it sends only what was ticked and loses politely to a
sweep that went first, the SMS manager's usage filter and date order, and the
dashboard's role matrix — that every destination it draws for a role opens for
that role, that the figures it shows match the pages behind them, and that the
leaderboard is one query however many customers the shop has — and the downgrade
rule's promise that nothing is demoted without a tick and a confirmation, that a
customer who bought while the form was open is turned away, and that no nightly
job runs it. The cash drawer has its own suite, mostly about what must not
happen: a second drawer cannot be opened (the database refuses it, not just the
route), a cashier cannot close somebody else's shift, a card-paid expense never
leaves the till, a shift counts only what moved inside it, a withdrawal needs an
amount and a reason and cannot be reversed once its shift is closed, and the
expected figure reaches neither the counter's page nor the shift statement while
the shift is open — while the difference it finds is listed on the page, on its
statement and, when it is not zero, on the dashboard. The shell is covered the
same way: every door the sidebar draws opens for the role it was drawn for, the
menu, the tab, the heading and the last crumb are one string on every destination
a role can open, every crumb on a page
opens for the role shown it, exactly one item is current on each page, no page the
till grants offers a link back into the admin panel, and a refusal lands on a
Persian page that names the role asking rather than a JSON payload. The palette
is covered the same way: every theme defines every colour the stylesheet reads,
so a token a theme forgot fails the suite instead of quietly rendering in the
inherited colour, the same rule holds for the markup a page writes itself, and no
page of the shell may write a colour of its own — a hex literal fails the suite,
so the charts, the photo panels and the lightbox follow dark and high-contrast
instead of staying the palette the app shipped with. That promise is then
checked on what the shop actually receives rather than on what the sources say:
the suite opens every address the shell serves, as each of the three roles, with
each of the ten palettes active, and resolves every colour the rendered page
reads against the palette it was loaded with — so a page that loads without its
theme, or answers differently depending on the palette, or crashes for a role,
fails the build as well. Colour is never the only thing a chart says, either:
every chart on سود و زیان carries a sentence built from the figures it draws,
shown beside it and given as the canvas's accessible name, and the renderer
writes the number on every mark it can fit — a ring names and counts its slices,
its hole holds their sum, and the highest and lowest of a crowded series keep
their labels when the rest are thinned — so an owner who cannot separate the
accents, and a page printed in one colour, lose nothing. A canvas without a
sentence, or a sentence that disagrees with its own chart, fails the suite.
One page sits outside the shell — the phone capture tool
the counter opens — and it is held from the other side: it loads no stylesheet,
so it may read only what its palette hands it and what it declares itself, and a
colour it cannot resolve fails the suite too. That walk is then run twice more,
over the two shops it otherwise cannot see: one with no records at all, and the
one the app's own boot leaves behind on a database nobody has opened — an owner
account from the admin password, the built-in message templates switched off, and
not one setting the shop has chosen. Every address is opened as every role on
every palette in both, held to the same rules, plus two of their own: a page whose
job is to list records has to draw with none of them, and no page may print the
renderer's own words — `None`, `undefined`, a number that is not a number —
anywhere the shop can read them, a field's value included, because a field holding
one reads as a field the shop filled in. The replay of the start-up is checked
against the app's own `lifespan`, so a step added there later has to be replayed
in the pass or explained. The first run of that pass found two live faults: a
colour × size matrix that grouped nothing, so SQLite answered one synthesised row
— the heatmap showed a single cell holding the total on a shop that had sold four
combinations, and «None» down the page on a shop that had sold nothing — and a
product form that filled every optional field with `None`, which the next save
wrote into the database.

The same shape of bug — a value that renders, looks deliberate and is wrong — is
hunted one layer up, where a missing, unreadable or empty input is quietly
answered with something that looks like an answer. A name a page was never given
prints as nothing, so every undefined name the templates reach for is recorded
while those pages are opened, and the page that printed one fails the build. A
range nobody could read is answered with the month the app defaults to *and* a
sentence naming the range that was asked for, rather than silently widening to
the whole ledger; a half-typed window and an unreadable export range are refused
the same way instead of being read as «everything». A share over an empty base
reads «—», not a confident «0٪» — a category nobody bought from has no margin to
state, a morning before the first sale has none either, and a campaign that
reached nobody has no response rate — and the arithmetic written out by hand
fails the suite at the source, in a template and in a service alike: the services
hand a page `None` where there is nothing to take a share of, and the page prints
«—». A figure a record simply does not have is not a nought either: a customer
whose counter was never computed printed as one who had never bought anything —
`customer.total_spent or 0` on the list the shop uses to decide who to ring, and
`|default(0)` on the customer's own page — so a figure comes from `figure(x)`, the
number or «—», and `or 0` on a column that can be empty fails the suite unless the
empty column means the nought it prints: nothing paid against
an unpaid invoice, no points earned on it, no referral, no recorded demand. Each of
those is written down with its reason, and an excuse that stops being used fails
too. A field the form cannot read is the same defect inside a form: an empty cost
column printed `None` into the cost input's own value, and a variant's empty stock
printed `None` into its quantity box, which the next save then writes as nought. A
cheque whose issue date was typed and not understood is refused
rather than dated today. A form field that guards its record but not its column —
`{{ product.brand if product else '' }}`, where the column is simply NULL — prints
`None` into a field the shop reads as filled in, and fails the suite as well. A
colour may not hide in a fallback for a property:
`var(--token, #fff)` is a hard-coded colour wearing the theme's clothes, and one
was live on the till until this rule found it. And every setting the app reads is
a key something writes, so a preference the shop can never change cannot sit on a
settings page looking chosen. Each of those guards was falsified — the thing it
watches was deliberately broken, and the guard had to notice — before it was
trusted.

The same question is asked of the controls themselves, in all ten palettes at
once. A state colour written into the base palette is a colour every palette
inherits, whether or not it fits the surfaces it lands on: the focus ring read
1.00:1 on the close-toned topbar, a hovered menu item 1.10:1 against the sidebar
it sat in, the sign-out label 1.09:1 where the label and its fill were the same
hue on a dark palette, and a disabled button faded to 62% left its own label at
2.10:1 while the calendar's own greyed day read 1.72:1. So the ring (one value for the page's surfaces, one for the shell's own
bars, because no single colour reads on both a white card and a near-black
topbar), the two sidebar pills, the disabled pair and the fill under a
destructive button are derived from each palette's own colours and measured
against them — 3:1 for a ring and for a control's boundary, 4.5:1 for anything
read as text, 1.2:1 for two surfaces that only have to be told apart — in all ten
palettes and in a shop's own two brand colours, and a hover may not move the
ground out from under a label. The rules that paint those states are read out of
the stylesheet too, so no interaction rule may name a colour of its own, and a
rule that takes the outline away has to draw a ring in its place. Three states
were naming their own: a table row's hover was a hard-coded pink that vanished on
the dark palettes, the sign-out button hovered to a light pink under a dark
sidebar, and the basket's remove button put a white label on a red at 3.34:1.

A palette's hue is a hue, not a legibility. As text the brand colour read 3.36:1
on the card in Kids Boutique — and as `--candy-dark` 4.12:1 in Midnight, where
`--mint-dark` on a tint of itself read 3.64:1 and `--sky-dark` 3.77:1 — so the
inks the app speaks in (a link, a figure, a chip's label, a positive number, a
note, a warning) are derived from the hue with the same treatment, each measured
on the four surfaces a page is made of, on the strongest tint of its own hue
beneath it, and on the alert that belongs to it: the warning banner printed gold
on its own pale gold at 1.84:1. The same goes for a fill a label sits on — white
on the brand colour measured 3.34:1 on the light palettes and 2.10:1 on the dark
one — so a labelled surface is the brand, success or alarm fill derived against
its label, both ends of the gradient included. The hue keeps its place in
borders, tints, gradients and bars, and no rule may write text in it or put a
label on it: the stylesheet, a template's own `<style>` block and a
`style="…"` attribute are read for that pair, so a new chip cannot quietly bring
it back.

And the shell is opened without a mouse. The drawer is *hidden* when it is closed
rather than only slid off screen — a transform leaves every entry of a menu nobody
can see in the tab order — Escape closes it and hands the keyboard back to the
button that opened it, that button says what pressing it does next, a skip link
before the menu is the first thing Tab reaches, and nothing clickable is a `<div>`
or an `<img>`: the lightbox's ✕ and the picture that opens it are buttons, and
opening it moves the keyboard inside. Escape closing the drawer is driven in a
real JavaScript engine against the shell's own script, so a handler that stops
closing anything fails the suite rather than the shop, and every guard here was
falsified the same way as the rest — the state was deliberately broken and the
guard had to notice.

The walks hold the shop empty as well as full. Two companion passes open every
address the matrix serves, in every palette and as every role, with *nothing* in
the database — the empty branch of every table, an id that matches no record —
and once more as the shop's very first boot, replaying the start-up in order and
opening the first page as the owner it created, before any palette has been
chosen. A guard reads `main.py`'s lifespan and requires every call the boot
makes to be replayed by that pass or excused by name, so the pass cannot quietly
stop describing the first run. Both found live defects on their first run: the
colour × size matrix grouped nothing, so SQLite answered the aggregate with one
synthesised row and a busy shop drew a matrix of a single cell holding the total
of everything; a page whose job is to list records must draw 200 with none of
them; and no document may print the renderer's own vocabulary — `None`,
`undefined`, `nan`, `Infinity` — anywhere the shop can read it, a field's value
included.

The stylesheet declares no colour the theme should own: the guard cuts the one
`:root` block away and fails on any literal left outside it, is not fooled by a
token name that contains a colour word nor by a hex hidden inside a `var()`
call, and the loyalty metals and the printed page are measured in all ten
palettes like every other state — with an inventory so the floors table cannot
be weakened silently. The capture phone's pairing is guarded the way the
gateway's is: hash-only storage, a QR shown once, the key gate, the unpair and
the role, each proven to bite by sabotage before it was trusted.

The suite uses a throwaway SQLite database — never your real data.

Continuous integration runs this suite plus `compileall`, refuses a tracked
`.env`, `.db` or `.log` file, and refuses any commit **message** that credits a
tool or a bot instead of a person — a message is public the moment it lands, so
every commit in the push is checked, not only the newest one.

## Security notes

- Admin auth is session-based (signed cookie, 12h expiry) with PBKDF2-hashed
  passwords stored in the settings table, plus a login rate limiter.
- Every state-changing form is CSRF-protected.
- `/api/*` endpoints (customer lookup, photo upload, image generation) require the
  `API_TOKEN` or an admin session — never open to the network.
- Image generation is capped per day (setting `tryon_daily_limit`) and
  rate-limited per client, because each generation uses a metered service.
- Keep the machine on a trusted network; nothing here is designed to face the
  public internet without additional hardening.

## Desktop app (double-clickable installer)

The app can be packaged as a native desktop app using **pywebview** +
**PyInstaller** — a shop owner gets a double-clickable `.app` (macOS) or
`.exe` (Windows) with no Python, no terminal, no install steps. The bundled app runs the FastAPI server on the fixed port configured as
`DESKTOP_PORT` in `deployment.py` (default: `8100`) and opens a native window.

### Build from source

```bash
# 1. Install the desktop packaging deps (commented out in requirements.txt)
pip install pywebview pyinstaller

# 2. Build (on the target platform — you can't cross-compile)
./build_mac.sh          # macOS → dist/Raykid Store.app
build_windows.bat       # Windows → dist\RaykidStore\RaykidStore.exe
```

### Data location

In the desktop app, all writable data lives in the user data dir:

- **macOS:** `~/Library/Application Support/RaykidStore/`
- **Windows:** `%APPDATA%\RaykidStore\`

This includes the database (`referral.db`), uploads, backups, and a `.env`
file. A `.env` there overrides the bundled defaults (API keys, admin password).
The phone URL is stable: `http://COMPUTER_IP:8100/admin/mobile` by default.

### First launch

- **Owner build (default):** provision the private database and `.env` first
  with `provision_owner.py`; then double-click the app. It opens directly to
  the POS. The setup wizard is disabled and `/admin/setup` returns 404.
- **macOS (unsigned):** right-click the .app → **Open** → confirm. This
  bypasses Gatekeeper for a local shop install. For distribution, sign and
  notarize with an Apple Developer ID.
- **Windows:** the `.exe` may trigger SmartScreen — click **More info** →
  **Run anyway**. For distribution, sign with a code-signing certificate.

For a customer/demo build, change `OWNER_MODE = False` in `deployment.py`
*before building*. This is a developer/source-code decision; it cannot be
changed from the application UI. Never ship your owner `.env` or database in
that build.

### Desktop vs. web-only

The desktop app is for **shop owners** — it's self-contained, no Python
needed, and stores data per-user. The web-only mode (`uvicorn main:app`) is
for **you (the developer)** during development, or if you want to serve the
app over the local network to multiple devices (phone checkout, etc.).
The packaged desktop app also serves the phone page on the LAN using the fixed
`DESKTOP_PORT`; the phone and computer must be on the same Wi‑Fi network.
