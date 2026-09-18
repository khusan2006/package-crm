"""Qarzdor mijoz kiritish — qarzlar ro'yxatining o'zidan mijoz + eski qarz.

Daftarda qolib ketgan qarzdorni kiritish uchun avval mijoz ochib, keyin boshqa
sahifada boshlang'ich qarz yozish kerak edi. Bu yerda ikkalasi bitta oynada.

Yoziladigan narsa — boshlang'ich qarz (`Sale.is_opening`): qatorlari yo'q chek,
shuning uchun savdo/foyda/sotilgan kg hisobotlariga tushmaydi, faqat qarz bo'lib
ko'rinadi.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.models import Client, Sale

pytestmark = pytest.mark.django_db

URL = "/debts/qarzdor-qoshish/"
TODAY = timezone.localdate()
OLD_DAY = TODAY - timedelta(days=90)


def _payload(**over):
    data = {
        "name": "ЭСКИ ҚАРЗДОР",
        "phone": "+998 90 123 45 67",
        "amount": "1500000",
        "date": OLD_DAY.isoformat(),
        "debt_days": "14",
    }
    data.update(over)
    return data


def test_sotuvchi_qarzdor_kiritadi(client, seller_user):
    """Sotuvchi kiritgan qarzdor o'ziniki bo'ladi va qarz ro'yxatiga tushadi."""
    client.force_login(seller_user)
    response = client.post(URL, _payload())
    assert response.status_code == 302

    mijoz = Client.objects.get(name="ЭСКИ ҚАРЗДОР")
    assert mijoz.owner == seller_user
    assert mijoz.phone == "+998 90 123 45 67"

    sale = Sale.objects.get(client=mijoz)
    assert sale.is_opening is True
    assert sale.opening_amount == Decimal("1500000")
    assert sale.sales_rep == seller_user
    assert sale.date == OLD_DAY
    assert sale.debt_term_days == 14
    # Muddat kiritilgan sanadan sanaladi — to'lov bo'lmagani uchun surilmaydi.
    assert sale.debt_deadline == OLD_DAY + timedelta(days=14)
    # Tovar yo'q: hisobotlarga tushmaydi, faqat qarz.
    assert sale.items.count() == 0
    assert sale.debt_remaining == Decimal("1500000")


def test_qarzlar_royxatida_korinadi(client, seller_user):
    """Kiritilgan qarzdor darhol qarzlar ro'yxatida chiqadi."""
    client.force_login(seller_user)
    client.post(URL, _payload())
    page = client.get(reverse("debt_list")).content.decode()
    assert "ЭСКИ ҚАРЗДОР" in page
    # Tugmaning o'zi ham shu sahifada turadi.
    assert "debts/qarzdor-qoshish" in page


def test_admin_masul_sotuvchini_tanlaydi(client, admin_user, seller_user):
    """Admin qarzdorni boshqa sotuvchi nomiga yozadi; sotuvchida bu maydon yo'q."""
    client.force_login(admin_user)
    assert b"id_owner" in client.get(URL).content

    client.post(URL, _payload(owner=str(seller_user.pk)))
    mijoz = Client.objects.get(name="ЭСКИ ҚАРЗДОР")
    assert mijoz.owner == seller_user
    assert Sale.objects.get(client=mijoz).sales_rep == seller_user

    client.force_login(seller_user)
    assert b"id_owner" not in client.get(URL).content


def test_bir_xil_nomli_qarzdor_ogohlantiradi(client, seller_user):
    """Bir xil nom ikki marta kiritilsa — to'xtatadi; katakcha bilan baribir yoziladi."""
    client.force_login(seller_user)
    client.post(URL, _payload())

    again = client.post(URL, _payload())
    assert again.status_code == 200  # forma xato bilan qaytadi
    assert "allaqachon bor" in again.content.decode()
    assert Client.objects.filter(name="ЭСКИ ҚАРЗДОР").count() == 1

    forced = client.post(URL, _payload(allow_duplicate="on"))
    assert forced.status_code == 302
    assert Client.objects.filter(name="ЭСКИ ҚАРЗДОР").count() == 2


def test_kelajak_sana_rad_etiladi(client, seller_user):
    """Qarz kelasi kundan boshlanmaydi — eski sana esa mumkin."""
    client.force_login(seller_user)
    response = client.post(URL, _payload(date=(TODAY + timedelta(days=1)).isoformat()))
    assert response.status_code == 200
    assert not Client.objects.filter(name="ЭСКИ ҚАРЗДОР").exists()


def test_bosh_muddat_default_boladi(client, seller_user):
    """Muddat bo'sh qolsa — standart muddat qo'yiladi."""
    from crm.models import DEFAULT_DEBT_DAYS

    client.force_login(seller_user)
    client.post(URL, _payload(debt_days=""))
    sale = Sale.objects.get(client__name="ЭСКИ ҚАРЗДОР")
    assert sale.debt_term_days == DEFAULT_DEBT_DAYS
    assert sale.debt_deadline == OLD_DAY + timedelta(days=DEFAULT_DEBT_DAYS)
