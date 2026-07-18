# Per-seller ombor (goods received from production) — design

**Date:** 2026-07-15
**Status:** Approved (design), pending implementation plan

## Goal

Give every seller their **own warehouse (ombor)**. Sellers log the goods they
receive from production, sell out of their own stock, and see only their own
ombor — the shared/global warehouse view goes away for them. Admins/managers keep
a consolidated view across all sellers.

This is an **inventory/accountability** feature only. The kassa and
production-debt logic are **not** changed (production debt stays
`sold tannarx − remitted`).

## Decisions (locked)

| Question | Decision |
|---|---|
| What does a receipt affect? | Inventory/audit only — debt logic unchanged. |
| Stock origin | **Direct**: production → seller ombor. No central warehouse above sellers. |
| Oversell on a sale | **Block** — a seller cannot sell more than their ombor holds. |
| Model shape | New **`ProductionReceipt` + `ProductionReceiptItem`** (header + lines). |
| Cutover | **One-time opening count** — each seller logs what they physically hold at launch. |

## Current state (what exists today)

- Stock is a **single global warehouse**: `StockEntry` records goods in (kg);
  `Product.current_stock = total_received − total_sold + total_returned`
  (`ProductQuerySet.with_stock()`), a single number per product.
- A seller's stock is **not** tracked; the docstring on `ProductionRemittance`
  even notes "a seller takes goods from the shared warehouse" — but that taking
  is never recorded.
- Sales (`SaleItem`, keyed to `sale.sales_rep`) decrement global stock.
  `_warn_if_negative_stock` (`views.py`) only *warns* when global stock < 0.
- `ProductionRemittance` already models the cash side (seller → production).
- Existing `StockEntry` touchpoints that must change when it retires as the
  goods-in source: `ProductForm` opening-stock field (`forms.py:105`),
  `product_list` (`views.py:680`), the stock-adjust view/form (`views.py:793,810`).

## Design

### 1. Model — per-seller ombor via receipts

Two new tables, mirroring `Sale`/`SaleItem` and sitting next to
`ProductionRemittance` (its cash-side twin):

- **`ProductionReceipt`** (header) — one production→seller handover:
  - `seller` (FK User), `date` (default today), `note` (blank),
    `created_by` (FK User), `created_at`.
  - `Meta.ordering = ["-date", "-created_at"]`.
- **`ProductionReceiptItem`** (line):
  - `receipt` (FK, `related_name="items"`), `product` (FK, PROTECT),
    `quantity_kg` (Decimal, 12/3 — same unit as `StockEntry.quantity_kg`). May be
    **negative** for an admin write-off / correction (a receipt line can subtract).

`StockEntry` is **retired as the goods-in source** — the primary stock-in becomes
`ProductionReceiptItem`, and corrections/write-offs are done with a negative
receipt line (above) rather than a separate mechanism. Existing `StockEntry` rows
are ignored by the ombor era (they predate the cutover); the table may be dropped
in a later cleanup.

### 2. Per-seller stock computation

Per seller, per product:

```
on_hand = Σ received  −  Σ sold-by-seller  +  Σ restocked-returns-by-seller
```

- received → `ProductionReceiptItem` where `receipt.seller = S`
- sold → `SaleItem` where `sale.sales_rep = S`
- returns → `Return` where `sale.sales_rep = S` and `restock = True`

The seller is derivable everywhere (sales and returns both reach the seller via
`sale.sales_rep`), so **no schema change to `SaleItem` or `Return`.** Implemented
as a `ProductQuerySet.with_stock(seller=…)` variant using the same subquery style
already in `with_stock()`.

**Global stock (admin) = sum over all sellers.** `Product.total_received` /
`current_stock` / `with_stock()` are rewired to read `ProductionReceiptItem`
instead of `StockEntry`.

### 3. Cutover — one-time opening count

To avoid every seller starting deeply negative (they have historical sales but no
receipts), the ombor era starts at a **cutover date** (`OMBOR_START_DATE`,
a settings constant):

- **All** stock calculations — per-seller and the admin global total — count only
  receipts / sales / returns dated **on or after** `OMBOR_START_DATE`. Pre-cutover
  history is ignored. Because the opening receipts capture current physical stock
  at the cutover, the global total stays correct too.
- At launch each seller logs an **opening receipt** (dated at cutover) for the
  goods they physically hold. From then on, ombor = opening + new receipts −
  new sales + restocked returns.

This keeps the numbers real and auditable, with no fabricated data, and cleanly
separates the new inventory era from legacy sales.

### 4. Sale flow — block on oversell

At sale create/edit, for each line validate the requested quantity against **the
sale's seller's current ombor** for that product (aggregating duplicate lines of
the same product). If insufficient, raise a **form validation error** —
`"Omboringizda yetarli emas — {product}: qoldiq {x} kg"` — and reject the sale.

- Enforced **going forward only**; existing sales are untouched.
- The check is **uniform per `sales_rep`** — there is no admin bypass. A seller
  selling their own stock checks their ombor; an admin creating on behalf checks
  the chosen `sales_rep`'s ombor; an admin who is themselves the `sales_rep`
  checks their own ombor (so an admin who sells also needs receipts).
- Replaces the soft `_warn_if_negative_stock` path.

### 5. Views & scoping

- **Sellers**
  - New **"Mening omborim"** view — per-product on-hand for that seller.
  - Product/stock figures shown to a seller are scoped to *their* ombor. The
    overall warehouse is no longer shown to them.
  - Receipt CRUD (list / create / edit / delete), seller pinned to self —
    mirrors the `ProductionRemittance`/`Expense` view pattern.
- **Admins/managers**
  - Consolidated ombor view: total per product **plus** the per-seller breakdown.
  - Reconciliation angle: received vs sold vs on-hand, flagging negatives.
  - Can log receipts on behalf of any seller.
- Navigation gains an **Ombor / Qabul** entry.

### 6. Permissions

Follow the existing `can_see_all_records` split: sellers manage their own
receipts and see only their own ombor; admins/managers manage all and see the
consolidated view.

## Out of scope (explicitly unchanged)

- **Kassa & production-debt logic** — stays `sold tannarx − remitted`.
- No central/two-tier warehouse.
- No currency dimension on goods (ombor is kg; tannarx/debt stay in so'm as-is).
- No per-seller pricing.

## Affected areas

- `crm/models.py` — 2 new models; rewire `ProductQuerySet.with_stock` (+ seller
  variant); `Product.total_received`/`current_stock` read receipts; migration.
- `crm/forms.py` — `ProductionReceiptForm` + item formset; sale-form ombor
  validation; rework/remove the `ProductForm` opening-stock field.
- `crm/views.py` — receipt CRUD; "Mening omborim" + admin consolidated view;
  `product_list` scoping; sale create/edit block; rework the stock-adjust view.
- `crm/urls.py` — receipt + ombor routes.
- `templates/crm/` — receipt form/list, ombor view(s), `product_list` scoping,
  nav.
- `crm/tests.py` — see below.

## Testing

- Per-seller `on_hand` calc (received − sold + restocked returns), and
  global = sum of sellers.
- Cutover: records before `OMBOR_START_DATE` excluded; opening receipt seeds
  correctly.
- Block-on-oversell: seller selling their own stock; admin selling on behalf of a
  seller; aggregated duplicate lines; edit path.
- Restocked return credits the correct seller's ombor.
- Receipt CRUD + scoping (seller sees/edits only own; admin sees all).
- Kassa/debt figures unchanged by receipts (regression guard).

## Risks for the plan

- **`ProductForm` opening-stock field** (`forms.py:105`) currently creates a
  `StockEntry` on product create — must become a seller receipt or be dropped.
- **Performance**: per-seller stock subqueries on the product list — mirror the
  existing annotated `with_stock()` approach to stay a single query.
- **Cutover data hygiene**: opening counts must be entered before sellers start
  creating sales, or the block will fire on real stock that isn't logged yet.
