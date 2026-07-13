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
