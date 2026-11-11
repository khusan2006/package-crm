"""Put sales typed before the Yetkazish board tracked them onto their day's board.

Since the update, every sale typed in Sotuvlar lands on its day's Yetkazish board
(views._track_sale_delivery) and Sotuvlar shows its delivery status. A sale typed
before that has no stop, and its «Yetkazish» column reads «—». This gives each such
sale in the chosen days its stop, exactly as if it were typed now: onto the client's
open stop that day if there is one, else a new one with no method picked — so it
waits in «Buyurtmalar» for how it leaves. Opening balances are left alone.

    manage.py track_sales_delivery                    # today and later
    manage.py track_sales_delivery --dan 2026-11-09   # from that day on
    manage.py track_sales_delivery --dan 2026-11-09 --gacha 2026-11-10 --dry-run

Safe to run again: a sale already on the board is skipped.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef
from django.utils import timezone

from crm.models import LoadItem, LoadLine, Sale


class Command(BaseCommand):
    help = "Yetkazishga tushmagan (yangilanishdan oldingi) sotuvlarni o'z kunining yetkazishiga qo'shadi."

    def add_arguments(self, parser):
        parser.add_argument("--dan", help="Shu kundan (YYYY-MM-DD); bo'lmasa bugundan.")
        parser.add_argument("--gacha", help="Shu kungacha (YYYY-MM-DD); bo'lmasa oxirigacha.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Hech narsa yozmaydi, faqat nechta qo'shilishini aytadi.")

    def handle(self, *args, dan=None, gacha=None, dry_run=False, **options):
        from crm.views import _track_sale_delivery

        try:
            start = date.fromisoformat(dan) if dan else timezone.localdate()
            end = date.fromisoformat(gacha) if gacha else None
        except ValueError as err:
            raise CommandError(f"Sana noto'g'ri: {err}") from err
        sales = (Sale.objects.filter(is_opening=False, date__gte=start)
                 .exclude(Exists(LoadItem.objects.filter(sale=OuterRef("pk"))))
                 .exclude(Exists(LoadLine.objects.filter(sale=OuterRef("pk"))))
                 .select_related("client", "entered_by", "sales_rep")
                 .order_by("date", "created_at", "pk"))
        if end:
            sales = sales.filter(date__lte=end)
        sales = list(sales)
        days = sorted({s.date for s in sales})
        span = f"{days[0]:%d.%m.%Y} – {days[-1]:%d.%m.%Y}" if days else "—"
        if dry_run:
            self.stdout.write(f"Qo'shiladi: {len(sales)} ta sotuv ({span}). Hech narsa yozilmadi.")
            return
        done = 0
        for sale in sales:
            if _track_sale_delivery(sale, sale.entered_by or sale.sales_rep) is not None:
                done += 1
        self.stdout.write(self.style.SUCCESS(
            f"Yetkazishga qo'shildi: {done} ta sotuv ({span})."))
