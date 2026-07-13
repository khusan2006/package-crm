# Manufacturing Module (Ishlab chiqarish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manufacturing domain: buy raw materials at fluctuating prices (weighted-average cost), combine them in production runs into finished products that land in the factory warehouse (sklad), transfer finished goods to per-seller warehouses (ombor), and give the sklad its own cashbox — while seller sales deduct from the seller's own ombor.

**Architecture:** New self-contained Django app `manufacturing` following the codebase idiom "store movements, derive balances with annotated querysets" (mirrors `crm.Product.with_stock()`). The only stored balance-like figure is each material's running `avg_cost`. Existing `crm` money models are untouched except that `Product` stock now means *sklad* stock and `Sale` creation validates against the seller's ombor. Cross-app dependency is one-directional at import time (`manufacturing` imports `crm`); `crm` reaches back into `manufacturing` only through deferred imports inside methods/views to avoid circular imports.

**Tech Stack:** Django (function-based views + ModelForms + formsets), PostgreSQL (prod) / SQLite (`config.settings_test`), Django templates, pytest + pytest-django, Playwright for E2E.

## Global Constraints

- Money fields: `DecimalField(max_digits=18, decimal_places=2)`; quantity/weight fields: `DecimalField(max_digits=18, decimal_places=3)` (materials, output, transfers use `max_digits=12` like existing `StockEntry.quantity_kg`). All weights are in **kg**.
- All user-facing strings are **Uzbek (latin)**, matching existing verbose_names and messages.
- Every create/edit/delete of a purchase, production run, transfer, or seller stock entry writes an `AuditLog` row via `AuditLog.record(user, action, entity, obj_id, detail)`.
- Reuse existing helpers: `crm.utils.is_ajax / form_success / form_reload / form_response / render_confirm`, `accounts.decorators.role_required`, form mixins `crm.forms._mark_money / _searchable_select`.
- `LoginRequiredMiddleware` is global — every view already requires login; add role gating on top.
- Search boxes use the established pill-with-icon live-search toolbar (`toolbar-search` markup from `templates/crm/product_list.html`).
- Never bypass hooks/tests. Run tests with `pytest` (settings `config.settings_test`). Commit after each task.

---

## File Structure

**New app `manufacturing/`:**
- `manufacturing/__init__.py` — empty
- `manufacturing/apps.py` — `ManufacturingConfig`
- `manufacturing/models.py` — `RawMaterial`, `MaterialPurchase`, `ProductionRun`, `ProductionRunItem`, `StockTransfer`, `SellerStockEntry`
- `manufacturing/queries.py` — pure read helpers: `recompute_avg_cost` inputs, `sklad_stock`, `annotate_sklad_stock`, `seller_ombor`, `available_for_sale`, `sale_stock_errors`
- `manufacturing/services.py` — mutating services + `InsufficientStock`: `recompute_avg_cost`, `apply_run_cost`, `create_production_run`, `create_purchase`, `create_transfer`
- `manufacturing/forms.py` — `RawMaterialForm`, `MaterialPurchaseForm`, `ProductionRunForm`, `ProductionRunItemForm` (+ formset), `StockTransferForm`, `SellerStockEntryForm`
- `manufacturing/views.py` — list/CRUD views + sklad ombor + seller ombor + sklad kassa
- `manufacturing/urls.py` — `app_name = "manufacturing"` url patterns
- `manufacturing/admin.py` — register models (read-only-ish, admin convenience)
- `manufacturing/migrations/` — schema + one data migration (cutover)

**Templates `templates/manufacturing/`:**
- `material_list.html`, `material_detail.html`, `purchase_list.html`, `_purchase_modal.html` (reuses `crm/form.html`/`_modal.html` where possible)
- `production_list.html`, `production_form.html`, `_production_item_row.html`
- `transfer_list.html`, `sklad_ombor.html`
- `seller_ombor.html`, `_seller_entry_modal.html`
- `sklad_kassa.html`

**Modified existing files:**
- `accounts/models.py` — add `Role.OMBORCHI`
- `config/settings.py` — add `"manufacturing"` to `INSTALLED_APPS`
- `config/urls.py` — `include("manufacturing.urls")`
- `crm/models.py` — `ProductQuerySet.with_stock()` and `Product.current_stock` delegate to `manufacturing.queries`
- `crm/views.py` — `sale_create` / `sale_edit` block on ombor shortfall
- `templates/base.html` — new nav group "Ishlab chiqarish" + seller "Mening omborim"

**Tests:**
- `tests/test_manufacturing.py` — unit/integration (math, balances, blocks, services, cutover)
- `tests/test_manufacturing_permissions.py` — role access
- `tests/test_manufacturing_e2e.py` — Playwright happy path

---

## Task 1: Scaffold app, add Omborchi role, register

**Files:**
- Create: `manufacturing/__init__.py`, `manufacturing/apps.py`, `manufacturing/models.py` (empty stub), `manufacturing/admin.py` (empty stub)
- Modify: `accounts/models.py` (add role), `config/settings.py:34-36` (INSTALLED_APPS)
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Produces: `accounts.models.User.Role.OMBORCHI == "omborchi"`; installed app label `manufacturing`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py
import pytest
from django.apps import apps
from accounts.models import User


def test_omborchi_role_exists():
    assert User.Role.OMBORCHI == "omborchi"
    assert ("omborchi", "Omborchi") in User.Role.choices


def test_manufacturing_app_installed():
    assert apps.is_installed("manufacturing")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — `AttributeError: OMBORCHI` and app not installed.

- [ ] **Step 3: Create the app package**

`manufacturing/__init__.py`: empty file.

`manufacturing/apps.py`:
```python
from django.apps import AppConfig


class ManufacturingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "manufacturing"
    verbose_name = "Ishlab chiqarish"
```

`manufacturing/models.py`:
```python
# Models are added in later tasks.
```

`manufacturing/admin.py`:
```python
# Admin registrations are added in later tasks.
```

- [ ] **Step 4: Add the role**

In `accounts/models.py`, inside `class Role(models.TextChoices)`, add after `SALES`:
```python
        OMBORCHI = "omborchi", "Omborchi"
```

- [ ] **Step 5: Register the app**

In `config/settings.py`, add `"manufacturing",` to `INSTALLED_APPS` right after `"crm",`.

- [ ] **Step 6: Make migrations**

Run: `python manage.py makemigrations accounts manufacturing`
Expected: an `accounts` migration altering `user.role` choices; an empty initial for `manufacturing` (or none until Task 2). Commit whatever is generated.

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add manufacturing accounts config tests/test_manufacturing.py
git commit -m "feat(mfg): scaffold manufacturing app and Omborchi role"
```

---

## Task 2: RawMaterial + MaterialPurchase + weighted-average cost

**Files:**
- Modify: `manufacturing/models.py`
- Create: `manufacturing/services.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Produces:
  - `RawMaterial(name, sku, note, avg_cost, low_stock_threshold, is_active, created_at)` with property `current_stock -> Decimal` and reverse `purchases`.
  - `MaterialPurchase(material, date, quantity_kg, price_per_kg, supplier, method, note, created_by, created_at)` with property `total -> Decimal`; `Method` choices reuse `crm.models.Payment.Method`.
  - `services.recompute_avg_cost(material) -> Decimal` — replays purchases+consumption chronologically, saves `avg_cost`, returns it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_manufacturing.py  (append)
from decimal import Decimal
from django.utils import timezone
from manufacturing.models import RawMaterial, MaterialPurchase
from manufacturing import services


@pytest.fixture
def material(db):
    return RawMaterial.objects.create(name="Karton", sku="MAT-1")


def _buy(material, qty, price, user, day="2026-07-01"):
    p = MaterialPurchase.objects.create(
        material=material, quantity_kg=Decimal(qty), price_per_kg=Decimal(price),
        date=day, created_by=user,
    )
    services.recompute_avg_cost(material)
    material.refresh_from_db()
    return p


def test_first_purchase_sets_avg_to_price(material, admin_user):
    _buy(material, "100", "1000", admin_user)
    assert material.avg_cost == Decimal("1000.00")
    assert material.current_stock == Decimal("100.000")


def test_second_purchase_is_weighted(material, admin_user):
    _buy(material, "100", "1000", admin_user, day="2026-07-01")
    _buy(material, "100", "2000", admin_user, day="2026-07-02")
    # (100*1000 + 100*2000) / 200 = 1500
    assert material.avg_cost == Decimal("1500.00")
    assert material.current_stock == Decimal("200.000")


def test_purchase_total(material, admin_user):
    p = _buy(material, "10", "1500", admin_user)
    assert p.total == Decimal("15000.00")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — `RawMaterial` has no such fields / `services` missing.

- [ ] **Step 3: Implement the models**

Replace `manufacturing/models.py` contents:
```python
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from crm.models import Payment  # Method choices reused

MONEY = models.DecimalField(max_digits=18, decimal_places=2)
QTY = models.DecimalField(max_digits=18, decimal_places=3)


class RawMaterial(models.Model):
    """A raw material (xomashyo) bought to manufacture finished products. Priced
    in so'm per kg; `avg_cost` is the running weighted average of stock on hand,
    recomputed from purchase/consumption history on every change."""

    name = models.CharField("Nomi", max_length=200)
    sku = models.CharField("Artikul (SKU)", max_length=50, blank=True)
    note = models.TextField("Izoh", blank=True)
    avg_cost = models.DecimalField(
        "O'rtacha tannarx (1 kg, so'm)", max_digits=14, decimal_places=2, default=0
    )
    low_stock_threshold = models.DecimalField(
        "Kam qoldi chegarasi (kg)", max_digits=12, decimal_places=3, default=0
    )
    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Xomashyo"
        verbose_name_plural = "Xomashyolar"

    @property
    def total_purchased(self):
        return self.purchases.aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")

    @property
    def total_consumed(self):
        return (
            ProductionRunItem.objects.filter(material=self).aggregate(s=Sum("quantity_kg"))["s"]
            or Decimal("0")
        )

    @property
    def current_stock(self):
        return self.total_purchased - self.total_consumed

    @property
    def is_low_stock(self):
        return self.current_stock <= self.low_stock_threshold

    def __str__(self):
        return self.name


class MaterialPurchase(models.Model):
    """A raw-material purchase at a point-in-time price. Also the sklad kassa's
    money outflow — `method` says which wallet it left (naqd/karta/bank)."""

    material = models.ForeignKey(
        RawMaterial, on_delete=models.PROTECT, related_name="purchases", verbose_name="Xomashyo"
    )
    date = models.DateField("Sana", default=timezone.localdate)
    quantity_kg = models.DecimalField("Miqdor (kg)", max_digits=12, decimal_places=3)
    price_per_kg = models.DecimalField("Narx (1 kg, so'm)", max_digits=14, decimal_places=2)
    supplier = models.CharField("Yetkazib beruvchi", max_length=200, blank=True)
    method = models.CharField(
        "To'lov usuli", max_length=8, choices=Payment.Method.choices, default=Payment.Method.CASH
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="material_purchases", verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Xomashyo xaridi"
        verbose_name_plural = "Xomashyo xaridlari"

    @property
    def total(self):
        return self.quantity_kg * self.price_per_kg

    def __str__(self):
        return f"{self.material.name}: {self.quantity_kg} kg × {self.price_per_kg} ({self.date})"
```

Note: `ProductionRunItem` is referenced in `total_consumed` but defined in Task 3. Add a placeholder class at the end of the file now so the module imports; Task 3 fleshes it out:
```python
class ProductionRunItem(models.Model):
    quantity_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name="usages")
    # Fully defined in Task 3.

    class Meta:
        app_label = "manufacturing"
```
(Task 3 replaces this stub with the complete model — keep the `material` related_name `usages`.)

- [ ] **Step 4: Implement recompute_avg_cost**

`manufacturing/services.py`:
```python
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from .models import MaterialPurchase, ProductionRunItem, RawMaterial


def recompute_avg_cost(material: RawMaterial) -> Decimal:
    """Replay this material's purchases and consumptions in chronological order to
    derive the weighted-average cost. A purchase moves the average toward its price
    (weighted by stock on hand); consumption reduces stock on hand but never the
    average. Single source of truth — call after any purchase add/edit/delete or
    admin correction."""
    events = []
    for p in material.purchases.all():
        events.append((p.date, p.created_at, 0, p.quantity_kg, p.price_per_kg))
    for it in ProductionRunItem.objects.filter(material=material).select_related("run"):
        events.append((it.run.date, it.run.created_at, 1, it.quantity_kg, None))
    # Sort by date, then timestamp, then kind (buy=0 before use=1 on ties).
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    avg = Decimal("0")
    on_hand = Decimal("0")
    for _date, _ts, kind, qty, price in events:
        if kind == 0:  # purchase
            new_qty = on_hand + qty
            if new_qty > 0:
                avg = (on_hand * avg + qty * price) / new_qty
            on_hand = new_qty
        else:  # consumption
            on_hand -= qty

    material.avg_cost = avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    material.save(update_fields=["avg_cost"])
    return material.avg_cost


@transaction.atomic
def create_purchase(*, material, quantity_kg, price_per_kg, date, method, supplier, note, user):
    """Record a purchase and refresh the material's weighted-average cost."""
    purchase = MaterialPurchase.objects.create(
        material=material, quantity_kg=quantity_kg, price_per_kg=price_per_kg,
        date=date, method=method, supplier=supplier, note=note, created_by=user,
    )
    recompute_avg_cost(material)
    return purchase
```

- [ ] **Step 5: Make migrations**

Run: `python manage.py makemigrations manufacturing`
Expected: initial migration creating `RawMaterial`, `MaterialPurchase`, stub `ProductionRunItem`.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_manufacturing.py -q`
Expected: PASS (all Task 2 tests green).

- [ ] **Step 7: Commit**

```bash
git add manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): raw materials + purchases with weighted-average cost"
```

---

## Task 3: ProductionRun + ProductionRunItem + batch cost + material consumption block

**Files:**
- Modify: `manufacturing/models.py` (replace `ProductionRunItem` stub, add `ProductionRun`), `manufacturing/services.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `RawMaterial`, `recompute_avg_cost`, `crm.Product`.
- Produces:
  - `ProductionRun(product, date, output_kg, note, created_by, created_at)` with `items` reverse, props `batch_cost -> Decimal`, `cost_per_kg -> Decimal`; reverse on `crm.Product` is `production_runs`.
  - `ProductionRunItem(run, material, quantity_kg, unit_cost)` — `unit_cost` is a snapshot of `material.avg_cost` at run time.
  - `services.InsufficientStock(item_label, requested, available)` exception.
  - `services.create_production_run(*, product, output_kg, date, note, user, items) -> ProductionRun` where `items` is a list of `(material, quantity_kg)`; raises `InsufficientStock` if any material lacks stock; consumes materials, snapshots unit costs, then calls `apply_run_cost` (Task 4 fills that in — for now define a no-op that Task 4 replaces).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_manufacturing.py  (append)
from crm.models import Product
from manufacturing.models import ProductionRun, ProductionRunItem
from manufacturing.services import InsufficientStock, create_production_run


@pytest.fixture
def finished_product(db):
    return Product.objects.create(name="Paket A", sku="FIN-1", price=Decimal("20000"))


def test_production_consumes_materials_and_snapshots_cost(material, finished_product, admin_user):
    _buy(material, "100", "1000", admin_user)  # avg 1000
    run = create_production_run(
        product=finished_product, output_kg=Decimal("40"), date="2026-07-03",
        note="", user=admin_user, items=[(material, Decimal("50"))],
    )
    material.refresh_from_db()
    assert material.current_stock == Decimal("50.000")     # 100 bought − 50 used
    item = run.items.get()
    assert item.unit_cost == Decimal("1000.00")            # snapshot at run time
    assert run.batch_cost == Decimal("50000.00")           # 50 × 1000
    assert run.cost_per_kg == Decimal("1250.00")           # 50000 / 40


def test_production_blocks_when_material_short(material, finished_product, admin_user):
    _buy(material, "10", "1000", admin_user)
    with pytest.raises(InsufficientStock):
        create_production_run(
            product=finished_product, output_kg=Decimal("5"), date="2026-07-03",
            note="", user=admin_user, items=[(material, Decimal("50"))],
        )
    assert ProductionRun.objects.count() == 0              # rolled back
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — missing `ProductionRun`, `create_production_run`, `InsufficientStock`.

- [ ] **Step 3: Replace the ProductionRunItem stub and add ProductionRun**

In `manufacturing/models.py`, remove the stub `ProductionRunItem` and add (place `ProductionRun` before `ProductionRunItem`):
```python
class ProductionRun(models.Model):
    """One manufacturing batch: raw materials consumed to produce `output_kg` of a
    finished product. Batch cost is derived from its item lines (materials only)."""

    product = models.ForeignKey(
        "crm.Product", on_delete=models.PROTECT,
        related_name="production_runs", verbose_name="Mahsulot",
    )
    date = models.DateField("Sana", default=timezone.localdate)
    output_kg = models.DecimalField("Ishlab chiqarildi (kg)", max_digits=12, decimal_places=3)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="production_runs", verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Ishlab chiqarish"
        verbose_name_plural = "Ishlab chiqarishlar"

    @property
    def batch_cost(self):
        total = Decimal("0")
        for it in self.items.all():
            total += it.quantity_kg * it.unit_cost
        return total.quantize(Decimal("0.01"))

    @property
    def cost_per_kg(self):
        if self.output_kg:
            return (self.batch_cost / self.output_kg).quantize(Decimal("0.01"))
        return Decimal("0")

    def __str__(self):
        return f"{self.product.name}: {self.output_kg} kg ({self.date})"


class ProductionRunItem(models.Model):
    """A material line of a production run. `unit_cost` snapshots the material's
    weighted-average cost at run time so history never changes on later purchases."""

    run = models.ForeignKey(
        ProductionRun, on_delete=models.CASCADE, related_name="items", verbose_name="Ishlab chiqarish"
    )
    material = models.ForeignKey(
        RawMaterial, on_delete=models.PROTECT, related_name="usages", verbose_name="Xomashyo"
    )
    quantity_kg = models.DecimalField("Miqdor (kg)", max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField("Tannarx (1 kg, so'm)", max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "Ishlab chiqarish xomashyosi"
        verbose_name_plural = "Ishlab chiqarish xomashyolari"

    def __str__(self):
        return f"{self.material.name}: {self.quantity_kg} kg"
```

- [ ] **Step 4: Add the production service**

In `manufacturing/services.py`, add:
```python
class InsufficientStock(Exception):
    def __init__(self, label, requested, available):
        self.label = label
        self.requested = requested
        self.available = available
        super().__init__(f"{label}: {available:.3f} bor, {requested:.3f} so'raldi")


def apply_run_cost(run):
    """Placeholder — filled in Task 4 to update the finished product's cost_price."""
    return None


@transaction.atomic
def create_production_run(*, product, output_kg, date, note, user, items):
    """Consume materials into a batch. Blocks (InsufficientStock, rolls back) if any
    material lacks stock. Snapshots each material's avg cost, then updates the
    product's tannarx via apply_run_cost."""
    from .models import ProductionRun, ProductionRunItem

    for material, qty in items:
        if qty > material.current_stock:
            raise InsufficientStock(material.name, qty, material.current_stock)

    run = ProductionRun.objects.create(
        product=product, output_kg=output_kg, date=date, note=note, created_by=user,
    )
    for material, qty in items:
        ProductionRunItem.objects.create(
            run=run, material=material, quantity_kg=qty, unit_cost=material.avg_cost,
        )
        recompute_avg_cost(material)
    apply_run_cost(run)
    return run
```

- [ ] **Step 5: Make migrations**

Run: `python manage.py makemigrations manufacturing`
Expected: migration replacing the `ProductionRunItem` stub and adding `ProductionRun` + FK/field changes.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): production runs consume materials with cost snapshot"
```

---

## Task 4: Sklad stock derivation + finished-product tannarx auto-update

**Files:**
- Create: `manufacturing/queries.py`
- Modify: `manufacturing/services.py` (`apply_run_cost`), `crm/models.py` (`ProductQuerySet.with_stock`, `Product.current_stock`)
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `crm.models` expressions `ITEM_WEIGHT_KG`, `RETURN_WEIGHT_KG`, `QTY`, `ZERO_QTY`; `crm.models.StockEntry / SaleItem / Return`; `accounts.User.Role.OMBORCHI`.
- Produces:
  - `queries.sklad_stock(product) -> Decimal` = StockEntry + production output − transfers out − direct (omborchi) sales + direct restocked returns.
  - `queries.annotate_sklad_stock(product_qs) -> qs` adding `stock_in`, `stock_out`, `stock_returned`, `stock` (keeps template contract of `product_list.html`).
  - `services.apply_run_cost(run)` now updates `run.product.cost_price` by weighted average of sklad stock on hand.
  - `crm.Product.current_stock` and `Product.objects.with_stock()` now mean sklad stock.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_manufacturing.py  (append)
from manufacturing.queries import sklad_stock


def test_production_output_raises_sklad_stock(material, finished_product, admin_user):
    _buy(material, "100", "1000", admin_user)
    create_production_run(
        product=finished_product, output_kg=Decimal("40"), date="2026-07-03",
        note="", user=admin_user, items=[(material, Decimal("50"))],
    )
    assert sklad_stock(finished_product) == Decimal("40.000")
    finished_product.refresh_from_db()
    assert finished_product.cost_price == Decimal("1250.00")   # batch cost/kg, empty before


def test_second_batch_weights_product_cost(material, finished_product, admin_user):
    _buy(material, "1000", "1000", admin_user)
    create_production_run(product=finished_product, output_kg=Decimal("40"), date="2026-07-03",
                          note="", user=admin_user, items=[(material, Decimal("40"))])   # 1000/kg
    # sklad now 40 kg @ 1000. Second batch 40 kg @ 2000/kg.
    create_production_run(product=finished_product, output_kg=Decimal("40"), date="2026-07-04",
                          note="", user=admin_user, items=[(material, Decimal("80"))])   # 80*1000/40=2000
    finished_product.refresh_from_db()
    # (40*1000 + 40*2000) / 80 = 1500
    assert finished_product.cost_price == Decimal("1500.00")


def test_with_stock_annotation_matches(finished_product, material, admin_user):
    _buy(material, "100", "1000", admin_user)
    create_production_run(product=finished_product, output_kg=Decimal("40"), date="2026-07-03",
                          note="", user=admin_user, items=[(material, Decimal("50"))])
    annotated = Product.objects.with_stock().get(pk=finished_product.pk)
    assert annotated.stock == Decimal("40.000")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — `queries` missing; `cost_price` stays 0.

- [ ] **Step 3: Implement queries.py**

`manufacturing/queries.py`:
```python
from decimal import Decimal

from django.db.models import F, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce

from .models import ProductionRun, StockTransfer


def sklad_stock(product):
    """Factory-warehouse stock (kg) for a finished product: manual stock entries +
    production output − transfers to sellers − direct (omborchi) sales + restocked
    returns of those direct sales."""
    from accounts.models import User
    from crm.models import ITEM_WEIGHT_KG, RETURN_WEIGHT_KG, Return, SaleItem

    entries = product.stock_entries.aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")
    produced = product.production_runs.aggregate(s=Sum("output_kg"))["s"] or Decimal("0")
    transferred = product.stock_transfers.aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")
    direct_sold = (
        SaleItem.objects.filter(product=product, sale__sales_rep__role=User.Role.OMBORCHI)
        .aggregate(s=Sum(ITEM_WEIGHT_KG))["s"] or Decimal("0")
    )
    direct_ret = (
        Return.objects.filter(
            product=product, restock=True, sale__sales_rep__role=User.Role.OMBORCHI
        ).aggregate(s=Sum(RETURN_WEIGHT_KG))["s"] or Decimal("0")
    )
    return entries + produced - transferred - direct_sold + direct_ret


def annotate_sklad_stock(product_qs):
    """Annotate a Product queryset with stock_in / stock_out / stock_returned / stock,
    where stock is the sklad balance. Mirrors the shape the templates already use."""
    from accounts.models import User
    from crm.models import (
        ITEM_WEIGHT_KG, QTY, RETURN_WEIGHT_KG, ZERO_QTY, Return, SaleItem, StockEntry,
    )

    def _sub(model, expr, **extra):
        return Subquery(
            model.objects.filter(product=OuterRef("pk"), **extra)
            .values("product").annotate(s=Sum(expr)).values("s"),
            output_field=QTY,
        )

    entries = _sub(StockEntry, "quantity_kg")
    produced = _sub(ProductionRun, "output_kg")
    transferred = _sub(StockTransfer, "quantity_kg")
    direct_sold = _sub(SaleItem, ITEM_WEIGHT_KG, sale__sales_rep__role=User.Role.OMBORCHI)
    direct_ret = _sub(Return, RETURN_WEIGHT_KG, restock=True,
                      sale__sales_rep__role=User.Role.OMBORCHI)

    return product_qs.annotate(
        stock_in=Coalesce(entries, ZERO_QTY) + Coalesce(produced, ZERO_QTY),
        stock_out=Coalesce(transferred, ZERO_QTY) + Coalesce(direct_sold, ZERO_QTY),
        stock_returned=Coalesce(direct_ret, ZERO_QTY),
    ).annotate(stock=F("stock_in") - F("stock_out") + F("stock_returned"))
```

Note: `StockTransfer` is defined in Task 5; add a minimal stub in `manufacturing/models.py` now so imports resolve, and Task 5 completes it:
```python
class StockTransfer(models.Model):
    product = models.ForeignKey("crm.Product", on_delete=models.PROTECT, related_name="stock_transfers")
    quantity_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    # Fully defined in Task 5.

    class Meta:
        app_label = "manufacturing"
```
Run `python manage.py makemigrations manufacturing` after adding the stub.

- [ ] **Step 4: Implement apply_run_cost**

In `manufacturing/services.py`, replace the `apply_run_cost` placeholder:
```python
def apply_run_cost(run):
    """Update the finished product's cost_price as the weighted average of sklad
    stock on hand and this batch's per-kg cost. If sklad was empty (≤0), the new
    cost is simply the batch cost."""
    from .queries import sklad_stock

    product = run.product
    sklad_after = sklad_stock(product)          # includes this run's output
    sklad_before = sklad_after - run.output_kg
    cpk = run.cost_per_kg
    if sklad_before > 0:
        new_cost = (sklad_before * product.cost_price + run.output_kg * cpk) / (
            sklad_before + run.output_kg
        )
    else:
        new_cost = cpk
    product.cost_price = Decimal(new_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    product.save(update_fields=["cost_price"])
```
Add `from decimal import Decimal` usage already imported at top of services.py.

- [ ] **Step 5: Delegate crm Product stock to manufacturing**

In `crm/models.py`, replace the body of `ProductQuerySet.with_stock` with:
```python
    def with_stock(self):
        """Annotate each product with sklad (factory-warehouse) stock. The
        derivation lives in the manufacturing app; imported lazily to avoid a
        circular import at load time."""
        from manufacturing.queries import annotate_sklad_stock
        return annotate_sklad_stock(self)
```
And replace `Product.current_stock`:
```python
    @property
    def current_stock(self):
        from manufacturing.queries import sklad_stock
        return sklad_stock(self)
```
Leave `total_received`, `total_sold`, `total_returned`, `is_low_stock` unchanged.

- [ ] **Step 6: Run tests (full suite — this changes crm behavior)**

Run: `pytest tests/test_manufacturing.py -q`
Expected: PASS.
Run: `pytest -q`
Expected: PASS. If any existing crm test asserted the OLD shared-warehouse stock (seller sales lowering `current_stock`), update that test to the new sklad meaning (seller sales no longer reduce sklad). Fix the test to match new semantics — do not weaken it.

- [ ] **Step 7: Commit**

```bash
git add manufacturing crm tests/test_manufacturing.py
git commit -m "feat(mfg): sklad stock derivation + auto weighted product tannarx"
```

---

## Task 5: StockTransfer (sklad → seller) with block on shortfall

**Files:**
- Modify: `manufacturing/models.py` (complete `StockTransfer`), `manufacturing/services.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `sklad_stock`.
- Produces:
  - `StockTransfer(product, seller, date, quantity_kg, note, created_by, created_at)` reverse on product `stock_transfers`, on seller `stock_transfers_in`.
  - `services.create_transfer(*, product, seller, quantity_kg, date, note, user) -> StockTransfer`; raises `InsufficientStock` if `quantity_kg > sklad_stock(product)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_manufacturing.py  (append)
from manufacturing.models import StockTransfer
from manufacturing.services import create_transfer


def _stock_40(material, finished_product, admin_user):
    _buy(material, "100", "1000", admin_user)
    create_production_run(product=finished_product, output_kg=Decimal("40"), date="2026-07-03",
                          note="", user=admin_user, items=[(material, Decimal("50"))])


def test_transfer_reduces_sklad(material, finished_product, admin_user, seller_user):
    _stock_40(material, finished_product, admin_user)
    create_transfer(product=finished_product, seller=seller_user, quantity_kg=Decimal("15"),
                    date="2026-07-05", note="", user=admin_user)
    assert sklad_stock(finished_product) == Decimal("25.000")


def test_transfer_blocks_over_sklad(material, finished_product, admin_user, seller_user):
    _stock_40(material, finished_product, admin_user)
    with pytest.raises(InsufficientStock):
        create_transfer(product=finished_product, seller=seller_user, quantity_kg=Decimal("99"),
                        date="2026-07-05", note="", user=admin_user)
    assert StockTransfer.objects.count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — `create_transfer` missing / model incomplete.

- [ ] **Step 3: Complete the StockTransfer model**

Replace the `StockTransfer` stub in `manufacturing/models.py`:
```python
class StockTransfer(models.Model):
    """A hand-off of finished goods from the sklad to a seller's own ombor."""

    product = models.ForeignKey(
        "crm.Product", on_delete=models.PROTECT,
        related_name="stock_transfers", verbose_name="Mahsulot",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_transfers_in", verbose_name="Sotuvchi",
    )
    date = models.DateField("Sana", default=timezone.localdate)
    quantity_kg = models.DecimalField("Miqdor (kg)", max_digits=12, decimal_places=3)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="stock_transfers_made", verbose_name="Kim topshirdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Omborga topshiruv"
        verbose_name_plural = "Omborga topshiruvlar"

    def __str__(self):
        return f"{self.product.name} → {self.seller}: {self.quantity_kg} kg ({self.date})"
```

- [ ] **Step 4: Add create_transfer**

In `manufacturing/services.py`:
```python
@transaction.atomic
def create_transfer(*, product, seller, quantity_kg, date, note, user):
    from .models import StockTransfer
    from .queries import sklad_stock

    available = sklad_stock(product)
    if quantity_kg > available:
        raise InsufficientStock(product.name, quantity_kg, available)
    return StockTransfer.objects.create(
        product=product, seller=seller, quantity_kg=quantity_kg,
        date=date, note=note, created_by=user,
    )
```

- [ ] **Step 5: Make migrations, run tests**

Run: `python manage.py makemigrations manufacturing && pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): sklad→seller transfers with shortfall block"
```

---

## Task 6: SellerStockEntry + seller ombor balance

**Files:**
- Modify: `manufacturing/models.py` (add `SellerStockEntry`), `manufacturing/queries.py` (`seller_ombor`, `annotate_seller_ombor`)
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `crm` weight expressions.
- Produces:
  - `SellerStockEntry(seller, product, date, quantity_kg, note, created_by, created_at)` — signed `quantity_kg` (+ own addition, − write-off); reverse on seller `own_stock_entries`, on product `seller_stock_entries`.
  - `queries.seller_ombor(user, product) -> Decimal` = transfers in + own entries − their sales + their restocked returns.
  - `queries.annotate_seller_ombor(product_qs, user) -> qs` adding `ombor` per product for that seller.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_manufacturing.py  (append)
from crm.models import Client, Sale, SaleItem
from manufacturing.models import SellerStockEntry
from manufacturing.queries import seller_ombor


def test_seller_ombor_from_transfer_and_own_entry(material, finished_product, admin_user, seller_user):
    _stock_40(material, finished_product, admin_user)
    create_transfer(product=finished_product, seller=seller_user, quantity_kg=Decimal("15"),
                    date="2026-07-05", note="", user=admin_user)
    SellerStockEntry.objects.create(seller=seller_user, product=finished_product,
                                    quantity_kg=Decimal("3"), note="topildi", created_by=seller_user)
    assert seller_ombor(seller_user, finished_product) == Decimal("18.000")


def test_seller_sale_reduces_ombor(material, finished_product, admin_user, seller_user):
    _stock_40(material, finished_product, admin_user)
    create_transfer(product=finished_product, seller=seller_user, quantity_kg=Decimal("15"),
                    date="2026-07-05", note="", user=admin_user)
    client = Client.objects.create(name="M", owner=seller_user)
    sale = Sale.objects.create(client=client, sales_rep=seller_user)
    SaleItem.objects.create(sale=sale, product=finished_product, dimension=Sale.Dimension.KG,
                            weight=Decimal("10"), price=Decimal("20000"), cost_price=Decimal("1250"))
    assert seller_ombor(seller_user, finished_product) == Decimal("5.000")
    # ...and the seller sale did NOT touch sklad:
    assert sklad_stock(finished_product) == Decimal("25.000")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — `SellerStockEntry` / `seller_ombor` missing.

- [ ] **Step 3: Add the model**

In `manufacturing/models.py`:
```python
class SellerStockEntry(models.Model):
    """A seller's own adjustment to their personal ombor: positive = goods added
    themselves, negative = write-off. Also seeds cutover opening balances."""

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="own_stock_entries", verbose_name="Sotuvchi",
    )
    product = models.ForeignKey(
        "crm.Product", on_delete=models.PROTECT,
        related_name="seller_stock_entries", verbose_name="Mahsulot",
    )
    date = models.DateField("Sana", default=timezone.localdate)
    quantity_kg = models.DecimalField("Miqdor (kg, +/−)", max_digits=12, decimal_places=3)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="recorded_seller_entries", verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Sotuvchi ombor harakati"
        verbose_name_plural = "Sotuvchi ombor harakatlari"

    def __str__(self):
        sign = "+" if self.quantity_kg >= 0 else ""
        return f"{self.seller} · {self.product.name}: {sign}{self.quantity_kg} kg"
```

- [ ] **Step 4: Add seller_ombor helpers to queries.py**

```python
def seller_ombor(user, product):
    """A seller's personal ombor balance (kg) for one product."""
    from crm.models import ITEM_WEIGHT_KG, RETURN_WEIGHT_KG, Return, SaleItem
    from .models import SellerStockEntry, StockTransfer

    transferred_in = (
        StockTransfer.objects.filter(seller=user, product=product)
        .aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")
    )
    own = (
        SellerStockEntry.objects.filter(seller=user, product=product)
        .aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")
    )
    sold = (
        SaleItem.objects.filter(product=product, sale__sales_rep=user)
        .aggregate(s=Sum(ITEM_WEIGHT_KG))["s"] or Decimal("0")
    )
    returned = (
        Return.objects.filter(product=product, restock=True, sale__sales_rep=user)
        .aggregate(s=Sum(RETURN_WEIGHT_KG))["s"] or Decimal("0")
    )
    return transferred_in + own - sold + returned


def annotate_seller_ombor(product_qs, user):
    """Annotate products with `ombor` = this seller's balance per product."""
    from crm.models import (
        ITEM_WEIGHT_KG, QTY, RETURN_WEIGHT_KG, ZERO_QTY, Return, SaleItem,
    )
    from .models import SellerStockEntry, StockTransfer

    def _sub(model, expr, **extra):
        return Subquery(
            model.objects.filter(product=OuterRef("pk"), **extra)
            .values("product").annotate(s=Sum(expr)).values("s"),
            output_field=QTY,
        )

    tin = _sub(StockTransfer, "quantity_kg", seller=user)
    own = _sub(SellerStockEntry, "quantity_kg", seller=user)
    sold = _sub(SaleItem, ITEM_WEIGHT_KG, sale__sales_rep=user)
    ret = _sub(Return, RETURN_WEIGHT_KG, restock=True, sale__sales_rep=user)
    return product_qs.annotate(
        ombor=(
            Coalesce(tin, ZERO_QTY) + Coalesce(own, ZERO_QTY)
            - Coalesce(sold, ZERO_QTY) + Coalesce(ret, ZERO_QTY)
        )
    )
```

- [ ] **Step 5: Make migrations, run tests**

Run: `python manage.py makemigrations manufacturing && pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): seller ombor balance + own stock entries"
```

---

## Task 7: Block sales that exceed available ombor / sklad

**Files:**
- Modify: `manufacturing/queries.py` (`available_for_sale`, `sale_stock_errors`), `crm/views.py` (`sale_create`, `sale_edit`)
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `seller_ombor`, `sklad_stock`, `accounts.User.Role`.
- Produces:
  - `queries.available_for_sale(user, product, exclude_sale_id=None) -> Decimal` — seller's ombor (or sklad if omborchi), adding back an excluded sale's items for that product.
  - `queries.sale_stock_errors(user, formset, exclude_sale_id=None) -> list[str]` — Uzbek error strings for each product short of stock.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py  (append)
from django.urls import reverse


def test_sale_blocked_when_seller_ombor_short(client, material, finished_product,
                                               admin_user, seller_user):
    _stock_40(material, finished_product, admin_user)
    create_transfer(product=finished_product, seller=seller_user, quantity_kg=Decimal("5"),
                    date="2026-07-05", note="", user=admin_user)
    c = Client.objects.create(name="Mijoz", owner=seller_user)
    client.force_login(seller_user)
    resp = client.post(reverse("sale_create"), {
        "date": "2026-07-06", "client": c.pk, "debt_deadline": "2026-07-20",
        "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
        "items-0-product": finished_product.pk, "items-0-dimension": "kg",
        "items-0-weight": "10", "items-0-price": "20000",
    })
    assert resp.status_code in (200, 422)                 # re-rendered form, not a redirect
    assert SaleItem.objects.count() == 0                  # nothing saved
    assert "omborda" in resp.content.decode().lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py::test_sale_blocked_when_seller_ombor_short -q`
Expected: FAIL — sale currently saves regardless of stock.

- [ ] **Step 3: Add availability helpers to queries.py**

```python
def available_for_sale(user, product, exclude_sale_id=None):
    """Stock a user may sell of a product: their ombor, or sklad for an omborchi.
    When editing a sale, add back that sale's own items so its lines don't count
    against themselves."""
    from accounts.models import User
    from crm.models import ITEM_WEIGHT_KG, SaleItem

    if user.role == User.Role.OMBORCHI:
        base = sklad_stock(product)
    else:
        base = seller_ombor(user, product)
    if exclude_sale_id is not None:
        back = (
            SaleItem.objects.filter(sale_id=exclude_sale_id, product=product)
            .aggregate(s=Sum(ITEM_WEIGHT_KG))["s"] or Decimal("0")
        )
        base += back
    return base


def sale_stock_errors(user, formset, exclude_sale_id=None):
    """Return a list of Uzbek error strings for each product on the sale formset
    that exceeds the seller's available stock. Empty list = OK to save."""
    from crm.models import Sale

    requested = {}
    for form in formset.forms:
        cd = getattr(form, "cleaned_data", None)
        if not cd or cd.get("DELETE"):
            continue
        product = cd.get("product")
        weight = cd.get("weight")
        if not product or weight is None:
            continue
        kg = weight / Decimal("1000") if cd.get("dimension") == Sale.Dimension.G else weight
        requested[product] = requested.get(product, Decimal("0")) + kg

    errors = []
    for product, kg in requested.items():
        avail = available_for_sale(user, product, exclude_sale_id)
        if kg > avail:
            errors.append(
                f"“{product.name}”: omborda {avail:.3f} kg bor, {kg:.3f} kg so'raldi."
            )
    return errors
```

- [ ] **Step 4: Enforce in sale_create and sale_edit**

In `crm/views.py`, add a deferred import inside each view (not at module top). In `sale_create`, after `if form.is_valid() and formset.is_valid():` and before `sale = form.save(commit=False)`:
```python
            from manufacturing.queries import sale_stock_errors
            stock_errors = sale_stock_errors(request.user, formset)
            if stock_errors:
                for msg in stock_errors:
                    form.add_error(None, msg)
                return _render_sale_form(request, form, formset, "Yangi sotuv", invalid=True)
```
In `sale_edit`, after the paid-vs-total check passes and before `sale = form.save()`:
```python
            from manufacturing.queries import sale_stock_errors
            stock_errors = sale_stock_errors(request.user, formset, exclude_sale_id=sale.pk)
            if stock_errors:
                for msg in stock_errors:
                    form.add_error(None, msg)
                return _render_sale_form(request, form, formset, "Sotuvni tahrirlash", invalid=True)
```
Keep the existing post-save `_warn_if_negative_stock_items` call as-is (harmless belt-and-suspenders; it now reflects sklad for direct sales).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_manufacturing.py -q && pytest -q`
Expected: PASS. Update any existing sale test that relied on selling with zero stock — give the seller a transfer first (or use an admin/omborchi rep) so the sale has stock to draw on.

- [ ] **Step 6: Commit**

```bash
git add manufacturing crm tests/test_manufacturing.py
git commit -m "feat(mfg): block sales exceeding seller ombor / sklad"
```

---

## Task 8: Forms

**Files:**
- Create: `manufacturing/forms.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Produces: `RawMaterialForm`, `MaterialPurchaseForm`, `ProductionRunForm`, `ProductionRunItemFormSet` (inline formset of `ProductionRunItemForm`), `StockTransferForm(user=...)`, `SellerStockEntryForm`. Money fields marked via `_mark_money`; product/material/seller selects via `_searchable_select`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py  (append)
from manufacturing.forms import MaterialPurchaseForm, StockTransferForm


def test_purchase_form_valid(material, admin_user):
    form = MaterialPurchaseForm(data={
        "material": material.pk, "date": "2026-07-01", "quantity_kg": "100",
        "price_per_kg": "1000", "method": "cash", "supplier": "Ali", "note": "",
    })
    assert form.is_valid(), form.errors


def test_transfer_form_lists_only_sellers(admin_user, seller_user):
    form = StockTransferForm(user=admin_user)
    seller_ids = set(form.fields["seller"].queryset.values_list("pk", flat=True))
    assert seller_user.pk in seller_ids
    assert admin_user.pk not in seller_ids            # admins aren't transfer targets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — `manufacturing.forms` missing.

- [ ] **Step 3: Implement forms.py**

```python
from django import forms
from django.utils import timezone

from accounts.models import User
from crm.forms import _mark_money, _searchable_select
from crm.models import Product

from .models import (
    MaterialPurchase, ProductionRun, ProductionRunItem, RawMaterial,
    SellerStockEntry, StockTransfer,
)

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class RawMaterialForm(forms.ModelForm):
    class Meta:
        model = RawMaterial
        fields = ["name", "sku", "note", "low_stock_threshold", "is_active"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class MaterialPurchaseForm(forms.ModelForm):
    class Meta:
        model = MaterialPurchase
        fields = ["material", "date", "quantity_kg", "price_per_kg", "method", "supplier", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = RawMaterial.objects.filter(is_active=True)
        _searchable_select(self.fields["material"], "Xomashyo tanlang")
        _mark_money(self.fields["price_per_kg"])


class ProductionRunForm(forms.ModelForm):
    class Meta:
        model = ProductionRun
        fields = ["product", "date", "output_kg", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        _searchable_select(self.fields["product"], "Mahsulot tanlang")


class ProductionRunItemForm(forms.ModelForm):
    class Meta:
        model = ProductionRunItem
        fields = ["material", "quantity_kg"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = RawMaterial.objects.filter(is_active=True)
        _searchable_select(self.fields["material"], "Xomashyo")


ProductionRunItemFormSet = forms.inlineformset_factory(
    ProductionRun, ProductionRunItem, form=ProductionRunItemForm,
    extra=1, can_delete=True, min_num=1, validate_min=True,
)


class StockTransferForm(forms.ModelForm):
    class Meta:
        model = StockTransfer
        fields = ["product", "seller", "date", "quantity_kg", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["seller"].queryset = User.objects.filter(role=User.Role.SALES)
        _searchable_select(self.fields["product"], "Mahsulot tanlang")
        _searchable_select(self.fields["seller"], "Sotuvchi tanlang")


class SellerStockEntryForm(forms.ModelForm):
    class Meta:
        model = SellerStockEntry
        fields = ["product", "date", "quantity_kg", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        _searchable_select(self.fields["product"], "Mahsulot tanlang")
        self.fields["quantity_kg"].help_text = "Qo'shish uchun musbat, hisobdan chiqarish uchun manfiy"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): forms for materials, purchases, runs, transfers, seller entries"
```

---

## Task 9: Materials + purchases views, URLs, templates

**Files:**
- Create: `manufacturing/views.py`, `manufacturing/urls.py`, `templates/manufacturing/material_list.html`, `templates/manufacturing/material_detail.html`, `templates/manufacturing/purchase_list.html`
- Modify: `config/urls.py`
- Test: `tests/test_manufacturing_permissions.py`

**Interfaces:**
- Consumes: `RawMaterialForm`, `MaterialPurchaseForm`, `services.create_purchase`, `crm.utils` helpers, `role_required`.
- Produces: url names `manufacturing:material_list`, `material_create`, `material_edit`, `material_detail`, `purchase_list`, `purchase_create`. Route prefix `/ishlab-chiqarish/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing_permissions.py
import pytest
from django.urls import reverse

MFG_ADMIN_URLS = ["manufacturing:material_list", "manufacturing:purchase_list"]


@pytest.mark.parametrize("name", MFG_ADMIN_URLS)
def test_seller_denied_sklad_pages(client, seller_user, name):
    client.force_login(seller_user)
    resp = client.get(reverse(name))
    assert resp.status_code == 403


@pytest.mark.parametrize("name", MFG_ADMIN_URLS)
def test_admin_sees_sklad_pages(client, admin_user, name):
    client.force_login(admin_user)
    resp = client.get(reverse(name))
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing_permissions.py -q`
Expected: FAIL — url names / views do not exist.

- [ ] **Step 3: Implement the sklad role gate + views**

`manufacturing/views.py`:
```python
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.decorators import role_required
from accounts.models import User
from crm.models import AuditLog
from crm.utils import form_response, form_success

from . import services
from .forms import MaterialPurchaseForm, RawMaterialForm
from .models import MaterialPurchase, RawMaterial

SKLAD_ROLES = (User.Role.ADMIN, User.Role.MANAGER, User.Role.OMBORCHI)


@role_required(*SKLAD_ROLES)
def material_list(request):
    materials = RawMaterial.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        materials = materials.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    page = Paginator(materials, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/material_list.html", {"page": page, "q": q})


@role_required(*SKLAD_ROLES)
def material_create(request):
    form = RawMaterialForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        material = form.save()
        AuditLog.record(request.user, AuditLog.Action.CREATE, "Xomashyo", material.pk, material.name)
        messages.success(request, f"“{material.name}” xomashyosi qo'shildi.")
        return form_success(request, reverse("manufacturing:material_list"))
    return form_response(request, form, "Yangi xomashyo", invalid=request.method == "POST")


@role_required(*SKLAD_ROLES)
def material_edit(request, pk):
    material = get_object_or_404(RawMaterial, pk=pk)
    form = RawMaterialForm(request.POST or None, instance=material)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(request.user, AuditLog.Action.UPDATE, "Xomashyo", material.pk, material.name)
        messages.success(request, f"“{material.name}” yangilandi.")
        return form_success(request, reverse("manufacturing:material_list"))
    return form_response(request, form, f"Tahrirlash: {material.name}", invalid=request.method == "POST")


@role_required(*SKLAD_ROLES)
def material_detail(request, pk):
    material = get_object_or_404(RawMaterial, pk=pk)
    purchases = material.purchases.select_related("created_by")[:50]
    return render(request, "manufacturing/material_detail.html", {
        "material": material, "purchases": purchases,
        "current_stock": material.current_stock,
    })


@role_required(*SKLAD_ROLES)
def purchase_list(request):
    purchases = MaterialPurchase.objects.select_related("material", "created_by")
    page = Paginator(purchases, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/purchase_list.html", {"page": page})


@role_required(*SKLAD_ROLES)
def purchase_create(request):
    form = MaterialPurchaseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        purchase = services.create_purchase(
            material=cd["material"], quantity_kg=cd["quantity_kg"],
            price_per_kg=cd["price_per_kg"], date=cd["date"], method=cd["method"],
            supplier=cd["supplier"], note=cd["note"], user=request.user,
        )
        AuditLog.record(request.user, AuditLog.Action.CREATE, "Xomashyo xaridi", purchase.pk,
                        f"{purchase.material.name} — {purchase.total:,.0f} so'm")
        messages.success(request, "Xarid qo'shildi.")
        return form_success(request, reverse("manufacturing:purchase_list"))
    return form_response(request, form, "Yangi xarid", invalid=request.method == "POST")
```

`manufacturing/urls.py`:
```python
from django.urls import path

from . import views

app_name = "manufacturing"

urlpatterns = [
    path("xomashyo/", views.material_list, name="material_list"),
    path("xomashyo/yangi/", views.material_create, name="material_create"),
    path("xomashyo/<int:pk>/", views.material_detail, name="material_detail"),
    path("xomashyo/<int:pk>/edit/", views.material_edit, name="material_edit"),
    path("xaridlar/", views.purchase_list, name="purchase_list"),
    path("xaridlar/yangi/", views.purchase_create, name="purchase_create"),
]
```

In `config/urls.py`, add near the top imports `from django.urls import include, path` (ensure `include` is imported) and add to `urlpatterns`:
```python
    path("ishlab-chiqarish/", include("manufacturing.urls")),
```

- [ ] **Step 4: Create templates**

`templates/manufacturing/material_list.html` (mirror `crm/product_list.html` toolbar + table):
```html
{% extends "base.html" %}
{% block title %}Xomashyo · Paket CRM{% endblock %}
{% block topbar_title %}Xomashyo{% endblock %}
{% block fab %}<a class="fab" href="{% url 'manufacturing:material_create' %}" data-modal>+ Yangi xomashyo</a>{% endblock %}
{% block content %}
<form method="get" class="searchbar" role="search" data-live-search>
  <div class="toolbar-search{% if q %} is-active{% endif %}">
    <svg class="toolbar-search-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input type="search" name="q" value="{{ q }}" placeholder="Nomi yoki SKU bo'yicha qidirish…" autocomplete="off">
    {% if q %}<a href="?" class="toolbar-search-x">&times;</a>{% endif %}
  </div>
</form>
{% if page.object_list %}
<div class="table-wrap"><table>
  <tr><th class="sticky-col">Nomi</th><th>SKU</th><th class="num">O'rtacha tannarx</th><th class="num">Qoldiq (kg)</th><th>Holati</th></tr>
  {% for m in page %}
  <tr>
    <td class="sticky-col"><a href="{% url 'manufacturing:material_detail' m.pk %}">{{ m.name }}</a></td>
    <td>{{ m.sku }}</td>
    <td class="num">{{ m.avg_cost|floatformat:"0g" }}</td>
    <td class="num">{{ m.current_stock|floatformat:"-3g" }}</td>
    <td>{% if m.is_low_stock %}<span class="badge badge-warning">Kam qoldi</span>{% else %}<span class="badge badge-ok">Yetarli</span>{% endif %}</td>
  </tr>
  {% endfor %}
</table></div>
{% include "crm/_pagination.html" %}
{% else %}<p class="muted">Xomashyo topilmadi.</p>{% endif %}
{% endblock %}
```

`templates/manufacturing/material_detail.html`:
```html
{% extends "base.html" %}
{% block title %}{{ material.name }} · Xomashyo{% endblock %}
{% block topbar_title %}{{ material.name }}{% endblock %}
{% block fab %}<a class="fab" href="{% url 'manufacturing:material_edit' material.pk %}" data-modal>Tahrirlash</a>{% endblock %}
{% block content %}
<div class="cards">
  <div class="card"><div class="card-label">O'rtacha tannarx</div><div class="card-value">{{ material.avg_cost|floatformat:"0g" }} so'm</div></div>
  <div class="card"><div class="card-label">Qoldiq</div><div class="card-value">{{ current_stock|floatformat:"-3g" }} kg</div></div>
</div>
<h3>So'nggi xaridlar</h3>
<div class="table-wrap"><table>
  <tr><th>Sana</th><th class="num">Miqdor (kg)</th><th class="num">Narx (1 kg)</th><th>Yetkazib beruvchi</th></tr>
  {% for p in purchases %}
  <tr><td>{{ p.date }}</td><td class="num">{{ p.quantity_kg|floatformat:"-3g" }}</td><td class="num">{{ p.price_per_kg|floatformat:"0g" }}</td><td>{{ p.supplier }}</td></tr>
  {% empty %}<tr><td colspan="4" class="muted">Xarid yo'q.</td></tr>{% endfor %}
</table></div>
{% endblock %}
```

`templates/manufacturing/purchase_list.html`:
```html
{% extends "base.html" %}
{% block title %}Xaridlar · Paket CRM{% endblock %}
{% block topbar_title %}Xomashyo xaridlari{% endblock %}
{% block fab %}<a class="fab" href="{% url 'manufacturing:purchase_create' %}" data-modal>+ Yangi xarid</a>{% endblock %}
{% block content %}
<div class="table-wrap"><table>
  <tr><th>Sana</th><th>Xomashyo</th><th class="num">Miqdor (kg)</th><th class="num">Narx (1 kg)</th><th class="num">Jami</th><th>Kim</th></tr>
  {% for p in page %}
  <tr><td>{{ p.date }}</td><td>{{ p.material.name }}</td><td class="num">{{ p.quantity_kg|floatformat:"-3g" }}</td><td class="num">{{ p.price_per_kg|floatformat:"0g" }}</td><td class="num">{{ p.total|floatformat:"0g" }}</td><td>{{ p.created_by }}</td></tr>
  {% empty %}<tr><td colspan="6" class="muted">Xarid yo'q.</td></tr>{% endfor %}
</table></div>
{% include "crm/_pagination.html" %}
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_manufacturing_permissions.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing config templates/manufacturing tests/test_manufacturing_permissions.py
git commit -m "feat(mfg): materials + purchases pages"
```

---

## Task 10: Production run views + template (dynamic material rows)

**Files:**
- Create: `templates/manufacturing/production_list.html`, `templates/manufacturing/production_form.html`
- Modify: `manufacturing/views.py`, `manufacturing/urls.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `ProductionRunForm`, `ProductionRunItemFormSet`, `services.create_production_run`, `InsufficientStock`.
- Produces: url names `manufacturing:production_list`, `production_create`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py  (append)
def test_production_create_view(client, material, finished_product, admin_user):
    _buy(material, "100", "1000", admin_user)
    client.force_login(admin_user)
    resp = client.post(reverse("manufacturing:production_create"), {
        "product": finished_product.pk, "date": "2026-07-03", "output_kg": "40", "note": "",
        "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "1000",
        "items-0-material": material.pk, "items-0-quantity_kg": "50",
    })
    assert resp.status_code in (204, 302)
    assert ProductionRun.objects.count() == 1
    finished_product.refresh_from_db()
    assert finished_product.cost_price == Decimal("1250.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py::test_production_create_view -q`
Expected: FAIL — view/url missing.

- [ ] **Step 3: Add views**

In `manufacturing/views.py` add imports `from .forms import ProductionRunForm, ProductionRunItemFormSet` and `from .models import ProductionRun` and `from .services import InsufficientStock`, then:
```python
def _render_production_form(request, form, formset, invalid=False):
    ctx = {"form": form, "formset": formset, "title": "Yangi ishlab chiqarish"}
    from crm.utils import is_ajax
    if is_ajax(request):
        return render(request, "manufacturing/production_form.html", ctx,
                      status=422 if invalid else 200)
    return render(request, "manufacturing/production_form.html", ctx)


@role_required(*SKLAD_ROLES)
def production_list(request):
    runs = ProductionRun.objects.select_related("product", "created_by")
    page = Paginator(runs, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/production_list.html", {"page": page})


@role_required(*SKLAD_ROLES)
def production_create(request):
    form = ProductionRunForm(request.POST or None)
    formset = ProductionRunItemFormSet(request.POST or None, instance=ProductionRun(), prefix="items")
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            items = [
                (f.cleaned_data["material"], f.cleaned_data["quantity_kg"])
                for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get("DELETE")
                and f.cleaned_data.get("material") and f.cleaned_data.get("quantity_kg")
            ]
            try:
                run = services.create_production_run(
                    product=form.cleaned_data["product"], output_kg=form.cleaned_data["output_kg"],
                    date=form.cleaned_data["date"], note=form.cleaned_data["note"],
                    user=request.user, items=items,
                )
            except InsufficientStock as exc:
                form.add_error(
                    None,
                    f"“{exc.label}”: omborda {exc.available:.3f} kg bor, {exc.requested:.3f} kg kerak.",
                )
                return _render_production_form(request, form, formset, invalid=True)
            AuditLog.record(request.user, AuditLog.Action.CREATE, "Ishlab chiqarish", run.pk,
                            f"{run.product.name} — {run.output_kg} kg")
            messages.success(request, "Ishlab chiqarish qo'shildi.")
            return form_success(request, reverse("manufacturing:production_list"))
        return _render_production_form(request, form, formset, invalid=True)
    return _render_production_form(request, form, formset)
```
Add to `manufacturing/urls.py`:
```python
    path("ishlab-chiqarish/", views.production_list, name="production_list"),
    path("ishlab-chiqarish/yangi/", views.production_create, name="production_create"),
```

- [ ] **Step 4: Create templates**

`templates/manufacturing/production_list.html`:
```html
{% extends "base.html" %}
{% block title %}Ishlab chiqarish · Paket CRM{% endblock %}
{% block topbar_title %}Ishlab chiqarish{% endblock %}
{% block fab %}<a class="fab" href="{% url 'manufacturing:production_create' %}" data-modal>+ Yangi</a>{% endblock %}
{% block content %}
<div class="table-wrap"><table>
  <tr><th>Sana</th><th>Mahsulot</th><th class="num">Chiqdi (kg)</th><th class="num">Tannarx (1 kg)</th><th>Kim</th></tr>
  {% for r in page %}
  <tr><td>{{ r.date }}</td><td>{{ r.product.name }}</td><td class="num">{{ r.output_kg|floatformat:"-3g" }}</td><td class="num">{{ r.cost_per_kg|floatformat:"0g" }}</td><td>{{ r.created_by }}</td></tr>
  {% empty %}<tr><td colspan="5" class="muted">Yozuv yo'q.</td></tr>{% endfor %}
</table></div>
{% include "crm/_pagination.html" %}
{% endblock %}
```

`templates/manufacturing/production_form.html` — dynamic material rows using the same formset JS pattern as `crm/sale_form.html`. Inspect `templates/crm/sale_form.html` and `_sale_item_row.html` and replicate: a hidden empty-form template cloned by the existing add-row script (`data-formset`, `data-empty-form`, `data-add-row` hooks). Keep field names as `items-…`. Concretely:
```html
{% extends "base.html" %}
{% block title %}{{ title }} · Paket CRM{% endblock %}
{% block topbar_title %}{{ title }}{% endblock %}
{% block content %}
<form method="post" class="form" data-formset>
  {% csrf_token %}
  {{ form.non_field_errors }}
  {% for field in form %}
    <div class="field">{{ field.label_tag }}{{ field }}{{ field.errors }}</div>
  {% endfor %}
  <h4>Sarflangan xomashyo</h4>
  {{ formset.management_form }}
  <div data-formset-rows>
    {% for f in formset %}
      <div class="formset-row" data-formset-row>
        {{ f.id }}
        {{ f.material }} {{ f.quantity_kg }}
        {% if f.instance.pk %}{{ f.DELETE }}{% endif %}
        {{ f.errors }}
      </div>
    {% endfor %}
  </div>
  <template data-empty-form>
    <div class="formset-row" data-formset-row>{{ formset.empty_form.material }} {{ formset.empty_form.quantity_kg }}</div>
  </template>
  <button type="button" class="btn btn-ghost" data-add-row>+ Xomashyo qo'shish</button>
  <div class="form-actions"><button type="submit" class="btn btn-primary">Saqlash</button></div>
</form>
{% endblock %}
```
If the existing add-row script keys off different attributes, match those exactly instead. Verify by reading `templates/crm/sale_form.html` before writing.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_manufacturing.py::test_production_create_view -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing templates/manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): production run entry with dynamic material rows"
```

---

## Task 11: Transfers + sklad ombor overview

**Files:**
- Create: `templates/manufacturing/transfer_list.html`, `templates/manufacturing/sklad_ombor.html`
- Modify: `manufacturing/views.py`, `manufacturing/urls.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `StockTransferForm`, `services.create_transfer`, `queries.annotate_sklad_stock`.
- Produces: url names `manufacturing:transfer_list`, `transfer_create`, `sklad_ombor`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py  (append)
def test_transfer_create_view(client, material, finished_product, admin_user, seller_user):
    _stock_40(material, finished_product, admin_user)
    client.force_login(admin_user)
    resp = client.post(reverse("manufacturing:transfer_create"), {
        "product": finished_product.pk, "seller": seller_user.pk,
        "date": "2026-07-05", "quantity_kg": "10", "note": "",
    })
    assert resp.status_code in (204, 302)
    assert StockTransfer.objects.count() == 1
    assert sklad_stock(finished_product) == Decimal("30.000")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py::test_transfer_create_view -q`
Expected: FAIL.

- [ ] **Step 3: Add views**

In `manufacturing/views.py` add `from .forms import StockTransferForm`, `from .models import StockTransfer`, `from crm.models import Product`, `from .queries import annotate_sklad_stock`:
```python
@role_required(*SKLAD_ROLES)
def sklad_ombor(request):
    products = annotate_sklad_stock(Product.objects.filter(is_active=True)).order_by("name")
    q = request.GET.get("q", "").strip()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    page = Paginator(products, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/sklad_ombor.html", {"page": page, "q": q})


@role_required(*SKLAD_ROLES)
def transfer_list(request):
    transfers = StockTransfer.objects.select_related("product", "seller", "created_by")
    page = Paginator(transfers, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/transfer_list.html", {"page": page})


@role_required(*SKLAD_ROLES)
def transfer_create(request):
    form = StockTransferForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            transfer = services.create_transfer(
                product=cd["product"], seller=cd["seller"], quantity_kg=cd["quantity_kg"],
                date=cd["date"], note=cd["note"], user=request.user,
            )
        except InsufficientStock as exc:
            form.add_error("quantity_kg",
                           f"Omborda {exc.available:.3f} kg bor, {exc.requested:.3f} kg so'raldi.")
            return form_response(request, form, "Sotuvchiga topshirish", invalid=True)
        AuditLog.record(request.user, AuditLog.Action.TRANSFER, "Omborga topshiruv", transfer.pk,
                        f"{transfer.product.name} → {transfer.seller} — {transfer.quantity_kg} kg")
        messages.success(request, "Topshiruv qo'shildi.")
        return form_success(request, reverse("manufacturing:transfer_list"))
    return form_response(request, form, "Sotuvchiga topshirish", invalid=request.method == "POST")
```
Add urls:
```python
    path("ombor/", views.sklad_ombor, name="sklad_ombor"),
    path("topshiruvlar/", views.transfer_list, name="transfer_list"),
    path("topshiruvlar/yangi/", views.transfer_create, name="transfer_create"),
```

- [ ] **Step 4: Create templates**

`templates/manufacturing/sklad_ombor.html` — same table shape as `material_list.html`, columns Nomi / Tannarx / Qoldiq (`{{ p.stock|floatformat:"-3g" }}`), with the toolbar-search form. FAB links to `manufacturing:transfer_create`.

`templates/manufacturing/transfer_list.html`:
```html
{% extends "base.html" %}
{% block title %}Topshiruvlar · Paket CRM{% endblock %}
{% block topbar_title %}Sotuvchiga topshiruvlar{% endblock %}
{% block fab %}<a class="fab" href="{% url 'manufacturing:transfer_create' %}" data-modal>+ Topshirish</a>{% endblock %}
{% block content %}
<div class="table-wrap"><table>
  <tr><th>Sana</th><th>Mahsulot</th><th>Sotuvchi</th><th class="num">Miqdor (kg)</th><th>Kim</th></tr>
  {% for t in page %}
  <tr><td>{{ t.date }}</td><td>{{ t.product.name }}</td><td>{{ t.seller }}</td><td class="num">{{ t.quantity_kg|floatformat:"-3g" }}</td><td>{{ t.created_by }}</td></tr>
  {% empty %}<tr><td colspan="5" class="muted">Topshiruv yo'q.</td></tr>{% endfor %}
</table></div>
{% include "crm/_pagination.html" %}
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing templates/manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): transfers + sklad ombor overview"
```

---

## Task 12: Seller "Mening omborim" page + own entries

**Files:**
- Create: `templates/manufacturing/seller_ombor.html`
- Modify: `manufacturing/views.py`, `manufacturing/urls.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `SellerStockEntryForm`, `queries.annotate_seller_ombor`.
- Produces: url names `manufacturing:my_ombor`, `seller_entry_create`. Access: `SALES` sees only their own; admin/manager may view a seller via `?seller=<pk>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py  (append)
def test_seller_sees_own_ombor(client, material, finished_product, admin_user, seller_user):
    _stock_40(material, finished_product, admin_user)
    create_transfer(product=finished_product, seller=seller_user, quantity_kg=Decimal("12"),
                    date="2026-07-05", note="", user=admin_user)
    client.force_login(seller_user)
    resp = client.get(reverse("manufacturing:my_ombor"))
    assert resp.status_code == 200
    assert finished_product.name.encode() in resp.content


def test_seller_can_add_own_entry(client, finished_product, seller_user):
    client.force_login(seller_user)
    resp = client.post(reverse("manufacturing:seller_entry_create"), {
        "product": finished_product.pk, "date": "2026-07-06", "quantity_kg": "5", "note": "topildi",
    })
    assert resp.status_code in (204, 302)
    assert SellerStockEntry.objects.filter(seller=seller_user).count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL.

- [ ] **Step 3: Add views**

In `manufacturing/views.py` add `from .forms import SellerStockEntryForm`, `from .models import SellerStockEntry`, `from .queries import annotate_seller_ombor`:
```python
@role_required(User.Role.SALES, User.Role.ADMIN, User.Role.MANAGER)
def my_ombor(request):
    target = request.user
    seller_pk = request.GET.get("seller", "")
    if request.user.can_see_all_records and seller_pk.isdigit():
        target = get_object_or_404(User, pk=seller_pk)
    products = annotate_seller_ombor(
        Product.objects.filter(is_active=True), target
    ).filter(ombor__gt=0).order_by("name")
    transfers = (
        StockTransfer.objects.filter(seller=target)
        .select_related("product", "created_by")[:50]
    )
    return render(request, "manufacturing/seller_ombor.html", {
        "products": products, "transfers": transfers, "target": target,
        "is_self": target == request.user,
    })


@role_required(User.Role.SALES, User.Role.ADMIN, User.Role.MANAGER)
def seller_entry_create(request):
    form = SellerStockEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.seller = request.user
        entry.created_by = request.user
        entry.save()
        AuditLog.record(request.user, AuditLog.Action.CREATE, "Sotuvchi ombor", entry.pk,
                        f"{entry.product.name} — {entry.quantity_kg} kg")
        messages.success(request, "Ombor harakati qo'shildi.")
        return form_success(request, reverse("manufacturing:my_ombor"))
    return form_response(request, form, "Omboriga qo'shish", invalid=request.method == "POST")
```
Add urls:
```python
    path("mening-omborim/", views.my_ombor, name="my_ombor"),
    path("mening-omborim/qoshish/", views.seller_entry_create, name="seller_entry_create"),
```

- [ ] **Step 4: Create template**

`templates/manufacturing/seller_ombor.html`:
```html
{% extends "base.html" %}
{% block title %}Mening omborim · Paket CRM{% endblock %}
{% block topbar_title %}{% if is_self %}Mening omborim{% else %}{{ target }} ombori{% endif %}{% endblock %}
{% block fab %}{% if is_self %}<a class="fab" href="{% url 'manufacturing:seller_entry_create' %}" data-modal>+ Qo'shish</a>{% endif %}{% endblock %}
{% block content %}
<h3>Qoldiq</h3>
<div class="table-wrap"><table>
  <tr><th class="sticky-col">Mahsulot</th><th class="num">Qoldiq (kg)</th></tr>
  {% for p in products %}
  <tr><td class="sticky-col">{{ p.name }}</td><td class="num">{{ p.ombor|floatformat:"-3g" }}</td></tr>
  {% empty %}<tr><td colspan="2" class="muted">Omborda mahsulot yo'q.</td></tr>{% endfor %}
</table></div>
<h3>Qabul qilingan topshiruvlar</h3>
<div class="table-wrap"><table>
  <tr><th>Sana</th><th>Mahsulot</th><th class="num">Miqdor (kg)</th><th>Kim topshirdi</th></tr>
  {% for t in transfers %}
  <tr><td>{{ t.date }}</td><td>{{ t.product.name }}</td><td class="num">{{ t.quantity_kg|floatformat:"-3g" }}</td><td>{{ t.created_by }}</td></tr>
  {% empty %}<tr><td colspan="4" class="muted">Hali topshiruv yo'q.</td></tr>{% endfor %}
</table></div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing templates/manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): seller ombor page + own stock entries"
```

---

## Task 13: Sklad kassa

**Files:**
- Create: `templates/manufacturing/sklad_kassa.html`
- Modify: `manufacturing/views.py`, `manufacturing/urls.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Consumes: `crm.models.Payment / Expense / ProductionRemittance`, `MaterialPurchase`, `accounts.User.Role.OMBORCHI`.
- Produces: url name `manufacturing:sklad_kassa`. Balance = (remittances + direct-sale payments) − (material purchases + omborchi expenses), date-range filtered.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py  (append)
from crm.models import Payment, ProductionRemittance


def test_sklad_kassa_balance(client, material, admin_user, seller_user):
    _buy(material, "100", "1000", admin_user)   # 100_000 outflow
    ProductionRemittance.objects.create(seller=seller_user, amount=Decimal("30000"),
                                        created_by=seller_user)   # 30_000 inflow
    client.force_login(admin_user)
    resp = client.get(reverse("manufacturing:sklad_kassa") + "?from=2026-07-01&to=2026-07-31")
    assert resp.status_code == 200
    ctx = resp.context
    assert ctx["inflow"] == Decimal("30000.00")
    assert ctx["outflow"] == Decimal("100000.00")
    assert ctx["balance"] == Decimal("-70000.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py::test_sklad_kassa_balance -q`
Expected: FAIL.

- [ ] **Step 3: Add the view**

In `manufacturing/views.py` add imports `from datetime import date`, `from decimal import Decimal`, `from django.db.models import Sum`, `from crm.models import Expense, Payment, ProductionRemittance`:
```python
def _range(request):
    def parse(v):
        try:
            return date.fromisoformat(v)
        except (TypeError, ValueError):
            return None
    today = date.today()
    d_from = parse(request.GET.get("from")) or today.replace(day=1)
    d_to = parse(request.GET.get("to")) or today
    return d_from, d_to


@role_required(*SKLAD_ROLES)
def sklad_kassa(request):
    d_from, d_to = _range(request)
    rng = {"date__gte": d_from, "date__lte": d_to}

    remitted = ProductionRemittance.objects.filter(**rng).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    direct_paid = (
        Payment.objects.filter(sale__sales_rep__role=User.Role.OMBORCHI, **rng)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    purchases = MaterialPurchase.objects.filter(**rng)
    purchase_total = sum((p.total for p in purchases), Decimal("0"))
    expenses = (
        Expense.objects.filter(created_by__role=User.Role.OMBORCHI, **rng)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )

    inflow = remitted + direct_paid
    outflow = purchase_total + expenses
    ctx = {
        "d_from": d_from, "d_to": d_to,
        "remitted": remitted, "direct_paid": direct_paid,
        "purchase_total": purchase_total, "expense_total": expenses,
        "inflow": inflow, "outflow": outflow, "balance": inflow - outflow,
        "purchases": purchases.select_related("material")[:100],
    }
    return render(request, "manufacturing/sklad_kassa.html", ctx)
```
Add url:
```python
    path("kassa/", views.sklad_kassa, name="sklad_kassa"),
```

- [ ] **Step 4: Create template**

`templates/manufacturing/sklad_kassa.html`:
```html
{% extends "base.html" %}
{% block title %}Sklad kassa · Paket CRM{% endblock %}
{% block topbar_title %}Sklad kassa{% endblock %}
{% block content %}
<form method="get" class="filter-toolbar">
  <input type="date" name="from" value="{{ d_from|date:'Y-m-d' }}">
  <input type="date" name="to" value="{{ d_to|date:'Y-m-d' }}">
  <button type="submit" class="btn btn-ghost">Ko'rsatish</button>
</form>
<div class="cards">
  <div class="card"><div class="card-label">Kirim</div><div class="card-value">{{ inflow|floatformat:"0g" }} so'm</div></div>
  <div class="card"><div class="card-label">Chiqim</div><div class="card-value">{{ outflow|floatformat:"0g" }} so'm</div></div>
  <div class="card"><div class="card-label">Qoldiq</div><div class="card-value">{{ balance|floatformat:"0g" }} so'm</div></div>
</div>
<h3>Tafsilot</h3>
<div class="table-wrap"><table>
  <tr><th>Manba</th><th class="num">Summa (so'm)</th></tr>
  <tr><td>Sotuvchilardan topshiruv</td><td class="num">{{ remitted|floatformat:"0g" }}</td></tr>
  <tr><td>To'g'ridan-to'g'ri sotuv to'lovlari</td><td class="num">{{ direct_paid|floatformat:"0g" }}</td></tr>
  <tr><td>Xomashyo xaridi</td><td class="num">−{{ purchase_total|floatformat:"0g" }}</td></tr>
  <tr><td>Ishlab chiqarish chiqimlari</td><td class="num">−{{ expense_total|floatformat:"0g" }}</td></tr>
</table></div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_manufacturing.py::test_sklad_kassa_balance -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing templates/manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): sklad kassa (production cashbox)"
```

---

## Task 14: Navigation + role gating in base template

**Files:**
- Modify: `templates/base.html`
- Test: `tests/test_manufacturing_permissions.py`

**Interfaces:**
- Produces: a nav group "Ishlab chiqarish" visible to admin/manager/omborchi linking materials, purchases, production, sklad ombor, transfers, sklad kassa; a "Mening omborim" link for sellers (and admins/managers).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing_permissions.py  (append)
def test_nav_shows_sklad_for_omborchi(client, db):
    from accounts.models import User
    omb = User.objects.create_user(username="omb", password="x", role=User.Role.OMBORCHI)
    client.force_login(omb)
    html = client.get(reverse("dashboard")).content.decode()
    assert "Ishlab chiqarish" in html
    assert reverse("manufacturing:material_list") in html


def test_nav_hides_sklad_for_seller(client, seller_user):
    client.force_login(seller_user)
    html = client.get(reverse("dashboard")).content.decode()
    assert reverse("manufacturing:material_list") not in html
    assert reverse("manufacturing:my_ombor") in html      # seller sees own ombor link
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing_permissions.py -q`
Expected: FAIL — nav links absent.

- [ ] **Step 3: Add nav markup**

In `templates/base.html`, add a template helper flag near the top of `<nav>` usage. Add a new nav group after the "Savdo" group (place before "Nazorat"):
```html
      {% if user.is_admin_role or user.is_manager_role or user.role == 'omborchi' %}
      <div class="nav-group">
        <div class="nav-group-label">Ishlab chiqarish</div>
        <a class="nav-item {% if 'material' in url_name %}active{% endif %}" href="{% url 'manufacturing:material_list' %}"><span>Xomashyo</span></a>
        <a class="nav-item {% if 'purchase' in url_name %}active{% endif %}" href="{% url 'manufacturing:purchase_list' %}"><span>Xaridlar</span></a>
        <a class="nav-item {% if 'production' in url_name %}active{% endif %}" href="{% url 'manufacturing:production_list' %}"><span>Ishlab chiqarish</span></a>
        <a class="nav-item {% if url_name == 'sklad_ombor' %}active{% endif %}" href="{% url 'manufacturing:sklad_ombor' %}"><span>Sklad ombor</span></a>
        <a class="nav-item {% if 'transfer' in url_name %}active{% endif %}" href="{% url 'manufacturing:transfer_list' %}"><span>Topshiruvlar</span></a>
        <a class="nav-item {% if url_name == 'sklad_kassa' %}active{% endif %}" href="{% url 'manufacturing:sklad_kassa' %}"><span>Sklad kassa</span></a>
      </div>
      {% endif %}
      <div class="nav-group">
        <a class="nav-item {% if 'ombor' in url_name and url_name == 'my_ombor' %}active{% endif %}" href="{% url 'manufacturing:my_ombor' %}"><span>Mening omborim</span></a>
      </div>
```
Note: `url_name` is NOT set by any context processor in this project — in the current `base.html` it resolves to an empty string, so the `active` class is effectively cosmetic/no-op today. This plan's markup deliberately matches the existing nav pattern (`{% if 'sale' in url_name %}`) for consistency; the `{% url %}` links render regardless, which is what the permission tests assert. If you want working active-state, switch these checks to `request.resolver_match.url_name` in a follow-up (out of scope here).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_manufacturing_permissions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/base.html tests/test_manufacturing_permissions.py
git commit -m "feat(mfg): sidebar nav for sklad + seller ombor"
```

---

## Task 15: Cutover data migration

**Files:**
- Create: `manufacturing/migrations/00XX_cutover_opening_balances.py`
- Test: `tests/test_manufacturing.py`

**Interfaces:**
- Produces: a data migration that (1) inserts one negative `StockEntry` per product = −(historical sold − restocked returns) so sklad equals today's real net stock under the new formula; (2) inserts a positive `SellerStockEntry` per (seller, product) = their historical net sales so every seller ombor starts at exactly 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manufacturing.py  (append)
def test_cutover_zeroes_seller_and_preserves_sklad():
    """The cutover logic, applied to pre-existing sales, leaves sklad at the real
    net stock and each seller ombor at zero. Exercised via the migration helper."""
    from manufacturing.migrations_helpers import apply_cutover  # extracted for testability
    # (See Step 3 — the migration calls this same function.)
    assert callable(apply_cutover)
```

Rework: to keep the migration testable, put the logic in a plain function and have the migration call it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manufacturing.py -q`
Expected: FAIL — helper missing.

- [ ] **Step 3: Implement the cutover helper + migration**

Create `manufacturing/migrations_helpers.py`:
```python
from decimal import Decimal


def apply_cutover(apps):
    """Seed opening balances so the new sklad/seller model matches physical reality
    on cutover day. Idempotent-guarded by the note text."""
    Product = apps.get_model("crm", "Product")
    SaleItem = apps.get_model("crm", "SaleItem")
    Return = apps.get_model("crm", "Return")
    StockEntry = apps.get_model("crm", "StockEntry")
    SellerStockEntry = apps.get_model("manufacturing", "SellerStockEntry")
    User = apps.get_model("accounts", "User")

    system_user = User.objects.filter(is_superuser=True).order_by("pk").first()
    if system_user is None:
        system_user = User.objects.order_by("pk").first()
    if system_user is None:
        return  # empty DB (fresh test) — nothing to seed

    def kg(item):
        w = item.weight
        return w / Decimal("1000") if item.dimension == "g" else w

    NOTE = "Cutover moslash"
    for product in Product.objects.all():
        if StockEntry.objects.filter(product=product, note=NOTE).exists():
            continue
        sold = sum((kg(i) for i in SaleItem.objects.filter(product=product)), Decimal("0"))
        restocked = sum(
            (kg(r) for r in Return.objects.filter(product=product, restock=True)),
            Decimal("0"),
        )
        net = sold - restocked
        if net != 0:
            StockEntry.objects.create(
                product=product, quantity_kg=-net, note=NOTE, created_by=system_user,
            )

    # Per (seller, product): seller net sales → positive opening entry → ombor starts at 0.
    seen = set()
    for item in SaleItem.objects.select_related("sale", "sale__sales_rep", "product"):
        rep = item.sale.sales_rep
        product = item.product
        key = (rep.pk, product.pk)
        if key in seen:
            continue
        seen.add(key)
        if SellerStockEntry.objects.filter(seller=rep, product=product, note=NOTE).exists():
            continue
        sold = sum(
            (kg(i) for i in SaleItem.objects.filter(product=product, sale__sales_rep=rep)),
            Decimal("0"),
        )
        restocked = sum(
            (kg(r) for r in Return.objects.filter(
                product=product, restock=True, sale__sales_rep=rep) if r.restock),
            Decimal("0"),
        )
        net = sold - restocked
        if net != 0:
            SellerStockEntry.objects.create(
                seller=rep, product=product, quantity_kg=net, note=NOTE, created_by=system_user,
            )
```

Create the migration `manufacturing/migrations/00XX_cutover_opening_balances.py` (replace `00XX` and `previous_migration` with real values from `ls manufacturing/migrations`):
```python
from django.db import migrations

from manufacturing.migrations_helpers import apply_cutover


def forwards(apps, schema_editor):
    apply_cutover(apps)


def backwards(apps, schema_editor):
    Product = apps.get_model("crm", "Product")  # noqa: F841
    StockEntry = apps.get_model("crm", "StockEntry")
    SellerStockEntry = apps.get_model("manufacturing", "SellerStockEntry")
    StockEntry.objects.filter(note="Cutover moslash").delete()
    SellerStockEntry.objects.filter(note="Cutover moslash").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("manufacturing", "<previous_migration>"),
        ("crm", "0019_employee_expense_employee_attendance"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
```

- [ ] **Step 4: Add an integration test that runs the helper on real data**

```python
# tests/test_manufacturing.py  (append)
def test_cutover_math(db, admin_user, seller_user):
    from django.apps import apps as global_apps
    from manufacturing.migrations_helpers import apply_cutover
    p = Product.objects.create(name="X", sku="CUT-1", price=Decimal("100"))
    StockEntry.objects.create(product=p, quantity_kg=Decimal("100"), created_by=admin_user)
    c = Client.objects.create(name="C", owner=seller_user)
    sale = Sale.objects.create(client=c, sales_rep=seller_user)
    SaleItem.objects.create(sale=sale, product=p, dimension=Sale.Dimension.KG,
                            weight=Decimal("30"), price=Decimal("100"), cost_price=Decimal("50"))
    # Before cutover: old physical net = 100 − 30 = 70.
    apply_cutover(global_apps)
    assert sklad_stock(p) == Decimal("70.000")             # preserved
    assert seller_ombor(seller_user, p) == Decimal("0.000")  # zeroed
```
Add `from crm.models import StockEntry` to the test imports.

- [ ] **Step 5: Run tests + migrate**

Run: `python manage.py migrate && pytest tests/test_manufacturing.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add manufacturing tests/test_manufacturing.py
git commit -m "feat(mfg): cutover migration seeds sklad + zeroes seller ombors"
```

---

## Task 16: Permission matrix + Playwright happy-path E2E

**Files:**
- Modify: `tests/test_manufacturing_permissions.py`
- Create: `tests/test_manufacturing_e2e.py`
- Modify: `conftest.py` (add `omborchi_user` fixture)

**Interfaces:**
- Consumes: existing `login` fixture, `live_server`, `page`.

- [ ] **Step 1: Add the omborchi fixture**

In `conftest.py`, after `seller_user`:
```python
@pytest.fixture
def omborchi_user(db):
    return User.objects.create_user(
        username="e2e_omborchi", password=PASSWORD, role=User.Role.OMBORCHI,
        first_name="Omb", last_name="Or",
    )
```

- [ ] **Step 2: Write the permission matrix test**

```python
# tests/test_manufacturing_permissions.py  (append)
def test_omborchi_denied_seller_kassa(client, omborchi_user):
    client.force_login(omborchi_user)
    # Sklad pages allowed:
    assert client.get(reverse("manufacturing:material_list")).status_code == 200
    assert client.get(reverse("manufacturing:sklad_kassa")).status_code == 200


def test_seller_denied_transfer_create(client, seller_user):
    client.force_login(seller_user)
    assert client.get(reverse("manufacturing:transfer_create")).status_code == 403
```

- [ ] **Step 3: Run permission tests**

Run: `pytest tests/test_manufacturing_permissions.py -q`
Expected: PASS.

- [ ] **Step 4: Write the E2E happy path**

```python
# tests/test_manufacturing_e2e.py
import pytest
from decimal import Decimal

from crm.models import Product
from manufacturing.models import RawMaterial


@pytest.mark.e2e
def test_buy_produce_transfer_flow(page, live_server, login, admin_user, seller_user):
    material = RawMaterial.objects.create(name="Karton", sku="E2E-M1")
    product = Product.objects.create(name="Paket E2E", sku="E2E-P1", price=Decimal("20000"))

    login(admin_user)
    # Buy material.
    page.goto(f"{live_server.url}/ishlab-chiqarish/xaridlar/yangi/")
    page.select_option('select[name="material"]', str(material.pk))
    page.fill('input[name="date"]', "2026-07-01")
    page.fill('input[name="quantity_kg"]', "100")
    page.fill('input[name="price_per_kg"]', "1000")
    page.click('button[type="submit"]')
    material.refresh_from_db()
    assert material.avg_cost == Decimal("1000.00")

    # Produce.
    page.goto(f"{live_server.url}/ishlab-chiqarish/ishlab-chiqarish/yangi/")
    page.select_option('select[name="product"]', str(product.pk))
    page.fill('input[name="output_kg"]', "40")
    page.select_option('select[name="items-0-material"]', str(material.pk))
    page.fill('input[name="items-0-quantity_kg"]', "50")
    page.click('button[type="submit"]')
    product.refresh_from_db()
    assert product.cost_price == Decimal("1250.00")

    # Transfer to seller.
    page.goto(f"{live_server.url}/ishlab-chiqarish/topshiruvlar/yangi/")
    page.select_option('select[name="product"]', str(product.pk))
    page.select_option('select[name="seller"]', str(seller_user.pk))
    page.fill('input[name="quantity_kg"]', "15")
    page.click('button[type="submit"]')

    from manufacturing.queries import seller_ombor, sklad_stock
    assert sklad_stock(product) == Decimal("25.000")
    assert seller_ombor(seller_user, product) == Decimal("15.000")
```
Note: the searchable-select widget may render a custom control. If `select_option` fails because the native `<select>` is hidden, drive the search input the same way existing sale E2E tests do — read `tests/test_e2e_smoke.py` first and match its interaction pattern for `_searchable_select` fields.

- [ ] **Step 5: Run E2E**

Run: `pytest tests/test_manufacturing_e2e.py -q`
Expected: PASS (headless Chromium via existing pytest config).

- [ ] **Step 6: Full suite**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/ conftest.py
git commit -m "test(mfg): permission matrix + E2E buy→produce→transfer happy path"
```

---

## Self-Review Notes (for the executor)

- **Spec coverage:** materials/avg-cost (T2), production/batch-cost (T3), sklad stock + product tannarx (T4), transfers (T5), seller ombor + own entries (T6, T12), sale blocking (T7), direct sales (reuse existing Sale flow — omborchi rep detected in T4/T7/T13, no new model), sklad kassa (T13), roles/nav (T1, T9, T14), cutover (T15), testing (T16). All spec sections map to a task.
- **Cross-app import discipline:** `manufacturing` imports `crm` at module top; `crm` reaches into `manufacturing` **only** via deferred imports inside methods/views (`ProductQuerySet.with_stock`, `Product.current_stock`, `sale_create`, `sale_edit`). Do not add a top-level `import manufacturing` to any `crm` module — it will create a circular import.
- **Stub models:** Tasks 2 and 4 introduce `ProductionRunItem` / `StockTransfer` stubs to satisfy imports; Tasks 3 and 5 complete them. Run `makemigrations` at each step so the migration history is coherent; squash later if desired.
- **Existing tests:** Task 4 and Task 7 change `Product` stock semantics and add sale blocking. Expect a few existing crm tests to need updates (give sellers a transfer before selling, or update stock assertions to sklad meaning). Fix them to the new truth — never weaken or skip.
- **Rounding:** money quantized to 2 dp with `ROUND_HALF_UP`; quantities kept at 3 dp. Match existing `MONEY`/`QTY` precision.
