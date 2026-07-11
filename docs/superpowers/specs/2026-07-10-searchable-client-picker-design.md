# Searchable client picker on the sale form

**Date:** 2026-07-10
**Status:** Approved

## Problem

When recording a sale, the client is chosen from a `<select>` enhanced into a
type-to-filter combobox. That combobox filters options only by their visible
text — the client name (`Client.__str__` returns `self.name`). A user who
remembers a client's phone number or address, but not the exact stored name,
can't find them. The team wants to search a client by **name, phone, or
address**, and to combine tokens so that e.g. "Ali Chilonzor" (name + address)
narrows to the right person.

## Approach

Extend the existing shared combobox rather than add a server-side typeahead.
The sale form already renders every selectable client as an `<option>` inside
`<select data-combobox>`, so the full list is already in the DOM and
client-side filtering is the natural fit. Three small, backward-compatible
changes:

### 1. Backend — a searchable Select widget (`crm/forms.py`)

A `Select` subclass overrides `create_option` to attach two data attributes to
each client option, read from the option's `value.instance` (a
`ModelChoiceIteratorValue`; Django 6.0):

- `data-search` — a lowercased haystack combining `name + company + phone +
  address`, plus a **digits-only copy of the phone** so that "998 90" and
  "9012345" both match a stored `+998 90 123 45 67`.
- `data-subtitle` — `phone · address` (dropping blanks) for the grey second
  line in the dropdown.

Applied only to `SaleForm.client`. The empty "— choose —" option has no
instance and is guarded (no data attributes).

### 2. Frontend — teach the shared combobox to use them (`templates/base.html`)

- In `build(filter)`: the haystack per option is `opt.dataset.search ||
  opt.textContent`. Split the query on whitespace and keep the option only if
  **every** token is a substring of the haystack. This single rule delivers
  "name OR phone OR address" *and* "name + address combined".
- Render a two-line option when `opt.dataset.subtitle` is present: the name on
  top, the subtitle muted below. Options without `data-subtitle` render
  single-line exactly as today.
- Backward compatible: every other `data-combobox` (product, owner, filter
  drawer) carries no `data-search`/`data-subtitle`, so it falls back to today's
  name-only text behavior untouched.

### 3. CSS (`static/css/app.css`)

Add `.combobox-sub` (small, muted) and minor `.combobox-option` layout so the
stacked two-line item reads cleanly.

## Testing

- Widget/form unit test: a client option's rendered HTML carries the expected
  `data-search` (including the digits-only phone) and `data-subtitle`; the empty
  option carries neither.
- E2E test modeled on `tests/test_e2e_smoke.py`: on the sale form, type a phone
  fragment plus an address token and assert the right client filters in and can
  be selected.

## Out of scope

The client filter dropdown in `templates/crm/_filter_drawer.html` renders its
options directly in the template (not via a form field). It could get the same
treatment later; left alone here to keep this change focused on recording a
sale.
