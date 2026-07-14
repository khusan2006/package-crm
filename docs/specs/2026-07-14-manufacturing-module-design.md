# Manufacturing Module (Ishlab chiqarish) — Design Spec

**Date:** 2026-07-14
**Status:** Approved design (brainstormed with client dev Khusan)
**Related:** `docs/specs/2026-07-11-kassa-production-debt-tz.md` (deferred this module),
current `crm/models.py` (Product, StockEntry, Sale/SaleItem, Return, Payment, Expense,
ProductionRemittance), `accounts/models.py` (User roles).

## Purpose

The firm manufactures the goods it sells: it buys raw materials (xomashyo), combines
them in production runs, and the finished product goes into the factory warehouse
(sklad ombor). Sellers receive goods from the sklad into their own personal ombor and
sell onward to clients; manufacturing can also sell directly to clients. Material
prices fluctuate daily, so manufacturing cost is computed from a weighted-average
material cost, and the finished product's tannarx (cost_price) is kept current
automatically.

## Decisions (agreed)

| Question | Decision |
|---|---|
| Seller sales deduct from | The seller's own ombor (blocked if insufficient) |
| Raw materials catalog | Separate from Product (never sold directly) |
| Material units | Everything in kg |
| Production entry | Ad-hoc per run (no fixed recipes/BOM) |
| Costing rule | Weighted moving average of stock on hand |
| Batch cost | Materials only (no labor/overhead lines) |
| Product tannarx | Auto-updated per run, weighted by sklad stock; manual override stays |
| Sklad money | Separate full sklad kassa (not mixed with seller kassas) |
| Direct sales | Reuse existing Sale flow; rep is an omborchi user, deducts from sklad |
| Sklad operator | New role `OMBORCHI`; admin/manager retain full access |
| Seller "own details" | Sellers can add their own signed stock entries (+ addition / − write-off) |
| Cutover | All current net stock → sklad; seller ombors start at 0 |

## Architecture (Approach A)

New Django app **`manufacturing`**, following the codebase idiom: store movements,
derive balances with annotated querysets (same pattern as `Product.with_stock()`).
No balances are stored except each material's running `avg_cost`. Existing `crm`
money models are untouched; `Sale` only gains stock-source validation.

## Data model

New app `manufacturing`:

- **`RawMaterial`** — name, SKU (optional), note, `avg_cost` (so'm/kg, running
  weighted average — the only stored balance-like figure), `low_stock_threshold`,
  `is_active`. Unit is always kg.
- **`MaterialPurchase`** — material FK, date, `quantity_kg`, `price_per_kg`,
  supplier (free text), note, `created_by`, `created_at`. Saving updates
  `avg_cost` in the same DB transaction. The purchase itself is the sklad-kassa
  outflow (no duplicate Expense row).
- **`ProductionRun`** — `product` FK (crm.Product), date, `output_kg`, note,
  `created_by`, `created_at`. Batch cost derived from its items.
- **`ProductionRunItem`** — run FK, material FK, `quantity_kg`, `unit_cost`
  (snapshot of the material's avg cost at run time — history never changes when
  later purchases move the average).
- **`StockTransfer`** — sklad → seller: product FK, seller FK (User), date,
  `quantity_kg`, note, `created_by`, `created_at`.
- **`SellerStockEntry`** — seller FK, product FK, date, signed `quantity_kg`
  (positive = own addition, negative = write-off), note, `created_by`. Also used
  to seed cutover opening balances.

Changes to existing apps:

- `accounts.User.Role` — add `OMBORCHI = "omborchi", "Omborchi"`.
- `crm.Product.current_stock` / `with_stock()` — semantics become "sklad stock"
  (formula below). No schema change to money models.

## Stock & cost math

- **Material balance** (derived) = Σ purchases − Σ production consumption.
  A run may not consume more than on hand (blocked).
- **Weighted average on purchase:**
  `new_avg = (on_hand_qty × avg_cost + purchased_qty × price) / (on_hand_qty + purchased_qty)`.
  Consumption never changes the average. A replay utility recomputes `avg_cost`
  from chronological history after admin corrections.
- **Batch cost** = Σ(`item.quantity_kg` × `item.unit_cost`);
  `cost_per_kg = batch_cost / output_kg`.
- **Product tannarx auto-update** on each run:
  `cost_price = (sklad_qty × cost_price + output_kg × batch_cost_per_kg) / (sklad_qty + output_kg)`;
  if sklad stock ≤ 0, use the batch cost directly. Manual override remains.
- **Sklad ombor per product** = StockEntry (manual kirim/adjustment, kept)
  + production output − transfers out − direct sales (sales whose rep is an
  omborchi) + restocked returns of direct sales.
- **Seller ombor per (seller, product)** = transfers in + own entries − their
  sales + their restocked returns.
- **Blocking validations:** seller sale > seller ombor; transfer > sklad stock;
  production consumption > material stock. Errors are Uzbek, naming the shortfall
  ("Omborda 12.5 kg bor, 20 kg so'raldi").

## Sklad kassa

New page, same visual language as the existing kassa view:

- **Inflows:** `ProductionRemittance` rows + `Payment` rows on direct sales
  (sales whose rep is an omborchi).
- **Outflows:** `MaterialPurchase` totals + `Expense` rows created by omborchi
  users (production expenses via the existing Expense form — no new model).
- Balance = inflows − outflows; filterable by date range and method
  (naqd/karta/bank). Seller kassas untouched.

## Roles & permissions

- **Omborchi:** materials, purchases, production runs, sklad ombor, transfers,
  sklad kassa; can create direct sales with own clients (like a seller). Cannot
  see seller kassas or other sellers' clients.
- **Sotuvchi:** new page "Mening omborim" — balance per product, transfer history,
  form for own +/− entries. Sale form validates against their ombor. Everything
  else unchanged.
- **Admin/Menejer:** everything, including every seller's ombor and sklad kassa.

## Cutover migration

Data migration only — no schema rewrites of existing tables, no date-based
branching left in code afterward:

1. Per product, insert one negative `StockEntry` equal to historical
   (sold − restocked returns), note "Cutover moslash". Sklad then equals today's
   real net stock under the new formula.
2. Per (seller, product), insert an offsetting positive `SellerStockEntry` equal
   to their historical net sales, so every seller ombor starts at exactly 0.

## UI

New sidebar section "Ishlab chiqarish": materials list (stock + avg cost),
purchases, production runs (dynamic material lines like the sale-item formset),
sklad ombor, transfers, sklad kassa. Sellers get "Mening omborim". Search boxes
follow the established pill-with-icon live-search toolbar pattern.

## Audit & data safety

- Purchases, runs, transfers, and seller entries write `AuditLog` rows, same as
  money actions.
- Edits/deletes of purchases and runs are admin-only; afterwards the material's
  avg cost is recomputed by replaying history chronologically.

## Testing

- pytest: average-cost math, balance formulas, cutover migration, blocking
  validations, per-role permission tests.
- Playwright happy path: buy material → production run → transfer → seller sale
  → sklad kassa reflects all of it (existing E2E setup / playwright skill).

## Phasing

Single implementation plan; the module is self-contained. Later ideas explicitly
out of scope now: recipes/BOM templates, non-kg units, supplier credit tracking,
full multi-cashbox (Cashbox objects) rework.
