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
