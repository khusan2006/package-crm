# Packaging CRM — Design Spec

**Date:** 2026-07-03
**Stack:** Django (latest) + PostgreSQL (Docker) + Django templates
**Status:** Approved direction from client dev (Khusan): Django + Postgres, Docker available.

## Purpose

Internal CRM for a client that sells packaging. Sales team tracks customers,
products, and orders (sales). Role-based access controls who sees and manages what.

## Roles

| Role    | Access |
|---------|--------|
| Admin   | Everything: user management, products, all clients/orders, dashboard |
| Manager | All clients, orders, products, dashboard. No user management |
| Sales   | Only their own clients and orders. Can browse products (read-only) |

Implemented as a `role` field on a custom `User` model (extends `AbstractUser`).
Custom user model from day one — switching later is painful in Django.

## Apps

- **accounts** — custom User with role, login/logout, user management (admin-only views)
- **crm** — Client, Product, Order, OrderItem models + CRUD views + dashboard

## Data model

- `User(AbstractUser)` + `role: admin|manager|sales`
- `Client`: name, company, email, phone, address, notes, owner(FK User), created_at
- `Product`: name, sku(unique), description, unit (e.g. pcs/box/roll), price(Decimal), stock(int), is_active
- `Order`: number(auto), client(FK), sales_rep(FK User), status (draft/confirmed/shipped/paid/cancelled), created_at
- `OrderItem`: order(FK), product(FK), quantity, unit_price (snapshot at order time)
- Order total = sum of items (computed property, annotated in queries)

## Views (server-rendered templates)

- `/login`, `/logout`
- `/` dashboard — sales this month, order counts by status, recent orders, top clients (scoped by role)
- `/clients` list/create/edit/delete (sales: own only)
- `/products` list/create/edit (admin+manager write; sales read-only)
- `/orders` list/create/detail/status-change (sales: own only)
- `/users` list/create/edit (admin only)
- Django admin at `/admin` as a power-user fallback for Admin role

## Access control

- `LoginRequiredMiddleware`-style: all views require login except `/login`
- Role checks via small decorators/mixins (`role_required("admin", "manager")`)
- Queryset scoping: sales users get `.filter(owner=request.user)` / `.filter(sales_rep=request.user)`

## Infra

- Postgres 16 via `docker-compose.yml` (single service, volume for data)
- `.env` for DB credentials + SECRET_KEY (python-dotenv)
- `requirements.txt`: Django, psycopg[binary], python-dotenv
- Seed command (`manage.py seed_demo`) creating demo users (one per role), sample products/clients/orders

## Error handling & testing

- Django forms handle validation; messages framework for user feedback
- Tests: model logic (order totals), role scoping (sales can't see others' data), auth redirects

## v2 (2026-07-03, client requirements)

The client sells **plastic bags by weight**. The Order/OrderItem system was
replaced with a flat `Sale` model, and the whole UI was translated to Uzbek.

- `Sale`: date (Sana), client (Mijoz), product (Mahsulot), dimension kg/g
  (O'lchov birligi), weight (Og'irligi), price per unit (Narxi),
  cost_price snapshot (Tannarxi), is_debt (Qarzga sotildi),
  debt_deadline (Qarz muddati — required when is_debt), sales_rep
- Computed: umumiy narx (weight × price), umumiy tannarx, foyda (profit)
- `Product` gains `cost_price` (tannarx per 1 kg); prices are per kg, sale
  in grams converts cost by /1000. Unit/stock fields removed.
- Debt sales past their deadline are flagged (muddati o'tgan) on the
  dashboard and highlighted in the sales list.
- `LANGUAGE_CODE = "uz"`, `USE_THOUSAND_SEPARATOR = True`; all labels,
  messages, and templates in Uzbek.

## Out of scope (v1)

Invoicing/PDF, email notifications, lead pipeline/kanban, multi-currency,
REST API. Add when the client asks.
