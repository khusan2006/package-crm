# Paket CRM

CRM for a plastic-bag (packaging) business: clients, products, weight-based
sales with cost price (tannarx) and profit (foyda) tracking, debt sales with
deadlines, and a sales dashboard — with role-based access control. UI is in
Uzbek.

**Stack:** Django · PostgreSQL (Docker) · server-rendered templates

## Roles

| Role    | Access |
|---------|--------|
| Admin   | Everything, including user management and `/admin` |
| Manager | All clients, orders, products, dashboard |
| Sales   | Own clients and orders only; products read-only |

## Getting started

```bash
# 1. Postgres
docker compose up -d

# 2. Python env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Configure
cp .env.example .env   # then set SECRET_KEY

# 4. Migrate + demo data
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo

# 5. Run
.venv/bin/python manage.py runserver
```

Demo logins (password `demo1234`): `admin`, `manager`, `sales1`, `sales2`.

## Tests

```bash
.venv/bin/python manage.py test
```

Covers order totals, role-based queryset scoping, permission checks, and the
order creation flow.

## Design doc

See [docs/specs/2026-07-03-packaging-crm-design.md](docs/specs/2026-07-03-packaging-crm-design.md).
