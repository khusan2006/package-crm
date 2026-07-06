# Searchable Filters & Cross-Page Filter Consistency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every dynamic (DB-backed) `<select>` a searchable type-to-filter combobox, and give the To'lov and Qarz pages the same slide-in filter drawer that Sotuvlar has, in the same position.

**Architecture:** A single vanilla-JS combobox module in `templates/base.html` progressively enhances any `<select data-combobox>` — the native select stays in the DOM (kept in sync) so form submission and existing `change` listeners are untouched. The sales toolbar + drawer are extracted into two reusable partials driven by per-page context flags; `payment_list` and `debt_list` gain server-side filtering that mirrors `_filter_sales`.

**Tech Stack:** Django 6 templates + views, vanilla JS (no new dependency), custom CSS in `static/css/app.css`. Backend tests via Django `TestCase`.

## Global Constraints

- **No new JS/CSS dependency** — vanilla only, matching the hand-rolled date-range picker already in `base.html`.
- **No JS test framework exists** and none may be added. Frontend tasks (combobox, templates) are verified through the browser **preview tools** against the running dev server; backend tasks use `.venv/bin/python manage.py test`.
- **Dev server:** launch config `django-dev` (port 8000). Login `admin` / `demo1234`. Seed sales are on past dates — use `?dan=2026-01-01&gacha=2026-07-06` to see populated lists.
- **Default theme is light.** UI must look correct in light (and still work in dark via the toggle).
- **Fixed-choice selects stay plain** `<select>`: payment status (paid/debt/overdue), method (cash/card/transfer), dimension (kg/g), user role.
- **Seller filter** is shown only to `request.user.can_see_all_records` (admin/manager), matching Sotuvlar today.
- Uzbek UI copy. Currency amounts render with `floatformat:"0g"`.
- Run the full test suite before each commit on backend tasks: `.venv/bin/python manage.py test`.
- **Commit messages** match the repo style: capitalized imperative, no conventional-commit prefix (e.g. `Add a searchable combobox to the sales filter`, not `feat: ...`). End each with the `Co-Authored-By` trailer.

---

## Task 1: Combobox component (JS + CSS), applied to the Sotuvlar filter drawer

**Files:**
- Modify: `static/css/app.css` (append a "Searchable combobox" section)
- Modify: `templates/base.html` (add the combobox `<script>` module near the other IIFEs, before `</script></body>`)
- Modify: `templates/crm/sale_list.html:104,110,117` (add `data-combobox` to client/product/rep selects)

**Interfaces:**
- Produces: global `window.enhanceComboboxes(root)` — enhances every `select[data-combobox]` under `root` (defaults to `document`); idempotent per select (`dataset.comboboxReady`). Enhancing a select sets its value via a real `change` event so existing listeners fire.

- [ ] **Step 1: Add combobox CSS**

Append to the end of `static/css/app.css`:

```css
/* ============================================================
   Searchable combobox (progressive enhancement of <select>)
   ============================================================ */
.combobox { position: relative; width: 100%; }
.combobox-native { display: none; }               /* still submits with the form */
.combobox-input { max-width: none; cursor: text; }
.combobox-pop {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 50;
  max-height: 240px;
  overflow-y: auto;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--r);
  box-shadow: var(--shadow-lg);
}
.combobox-option {
  padding: 8px 10px;
  border-radius: var(--r-sm);
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.combobox-option:hover,
.combobox-option.is-active { background: var(--surface-hover); }
.combobox-option[aria-selected="true"] { color: var(--accent); font-weight: 600; }
.combobox-empty { padding: 8px 10px; color: var(--text-muted); font-size: 13px; }
```

- [ ] **Step 2: Add the combobox JS module**

In `templates/base.html`, inside the main `<script>` block at the end of `<body>` (alongside the other IIFEs), add:

```javascript
  // Searchable combobox: progressively enhances <select data-combobox> into a
  // type-to-filter input. The native <select> stays in the DOM (kept in sync)
  // so the form submits normally and existing change-listeners keep working.
  (function () {
    function enhance(select) {
      if (select.dataset.comboboxReady) { return; }
      select.dataset.comboboxReady = '1';

      var wrap = document.createElement('div');
      wrap.className = 'combobox';
      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'combobox-input';
      input.autocomplete = 'off';
      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-expanded', 'false');
      var pop = document.createElement('div');
      pop.className = 'combobox-pop';
      pop.setAttribute('role', 'listbox');
      pop.hidden = true;

      select.parentNode.insertBefore(wrap, select);
      wrap.appendChild(select);
      wrap.appendChild(input);
      wrap.appendChild(pop);
      select.classList.add('combobox-native');

      var visible = [];
      var active = -1;

      function selectedText() {
        var o = select.options[select.selectedIndex];
        return o ? o.textContent.trim() : '';
      }
      function syncInput() { input.value = selectedText(); }

      function build(filter) {
        var q = (filter || '').toLowerCase();
        pop.innerHTML = '';
        visible = [];
        Array.prototype.forEach.call(select.options, function (opt, i) {
          var text = opt.textContent.trim();
          if (q && text.toLowerCase().indexOf(q) === -1) { return; }
          var item = document.createElement('div');
          item.className = 'combobox-option';
          item.setAttribute('role', 'option');
          item.textContent = text;
          item.dataset.index = i;
          if (i === select.selectedIndex) { item.setAttribute('aria-selected', 'true'); }
          pop.appendChild(item);
          visible.push(item);
        });
        if (!visible.length) {
          var empty = document.createElement('div');
          empty.className = 'combobox-empty';
          empty.textContent = 'Topilmadi';
          pop.appendChild(empty);
        }
        active = -1;
      }

      function open() {
        build('');                 // rebuild from the live <select> each open
        pop.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        input.select();
      }
      function close() {
        pop.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        syncInput();               // discard any half-typed text
      }
      function choose(i) {
        select.selectedIndex = i;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        close();
      }
      function setActive(n) {
        if (!visible.length) { return; }
        active = (n + visible.length) % visible.length;
        visible.forEach(function (el, i) { el.classList.toggle('is-active', i === active); });
        visible[active].scrollIntoView({ block: 'nearest' });
      }

      input.addEventListener('focus', function () { if (pop.hidden) { open(); } });
      input.addEventListener('click', function () { if (pop.hidden) { open(); } });
      input.addEventListener('input', function () {
        pop.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        build(input.value);
      });
      input.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); if (pop.hidden) { open(); } setActive(active + 1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
        else if (e.key === 'Enter') {
          if (!pop.hidden && active >= 0) { e.preventDefault(); choose(parseInt(visible[active].dataset.index, 10)); }
        } else if (e.key === 'Escape') { if (!pop.hidden) { e.preventDefault(); close(); } }
      });
      input.addEventListener('blur', function () { setTimeout(close, 120); });
      pop.addEventListener('mousedown', function (e) {
        var opt = e.target.closest('.combobox-option');
        if (opt) { e.preventDefault(); choose(parseInt(opt.dataset.index, 10)); }
      });
      select.addEventListener('change', syncInput);

      syncInput();
    }

    function enhanceComboboxes(root) {
      (root || document).querySelectorAll('select[data-combobox]').forEach(enhance);
    }
    window.enhanceComboboxes = enhanceComboboxes;

    if (document.readyState !== 'loading') { enhanceComboboxes(); }
    else { document.addEventListener('DOMContentLoaded', function () { enhanceComboboxes(); }); }
    document.addEventListener('modal:loaded', function (e) { enhanceComboboxes(e.detail || document); });
  })();
```

- [ ] **Step 3: Mark the Sotuvlar drawer selects**

In `templates/crm/sale_list.html`, add `data-combobox` to the three dynamic selects:

```html
      <select name="client" data-combobox>
```
```html
      <select name="product" data-combobox>
```
```html
      <select name="rep" data-combobox>
```

(Leave `<select name="status">` unchanged — it is a fixed list.)

- [ ] **Step 4: Verify in the browser preview**

Start the server and open the Sotuvlar page:
- `preview_start` name `django-dev`; log in `admin`/`demo1234`; navigate to `http://127.0.0.1:8000/sales/?dan=2026-01-01&gacha=2026-07-06`.
- Click **Filtrlash** to open the drawer.
- In the **Mijoz** field: confirm a text input appears, typing filters the list, arrow keys + Enter select, Esc closes, an unmatched query shows "Topilmadi".
- Pick a client, submit **Qo'llash**; confirm the URL gets `?client=<id>` and the table filters correctly (unchanged server behavior).
- Confirm **status** is still a normal dropdown.
- `preview_console_logs` level error → none. Screenshot as proof.

- [ ] **Step 5: Commit**

```bash
git add static/css/app.css templates/base.html templates/crm/sale_list.html
git commit -m "Add a searchable combobox to the sales filter selects"
```

---

## Task 2: Combobox in create/edit forms + dynamic init (modal, formset, quick-add)

**Files:**
- Modify: `crm/forms.py` (add `data-combobox` widget attr in `SaleForm.__init__`, `SaleItemForm.__init__`, `ReturnForm.__init__`)
- Modify: `templates/base.html` (call `enhanceComboboxes` on the new formset row; dispatch `change` after quick-add)

**Interfaces:**
- Consumes: `window.enhanceComboboxes(root)` from Task 1; the existing `modal:loaded` event (already dispatched in `base.html` `openModal`).

- [ ] **Step 1: Mark the form selects as comboboxes**

In `crm/forms.py`, inside `SaleForm.__init__` (after the existing body):

```python
        self.fields["client"].widget.attrs["data-combobox"] = ""
```

Inside `SaleItemForm.__init__` (after `self.fields["product"].queryset = ...`):

```python
        self.fields["product"].widget.attrs["data-combobox"] = ""
```

Locate `ReturnForm.__init__` and add, after its `product` queryset is set:

```python
        self.fields["product"].widget.attrs["data-combobox"] = ""
```

(If `ReturnForm` has no `__init__`, add one:
```python
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].widget.attrs["data-combobox"] = ""
```
placed above its `Meta` is wrong — place it as a method inside the class, after `Meta`.)

- [ ] **Step 2: Enhance dynamically-added formset rows**

In `templates/base.html`, in the sale-item formset IIFE, find where a new row is appended:

```javascript
        rows.appendChild(wrap.firstElementChild);
        total.value = idx + 1;
```

Change to enhance the freshly added row:

```javascript
        var added = wrap.firstElementChild;
        rows.appendChild(added);
        total.value = idx + 1;
        if (window.enhanceComboboxes) { window.enhanceComboboxes(added); }
```

- [ ] **Step 3: Reflect quick-added customers in the combobox**

In `templates/base.html`, in the quick-add IIFE success handler, find:

```javascript
        select.add(new Option(res.data.text, res.data.id, true, true));
```

Add a `change` dispatch right after so the combobox input updates:

```javascript
        select.add(new Option(res.data.text, res.data.id, true, true));
        select.dispatchEvent(new Event('change', { bubbles: true }));
```

- [ ] **Step 4: Verify in the browser preview**

- On Sotuvlar, click **Yangi sotuv** (opens the sale form in a modal).
- Confirm **Mijoz** and the first line's **Mahsulot** are searchable comboboxes (init via `modal:loaded`).
- Click **add line** (`.item-add`); confirm the new row's **Mahsulot** is also searchable.
- Select a product in a line; confirm **price/cost auto-fill still fires** (the native `change` listener) — the price and cost inputs populate.
- Use the quick-add customer (`+`) panel; add a name; confirm the new customer appears **selected** in the Mijoz combobox input.
- Open a sale with a debt and use **Qaytarish** (return) if reachable; confirm its product select is searchable.
- `preview_console_logs` level error → none. Screenshot the sale form.

- [ ] **Step 5: Commit**

```bash
git add crm/forms.py templates/base.html
git commit -m "Make sale and return form selects searchable, including dynamic rows"
```

---

## Task 3: Extract shared filter partials; refactor Sotuvlar to use them

**Files:**
- Create: `templates/crm/_filter_toolbar.html`
- Create: `templates/crm/_filter_drawer.html`
- Modify: `templates/crm/sale_list.html` (replace inline toolbar + drawer with `{% include %}`s; pass config context)

**Interfaces:**
- Produces: two partials driven by context:
  - `_filter_toolbar.html` expects: `filter_url` (str), optional `export_url` (str), `active_filters`, `filter_count`, `has_filters`, and Sotuvlar-only date vars (`show_daterange_picker`, plus the existing `filters`, `is_today`, `is_single_day`, `date_from`, `date_to`, `range_days`, `prev_from`, `prev_to`, `next_from`, `next_to`, `today_iso`).
  - `_filter_drawer.html` expects: `filter_url`, `filters` (dict), `clients`; optional `products`, `reps`, and booleans `show_status`, `show_method`, `show_overdue`. (Date range lives in the toolbar picker, not the drawer.)

- [ ] **Step 1: Create `_filter_toolbar.html`**

Move the current toolbar markup out of `sale_list.html` into `templates/crm/_filter_toolbar.html`, generalized. The date-range calendar picker renders only when `show_daterange_picker` is set (Sotuvlar); the CSV button only when `export_url` is set:

```html
{% load crm_extras %}
<div class="table-toolbar">
  <div class="toolbar-left">
    {% if active_filters %}
    <div class="filter-chips">
      {% for chip in active_filters %}
      <span class="chip">{{ chip.label }}: <strong>{{ chip.value }}</strong><a href="{{ chip.remove_url }}" class="chip-x" title="Olib tashlash" aria-label="Olib tashlash">&times;</a></span>
      {% endfor %}
      <a href="{{ filter_url }}" class="chip-clear">Tozalash</a>
    </div>
    {% elif show_daterange_picker %}
    <div class="toolbar-label">
      {% if is_today %}<span class="today-chip">Bugun</span>
      {% elif is_single_day %}{{ date_from|date:"j-F, l" }}
      {% else %}{{ range_days }} kunlik oraliq{% endif %}
    </div>
    {% endif %}
  </div>
  <div class="toolbar-right">
    {% if show_daterange_picker and not active_filters %}
    <div class="daterange" data-from="{{ filters.dan }}" data-to="{{ filters.gacha }}" data-today="{{ today_iso }}">
      <a class="datebar-btn" href="{% qs_replace dan=prev_from gacha=prev_to %}" title="Oldingi kun">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
      </a>
      <button type="button" class="daterange-trigger" aria-haspopup="dialog" aria-expanded="false">
        <svg class="cal-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        <span class="daterange-text">
          {% if is_today %}Bugun{% elif is_single_day %}{{ date_from|date:"j-F" }}{% else %}{{ date_from|date:"j-F" }} – {{ date_to|date:"j-F" }}{% endif %}
        </span>
      </button>
      <a class="datebar-btn" href="{% qs_replace dan=next_from gacha=next_to %}" title="Keyingi kun">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
      </a>
      <div class="daterange-pop" role="dialog" aria-label="Sana tanlash" hidden></div>
    </div>
    {% if not is_today %}<a class="btn btn-sm btn-ghost" href="{% qs_replace dan=today_iso gacha=today_iso %}">Bugun</a>{% endif %}
    {% endif %}
    <button type="button" class="btn btn-sm btn-ghost filter-toggle{% if has_filters %} is-active{% endif %}" data-filter-open>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
      Filtrlash{% if filter_count %} <span class="filter-badge">{{ filter_count }}</span>{% endif %}
    </button>
    {% if export_url %}
    <a class="btn btn-sm btn-ghost" href="{{ export_url }}" title="CSV yuklab olish">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      CSV
    </a>
    {% endif %}
  </div>
</div>
```

- [ ] **Step 2: Create `_filter_drawer.html`**

```html
<div class="drawer-backdrop" data-filter-close hidden></div>
<aside class="filter-drawer" id="filter-drawer" aria-hidden="true" aria-label="Filtrlar">
  <div class="drawer-head">
    <h2>Filtrlar</h2>
    <button type="button" class="modal-x" data-filter-close aria-label="Yopish">&times;</button>
  </div>
  <form method="get" action="{{ filter_url }}" class="drawer-body">
    <label>Mijoz
      <select name="client" data-combobox>
        <option value="">Barchasi</option>
        {% for c in clients %}<option value="{{ c.pk }}" {% if filters.client == c.pk|stringformat:'s' %}selected{% endif %}>{{ c.name }}</option>{% endfor %}
      </select>
    </label>
    {% if products %}
    <label>Mahsulot
      <select name="product" data-combobox>
        <option value="">Barchasi</option>
        {% for p in products %}<option value="{{ p.pk }}" {% if filters.product == p.pk|stringformat:'s' %}selected{% endif %}>{{ p.name }}</option>{% endfor %}
      </select>
    </label>
    {% endif %}
    {% if reps %}
    <label>Sotuvchi
      <select name="rep" data-combobox>
        <option value="">Barchasi</option>
        {% for r in reps %}<option value="{{ r.pk }}" {% if filters.rep == r.pk|stringformat:'s' %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select>
    </label>
    {% endif %}
    {% if show_status %}
    <label>To'lov
      <select name="status">
        <option value="">Barchasi</option>
        <option value="paid" {% if filters.status == 'paid' %}selected{% endif %}>To'langan</option>
        <option value="debt" {% if filters.status == 'debt' %}selected{% endif %}>Qarz</option>
        <option value="overdue" {% if filters.status == 'overdue' %}selected{% endif %}>Muddati o'tgan</option>
      </select>
    </label>
    {% endif %}
    {% if show_method %}
    <label>To'lov usuli
      <select name="method">
        <option value="">Barchasi</option>
        <option value="cash" {% if filters.method == 'cash' %}selected{% endif %}>Naqd</option>
        <option value="card" {% if filters.method == 'card' %}selected{% endif %}>Karta</option>
        <option value="transfer" {% if filters.method == 'transfer' %}selected{% endif %}>O'tkazma</option>
      </select>
    </label>
    {% endif %}
    {% if show_overdue %}
    <label class="check-row"><input type="checkbox" name="overdue" value="1" {% if filters.overdue %}checked{% endif %}> Faqat muddati o'tgan</label>
    {% endif %}
    {% if show_status %}<p class="field-hint">Filtr qo'llanganda sana oralig'i hisobga olinmaydi — barcha kunlar bo'yicha qidiriladi.</p>{% endif %}
    <div class="drawer-actions">
      <button type="submit" class="btn">Qo'llash</button>
      <a href="{{ filter_url }}" class="btn btn-ghost">Tozalash</a>
    </div>
  </form>
</aside>
```

- [ ] **Step 3: Refactor `sale_list.html` to use the partials**

Replace the inline `<div class="table-toolbar">…</div>` block with:

```html
{% include "crm/_filter_toolbar.html" with show_daterange_picker=True export_url=sale_export_url %}
```

Replace the inline `<div class="drawer-backdrop">…</aside>` block at the bottom with:

```html
{% include "crm/_filter_drawer.html" with show_status=True %}
```

- [ ] **Step 4: Provide the new context keys from `sale_list`**

In `crm/views.py`, `sale_list`, add two context keys (the partials read `filter_url`, `export_url`) without changing existing behavior. Add to the render context dict:

```python
            "filter_url": reverse("sale_list"),
            "sale_export_url": reverse("sale_export") + (f"?{export_qs}" if export_qs else ""),
```

Then update the include to use `export_url=sale_export_url` (already shown). `filter_url` is picked up automatically by the partial from context.

- [ ] **Step 5: Add `.check-row` CSS (used by the overdue toggle later, harmless now)**

Append to `static/css/app.css`:

```css
.drawer-body .check-row { display: flex; align-items: center; gap: 8px; font-weight: 500; }
.drawer-body .check-row input[type=checkbox] { width: 16px; height: 16px; }
```

- [ ] **Step 6: Verify Sotuvlar is unchanged**

- Run the suite: `.venv/bin/python manage.py test` → all pass.
- Preview: Sotuvlar filter drawer opens in the same position, the date-range calendar still works, comboboxes still searchable, applying a filter still filters, chips clear individually. Screenshot.

- [ ] **Step 7: Commit**

```bash
git add templates/crm/_filter_toolbar.html templates/crm/_filter_drawer.html templates/crm/sale_list.html static/css/app.css crm/views.py
git commit -m "Extract shared filter toolbar and drawer partials"
```

---

## Task 4: Generalize backend filter helpers — chips + date-range context (tested)

**Files:**
- Modify: `crm/views.py` (add `_filter_chips` and `_date_range_context`; rewrite `_active_filter_chips` to use `_filter_chips`; refactor `sale_list` to use `_date_range_context`)
- Test: `crm/tests.py` (new `FilterChipTests`)

**Interfaces:**
- Produces: `_filter_chips(request, specs)` where `specs` is a list of `{"param": str, "label": str, "value": str}`; returns a list of `{"label","value","remove_url"}` for every spec whose `value` is truthy. The remove-URL drops that param, `page`, and any empty filter params among the specs.
- Produces: `_date_range_context(request)` → dict with `date_from`, `date_to`, `range_days`, `is_single_day`, `is_today`, `prev_from`, `prev_to`, `next_from`, `next_to`, `today_iso`. A today-default `dan`/`gacha` window plus the navigation vars the shared toolbar date-range picker needs; reused by `sale_list` and (Task 5) `payment_list`.

- [ ] **Step 1: Write the failing test**

Add to `crm/tests.py`:

```python
class FilterChipTests(BaseSetup):
    def test_sales_chips_resolve_names_and_remove_urls(self):
        self.client.force_login(self.admin)
        url = reverse("sale_list")
        resp = self.client.get(url, {"client": self.client1.pk, "status": "debt"})
        chips = resp.context["active_filters"]
        labels = {c["label"]: c["value"] for c in chips}
        self.assertEqual(labels["Mijoz"], "Mijoz A")
        self.assertEqual(labels["To'lov"], "Qarz")
        # removing the client chip keeps status, drops client + page
        client_chip = next(c for c in chips if c["label"] == "Mijoz")
        self.assertIn("status=debt", client_chip["remove_url"])
        self.assertNotIn("client=", client_chip["remove_url"])
```

- [ ] **Step 2: Run it to confirm it passes or fails as expected**

Run: `.venv/bin/python manage.py test crm.tests.FilterChipTests -v 2`
Expected: PASS if the refactor preserves behavior; run it first against current code to establish the baseline (it should already PASS with the existing `_active_filter_chips`, guarding the refactor).

- [ ] **Step 3: Add the generic helper**

In `crm/views.py`, add above `_active_filter_chips`:

```python
def _filter_chips(request, specs):
    """Build removable filter chips from a list of specs:
    {"param", "label", "value"}. Only specs with a truthy `value` produce a chip.
    The remove-URL drops that param, the page, and any empty filter params."""
    params = [s["param"] for s in specs]

    def without(param):
        qs = request.GET.copy()
        qs.pop(param, None)
        qs.pop("page", None)
        for key in params:
            if not qs.get(key):
                qs.pop(key, None)
        query = qs.urlencode()
        return f"{request.path}?{query}" if query else request.path

    return [
        {"label": s["label"], "value": s["value"], "remove_url": without(s["param"])}
        for s in specs
        if s.get("value")
    ]
```

- [ ] **Step 4: Rewrite `_active_filter_chips` on top of it**

Replace the body of `_active_filter_chips` with spec-building + delegation:

```python
def _active_filter_chips(request, filters, clients, products, reps):
    """Sotuvlar filter chips (client/product/rep/status)."""
    status_labels = {"paid": "To'langan", "debt": "Qarz", "overdue": "Muddati o'tgan"}
    client = clients.filter(pk=filters["client"]).first() if filters["client"].isdigit() else None
    product = products.filter(pk=filters["product"]).first() if filters["product"].isdigit() else None
    rep = reps.filter(pk=filters["rep"]).first() if reps and filters["rep"].isdigit() else None
    specs = [
        {"param": "client", "label": "Mijoz", "value": client.name if client else ""},
        {"param": "product", "label": "Mahsulot", "value": product.name if product else ""},
        {"param": "rep", "label": "Sotuvchi", "value": str(rep) if rep else ""},
        {"param": "status", "label": "To'lov", "value": status_labels.get(filters["status"], "")},
    ]
    return _filter_chips(request, specs)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python manage.py test crm.tests.FilterChipTests -v 2` and then the full suite `.venv/bin/python manage.py test`.
Expected: PASS.

- [ ] **Step 6: Add `_date_range_context` and refactor `sale_list` to use it**

In `crm/views.py`, add near the other filter helpers:

```python
def _date_range_context(request):
    """Parse ?dan/?gacha into a today-default window plus the navigation vars
    the shared toolbar's date-range picker needs."""
    today = timezone.localdate()
    date_from = _parse_date(request.GET.get("dan")) or today
    date_to = _parse_date(request.GET.get("gacha")) or date_from
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return {
        "date_from": date_from,
        "date_to": date_to,
        "range_days": (date_to - date_from).days + 1,
        "is_single_day": date_from == date_to,
        "is_today": date_from == today and date_to == today,
        "prev_from": (date_from - timedelta(days=1)).isoformat(),
        "prev_to": (date_to - timedelta(days=1)).isoformat(),
        "next_from": (date_from + timedelta(days=1)).isoformat(),
        "next_to": (date_to + timedelta(days=1)).isoformat(),
        "today_iso": today.isoformat(),
    }
```

In `sale_list`, delete these ten keys from the render context dict and add `**_date_range_context(request)` in their place (identical values — both parse the same `dan`/`gacha` with the same today-default and swap):

```python
            "date_from": date_from,
            "date_to": date_to,
            "range_days": (date_to - date_from).days + 1,
            "is_single_day": date_from == date_to,
            "is_today": date_from == today and date_to == today,
            "prev_from": (date_from - timedelta(days=1)).isoformat(),
            "prev_to": (date_to - timedelta(days=1)).isoformat(),
            "next_from": (date_from + timedelta(days=1)).isoformat(),
            "next_to": (date_to + timedelta(days=1)).isoformat(),
            "today_iso": today.isoformat(),
```

If the `today = timezone.localdate()` line in `sale_list` is now unused, delete it (the helper computes its own).

- [ ] **Step 7: Run the suite (sales unchanged)**

Run: `.venv/bin/python manage.py test`
Expected: PASS. Preview Sotuvlar: the date-range picker + Bugun still work identically.

- [ ] **Step 8: Commit**

```bash
git add crm/views.py crm/tests.py
git commit -m "Generalize filter chip and date-range context helpers"
```

---

## Task 5: To'lov (Payments) filter — backend + template

**Files:**
- Modify: `crm/views.py` (`payment_list`: add client/rep/method filtering + partial context)
- Modify: `templates/crm/payment_list.html` (replace `.searchbar` with the shared partials)
- Test: `crm/tests.py` (new `PaymentFilterTests`)

**Interfaces:**
- Consumes: `_filter_chips` and `_date_range_context` (Task 4); `_visible_clients` (existing); partials from Task 3.

- [ ] **Step 1: Write failing tests**

Add to `crm/tests.py`. Note: filter params (client/rep/method) set `has_filters`, which — mirroring Sotuvlar — makes the query search **all dates** (the today-default window is suppressed), so these assertions hold regardless of the seed payment dates:

```python
class PaymentFilterTests(BaseSetup):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # sale1 (client1/sales1) and sale2 (client2/sales2) each got a CASH sale payment.
        Payment.objects.create(
            sale=cls.sale1, amount=Decimal("1000"), method=Payment.Method.CARD,
            kind=Payment.Kind.DEBT, date=timezone.localdate(), created_by=cls.sales1,
        )

    def _get(self, **params):
        self.client.force_login(self.admin)
        return self.client.get(reverse("payment_list"), params)

    def test_filter_by_client(self):
        resp = self._get(client=self.client1.pk)
        for p in resp.context["page"].object_list:
            self.assertEqual(p.sale.client_id, self.client1.pk)

    def test_filter_by_method(self):
        resp = self._get(method="card")
        methods = {p.method for p in resp.context["page"].object_list}
        self.assertEqual(methods, {"card"})

    def test_filter_by_rep(self):
        resp = self._get(rep=self.sales2.pk)
        for p in resp.context["page"].object_list:
            self.assertEqual(p.sale.sales_rep_id, self.sales2.pk)

    def test_seller_cannot_filter_by_other_rep(self):
        # a plain seller only ever sees their own; rep param is ignored for them
        self.client.force_login(self.sales1)
        resp = self.client.get(reverse("payment_list"), {"rep": self.sales2.pk})
        for p in resp.context["page"].object_list:
            self.assertEqual(p.sale.sales_rep_id, self.sales1.pk)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python manage.py test crm.tests.PaymentFilterTests -v 2`
Expected: FAIL (`test_filter_by_client`/`method`/`rep` return unfiltered results).

- [ ] **Step 3: Add filtering + context to `payment_list`**

Rewrite `payment_list` in `crm/views.py`:

```python
def payment_list(request):
    payments = Payment.objects.select_related(
        "sale", "sale__client", "created_by"
    ).prefetch_related("sale__items__product")
    if not request.user.can_see_all_records:
        payments = payments.filter(sale__sales_rep=request.user)

    filters = {key: request.GET.get(key, "") for key in ("client", "rep", "method")}
    has_filters = bool(
        filters["client"].isdigit()
        or filters["method"] in dict(Payment.Method.choices)
        or (filters["rep"].isdigit() and request.user.can_see_all_records)
    )

    dates = _date_range_context(request)
    filters["dan"] = dates["date_from"].isoformat()
    filters["gacha"] = dates["date_to"].isoformat()
    # Mirror Sotuvlar: a content filter searches all dates; otherwise the window applies.
    if not has_filters:
        payments = payments.filter(date__gte=dates["date_from"], date__lte=dates["date_to"])
    if filters["client"].isdigit():
        payments = payments.filter(sale__client_id=filters["client"])
    if filters["rep"].isdigit() and request.user.can_see_all_records:
        payments = payments.filter(sale__sales_rep_id=filters["rep"])
    if filters["method"] in dict(Payment.Method.choices):
        payments = payments.filter(method=filters["method"])
    payments = payments.order_by("-date", "-created_at")

    totals = payments.aggregate(
        total=Sum("amount"),
        cash=Sum("amount", filter=Q(method=Payment.Method.CASH)),
        card=Sum("amount", filter=Q(method=Payment.Method.CARD)),
        debt=Sum("amount", filter=Q(kind=Payment.Kind.DEBT)),
    )
    page = Paginator(payments, 30).get_page(request.GET.get("page"))

    outstanding = (
        Sale.objects.visible_to(request.user)
        .outstanding()
        .select_related("client", "sales_rep")
        .prefetch_related("items__product")
        .order_by("debt_deadline", "date")
    )

    clients = _visible_clients(request.user).order_by("name")
    reps = (
        User.objects.filter(is_active=True).order_by("first_name", "username")
        if request.user.can_see_all_records
        else None
    )
    method_labels = {"cash": "Naqd", "card": "Karta", "transfer": "O'tkazma"}
    client_obj = clients.filter(pk=filters["client"]).first() if filters["client"].isdigit() else None
    rep_obj = reps.filter(pk=filters["rep"]).first() if reps and filters["rep"].isdigit() else None
    active_filters = _filter_chips(request, [
        {"param": "client", "label": "Mijoz", "value": client_obj.name if client_obj else ""},
        {"param": "rep", "label": "Sotuvchi", "value": str(rep_obj) if rep_obj else ""},
        {"param": "method", "label": "Usul", "value": method_labels.get(filters["method"], "")},
    ])
    export_qs = request.GET.urlencode()
    return render(
        request,
        "crm/payment_list.html",
        {
            "page": page,
            "totals": totals,
            "outstanding": outstanding,
            "filters": filters,
            "clients": clients,
            "reps": reps,
            "active_filters": active_filters,
            "filter_count": len(active_filters),
            "has_filters": has_filters,
            "filter_url": reverse("payment_list"),
            "payment_export_url": reverse("payment_export") + (f"?{export_qs}" if export_qs else ""),
            **dates,
        },
    )
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python manage.py test crm.tests.PaymentFilterTests -v 2`
Expected: PASS.

- [ ] **Step 5: Swap the template's `.searchbar` for the shared partials**

In `templates/crm/payment_list.html`, replace the top `<form class="searchbar">…</form>` (lines 7–13) with the toolbar; add the drawer at the end of the `{% block content %}` (before `{% endblock %}`):

```html
{% include "crm/_filter_toolbar.html" with show_daterange_picker=True export_url=payment_export_url %}
```
and, just before `{% endblock %}`:
```html
{% include "crm/_filter_drawer.html" with show_method=True %}
```

(The toolbar sits where the old searchbar was — same top-right Filtrlash position, now with the same calendar date-range picker as Sotuvlar. Payments defaults to today's window; a content filter searches all dates.)

- [ ] **Step 6: Verify in the browser preview**

- Navigate to `http://127.0.0.1:8000/payments/`.
- Confirm the **Filtrlash** button + drawer + calendar date-range picker appear top-right (same layout as Sotuvlar); the old inline date bar is gone.
- Confirm the date-range picker works (‹ › day nav, presets, Bugun).
- Open the drawer: **Mijoz** and **Sotuvchi** are searchable comboboxes; **To'lov usuli** present. Apply `Karta`; confirm only card payments show, the date picker is replaced by chips, and a "Usul: Karta" chip appears and clears.
- `preview_console_logs` error → none. Screenshot.

- [ ] **Step 7: Commit**

```bash
git add crm/views.py crm/tests.py templates/crm/payment_list.html
git commit -m "Add the unified searchable filter to the payments page"
```

---

## Task 6: Qarz (Debts) filter — backend + template

**Files:**
- Modify: `crm/views.py` (`debt_list`: filter open sales by client/rep + overdue toggle; add partial context)
- Modify: `templates/crm/debt_list.html` (add the shared partials)
- Test: `crm/tests.py` (new `DebtFilterTests`)

**Interfaces:**
- Consumes: `_filter_chips` (Task 4); `_visible_clients`; partials from Task 3.

- [ ] **Step 1: Write failing tests**

Add to `crm/tests.py`:

```python
class DebtFilterTests(BaseSetup):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # one overdue debt for client1/sales1, one current debt for client2/sales2
        cls.overdue = make_sale(
            cls.client1, cls.sales1, cls.product, is_debt=True,
            debt_deadline=timezone.localdate() - timedelta(days=3),
        )
        cls.current = make_sale(
            cls.client2, cls.sales2, cls.product, is_debt=True,
            debt_deadline=timezone.localdate() + timedelta(days=10),
        )

    def _debtors(self, **params):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("debt_list"), params)
        return {g["client"].pk for g in resp.context["debtors"]}

    def test_filter_by_client(self):
        self.assertEqual(self._debtors(client=self.client1.pk), {self.client1.pk})

    def test_filter_by_rep(self):
        self.assertEqual(self._debtors(rep=self.sales2.pk), {self.client2.pk})

    def test_overdue_only(self):
        self.assertEqual(self._debtors(overdue="1"), {self.client1.pk})
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python manage.py test crm.tests.DebtFilterTests -v 2`
Expected: FAIL (all debtors returned regardless of params).

- [ ] **Step 3: Add filtering + context to `debt_list`**

In `crm/views.py`, update `debt_list`. After `open_sales = (...)` and before the aggregation loop, apply filters; build context at the end:

```python
def debt_list(request):
    """One row per debtor client: total owed, open receipts, earliest deadline."""
    today = timezone.localdate()
    open_sales = (
        Sale.objects.visible_to(request.user).outstanding().select_related("client")
    )

    filters = {key: request.GET.get(key, "") for key in ("client", "rep", "overdue")}
    if filters["client"].isdigit():
        open_sales = open_sales.filter(client_id=filters["client"])
    if filters["rep"].isdigit() and request.user.can_see_all_records:
        open_sales = open_sales.filter(sales_rep_id=filters["rep"])
    if filters["overdue"] == "1":
        open_sales = open_sales.filter(debt_deadline__lt=today)

    groups = {}
    total_debt = Decimal("0")
    overdue_total = Decimal("0")
    for sale in open_sales:
        remaining = sale.remaining
        total_debt += remaining
        group = groups.get(sale.client_id)
        if group is None:
            group = groups[sale.client_id] = {
                "client": sale.client,
                "remaining": Decimal("0"),
                "count": 0,
                "earliest": sale.debt_deadline,
                "overdue_count": 0,
            }
        group["remaining"] += remaining
        group["count"] += 1
        if sale.debt_deadline and (
            group["earliest"] is None or sale.debt_deadline < group["earliest"]
        ):
            group["earliest"] = sale.debt_deadline
        if sale.debt_deadline and sale.debt_deadline < today:
            group["overdue_count"] += 1
            overdue_total += remaining

    debtors = sorted(groups.values(), key=lambda g: g["earliest"] or today)
    overdue_debtors = sum(1 for g in debtors if g["overdue_count"])

    clients = _visible_clients(request.user).order_by("name")
    reps = (
        User.objects.filter(is_active=True).order_by("first_name", "username")
        if request.user.can_see_all_records
        else None
    )
    client_obj = clients.filter(pk=filters["client"]).first() if filters["client"].isdigit() else None
    rep_obj = reps.filter(pk=filters["rep"]).first() if reps and filters["rep"].isdigit() else None
    active_filters = _filter_chips(request, [
        {"param": "client", "label": "Mijoz", "value": client_obj.name if client_obj else ""},
        {"param": "rep", "label": "Sotuvchi", "value": str(rep_obj) if rep_obj else ""},
        {"param": "overdue", "label": "Holat", "value": "Muddati o'tgan" if filters["overdue"] == "1" else ""},
    ])

    return render(
        request,
        "crm/debt_list.html",
        {
            "debtors": debtors,
            "total_debt": total_debt,
            "overdue_total": overdue_total,
            "total_debtors": len(debtors),
            "overdue_debtors": overdue_debtors,
            "filters": filters,
            "clients": clients,
            "reps": reps,
            "active_filters": active_filters,
            "filter_count": len(active_filters),
            "has_filters": bool(active_filters),
            "filter_url": reverse("debt_list"),
        },
    )
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python manage.py test crm.tests.DebtFilterTests -v 2`
Expected: PASS.

- [ ] **Step 5: Add the partials to `debt_list.html`**

In `templates/crm/debt_list.html`, immediately after `{% block content %}` (before the `.kpi-grid`), add the toolbar; add the drawer just before `{% endblock %}`:

```html
{% include "crm/_filter_toolbar.html" %}
```
and before `{% endblock %}`:
```html
{% include "crm/_filter_drawer.html" with show_overdue=True %}
```

(No `export_url`, no `show_daterange_picker`, no `products`/`show_status`/`show_method`/`show_dates` — Debts shows client, seller\*, and the overdue toggle only.)

- [ ] **Step 6: Verify in the browser preview**

- Navigate to `http://127.0.0.1:8000/debts/`.
- Confirm the **Filtrlash** button + drawer appear top-right (same position as the other two pages).
- Open the drawer: **Mijoz** and **Sotuvchi** searchable; **Faqat muddati o'tgan** checkbox present.
- Apply overdue-only; confirm only debtor clients with overdue receipts remain and a "Holat: Muddati o'tgan" chip appears and clears.
- `preview_console_logs` error → none. Screenshot.

- [ ] **Step 7: Commit**

```bash
git add crm/views.py crm/tests.py templates/crm/debt_list.html
git commit -m "Add the unified searchable filter to the debts page"
```

---

## Final verification

- [ ] `.venv/bin/python manage.py test` → whole suite green.
- [ ] Preview all three pages side by side: identical Filtrlash button + drawer position; comboboxes searchable everywhere (drawers + sale/return forms + dynamically added rows); no console/server errors in light and dark themes.

---

## Self-review (author check against the spec)

- **Spec §1 combobox** → Tasks 1 (drawers) + 2 (forms, dynamic rows, quick-add). ✅
- **Spec §2 shared UI** → Task 3 (partials + sales refactor); Tasks 5–6 reuse. ✅
- **Spec §3 per-page fields** → drawer partial flags: `show_status` (sales), `show_method`+`show_dates` (payments), `show_overdue` (debts); `products`/`reps` gate the rest. ✅
- **Spec §4 backend** → Task 4 (`_filter_chips`, `_date_range_context`), Task 5 (`payment_list`), Task 6 (`debt_list`). Payments now mirrors Sotuvlar's exclusive date/filter behavior (per user decision); Debts filters compose (it has no date window). ✅
- **Payments date behavior** (per user decision): Payments mirrors Sotuvlar — same calendar date-range picker in the toolbar, today-default window, and "a content filter searches all dates" rule. `_date_range_context` is shared by both views.
- **Type consistency:** `enhanceComboboxes(root)`, `_filter_chips(request, specs)` used with identical signatures throughout. ✅
- **No placeholders:** every step carries full code or an exact command + expected result. ✅
