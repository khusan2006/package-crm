# Kassa: yopilgan kunda qoldiq — holat va davomi

Holat sanasi: 07.10.2026 kechqurun. Yozgan: Sarvar (Claude bilan). Ish tugamagan —
quyida nima qilingani, nima ochiq qolgani va qanday davom ettirish yozilgan.

## Qisqacha

| Nima | Holat |
|---|---|
| Sabab tahlili | Tugadi, prod ma'lumotida tasdiqlangan |
| Kod (ogohlantirishlar + kassa eslatmasi) | `stage`da, commit `7152904`. `main`da **yo'q** — prodga chiqmagan |
| Ma'lumot tuzatishi (`fix_kamola_kassa` 6-bosqich) | Kodi `stage`da. Prod bazasida **qo'llanmagan** |
| Brauzerda ko'rib chiqish | Qilinmagan — faqat testlar va prod nusxasida hisob tekshirilgan |

## Muammo

Mijoz savoli: «Kassani 0 qilib yopsam ham, eski kunga qaytsam pul paydo bo'lib qolyapti».

Bu hisob xatosi emas. Ikki narsa birga shunday ko'rinish beradi:

1. Kassa sahifasidagi «Kassadagi pul» — tanlangan kunning **oxiridagi** qoldiq
   (`_kassa_summary`, `crm/views.py`). Boshidan o'sha kungacha bo'lgan hamma kirim va
   chiqim yig'indisi.
2. Yozuvlar tizimga kiritilgan vaqti bo'yicha emas, ustiga qo'yilgan **sana** bo'yicha
   joylashadi. Sotuvchilar kunni odatda 1–3 kun kechikib, eski sana bilan kiritadi
   (Kamolaning 1236 ta yozuvidan 1144 tasi shunday).

Demak kun topshiruv bilan 0 ga yopilgandan keyin o'sha kunga yana to'lov kiritilsa, kunda
qoldiq paydo bo'ladi. Pul keyingi kunning topshiruviga qo'shib yuborilsa, jami to'g'ri
chiqadi, lekin eski kun qoldiq bilan qolaveradi.

### Aniq misol: Kamola, 30.09.2026

Hammasi 03.10 da kiritilgan:

| Vaqt | Yozuv | 30.09 qoldig'i |
|---|---|---|
| 10:30 | 30.09 topshiruvi #89 — 114 146 832 | 0 |
| 10:38 | To'lov #4833, МАХМУД АКА ҚОЙЛУ, naqd, sana 30.09 — 5 411 700 | 5 411 700 |
| 10:39 | To'lov #4834 va #4835, ШУХРАТ АКА ПИСКЕНТ, naqd, sana 30.09 — 756 750 + 2 043 250 | 8 211 700 |
| 10:49 | 01.10 topshiruvi #90 — 32 130 320 | 8 211 700 |

01.10 topshiruvi o'sha kunning o'z sof kirimidan (23 918 620) aynan 8 211 700 ga ko'p.
Shuning uchun 01.10 dan boshlab kassa 0, 30.09 ni ochsa 8 211 700 turadi.

### Ikkinchi holat: 3 200 so'm (24.07 – 29.09)

Bu boshqa turdagi xato — pul umuman bo'lmagan.

- ДОНИЁР КЕЛЕС ФЛАКОН ЦЕХ (mijoz #1434) ning 18.08 dagi qaytarishidan 3 200 so'm krediti
  qolgan. Uning puli 25.07 dagi to'lov ichida kelgan va o'shanda topshirilgan.
- 02.10 08:37 da Kamola bu avansni o'chirgan, 08:38 da uni «naqd avans» qilib,
  **24.07 sanasi** bilan qayta kiritgan (to'lov #4811).
- Natijada kassaga ikkinchi marta kirim bo'lib, 24.07 dan 29.09 gacha har kuni 3 200
  ko'ringan, 03.10 da esa 30.09 topshiruviga (#89) qo'shilib ketgan.

## Kodda nima qilindi (commit `7152904`)

Uch joyda to'xtatiladi. Hech narsa avtomatik yozilmaydi — topshiruvni har doim sotuvchi
o'zi saqlaydi.

| Qayerda | Nima qiladi | Kod |
|---|---|---|
| To'lov formalari (qarz to'lovi, chek bo'yicha to'lov, avans) | Sana yopilgan kunga tushsa bir marta ogohlantiradi, o'sha kunda qancha topshirilmagan pul qolishini aytadi. Ikkinchi «Saqlash» o'tadi | `_closed_day_warning`, `DebtPaymentForm.clean` — `crm/forms.py` |
| Kassa sahifasi | Tepada eslatma: «30.09.2026 kunida 8 211 700 so'm topshirilmagan». Tugma topshiruv formasini o'sha kun sanasi va summasi bilan ochadi | `unremitted_closed_days` — `crm/models.py`; `_closed_day_notices` — `crm/views.py`; `templates/crm/kassa.html` |
| Topshiruv formasi | Yangi topshiruv oldingi yopilgan kundan qolgan pulni ham olib ketayotgan bo'lsa ogohlantiradi | `_older_leftover_warning` — `crm/forms.py` |

Qoidalar:

- **«Yopilgan kun»** — o'tgan kun, unda topshiruv (yoki foyda topshiruvi) bor.
- **Forma ogohlantirmaydi**, agar: kun bugungi bo'lsa; kunda hali topshirganidan ko'p
  kirim tursa (sotuvchi kunni to'ldirayotgan bo'ladi); avans «kassaga kirmasin» deb
  belgilangan bo'lsa; yoki o'chirilgan to'lov shunchaki qayta kiritilayotgan bo'lsa.
- **Kassa eslatmasi** bir vaqtda sotuvchining eng eski kunini ko'rsatadi, qolganlarini
  sanaydi. Keyingi sana bilan allaqachon topshirib yuborilgan pul eslatilmaydi —
  kassada topshiradigan narsa yo'q.

Testlar: `ClosedDayIncomeTests` (21 ta), jami 631 ta test o'tdi.

```bash
python manage.py test crm accounts --settings=config.settings_test
```

`pytest` ishlamasligi mumkin: `pytest.ini` dagi `--browser chromium` uchun
pytest-playwright o'rnatilgan bo'lishi kerak.

## Ma'lumot tuzatishi — `fix_kamola_kassa`, 6-bosqich

Versiya: `2026-10-07-closed-1`. Fayl: `crm/management/commands/fix_kamola_kassa.py`.
Bu buyruq deploy paytida **ishlamaydi** (`railway.json` dagi `startCommand`da yo'q),
qo'lda ishga tushiriladi.

Nima o'zgartiradi:

| Yozuv | Oldin | Keyin |
|---|---|---|
| Topshiruv #89 (30.09) | 114 146 832 | 122 355 332 |
| Topshiruv #90 (01.10) | 32 130 320 | 23 918 620 |
| To'lov #4811 (3 200, sana 24.07) | kassaga kirim | kassadan tashqari (`is_opening=True`), avans mijozda qoladi |

Hisob: #89 ga 8 211 700 qo'shiladi va 3 200 ayriladi; #90 dan 8 211 700 ayriladi.

Oqibatlari:

- 22.08 – 29.09 va 30.09 kunlari 0 ko'rsatadi.
- Kamolaning hozirgi kassasi o'zgarmaydi (0).
- Mijoz #1434 ning 3 200 so'm avansi joyida qoladi.
- **Ishlab chiqarish qarzi 3 200 so'mga oshadi** (07.10 nusxasida 1 146 689 844 →
  1 146 693 044). Sababi: 30.09 topshiruvida bo'lmagan pul «topshirildi» deb yozilgan edi.

Buyruq yozishdan oldin uchala yozuv aynan «Oldin» ustunidagi holatda ekanini tekshiradi.
Birortasi o'zgargan bo'lsa, hech narsa yozmaydi va farqni chiqaradi. Ikkinchi marta
ishga tushirilsa hech narsa qilmaydi.

Qayerda qo'llangan: faqat Sarvarning lokal bazasida (prodning 07.10.2026 23:20 dagi
nusxasi). **Prodda qo'llanmagan.**

### Prodda qo'llash tartibi

Avval egasidan (Sarvar orqali) ruxsat olinadi — pastdagi 1-savolga qarang.

1. Yangi prod dump oling. Ulanish satri: `railway variables --service Postgres --json`
   ichidagi `DATABASE_PUBLIC_URL`. `pg_dump` 18-versiya bo'lishi shart (masalan
   `postgres:18-alpine` konteyneri ichidan). Dump `backups/` ga yoziladi, u gitignore'da.
2. `stage` shoxida turing (commit `7152904` yoki undan keyingisi).
3. `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`
   o'zgaruvchilarini shu ulanish satridan olib, faqat shu terminal uchun o'rnating.
   `.env` ga yozmang.
4. Quruq yurgizish:

   ```bash
   python manage.py fix_kamola_kassa --dry-run
   ```

   Chiqishda 1–5-bosqichlar «allaqachon qo'llangan» bo'lishi, 6-bosqichda yuqoridagi
   uchta o'zgarish va «ishlab chiqarish qarzi +3 200 so'm» ko'rinishi kerak.
5. Qo'llash:

   ```bash
   python manage.py fix_kamola_kassa
   ```

6. Tekshirish: kassa sahifasida Kamola uchun 30.09 va 15.09 kunlarini oching —
   «Kassadagi pul» 0 bo'lishi kerak. Terminaldagi prod o'zgaruvchilarini tozalang.

## Ochiq savollar

1. **Prodga 6-bosqichni qo'llaymizmi?** Egasi hali javob bermagan. Alohida so'rash kerak:
   ishlab chiqarish qarzining 3 200 so'mga oshishiga rozimi. Rozi bo'lmasa muqobil yo'l —
   3 200 ni 24.07 sanali alohida topshiruv qilib yozish: qarz o'zgarmaydi, lekin iyulda
   bo'lmagan kirim va topshiruv qoladi. Bu variantning kodi yozilmagan.
2. **Kodni prodga chiqarish:** `stage` → `main` (`main`ga push avtomatik deploy qiladi).
   Undan oldin eslatma va ogohlantirishlarni brauzerda ko'rib chiqish kerak.
3. **Avgustdan qolgan eski kunlar** hali qoldiq ko'rsatadi. Avgustda egasi bilan
   kelishilgan holat bo'lgani uchun tegilmagan:

   | Kun | Kassadagi pul | Nima |
   |---|---|---|
   | 23.07 – 25.07 | 1 981 150 | CRM boshlanganda qo'lda bo'lgan naqd |
   | 26.07 – 01.08 | 1 150 | shuning qoldig'i |
   | 07.08 – 20.08 | ≈ 14,0 mln | ХОЖАКБАР ФЛАКОН ЦЕХ o'tkazmalari: 20.08 da 07.08 sanasi bilan kiritilgan, 21.08 da topshirilgan |
   | 21.08 | 39 200 | ШОКИР АЛГОРИТИМ ortiqcha to'lovi |

   Egasi xohlasa, xuddi shu usulda (topshiruv sanasini pul tegishli kunga ko'chirib)
   nolga tushirish mumkin.
4. **Umidada 14 700 so'm topshirilmagan:** 03.10 topshiruvi kun qoldig'idan 5 200 ga,
   05.10 topshiruvi yana 9 500 ga kam yozilgan. Yangi kassa eslatmasi buni ko'rsatadi.
   Admin ko'rinishida kassa shu sabab 0 emas. Bu yerda eski sana aralashmagan.

## Ma'lum cheklovlar

- **Tahrirlash formalari ogohlantirmaydi.** Mavjud to'lovning summasi yoki sanasi
  o'zgartirilsa (`PaymentEditForm`, `AdvanceEditForm`), forma to'xtatmaydi. Kassa
  eslatmasi baribir chiqadi.
- **Eslatma summani kunlarga taxminan bo'ladi.** U «shu kundan beri kassa eng past
  tushgan qoldiq»ni oladi. Agar eski qoldiq biror kun yopilishida topshirilib, keyin
  aynan o'sha kunga kech kirim tushsa, summaning bir qismi oldingi kunga yozilib ko'rinishi
  mumkin. Jami har doim to'g'ri; tugmalar ketma-ket bosilsa hamma kun nolga tushadi.
- **Eski 30.09 uchun eslatma chiqmaydi** (prodda, tuzatish qo'llanmaguncha ham): u pul
  01.10 sanasi bilan allaqachon topshirilgan. Uni faqat 6-bosqich nolga tushiradi.

## Lokalda ko'rib chiqish

Prod nusxasi yuklangan lokal bazada:

- Umida yoki admin bilan kassa sahifasini oching — «03.10.2026 kunida 5 200 so'm
  topshirilmagan» eslatmasi va «03.10 sanasi bilan topshirish» tugmasi chiqishi kerak.
- Kamola bilan: eslatma yo'q (kassasi 0).
- Ogohlantirishni ko'rish: Kamolaning biror mijoziga to'lov kiriting va sanani topshiruvi
  bor o'tgan kunga qo'ying — forma birinchi «Saqlash»da to'xtatishi kerak.
