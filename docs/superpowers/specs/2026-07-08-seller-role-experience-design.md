# Seller Role Experience — Design Spec

**Date:** 2026-07-08
**Status:** Approved for planning

## Goal

Make the CRM's role model match how the business actually works:

- **Admin / Manager** see everything — all clients, all sales, company-wide
  statistics, and every seller's performance.
- **Sellers** work inside a personal space — they see only their own clients,
  sales, debts, and till, and the UI is worded to feel like *theirs*.
- **Sellers and admins can transfer a client to another seller**, handing over
  the whole relationship.

Most of the *access scoping* already exists (`can_see_all_records`, `owner`,
`sales_rep`, `visible_to`). This spec fills the four remaining gaps.

## Current state (already built — no change needed)

- Roles `admin` / `manager` / `sales` in `accounts/models.py`, with
  `User.can_see_all_records` (admin + manager) driving visibility.
- Clients scoped by `Client.owner`; sales scoped by `Sale.sales_rep` via
  `Sale.objects.visible_to(user)` and `_visible_clients(user)`.
- Login enforced globally by Django's `LoginRequiredMiddleware`.
- Admin-only areas (`@role_required`): user management, product management,
  audit log, payment/expense delete.
- Dashboard already scopes to the seller and hides the rep filter for them.

## Non-goals

- No changes to the roles themselves, auth, or the money/debt math.
- No new dedicated seller layout or seller-only dashboard — same pages, scoped
  and reworded (per the "sidebar + page headers" decision, not "full layout").
- Payment/return/stock `created_by` are historical facts and are never rewritten.

---

## Part 1 — Client transfer (full handover)

Reassign a client and their entire sales history to another seller, atomically.

### Semantics (decision: **full handover**)

On transfer of client *C* from seller *A* to seller *B*:

- `C.owner` → *B*
- Every `Sale` of *C*: `sales_rep` → *B* (bulk update)

Deliberately **unchanged**: `Payment.created_by`, `Return.created_by`,
`StockEntry.created_by`, and existing audit rows — they record who physically
performed an action. *B* naturally becomes `created_by` on any *future*
payments they collect.

**Accepted consequence:** because per-seller performance (profit) is derived
from `Sale.sales_rep`, moving all of *C*'s sales also shifts that client's
*past* profit attribution from *A* to *B*. This is the intended meaning of
"this client is now entirely B's."

### Permissions

- **Seller:** may transfer only clients they own. Enforced by fetching the
  client through `_visible_clients(request.user)` — a non-owned client 404s.
- **Admin / Manager:** `_visible_clients` returns everyone, so any client.
- **Target seller:** any active user except the current owner.

### Implementation

- **Model:** add `TRANSFER = "transfer", "Sotuvchi o'zgartirildi"` to
  `AuditLog.Action`. Metadata-only migration.
- **Form:** `ClientTransferForm` with `new_owner` (`ModelChoiceField` over
  active users, excluding the current owner); `clean_new_owner` rejects the
  current owner.
- **View:** `client_transfer(request, pk)` in `crm/views.py`.
  - `client = get_object_or_404(_visible_clients(request.user), pk=pk)`
  - **GET** → render a modal form (target dropdown + a summary line: number of
    sales and outstanding debt that will move, `A → B`).
  - **POST**, inside `transaction.atomic()`:
    - `Sale.objects.filter(client=client).update(sales_rep=target)`
    - `client.owner = target; client.save(update_fields=["owner"])`
    - `AuditLog.record(user, TRANSFER, "Mijoz", client.pk, "<name>: A → B (N ta sotuv)")`
    - success toast, reload.
- **URL:** `path("clients/<int:pk>/transfer/", crm_views.client_transfer, name="client_transfer")`.
- **UI:** a "transfer" icon-action on each client-list row (next to Edit/Delete),
  opening the modal via the existing `data-modal` pattern. Labels: modal title
  "Mijozni o'tkazish", field "Yangi sotuvchi", button "O'tkazish".

---

## Part 2 — Seller-personalized navigation & page headers

Admins/managers keep neutral labels; sellers get personal ones. Driven by
`user.can_see_all_records`, using inline `{% if %}` in templates (matches the
existing convention — no new abstraction).

| Surface | Admin / Manager | Seller |
|---|---|---|
| Sidebar: clients | Mijozlar | **Mening mijozlarim** |
| Sidebar: sales | Sotuvlar | **Mening sotuvlarim** |
| Sidebar: debts | Qarzlar | **Mening qarzlarim** |
| Sidebar: kassa | Kassa | **Mening kassam** |
| Sidebar: Ombor, Asosiy | *(unchanged)* | *(unchanged)* |

The matching **page header / `topbar_title`** on the clients, sales, debts, and
kassa pages flips the same way. Content underneath is already scoped — this is
wording only.

### Touch points

- `templates/base.html` — sidebar nav items (clients / sales / debts / kassa).
- `templates/crm/client_list.html`, `sale_list.html`, `debt_list.html`,
  `kassa.html` — `topbar_title` / page headers.

---

## Part 3 — Kassa scoped to the seller

Today `kassa_view` shows the shared company till **and** every employee's
performance to everyone. For sellers, scope it to themselves.

In `kassa_view` / `_kassa_expenses`, when `not user.can_see_all_records`:

- Force `rep = request.user` (ignore any `?rep=` in the URL) — till drawers and
  the expense list show only the seller's own money in/out.
- `reps = None` — the employee filter never renders (same pattern as
  `sale_list` / `debt_list`).
- `per_employee = None` — skip the per-employee performance table; hide that
  block in `templates/crm/kassa.html` when absent.

Admins/managers keep the full company till, employee filter, and performance
table unchanged.

---

## Part 4 — Close the product-detail cross-seller leak

`product_detail` currently shows a product's recent sales across *all* sellers
(with client names) and a warehouse-movement log to everyone.

For sellers (`not user.can_see_all_records`):

- **Scope recent sales to self:** filter `recent_items` by
  `sale__sales_rep=request.user`, so a seller sees only their own recent sales
  of the product.
- **Hide the warehouse-movement log** ("Ombor harakatlari", which exposes
  `created_by` staff names): pass `entries = None` for sellers and guard the
  block in `templates/crm/product_detail.html`.

Sellers keep the stock KPIs (current stock, total received/sold, price) — shared
warehouse truth they need to sell. Admins/managers keep the full page.

---

## Permissions matrix (after this work)

| Capability | Seller | Manager | Admin |
|---|---|---|---|
| See own clients/sales/debts | ✅ | all | all |
| See other sellers' clients/sales | ❌ | ✅ | ✅ |
| Personalized "Mening ..." UI | ✅ | ❌ (neutral) | ❌ (neutral) |
| Kassa | own only | company | company |
| Per-employee performance | ❌ | ✅ | ✅ |
| Transfer own client | ✅ | ✅ | ✅ |
| Transfer any client | ❌ | ✅ | ✅ |
| Product recent sales | own only | all | all |

## Testing (following `crm/tests.py` patterns)

Part 1 — transfer:
- Seller transfers own client → `owner` and all `sales_rep` move to target.
- Seller cannot reach a client they don't own (404).
- Admin transfers any client.
- Transfer to the current owner is rejected (form invalid).
- After transfer: target seller sees the client's debt; original seller no
  longer sees the client/sales.
- An `AuditLog` TRANSFER row is written.
- Atomicity: owner and every sale move together.

Part 3 — kassa scoping:
- Seller's kassa figures cover only their own payments/expenses.
- Seller cannot widen scope via `?rep=` of another user.
- Seller response has no per-employee performance table; admin's does.

Part 4 — product detail:
- Seller sees only their own recent sales of a product; not another seller's.
- Seller does not see the warehouse-movement log; admin does.

Part 2 — labels:
- Seller response contains "Mening mijozlarim"; admin response contains
  "Mijozlar".

## Incremental build order

Each step compiles, passes tests, and is shippable on its own:

1. **Part 1 — client transfer** (model + form + view + URL + UI + tests).
2. **Part 3 — kassa scoping** (view + template + tests).
3. **Part 4 — product-detail leak** (view + template + tests).
4. **Part 2 — personalized labels** (templates + tests).
