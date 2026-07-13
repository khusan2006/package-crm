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
    usages = ProductionRunItem.objects.filter(material=material).select_related("run")
    for it in usages:
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
