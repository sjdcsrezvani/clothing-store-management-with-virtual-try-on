# Changelog

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
