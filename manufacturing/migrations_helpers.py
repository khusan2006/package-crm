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
                product=product, restock=True, sale__sales_rep=rep)),
            Decimal("0"),
        )
        net = sold - restocked
        if net != 0:
            SellerStockEntry.objects.create(
                seller=rep, product=product, quantity_kg=net, note=NOTE, created_by=system_user,
            )
