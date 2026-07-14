import pytest
from django.apps import apps
from accounts.models import User


def test_omborchi_role_exists():
    assert User.Role.OMBORCHI == "omborchi"
    assert ("omborchi", "Omborchi") in User.Role.choices


def test_manufacturing_app_installed():
    assert apps.is_installed("manufacturing")


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


def test_transfer_audit_event_label(admin_user):
    from crm.models import AuditLog
    log = AuditLog.record(
        admin_user, AuditLog.Action.TRANSFER, "Omborga topshiruv", 1, "test"
    )
    # Must NOT fall through to the "sale's seller reassigned" label.
    assert log.event["label"] == "Sotuvchiga topshirildi"


def test_material_audit_event_label(admin_user):
    from crm.models import AuditLog
    log = AuditLog.record(admin_user, AuditLog.Action.CREATE, "Xomashyo", 1, "Karton")
    assert log.event["label"] == "“Xomashyo” qo'shildi"


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


def test_cutover_zeroes_seller_and_preserves_sklad():
    """The cutover logic, applied to pre-existing sales, leaves sklad at the real
    net stock and each seller ombor at zero. Exercised via the migration helper."""
    from manufacturing.migrations_helpers import apply_cutover  # extracted for testability
    # (See the 0006 migration — it calls this same function.)
    assert callable(apply_cutover)


from crm.models import StockEntry


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
