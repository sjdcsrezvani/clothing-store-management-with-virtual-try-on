# RaiKids POS — سیستم فروش و باشگاه مشتریان

FastAPI + SQLite point-of-sale for a local kids' & teens' clothing shop:
products & variants with barcode tags, checkout with referral / tier / birthday
discounts, customer loyalty (points, gold/diamond tiers), SMS gateway
(welcome, birthday, tier-up, campaigns), virtual try-on product previews on
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

- **📒 حساب نسیه** (`/admin/credit`) — sell on credit at checkout; see who owes
you, record payments (cash/card) which settle the oldest unpaid invoices first
(FIFO), per-customer history. A credit limit (سقف اعتبار) blocks new نسیه
sales once a customer's debt exceeds it — set a store-wide default in
Settings and per-customer overrides on each customer's credit page.
- **📦 خرید از عمده‌فروش** (`/admin/purchases`) — record stock purchases; stock
and the cost basis update automatically. Reversible (deleting a purchase
restores stock).
- **🏭 تأمین‌کنندگان** (`/admin/suppliers`) — supplier list with total purchased.
- **💸 هزینه‌ها** (`/admin/expenses`) — rent, utilities, wages… with categories.
- **🧾 صندوق** (`/admin/cashbox`) — daily cash register: opening balance (set it
in the page), cash sales + نسیه receipts in, refunds/expenses/purchases out,
closing balance.
- **🧮 سود و زیان** (`/admin/accounting`) — revenue − COGS − expenses = net
profit for any period (today/week/month/year/custom, Persian dates), plus CSV
exports of sales, customers, purchases and expenses (Excel-friendly).

## Requirements

- Python 3.11+ (tested on 3.12)
- A barcode scanner that acts as a keyboard (any USB scanner works)
- Optional: an SMS gateway device + API key, and a try-on API key

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
| `SMS_GATEWAY_URL` / `SMS_API_KEY` / `SMS_DEVICE_ID` | Self-hosted SMS gateway |
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
API-token gate. They use a throwaway SQLite database — never your real data.

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
