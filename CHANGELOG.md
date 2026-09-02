## 1.1.11

- Unified dashboard totals with canonical financial reporting.
- Corrected period-scoped refunds and inventory reconciliation details.

# Changelog

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
