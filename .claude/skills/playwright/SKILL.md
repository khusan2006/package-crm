---
name: playwright
description: Run, write, or debug Playwright end-to-end and permission tests for the Paket CRM (Django). Use when the user wants to test the frontend in a browser, verify a page/flow works, add or fix E2E tests, check role-based access, or asks about the /playwright command.
---

# Playwright E2E testing — Paket CRM

Browser-driven end-to-end and view-layer permission testing for this Django CRM, run against an **isolated SQLite database** so the real Postgres data is never touched.

## Environment

- **Python:** `.venv/Scripts/python.exe` (Windows venv — always use this, never bare `python`/`pytest`).
- **Test settings:** `config.settings_test` (SQLite, axes disabled, fast password hashing). Wired via `pytest.ini`.
- **Files:**
  - `tests/test_e2e_smoke.py` — Playwright browser tests (headless Chromium via Django `live_server`).
  - `tests/test_permissions.py` — role/access matrix at the view layer (no browser).
  - `conftest.py` — fixtures: `admin_user`, `seller_user`, `sample_data`, and `login(user)` (signs a browser session in through the real login form).
  - `crm/tests.py` — existing unit tests (money math, querysets, scoping).
- **First-time setup:** `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt` then `.venv/Scripts/python.exe -m playwright install chromium`.

## Commands

```bash
# Whole browser + permission suite
.venv/Scripts/python.exe -m pytest tests/ -v

# One file / one keyword
.venv/Scripts/python.exe -m pytest tests/test_e2e_smoke.py -v
.venv/Scripts/python.exe -m pytest tests/ -k "seller" -v

# The full existing unit suite (Postgres, real settings)
.venv/Scripts/python.exe manage.py test
```

## Role model the tests encode

Keep every assertion consistent with this (see `crm/tests.py` for the canonical set):

- **Admin / Manager** — see and manage every seller's work. Only **user management** (`/users/`) is admin-only (manager is blocked too).
- **Seller** — sees only their OWN data (sales, debts, clients, own kassa kirim/chiqim, own audit rows), but has full technical access to add/edit/delete their own records.
- **Shared sections** — ombor/products and the kassa page are usable by every role; product create/edit/delete/kirim/adjust are open to all.
- **Owner scoping still holds** — a seller cannot touch another seller's payment/expense/client (expect `404`, not `403`).

## Writing a new test

1. Read the two `tests/` files and `conftest.py` first — match their style and reuse fixtures.
2. Browser flow (clicks, page renders, navigation) → add to `test_e2e_smoke.py`; access/permission check → `test_permissions.py`.
3. Use `login(user)` for a browser session; use the `client` + `force_login` fixture for fast view-layer checks.
4. Run just the new test to confirm green, then report.

## Gotchas

- Playwright's sync API + Django ORM: `conftest.py` sets `DJANGO_ALLOW_ASYNC_UNSAFE=1` — keep it.
- Tests must stay on the SQLite test DB; never repoint them at the real database.
- `test_db.sqlite3` is a disposable artifact (git-ignored) — safe to delete between runs.

The `/playwright` slash command (`.claude/commands/playwright.md`) wraps the run/write workflow: `/playwright`, `/playwright <keyword>`, or `/playwright new: <what to test>`.
