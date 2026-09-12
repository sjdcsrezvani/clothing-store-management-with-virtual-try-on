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
