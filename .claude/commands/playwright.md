---
description: Run or write Playwright E2E / permission tests for the Paket CRM
argument-hint: "[all | <keyword> | new: <what to test>]"
allowed-tools: Bash(.venv/Scripts/python.exe -m pytest:*), Bash(.venv/Scripts/python.exe -m playwright:*), Bash(.venv/Scripts/python.exe manage.py test:*)
---

You are driving the Playwright end-to-end / permission test suite for this Django CRM.

## Environment (this project)
- Python: `.venv/Scripts/python.exe` (Windows venv)
- Test settings: `config.settings_test` (isolated SQLite — NEVER touches the real Postgres data). Wired via `pytest.ini`.
- Browser tests live in `tests/test_e2e_smoke.py` (Playwright, headless Chromium against Django's `live_server`).
- View-layer permission tests live in `tests/test_permissions.py`.
- Fixtures (`admin_user`, `seller_user`, `sample_data`, `login`) are in `conftest.py`.
- Existing unit tests (money math, scoping) live in `crm/tests.py`.
- First-time setup only: `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt` then `.venv/Scripts/python.exe -m playwright install chromium`.

## Role model these tests encode (keep assertions consistent with it)
- **Admin/Manager** see & manage every seller's work. Only **user management** is admin-only.
- **Seller** sees only their OWN data (sales, debts, clients, own kassa kirim/chiqim, own audit) but has full technical access to fix their own records.
- **Shared sections** (ombor/products, kassa page) are usable by every role; product add/edit/delete/kirim/adjust are open to all.
- Owner scoping still holds: a seller can't touch another seller's payment/expense/client (404).

## What to do based on `$ARGUMENTS`

- **empty or `all`** → run the whole browser + permission suite and report pass/fail:
  `.venv/Scripts/python.exe -m pytest tests/ -v`

- **`new: <description>`** → write a NEW Playwright/permission test for `<description>`.
  1. Read `tests/test_e2e_smoke.py`, `tests/test_permissions.py`, and `conftest.py` to match style and reuse fixtures.
  2. Add the test to the right file (browser flow → `test_e2e_smoke.py`; access/permission → `test_permissions.py`).
  3. Keep it consistent with the role model above.
  4. Run just that test to confirm it passes, then summarise.

- **anything else** → treat `$ARGUMENTS` as a `-k` keyword filter and run the matching tests:
  `.venv/Scripts/python.exe -m pytest tests/ -v -k "$ARGUMENTS"`

## Rules
- Always use `.venv/Scripts/python.exe` (not bare `python`/`pytest`).
- Tests must stay isolated (SQLite test DB); never point them at the real database.
- If a run fails, read the failing test + the source it exercises, diagnose, and report the root cause (fix only if asked).
- Report a short, clear pass/fail summary at the end — don't dump full logs unless something failed.

Arguments: $ARGUMENTS
