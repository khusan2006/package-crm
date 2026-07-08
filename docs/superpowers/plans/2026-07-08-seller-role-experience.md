# Seller Role Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give sellers a personal, scoped CRM experience — their own clients/sales/debts/till, personalized wording — and let sellers and admins hand a client (with all their sales) over to another seller.

**Architecture:** Extend the existing role model (`User.can_see_all_records`, `Client.owner`, `Sale.sales_rep`, `visible_to`). Add one new view (client transfer) and tighten scoping in three existing views (kassa, product detail) plus template wording. No new models, no auth changes.

**Tech Stack:** Django 6.0, Django template language, Django `TestCase`. Uzbek UI copy.

## Global Constraints

- Python interpreter and test runner: `.venv/bin/python` (Python 3.14, Django 6.0.6).
- Run tests with: `.venv/bin/python manage.py test crm -v 2` (or a specific `crm.tests.ClassName.test_method`).
- Role gate: `user.can_see_all_records` is `True` for `admin`/`manager`, `False` for `sales`. Sellers are the personalized/scoped case.
- Never rewrite historical `created_by` on payments/returns/stock — only `Client.owner` and `Sale.sales_rep` move on transfer.
- Match existing conventions: inline `{% if user.can_see_all_records %}` in templates (no new abstraction); Uzbek labels; `data-modal` for modal links; `form_reload`/`is_ajax` helpers from `crm/utils.py`.
- Latest existing migration is `crm/migrations/0016_remove_client_email.py`; the new one will be `0017_*`.

---

### Task 1: Client transfer — backend (model, migration, form, view, URL)

Reassign a client and all their sales to another seller, atomically, with an audit entry. Sellers can transfer only clients they own; admins/managers can transfer any.

**Files:**
- Modify: `crm/models.py` (add `TRANSFER` to `AuditLog.Action`)
- Create: `crm/migrations/0017_alter_auditlog_action.py` (generated)
- Modify: `crm/forms.py` (add `ClientTransferForm`)
- Modify: `crm/views.py` (add `_render_client_transfer` + `client_transfer`)
- Modify: `config/urls.py` (add route)
- Test: `crm/tests.py` (add `ClientTransferTests`)

**Interfaces:**
- Produces:
  - `AuditLog.Action.TRANSFER` (value `"transfer"`, label `"Sotuvchi o'zgartirildi"`)
  - `ClientTransferForm(*args, client=None, **kwargs)` — a `forms.Form` with a single `new_owner` `ModelChoiceField` whose queryset is active users excluding `client.owner`.
  - View `client_transfer(request, pk)`; URL name `client_transfer` at `clients/<int:pk>/transfer/`.
  - On non-AJAX success: HTTP 302 redirect to `client_list`. On non-AJAX invalid: HTTP 200 re-render.

- [ ] **Step 1: Add the audit action (model)**

In `crm/models.py`, inside `class AuditLog(models.Model)` → `class Action(models.TextChoices)`, add the new member after `RETURN`:

```python
    class Action(models.TextChoices):
        CREATE = "create", "Qo'shildi"
        UPDATE = "update", "O'zgartirildi"
        DELETE = "delete", "O'chirildi"
        VOID = "void", "Bekor qilindi"
        PAYMENT = "payment", "To'lov"
        RETURN = "return", "Qaytarish"
        TRANSFER = "transfer", "Sotuvchi o'zgartirildi"
```

- [ ] **Step 2: Generate the migration**

Run: `.venv/bin/python manage.py makemigrations crm`
Expected: creates `crm/migrations/0017_alter_auditlog_action.py` (an `AlterField` on `auditlog.action`).

- [ ] **Step 3: Add the transfer form**

In `crm/forms.py`, `User` is already imported. Add at the end of the file:

```python
class ClientTransferForm(forms.Form):
    """Reassign a client to another seller. The target list excludes the current
    owner, so transferring to who already owns them is not selectable."""

    new_owner = forms.ModelChoiceField(
        label="Yangi sotuvchi",
        queryset=User.objects.none(),
        empty_label="— tanlang —",
    )

    def __init__(self, *args, client=None, **kwargs):
        self.client = client
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(is_active=True)
        if client is not None:
            qs = qs.exclude(pk=client.owner_id)
        self.fields["new_owner"].queryset = qs.order_by(
            "first_name", "last_name", "username"
        )
        self.fields["new_owner"].widget.attrs["data-combobox"] = ""
```

- [ ] **Step 4: Write the failing tests**

In `crm/tests.py`, add a new test class (uses the existing `BaseSetup`, `make_sale`, and imported `AuditLog`, `Client`, `Sale`):

```python
class ClientTransferTests(BaseSetup):
    def test_seller_transfers_own_client(self):
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("client_transfer", args=[self.client1.pk]),
            {"new_owner": self.sales2.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.client1.refresh_from_db()
        self.assertEqual(self.client1.owner, self.sales2)
        self.assertEqual(
            list(Sale.objects.filter(client=self.client1).values_list("sales_rep", flat=True)),
            [self.sales2.pk],
        )

    def test_transfer_moves_all_of_a_clients_sales(self):
        make_sale(self.client1, self.sales1, self.product)  # a second sale
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("client_transfer", args=[self.client1.pk]),
            {"new_owner": self.sales2.pk},
        )
        reps = set(
            Sale.objects.filter(client=self.client1).values_list("sales_rep", flat=True)
        )
        self.assertEqual(reps, {self.sales2.pk})

    def test_seller_cannot_transfer_another_sellers_client(self):
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("client_transfer", args=[self.client2.pk]),
            {"new_owner": self.sales1.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.client2.refresh_from_db()
        self.assertEqual(self.client2.owner, self.sales2)

    def test_admin_can_transfer_any_client(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("client_transfer", args=[self.client2.pk]),
            {"new_owner": self.sales1.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.client2.refresh_from_db()
        self.assertEqual(self.client2.owner, self.sales1)

    def test_cannot_transfer_to_current_owner(self):
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("client_transfer", args=[self.client1.pk]),
            {"new_owner": self.sales1.pk},
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        self.client1.refresh_from_db()
        self.assertEqual(self.client1.owner, self.sales1)

    def test_transfer_writes_audit_log(self):
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("client_transfer", args=[self.client1.pk]),
            {"new_owner": self.sales2.pk},
        )
        log = AuditLog.objects.filter(action=AuditLog.Action.TRANSFER).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.target_id, self.client1.pk)

    def test_new_owner_gains_and_old_owner_loses_visibility(self):
        make_sale(self.client1, self.sales1, self.product, is_debt=True)
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("client_transfer", args=[self.client1.pk]),
            {"new_owner": self.sales2.pk},
        )
        self.client.force_login(self.sales2)
        resp2 = self.client.get(reverse("client_list"))
        self.assertIn(self.client1, list(resp2.context["page"].object_list))
        self.client.force_login(self.sales1)
        resp1 = self.client.get(reverse("client_list"))
        self.assertNotIn(self.client1, list(resp1.context["page"].object_list))
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `.venv/bin/python manage.py test crm.tests.ClientTransferTests -v 2`
Expected: FAIL — `NoReverseMatch: 'client_transfer'` (URL not defined yet).

- [ ] **Step 6: Add the view**

In `crm/views.py`, add `ClientTransferForm` to the existing `from .forms import (...)` block (keep alphabetical order — before `DebtPaymentForm`). Then add the view in the Clients section (after `client_delete`):

```python
def _render_client_transfer(request, client, form, invalid=False):
    context = {
        "form": form,
        "client": client,
        "sales_count": Sale.objects.filter(client=client).count(),
        "title": f"Mijozni o'tkazish: {client.name}",
    }
    if is_ajax(request):
        return render(
            request, "crm/_client_transfer_modal.html", context,
            status=422 if invalid else 200,
        )
    return render(request, "crm/form.html", context)


def client_transfer(request, pk):
    """Hand a client — and their whole sales history — to another seller.

    Full handover: the client's owner and every one of their sales' sales_rep
    move to the target, atomically. Sellers may transfer only clients they own
    (a non-owned client 404s via the visible-clients scope); admins/managers
    may transfer anyone's."""
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    if request.method == "POST":
        form = ClientTransferForm(request.POST, client=client)
        if form.is_valid():
            target = form.cleaned_data["new_owner"]
            old_owner = client.owner
            with transaction.atomic():
                moved = Sale.objects.filter(client=client).update(sales_rep=target)
                client.owner = target
                client.save(update_fields=["owner"])
                AuditLog.record(
                    request.user, AuditLog.Action.TRANSFER, "Mijoz", client.pk,
                    f"{client.name}: {old_owner} → {target} ({moved} ta sotuv)",
                )
            messages.success(
                request, f"“{client.name}” {target}ga o'tkazildi ({moved} ta sotuv)."
            )
            return form_reload(request, reverse("client_list"))
        return _render_client_transfer(request, client, form, invalid=True)
    form = ClientTransferForm(client=client)
    return _render_client_transfer(request, client, form)
```

- [ ] **Step 7: Add the URL**

In `config/urls.py`, add after the `client_delete` line:

```python
    path("clients/<int:pk>/transfer/", crm_views.client_transfer, name="client_transfer"),
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python manage.py test crm.tests.ClientTransferTests -v 2`
Expected: PASS (7 tests). The non-AJAX invalid case renders `crm/form.html` (status 200); the modal template is added in Task 2.

- [ ] **Step 9: Commit**

```bash
git add crm/models.py crm/migrations/0017_alter_auditlog_action.py crm/forms.py crm/views.py config/urls.py crm/tests.py
git commit -m "feat: client transfer between sellers (full handover) — backend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Client transfer — UI (modal + row action)

Wire the transfer into the client list as a modal action.

**Files:**
- Create: `templates/crm/_client_transfer_modal.html`
- Modify: `templates/crm/client_list.html` (row action cell)
- Test: `crm/tests.py` (add to `ClientTransferTests`)

**Interfaces:**
- Consumes: `client_transfer` view/URL and `ClientTransferForm` from Task 1; the AJAX modal plumbing (`data-modal`, `is_ajax`) already in `base.html`/`crm/utils.py`.

- [ ] **Step 1: Write the failing tests**

Add to `class ClientTransferTests` in `crm/tests.py`:

```python
    def test_transfer_modal_renders_for_ajax(self):
        self.client.force_login(self.sales1)
        response = self.client.get(
            reverse("client_transfer", args=[self.client1.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Yangi sotuvchi")
        self.assertContains(response, "O'tkazish")

    def test_client_list_shows_transfer_action(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("client_list"))
        self.assertContains(response, reverse("client_transfer", args=[self.client1.pk]))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python manage.py test crm.tests.ClientTransferTests.test_transfer_modal_renders_for_ajax crm.tests.ClientTransferTests.test_client_list_shows_transfer_action -v 2`
Expected: FAIL — `TemplateDoesNotExist: crm/_client_transfer_modal.html` for the first; assertion failure (link absent) for the second.

- [ ] **Step 3: Create the modal partial**

Create `templates/crm/_client_transfer_modal.html`:

```html
<div class="modal-head">
  <h2>{{ title }}</h2>
  <button type="button" class="modal-x" data-modal-close aria-label="Yopish">&times;</button>
</div>
<p class="field-hint">
  Hozirgi sotuvchi: <strong>{{ client.owner }}</strong> · {{ sales_count }} ta sotuv.
  Mijoz va uning barcha sotuvlari yangi sotuvchiga o'tadi.
</p>
<form method="post" action="{{ request.path }}" class="stacked">
  {% csrf_token %}
  {% if form.non_field_errors %}<div class="flash flash-error">{{ form.non_field_errors.0 }}</div>{% endif %}
  <p>{{ form.new_owner.label_tag }}{{ form.new_owner }}{{ form.new_owner.errors }}</p>
  <div class="modal-actions">
    <button type="submit" class="btn">O'tkazish</button>
    <button type="button" class="btn btn-ghost" data-modal-close>Bekor qilish</button>
  </div>
</form>
```

- [ ] **Step 4: Add the row action**

In `templates/crm/client_list.html`, replace the actions cell (currently the single delete button):

```html
    <td><a class="btn btn-sm btn-danger" href="{% url 'client_delete' client.pk %}">O'chirish</a></td>
```

with:

```html
    <td class="row-actions">
      <a class="btn btn-sm btn-ghost" href="{% url 'client_transfer' client.pk %}" data-modal>O'tkazish</a>
      <a class="btn btn-sm btn-danger" href="{% url 'client_delete' client.pk %}">O'chirish</a>
    </td>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python manage.py test crm.tests.ClientTransferTests -v 2`
Expected: PASS (9 tests total in the class).

- [ ] **Step 6: Commit**

```bash
git add templates/crm/_client_transfer_modal.html templates/crm/client_list.html crm/tests.py
git commit -m "feat: client transfer modal + client-list row action

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Kassa scoped to the seller

Sellers see only their own till and expenses; no employee filter, no per-employee performance table. Admins/managers unchanged.

**Files:**
- Modify: `crm/views.py` (`_kassa_expenses`, `kassa_view`)
- Modify: `templates/crm/kassa.html` (guard the per-employee card)
- Test: `crm/tests.py` (add `KassaScopingTests`)

**Interfaces:**
- Consumes: `User.can_see_all_records`, existing `_kassa_summary`, `_per_employee_kassa`, `_date_range_context`, `_filter_chips`.
- Produces: `kassa_view` context key `per_employee` is `None` for sellers, a list for admins/managers; `reps` is `None` for sellers.

- [ ] **Step 1: Write the failing tests**

In `crm/tests.py`, `Expense`, `Payment`, `Decimal`, and `timezone` are already imported. Add:

```python
class KassaScopingTests(BaseSetup):
    def setUp(self):
        today = timezone.localdate()
        Expense.objects.create(
            amount=Decimal("50000"), category=Expense.Category.OTHER,
            method=Payment.Method.CASH, created_by=self.sales2, date=today,
        )
        Expense.objects.create(
            amount=Decimal("30000"), category=Expense.Category.OTHER,
            method=Payment.Method.CASH, created_by=self.sales1, date=today,
        )

    def test_seller_sees_only_own_expenses(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("kassa"))
        creators = {e.created_by_id for e in response.context["expenses"]}
        self.assertEqual(creators, {self.sales1.pk})

    def test_seller_cannot_widen_scope_via_rep_param(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("kassa"), {"rep": self.sales2.pk})
        creators = {e.created_by_id for e in response.context["expenses"]}
        self.assertEqual(creators, {self.sales1.pk})

    def test_seller_has_no_per_employee_table(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("kassa"))
        self.assertIsNone(response.context["per_employee"])
        self.assertIsNone(response.context["reps"])

    def test_admin_sees_all_expenses_and_per_employee(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("kassa"))
        creators = {e.created_by_id for e in response.context["expenses"]}
        self.assertEqual(creators, {self.sales1.pk, self.sales2.pk})
        self.assertIsNotNone(response.context["per_employee"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python manage.py test crm.tests.KassaScopingTests -v 2`
Expected: FAIL — seller currently sees both expenses and a non-None `per_employee`.

- [ ] **Step 3: Scope `_kassa_expenses` to the seller**

In `crm/views.py`, in `_kassa_expenses`, replace the `reps`/`rep` resolution:

```python
    reps = User.objects.filter(is_active=True).order_by("first_name", "username")
    rep = reps.filter(pk=filters["rep"]).first() if filters["rep"].isdigit() else None
```

with:

```python
    # Admins/managers may filter by any employee; a seller is locked to their own
    # till, so the employee filter is never offered to them.
    if request.user.can_see_all_records:
        reps = User.objects.filter(is_active=True).order_by("first_name", "username")
        rep = reps.filter(pk=filters["rep"]).first() if filters["rep"].isdigit() else None
    else:
        reps = None
        rep = request.user
```

- [ ] **Step 4: Scope `kassa_view` (per-employee + rep chip)**

In `crm/views.py`, in `kassa_view`, after `summary = _kassa_summary(...)`, replace the `per_employee` computation and the rep chip spec. Change the `active_filters` block and the `per_employee` render value.

Replace:

```python
    active_filters = _filter_chips(request, [
        {"param": "rep", "label": "Xodim", "value": str(rep) if rep else ""},
        {"param": "category", "label": "Turkum", "value": category_labels.get(filters["category"], "")},
        {"param": "method", "label": "Usul", "value": method_labels.get(filters["method"], "")},
        {"param": "currency", "label": "Valyuta", "value": currency_labels.get(filters["currency"], "")},
    ])
```

with:

```python
    # Only the company view exposes a rep chip; a seller's own scope isn't a filter.
    rep_chip = str(rep) if (reps is not None and rep) else ""
    active_filters = _filter_chips(request, [
        {"param": "rep", "label": "Xodim", "value": rep_chip},
        {"param": "category", "label": "Turkum", "value": category_labels.get(filters["category"], "")},
        {"param": "method", "label": "Usul", "value": method_labels.get(filters["method"], "")},
        {"param": "currency", "label": "Valyuta", "value": currency_labels.get(filters["currency"], "")},
    ])
```

Then in the `render(request, "crm/kassa.html", {...})` context, replace:

```python
        "per_employee": _per_employee_kassa(date_from, date_to),
```

with:

```python
        "per_employee": _per_employee_kassa(date_from, date_to) if request.user.can_see_all_records else None,
```

- [ ] **Step 5: Guard the per-employee card in the template**

In `templates/crm/kassa.html`, wrap the "Xodimlar bo'yicha" card (the `<div class="card">` containing `<h2>Xodimlar bo'yicha</h2>`) in a role check:

```html
{% if user.can_see_all_records %}
<div class="card">
  <h2>Xodimlar bo'yicha</h2>
  ...existing card contents unchanged...
</div>
{% endif %}
```

(The employee option in the filter drawer is already guarded by `{% if reps %}`, so passing `reps=None` hides it with no further change.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python manage.py test crm.tests.KassaScopingTests -v 2`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add crm/views.py templates/crm/kassa.html crm/tests.py
git commit -m "feat: scope kassa to the seller (own till, no employee performance)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Close the product-detail cross-seller leak

Sellers see only their own recent sales of a product and no warehouse-movement log; stock KPIs remain shared.

**Files:**
- Modify: `crm/views.py` (`product_detail`)
- Modify: `templates/crm/product_detail.html` (guard the movements card)
- Test: `crm/tests.py` (add `ProductDetailScopingTests`)

**Interfaces:**
- Consumes: `User.can_see_all_records`; `Product`, `Sale` (`sale_items`, `sales_rep`).
- Produces: `product_detail` context — `entries` is `None` for sellers; `recent_items` filtered to `sale__sales_rep=user` for sellers.

- [ ] **Step 1: Write the failing tests**

In `crm/tests.py`, add:

```python
class ProductDetailScopingTests(BaseSetup):
    def test_seller_sees_only_own_recent_sales(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        sale_ids = {i.sale_id for i in response.context["recent_items"]}
        self.assertIn(self.sale1.pk, sale_ids)
        self.assertNotIn(self.sale2.pk, sale_ids)

    def test_seller_has_no_stock_entries(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        self.assertIsNone(response.context["entries"])

    def test_admin_sees_all_recent_sales_and_entries(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        sale_ids = {i.sale_id for i in response.context["recent_items"]}
        self.assertIn(self.sale1.pk, sale_ids)
        self.assertIn(self.sale2.pk, sale_ids)
        self.assertIsNotNone(response.context["entries"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python manage.py test crm.tests.ProductDetailScopingTests -v 2`
Expected: FAIL — seller currently sees `sale2` in recent items and a non-None `entries`.

- [ ] **Step 3: Scope the view**

In `crm/views.py`, replace the body of `product_detail` up to the `context` dict:

```python
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    recent_items = product.sale_items.select_related("sale", "sale__client").order_by(
        "-sale__date", "-sale__created_at"
    )
    # Sellers see only their OWN recent sales of the product, and not the
    # warehouse-movement log (which exposes other staff). Filter before slicing.
    if request.user.can_see_all_records:
        entries = product.stock_entries.select_related("created_by")[:50]
    else:
        entries = None
        recent_items = recent_items.filter(sale__sales_rep=request.user)
    recent_items = recent_items[:10]
    context = {
        "product": product,
        "current_stock": product.current_stock,
        "total_received": product.total_received,
        "total_sold": product.total_sold,
        "entries": entries,
        "recent_items": recent_items,
    }
    return render(request, "crm/product_detail.html", context)
```

- [ ] **Step 4: Guard the movements card in the template**

In `templates/crm/product_detail.html`, wrap the "Ombor harakatlari" card (the `<div class="card">` containing `<h2>Ombor harakatlari</h2>`) in:

```html
{% if user.can_see_all_records %}
<div class="card">
  <h2>Ombor harakatlari</h2>
  ...existing card contents unchanged...
</div>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python manage.py test crm.tests.ProductDetailScopingTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add crm/views.py templates/crm/product_detail.html crm/tests.py
git commit -m "fix: scope product detail's recent sales + hide stock log from sellers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Seller-personalized navigation & page headers

Sellers see "Mening ..." wording in the sidebar and matching page headers; admins/managers keep neutral labels.

**Files:**
- Modify: `templates/base.html` (4 sidebar labels)
- Modify: `templates/crm/client_list.html`, `sale_list.html`, `debt_list.html`, `kassa.html` (`title` + `topbar_title`)
- Test: `crm/tests.py` (add `SellerLabelTests`)

**Interfaces:**
- Consumes: `user.can_see_all_records` in templates. No Python changes.

- [ ] **Step 1: Write the failing tests**

In `crm/tests.py`, add:

```python
class SellerLabelTests(BaseSetup):
    def test_seller_sees_personalized_labels(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("client_list"))
        self.assertContains(response, "Mening mijozlarim")
        response = self.client.get(reverse("sale_list"))
        self.assertContains(response, "Mening sotuvlarim")

    def test_admin_sees_neutral_labels(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("client_list"))
        self.assertNotContains(response, "Mening mijozlarim")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python manage.py test crm.tests.SellerLabelTests -v 2`
Expected: FAIL — "Mening mijozlarim" not present yet.

- [ ] **Step 3: Personalize the sidebar labels**

In `templates/base.html`, replace each of these four `<span>` label lines inside the "Savdo" nav group:

```html
          <span>Sotuvlar</span>
```
→
```html
          <span>{% if user.can_see_all_records %}Sotuvlar{% else %}Mening sotuvlarim{% endif %}</span>
```

```html
          <span>Qarzlar</span>
```
→
```html
          <span>{% if user.can_see_all_records %}Qarzlar{% else %}Mening qarzlarim{% endif %}</span>
```

```html
          <span>Kassa</span>
```
→
```html
          <span>{% if user.can_see_all_records %}Kassa{% else %}Mening kassam{% endif %}</span>
```

```html
          <span>Mijozlar</span>
```
→
```html
          <span>{% if user.can_see_all_records %}Mijozlar{% else %}Mening mijozlarim{% endif %}</span>
```

- [ ] **Step 4: Personalize the page headers**

In each list template, replace the `title` and `topbar_title` blocks:

`templates/crm/client_list.html`:
```html
{% block title %}{% if user.can_see_all_records %}Mijozlar{% else %}Mening mijozlarim{% endif %} · Paket CRM{% endblock %}
{% block topbar_title %}{% if user.can_see_all_records %}Mijozlar{% else %}Mening mijozlarim{% endif %}{% endblock %}
```

`templates/crm/sale_list.html`:
```html
{% block title %}{% if user.can_see_all_records %}Sotuvlar{% else %}Mening sotuvlarim{% endif %} · Paket CRM{% endblock %}
{% block topbar_title %}{% if user.can_see_all_records %}Sotuvlar{% else %}Mening sotuvlarim{% endif %}{% endblock %}
```

`templates/crm/debt_list.html`:
```html
{% block title %}{% if user.can_see_all_records %}Qarzlar{% else %}Mening qarzlarim{% endif %} · Paket CRM{% endblock %}
{% block topbar_title %}{% if user.can_see_all_records %}Qarzlar{% else %}Mening qarzlarim{% endif %}{% endblock %}
```

`templates/crm/kassa.html`:
```html
{% block title %}{% if user.can_see_all_records %}Kassa{% else %}Mening kassam{% endif %} · Paket CRM{% endblock %}
{% block topbar_title %}{% if user.can_see_all_records %}Kassa{% else %}Mening kassam{% endif %}{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python manage.py test crm.tests.SellerLabelTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python manage.py test crm -v 2`
Expected: PASS (all pre-existing tests plus the new `ClientTransferTests`, `KassaScopingTests`, `ProductDetailScopingTests`, `SellerLabelTests`).

- [ ] **Step 7: Commit**

```bash
git add templates/base.html templates/crm/client_list.html templates/crm/sale_list.html templates/crm/debt_list.html templates/crm/kassa.html crm/tests.py
git commit -m "feat: personalize seller sidebar + page headers (Mening ...)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Migration file name:** if `makemigrations` picks a different suffix than `0017_alter_auditlog_action`, use whatever it generates and adjust the `git add` path in Task 1 Step 9 accordingly.
- **`data-modal` plumbing** (AJAX fetch, inject, close-on-204/redirect-on-`X-Redirect`) already lives in `base.html`. The transfer link only needs the `data-modal` attribute — no new JS.
- **Combobox:** `data-combobox` on the `new_owner` widget is enhanced by the same JS that powers the other select comboboxes; it degrades to a plain `<select>` without it.
- **Do not** touch `Payment.created_by` / `Return.created_by` / `StockEntry.created_by` anywhere — only `Client.owner` and `Sale.sales_rep` move on transfer.
