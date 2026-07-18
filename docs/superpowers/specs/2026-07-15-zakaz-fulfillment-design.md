# Zakaz (backorder) fulfillment — design

**Date:** 2026-07-15
**Status:** Approved (design), follows [seller-ombor](2026-07-15-seller-ombor-design.md)

## Why

Sellers work **on zakaz** — they sometimes sell goods that aren't in stock yet
(a pre-order). The hard oversell block from the ombor feature is therefore wrong:
selling short must be *allowed*. When production later delivers the stock, some of
it is already spoken for by customers who ordered, so the seller should be able to
**bind** the arriving stock to those pending orders.

## Decisions (locked)

| Question | Decision |
|---|---|
| Oversell on a sale | **Allow, but confirm** ("zakaz sifatida davom etilsinmi?"). Ombor goes negative = a backorder. |
| Bind on receipt | **Fulfill pending orders** — pick which customers' orders the arriving stock makes ready. |
| Grain | Whole line-item (a `SaleItem` is pending or ready); partial fulfilment is a later extension. |

## Model

Add to **`SaleItem`**:
- `fulfilled_at` (DateField, null=True) — when the ordered goods became available.
- `fulfilled_by_receipt` (FK → `ProductionReceipt`, null, SET_NULL) — the receipt
  that fulfilled it (the binding record).

Rules:
- In-stock sale line → `fulfilled_at = sale.date` at creation.
- Zakaz line (sold short) → `fulfilled_at = NULL` → **pending order**.
- Binding at receipt → sets `fulfilled_at` (receipt date) + `fulfilled_by_receipt`.

**Pending zakaz = `SaleItem.objects.filter(fulfilled_at__isnull=True)`.** This is
orthogonal to ombor: a pending line still counts as *sold*, so ombor already shows
the backorder as negative. The ombor stock math is unchanged.

**Migration:** add the two fields; backfill existing lines `fulfilled_at = sale.date`
(all pre-existing sales were in-stock under the old rules).

## Oversell → soft confirm (replaces the hard block)

`_block_if_ombor_short` becomes `_zakaz_shortfall` (detect only). In
`sale_create`/`sale_edit`:
- Compute the per-product shortfall (existing logic).
- If short **and** `allow_zakaz` not set → re-render with
  "Omborda yetarli emas — zakaz sifatida davom etilsinmi?" and a confirm button
  that resubmits with `allow_zakaz=1`. Nothing saved.
- If short **and** `allow_zakaz` set (or no shortfall) → save. Each line's
  `fulfilled_at` = `NULL` if that product was short, else `sale.date`.

## Bind step when stock arrives

After a receipt saves, if the seller has pending zakaz lines for any received
product → redirect to a **bind page** (`receipt_bind`): pending lines listed by
customer + product, each with a checkbox. Ticking sets `fulfilled_at` (receipt
date) + `fulfilled_by_receipt`. If none pending → go straight to the ombor page.

Also surface a **"Zakaz — kutilmoqda"** section on the ombor page listing pending
orders, so they can be bound anytime, not only right after a receipt.

## Display

- A **"Zakaz" / "Tayyor"** badge on sale line items / the sale list where useful.

## Out of scope / unchanged

- Kassa & production-debt logic — untouched (order status only).
- Partial-quantity fulfilment (order 100, 60 arrives) — v1 binds whole lines.
- The deferred shared-warehouse retirement (separate task) is independent of this.

## Affected areas

- `crm/models.py` — `SaleItem` fields + migration/backfill; a `fulfilled` helper.
- `crm/views.py` — `sale_create`/`sale_edit` soft-confirm + line fulfilment;
  `receipt_create` → bind redirect; new `receipt_bind` view; ombor pending list.
- `crm/forms.py` — none required (uses POST flags); maybe a bind form.
- `templates/crm/` — sale form confirm prompt, `ombor.html` pending section,
  a bind page, sale badges.
- `crm/tests.py` — flip block tests to confirm-then-allow; add fulfilment + bind tests.

## Testing

- In-stock sale line → `fulfilled_at` set; zakaz line → NULL.
- Oversell without confirm → prompt, nothing saved; with `allow_zakaz` → saved, short line pending.
- Receipt with pending zakaz → redirects to bind; binding sets `fulfilled_at` + `fulfilled_by_receipt`.
- Pending-zakaz list scoped to the seller.
- Ombor math and kassa figures unchanged by fulfilment state.
