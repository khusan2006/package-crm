"""One-time removal of the imported Excel ledger data.

Empties every crm_* table (clients, products, sales, returns, payments,
expenses, production records) — but ONLY while the import marker (an IMP-*
product from `import_excel`) is present. After the clear, or once real records
replace the import, the command is a no-op, so it is safe to keep in the
deploy start command: it cannot wipe data entered by users afterwards.

Uses raw SQL on purpose and must run BEFORE `migrate` in the start command:
migration 0024 makes Return.sale_item non-null, which cannot be applied while
imported returns (which have no sale_item) exist — the ORM models would not
match the pre-0024 schema at that point, but TRUNCATE works on any schema.

User accounts and django_* tables are never touched.
"""

from django.core.management.base import BaseCommand
from django.db import connection, transaction


class Command(BaseCommand):
    help = (
        "Remove the imported Excel ledger (one-time: runs only while an "
        "IMP-* product exists, otherwise does nothing)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be deleted, then roll everything back.",
        )

    def handle(self, *args, **opts):
        tables = connection.introspection.table_names()
        crm_tables = sorted(t for t in tables if t.startswith("crm_"))
        if "crm_product" not in crm_tables:
            self.stdout.write("crm_product jadvali yo'q — o'tkazib yuborildi.")
            return

        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM crm_product WHERE sku LIKE 'IMP-%')"
                )
                if not cur.fetchone()[0]:
                    self.stdout.write(
                        "Import ma'lumotlari yo'q — hech narsa o'chirilmadi.")
                    return
                for t in crm_tables:
                    cur.execute(f"SELECT count(*) FROM {t}")
                    n = cur.fetchone()[0]
                    if n:
                        self.stdout.write(f"  o'chiriladi: {t} x {n}")
                quoted = ", ".join(f'"{t}"' for t in crm_tables)
                cur.execute(f"TRUNCATE {quoted} CASCADE")
            if opts["dry_run"]:
                transaction.set_rollback(True)

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — hech narsa saqlanmadi."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "Import ma'lumotlari tozalandi (foydalanuvchilar saqlanib qoldi)."))
