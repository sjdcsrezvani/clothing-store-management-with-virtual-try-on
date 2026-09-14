# RaiKids POS — سیستم فروش و باشگاه مشتریان

FastAPI + SQLite point-of-sale for a local kids' & teens' clothing shop:
products & variants with barcode tags, checkout with referral / tier / birthday
discounts, customer loyalty (points, gold/diamond tiers), an SMS suite
(template manager, manual sends, welcome / birthday / tier-up / campaign
messages, and a send log whose entries can be replayed), virtual try-on previews on
the child's picture), invoices (HTML + Persian PDF), a deep analytics suite,
and accounting-lite: credit sales (نسیه) with a debt ledger, supplier purchases
that update stock & cost, expenses, a cash register, a net profit & loss
report, and CSV exports.

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
- **🧾 صندوق** (`/admin/cashbox`) — daily cash register: opening balance (set it
in the page), cash sales + نسیه receipts in, refunds/expenses/purchases out,
closing balance.
- **🧮 سود و زیان** (`/admin/accounting`) — revenue − COGS − expenses = net
profit for any period (today/week/month/year/custom, Persian dates), plus CSV
exports of sales, customers, purchases and expenses (Excel-friendly).

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

## The phone app (photo upload)

Open `/admin/mobile` on the shop phone (or save it to the home screen as a PWA).
Enter the computer's IP (e.g. `192.168.1.20:8000`) and the `API_TOKEN`, then
take the child's photo — it uploads to `/api/image-gen/upload-kid-photo`.

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
sweep that went first, and the SMS manager's usage filter and date order. They
use a throwaway SQLite database — never your real data.

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
