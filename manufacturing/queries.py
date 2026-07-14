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


def company_net(product):
    """Company-wide net stock (kg) of a finished product = sklad + every seller's
    ombor. Because transfers out of the sklad exactly match transfers into sellers,
    they cancel, leaving: manual entries + production + seller own-entries − all
    sales + all restocked returns. A NEGATIVE value is make-to-order demand — goods
    ordered from customers that the factory has not produced yet."""
    from crm.models import ITEM_WEIGHT_KG, RETURN_WEIGHT_KG, Return, SaleItem, StockEntry

    from .models import SellerStockEntry

    entries = StockEntry.objects.filter(product=product).aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")
    produced = product.production_runs.aggregate(s=Sum("output_kg"))["s"] or Decimal("0")
    own = SellerStockEntry.objects.filter(product=product).aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")
    sold = SaleItem.objects.filter(product=product).aggregate(s=Sum(ITEM_WEIGHT_KG))["s"] or Decimal("0")
    returned = (
        Return.objects.filter(product=product, restock=True)
        .aggregate(s=Sum(RETURN_WEIGHT_KG))["s"] or Decimal("0")
    )
    return entries + produced + own - sold + returned


def annotate_company_net(product_qs):
    """Annotate products with `company_net` (sklad + all seller ombors) in one query.
    Filter `company_net__lt=0` for the make-to-order production backlog."""
    from crm.models import (
        ITEM_WEIGHT_KG, QTY, RETURN_WEIGHT_KG, ZERO_QTY, Return, SaleItem, StockEntry,
    )

    from .models import SellerStockEntry

    def _sub(model, expr, **extra):
        return Subquery(
            model.objects.filter(product=OuterRef("pk"), **extra)
            .values("product").annotate(s=Sum(expr)).values("s"),
            output_field=QTY,
        )

    entries = _sub(StockEntry, "quantity_kg")
    produced = _sub(ProductionRun, "output_kg")
    own = _sub(SellerStockEntry, "quantity_kg")
    sold = _sub(SaleItem, ITEM_WEIGHT_KG)
    ret = _sub(Return, RETURN_WEIGHT_KG, restock=True)
    return product_qs.annotate(
        company_net=(
            Coalesce(entries, ZERO_QTY) + Coalesce(produced, ZERO_QTY)
            + Coalesce(own, ZERO_QTY) - Coalesce(sold, ZERO_QTY) + Coalesce(ret, ZERO_QTY)
        )
    )


def ombor_shortfall_warnings(user, sale):
    """After a make-to-order sale is saved, produce a Uzbek warning for each product
    whose actor-ombor (seller's ombor, or sklad for an omborchi) is now negative —
    i.e. sold beyond stock and therefore needs manufacturing. Non-blocking."""
    msgs = []
    seen = set()
    for item in sale.items.select_related("product"):
        if item.product_id in seen:
            continue
        seen.add(item.product_id)
        available = available_for_sale(user, item.product)
        if available < 0:
            msgs.append(
                f"«{item.product.name}»: ombor manfiy ({available:.3f} kg) — "
                f"ishlab chiqarish kerak."
            )
    return msgs
