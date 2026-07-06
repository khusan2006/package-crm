# Searchable filters & cross-page filter consistency

**Date:** 2026-07-06
**Status:** Approved (design) — pending implementation plan

## Problem

1. The filter drawers use plain `<select>` dropdowns. When a list (clients,
   products, sellers) grows long, users cannot type to find an option.
2. The three list pages are inconsistent: **Sotuvlar** has a modern
   toolbar + slide-in filter drawer; **To'lov** has an older inline date-only
   `.searchbar`; **Qarz** has no filter at all.

## Goals

- Turn every **dynamic (DB-backed) select** into a searchable, type-to-filter
  combobox — in filter drawers **and** create/edit forms.
- Give **Sotuvlar**, **To'lov**, and **Qarz** the same filter pattern (same
  button, same slide-in drawer, same top-right position), with fields adapted
  to each page's data.

## Non-goals

- No change to fixed-choice selects (payment status paid/debt/overdue, method
  cash/card/transfer, dimension kg/g, user role). These stay plain `<select>`.
- No new JS/CSS dependency — vanilla only, matching the existing hand-rolled
  date-range picker.
- No change to money handling, permissions/visibility, or the sales page's
  existing date-vs-filter behavior.

---

## Component 1 — Searchable combobox (vanilla, progressive enhancement)

A single JS module in `templates/base.html` plus CSS in `static/css/app.css`.

### Behavior

- Enhances any `<select data-combobox>`: renders a text input + a filterable
  dropdown listbox on top of the native select. The native `<select>` stays in
  the DOM (visually hidden, kept in sync) so **form submission and the existing
  server-side filter logic are unchanged**.
- Typing filters options by case-insensitive substring. Shows **"Topilmadi"**
  when nothing matches. Keeps the placeholder/"Barchasi" reset option.
- Picking an option sets `select.value` **and dispatches a native `change`
  event**, so existing listeners keep working (e.g. the sale form's
  product→price/cost auto-fill in `base.html`).
- The dropdown list is **rebuilt from the live `<select>` each time it opens**
  (no cached option list), so options added at runtime — e.g. the quick-add
  customer flow's `select.add(new Option(...))` — appear automatically. After
  quick-add, the code dispatches `change` so the combobox input reflects the
  new selection.

### Accessibility & interaction

- `role="combobox"` on the input, `role="listbox"`/`option` on the popup,
  `aria-expanded`, `aria-activedescendant`.
- Keyboard: ↑/↓ move the active option, Enter selects, Esc closes, typing
  filters. Click-outside and Esc close the popup.
- Works inside the slide-in drawer and inside modal dialogs.

### Marked selects

- **Filter drawers** (raw markup): `client`, `product`, `rep` gain
  `data-combobox`.
- **Forms** (via widget attr in `forms.py __init__`): `SaleForm.client`,
  `SaleItemForm.product`, `ReturnForm.product`.

### Init hooks (cover the dynamic cases)

One exposed `enhanceComboboxes(root)`; each enhanced select is marked idempotent
(`dataset.comboboxReady`) so re-runs are safe. Called from:

1. `DOMContentLoaded` → static selects (filter drawers, non-modal forms).
2. `modal:loaded` event → sale/return create/edit forms opened in the dialog.
3. The sale formset "add row" handler → the product select in the new row.

---

## Component 2 — Shared filter UI

Extract the sales toolbar + drawer into reusable partials rather than
copy-pasting per page:

- `templates/crm/_filter_toolbar.html` — the top bar: left = active-filter
  chips / date label; right = date-range picker (where applicable), Filtrlash
  button (opens drawer), CSV (where applicable).
- `templates/crm/_filter_drawer.html` — the slide-in drawer; renders only the
  fields enabled for the page.

Both are driven by per-page context (booleans + the option querysets + the
form `action` URL + the CSV export URL). `sale_list.html` is refactored to use
them; `payment_list.html`'s inline `.searchbar` is **replaced** by them;
`debt_list.html` gains them.

Existing CSS (`.table-toolbar`, `.filter-drawer`, `.daterange`, chips) and the
existing drawer-open/close + date-picker JS are reused unchanged — they key off
`#filter-drawer` and `.daterange`, one instance per page.

---

## Component 3 — Per-page filter fields (adapt per page)

| Page | Drawer fields |
|---|---|
| **Sotuvlar** | client, product, seller\*, status (paid/debt/overdue) + date range — unchanged |
| **To'lov** | client, seller\*, method (cash/card/transfer) + date range |
| **Qarz** | client, seller\* + "faqat muddati o'tgan" (overdue-only) toggle |

\* seller shown only to `can_see_all_records` (admin/manager), matching Sotuvlar.

---

## Component 4 — Backend

### `payment_list` (crm/views.py)

Add filtering on top of the existing date range (they **compose** — date AND
filters apply together):

- `client` → `sale__client_id`
- `rep` → `sale__sales_rep_id` (only when `can_see_all_records`)
- `method` → `method` (cash/card/transfer)

Pass to the template: the client list, seller list, active-filter chips, and the
selected values. Reuse the same drawer/toolbar partials.

### `debt_list` (crm/views.py)

The view aggregates one row per debtor client. Apply filters to the underlying
`open_sales` **before** aggregation:

- `client` → `client_id`
- `rep` → `sales_rep_id` (only when `can_see_all_records`)
- `overdue` (checkbox) → keep only sales with `debt_deadline < today`

Pass the client/seller lists + active chips to the template.

### Active-filter chips

Generalize chip-building into a small reusable helper (param → label + value +
remove-URL) so each view builds its chips from a short config, rather than
duplicating `_active_filter_chips`.

### Behavior note (deliberate)

- **Sotuvlar** keeps its current rule: a content filter searches all dates and
  the date window is suppressed (unchanged).
- **To'lov / Qarz**: date range and filters **compose** (e.g. "card payments in
  January"), which matches how those pages are queried.

---

## Files

**Modify**

- `static/css/app.css` — combobox styles (+ minor shared-filter tweaks).
- `templates/base.html` — combobox JS module + the three init hooks.
- `crm/forms.py` — `data-combobox` widget attr on the three form selects.
- `crm/views.py` — generalize chip helper; add filtering + context to
  `payment_list` and `debt_list`.
- `templates/crm/sale_list.html` — use the shared partials; mark drawer selects.
- `templates/crm/payment_list.html` — replace `.searchbar` with shared partials.
- `templates/crm/debt_list.html` — add shared partials.

**Add**

- `templates/crm/_filter_toolbar.html`
- `templates/crm/_filter_drawer.html`

---

## Verification

Run via the dev server (light theme default) and drive with the browser preview:

- **Combobox**: type in each enhanced select (drawer + sale form + return form),
  confirm live filtering, keyboard nav, "Topilmadi", selection submits the right
  value. Confirm a **newly added** sale line-item row's product picker is
  searchable, and a **quick-added** customer appears selected.
- **Regression**: product→price/cost auto-fill still fires (native `change`
  dispatched). Existing sales filters still return the same results.
- **Consistency**: Filtrlash button + drawer render in the same top-right
  position on all three pages. Each page filters correctly (client/seller/method
  on To'lov; client/seller/overdue on Qarz) and active-filter chips clear
  individually.
- No server errors; no console errors.
