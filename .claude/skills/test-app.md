---
name: test-app
description: Run the app test suite — critical sales flow, birthday parser, and discount logic
---

# Test Skill: رای کیدز

Run the test suite for the referral/sales app. Covers the critical path that had the "phone number required" bug.

## Usage

```bash
# Install deps (if not already done)
pip install -r requirements.txt pytest httpx

# Run all tests
pytest tests/ -v

# Run a single test
pytest tests/test_sales.py -v -k test_sales_flow

# Self-check (no pytest needed)
PYTHONPATH=. python3 services/_common.py
PYTHONPATH=. python3 tests/test_sales.py
```

## What's tested

| Test | File | What it covers |
|------|------|----------------|
| `test_sales_flow` | `tests/test_sales.py` | Full cycle: phone lookup → create customer → scan → confirm → receipt. Asserts no "phone required" error (the bug fix). |
| `test_birthday_parser_edge_cases` | `tests/test_sales.py` | Persian date parsing edge cases (bad month/day, garbage input, Persian digits). |
| `_common.py self-check` | `services/_common.py` | Quick assert-based self-check for `parse_persian_birthday`. Run with `PYTHONPATH=. python3 services/_common.py`. |

## Not yet tested

- Admin routes (login, dashboard, settings)
- Analytics/aggregation queries
- SMS sending (requires real API key)
- PDF invoice generation
- Barcode generation
- Referral discount accumulation across multiple purchases

Add tests for these when those features become unstable or get bug reports. (Ponytail: YAGNI until needed.)

## When to run

- After modifying `routers/sales.py` (the core sales flow)
- After touching `services/_common.py` or `services/discount.py`
- After any change to the customer creation or checkout flow
- Before deploying to verify the "phone required" bug hasn't regressed
