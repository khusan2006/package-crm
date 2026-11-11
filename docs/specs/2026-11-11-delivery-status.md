# Yetkazish: how each stop leaves and where it stands — spec

Status: built 2026-11-11, not yet deployed. Builds on
[2026-11-08-loads-to-sales.md](2026-11-08-loads-to-sales.md).

## What the client asked for (Telegram, 2026-11-11)

1. Every load of the day visible in one place, with statuses like in their logistics
   CRM: o'zi olib ketdi, Yandexdan chiqarildi, yetkazib beriladi, ortilyapti,
   yetkazildi. The route is built only from the ones marked for delivery.
2. An order whose goods aren't in the warehouse yet stays as an order.
3. Sales made in Sotuvlar today are tracked there too.

## Decisions

| Question | Answer |
|---|---|
| Where statuses are set | One board per day on the Yetkazish page, across all loads of that day. |
| Shape | Two fields per stop: **Qanday** (yetkazib beriladi / o'zi olib ketadi / Yandex) and **Holati** (kutilmoqda / buyurtma / ortilmoqda / jo'natildi / yetkazildi). Shown in the client's words: a done pickup reads «Olib ketdi», a sent Yandex one «Yandexdan jo'natildi». |
| What goes on the route | «Yetkazib beriladi» stops that are not «Buyurtma». Pickup, Yandex, unset and buyurtma stops stay off the route and out of the trucks' kg. |
| Who changes them | Admins/managers, or the client's own seller. Still open after ✓. |
| «Jo'natish» | Marks the load's truck stops that are kutilmoqda/ortilmoqda as jo'natildi; «Bekor qilish» puts them back to ortilmoqda. |
| Existing stops | Method «yetkazib beriladi»; those already turned into a sale → «yetkazildi». |
| Stops added on a Yuk page | «Yetkazib beriladi», kutilmoqda. |
| Sale typed in Sotuvlar | Dated today or later, not an opening balance → put on that day's board, **with no method** (user's choice). If the client has an open stop that day, the sale becomes its sale and its lines are replaced by the sale's lines (method kept). Else the day's first load without the client, or a new load «Sotuvlardan». |
| Sotuvlar list | A «Yetkazish» column with the status, linking to that day's board. |
| Buyurtma carried to the next day | Not automatic. «Boshqa kunga» moves it as before. |

## To confirm with the client

- Is ✓ (sale) pressed before the truck leaves or on delivery? Today it can be either.
- Should the driver set «yetkazildi» from the route link on their phone?
- Does Yandex need its cost recorded?
- Deleting a sale that came from Sotuvlar leaves its stop on the board, unlocked
  (as with ✓ sales). Should it disappear instead?
