# TZ — Kassa qayta qurish: sotuvchi ↔ ishlab chiqarish qarzi va ko'p kassa

**Sana:** 2026-07-11
**Holati:** Reja / muhokama (kod yozilmagan)
**Bog'liq:** hozirgi `crm/models.py` (Sale, SaleItem, Payment, Expense), `kassa_view` (`crm/views.py:1502`)

---

## 1. Firma jarayoni (biznes oqimi)

Uch bo'g'in: **Ishlab chiqarish → Sotuvchi → Mijoz**. Ikkita alohida qarz aylanadi.

1. **Ishlab chiqarish** mahsulotni tayyorlaydi va **tannarx** belgilaydi (masalan 10 000).
2. **Sotuvchi** umumiy ombordan tovarni oladi, ustiga foyda qo'yib **mijozga** sotadi
   (10 000 → 12 000), ko'pincha **qarzga**.
   - Qarzga sotilgani uchun **o'sha kuni kassaga naqd kirim tushmaydi**.
   - Sotilgan tovarning **tannarx qismi (10 000)** — sotuvchining **ishlab
     chiqarishga qarzi** bo'lib yoziladi.
3. **Mijoz** keyinroq to'laydi → sotuvchining kassasiga naqd **kirim**.
4. **Sotuvchi** yig'ilgan naqd pulni **kunlik ishlab chiqarishga topshiradi** →
   uning ishlab chiqarishga qarzi kamayadi.
5. **Chiqim:** sotuvchi/ishchi rasxodlari (benzin, ovqat, oylik...) puldan chiqadi,
   lekin ishlab chiqarish qarzini **kamaytirmaydi** — shu sabab ba'zan sotuvchi
   hammasini topshira olmaydi.

**Kunlik aylanma:** bir kun ichida mijozlar eski qarzni to'laydi, sotuvchi yangi
tovarni qarzga beradi, sotuvchi pulni ishlab chiqarishga topshiradi — takrorlanadi.

---

## 2. Asosiy qarorlar (kelishilgan)

| Savol | Qaror |
|---|---|
| Sotuvchi qachon ishlab chiqarishga qarzdor bo'ladi? | **Mijozga sotganda** (sotilmagan tovar qarz emas) |
| Ombor | **Umumiy ombor** — tovar bitta omborda, qarz sotuvchi bo'yicha ajraladi |
| Kassa turi | **Ko'p kassa + umumiy** (B): har sotuvchi o'z kassasi, ustida umumiy |
| Ishlab chiqarish moduli (material → tannarx) | **Hozir emas**, keyingi bosqich |
| Oylik (HR) | Bazaviy: fikslangan; ba'zi ishchilar soatbay — keyin aniqlanadi |

---

## 3. Har sotuvchi bo'yicha uchta ko'rsatkich

| Ko'rsatkich | Formula |
|---|---|
| **Kassadagi puli (naqd)** | mijoz to'lovlari − chiqimlar − ishlab chiqarishga topshirilgan |
| **Ishlab chiqarishga qarzi** | sotilgan tannarx − ishlab chiqarishga topshirilgan |
| **Sof foydasi** | (sotish − tannarx) − chiqim |

**Muhim:** `SaleItem.cost_price` (tannarx) allaqachon saqlanadi → "sotilgan tannarx"
hozirgi bazadan hisoblanadi. Ya'ni yagona yetishmayotgan narsa — **"ishlab
chiqarishga topshirish"** amali.

---

## 4. Ma'lumot modeli

### Mavjud (o'zgarmaydi yoki minimal)
- `Sale` / `SaleItem` — mijozga sotuv, tannarx + narx bilan. **Bor.**
- `Payment` — mijoz to'lovi (kirim), usul/valyuta/komissiya bilan. **Bor.**
- `Expense` — chiqim (benzin, oylik, ijara...), `created_by` bilan. **Bor.**
- `StockEntry` — umumiy ombor kirimi. **Bor, o'zgarmaydi.**

### Yangi (1-bosqich)
- **ProductionRemittance** ("Ishlab chiqarishga topshirish") — yangi model:
  - `seller` (kim topshirdi), `date`, `amount` (so'm), `method`, `note`, `created_by`.
  - Effekt: sotuvchi kassasidan chiqim + ishlab chiqarishga qarzi kamayadi.
  - *Muqobil:* `Expense`ga yangi turkum qo'shish — lekin bu "toza chiqim" emas,
    qarz to'lovi, shuning uchun alohida model tozaroq. (Qaror kerak — 8-bo'lim.)

### Yangi (2-bosqich — ko'p kassa)
- **Cashbox** (Kassa) — obyekt: har sotuvchi/nuqta/ishlab chiqarish uchun.
- Har `Payment` / `Expense` / `ProductionRemittance` bitta kassaga bog'lanadi
  (mavjud yozuvlarni default kassaga ko'chirish — migratsiya).
- **CashTransfer** — kassadan kassaga o'tkazma (sotuvchi kassa → ishlab chiqarish kassa).

---

## 5. Kassa ko'rinishi (UI)

**Sotuvchi nazorati (kunlik):** har sotuvchi qatori — kassadagi puli, ishlab
chiqarishga qarzi, foyda, chiqim. Kunlik boshlang'ich/yakuniy qoldiq bilan.

**Amallar:**
- Mijozdan to'lov qabul qilish (bor).
- Chiqim yozish (bor).
- **Ishlab chiqarishga pul topshirish (yangi).**

**Rollar:** sotuvchi faqat o'z kassasini ko'radi; admin/menejer umumiy + har kimni
(hozirgi `can_see_all_records` mantig'i saqlanadi).

---

## 6. Bosqichlar

- **1-bosqich (kichik, hozir):** "Ishlab chiqarishga topshirish" amali + har
  sotuvchi bo'yicha kassa/qarz/foyda ko'rinishi. Hozirgi bazaga mos, migratsiyasiz.
- **2-bosqich:** to'liq ko'p kassa (Cashbox obyektlari + o'tkazmalar + migratsiya).
- **Keyingi modullar:** Telegram bot (diler + mijoz self-service), HR (oylik),
  Ishlab chiqarish (material → tannarx avtomatik). Qarang: memory `roadmap-modules`.

---

## 7. Diqqat qilinadigan joylar (bog'liqliklar)
- Chiqim puldan chiqadi, qarzni kamaytirmaydi — ikkovi alohida hisoblanadi.
- Sotuvchi/ishchi chiqimlari sotuvchi kassasi/foydasiga bog'lanishi shart.
- Valyuta: hozirgi so'm/dollar chekmecha mantig'i saqlanadi.
- Pul kodiga tegiladi — har o'zgarish testlardan o'tishi shart (pytest + Playwright).

---

## 8. Ochiq savollar (keyin hal qilinadi)
1. "Ishlab chiqarishga topshirish" — alohida model bo'lsinmi yoki `Expense` turkumimi?
2. Kunlik kassa qoldig'i: boshlang'ich qoldiq har kuni oldingi kundan ko'chirilsinmi
   (running balance) yoki faqat davr oralig'i ko'rsatilsinmi?
3. Sotuvchi bir kunda bir necha marta topshirsa — har biri alohida yozuvmi?
4. Ishlab chiqarish "kassasi" 1-bosqichda kerakmi yoki faqat 2-bosqichda?
