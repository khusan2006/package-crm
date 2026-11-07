from django.db import migrations

# The fixed-date national holidays, seeded for a span of years so the davomad sheet
# has something to measure against the first time it is opened.
#
# Only the ones that sit on the same date every year. The two hayits move against the
# calendar and are added by hand — a seeded list that looked complete while quietly
# going stale would be worse than one the supervisor knows to finish.
#
# The span is fixed rather than read off the clock: a migration runs once, at whatever
# moment somebody happens to deploy, and a range built from `today` would seed a
# different set of years on every machine. Beyond this span the Bayramlar page has a
# button that fills in a year on demand.
FIRST_YEAR, LAST_YEAR = 2025, 2030

FIXED = [
    (1, 1, "Yangi yil"),
    (3, 8, "Xotin-qizlar kuni"),
    (3, 21, "Navro'z"),
    (5, 9, "Xotira va qadrlash kuni"),
    (9, 1, "Mustaqillik kuni"),
    (10, 1, "O'qituvchi va murabbiylar kuni"),
    (12, 8, "Konstitutsiya kuni"),
]


def seed(apps, schema_editor):
    from datetime import date

    Holiday = apps.get_model("crm", "Holiday")
    rows = [
        Holiday(date=date(year, month, day), name=name, rest=True)
        for year in range(FIRST_YEAR, LAST_YEAR + 1)
        for month, day, name in FIXED
    ]
    # `ignore_conflicts` so re-running on a database that already has some of these
    # dates leaves the existing rows exactly as they are — the supervisor may have
    # turned one into a working day, and a migration must not undo that decision.
    Holiday.objects.bulk_create(rows, ignore_conflicts=True)


def unseed(apps, schema_editor):
    """Only the untouched ones go back.

    A holiday somebody renamed or turned into a working day is a decision that was
    made here, not seed data, so it survives a reverse."""
    from datetime import date

    Holiday = apps.get_model("crm", "Holiday")
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for month, day, name in FIXED:
            Holiday.objects.filter(
                date=date(year, month, day), name=name, rest=True, note=""
            ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0045_hr_davomad"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
