# Delivery route planning — design

> **Changed 2026-11-08 — loads (yuk), not sales.** Delivery is now built from
> loads put together by hand: an admin or manager creates a load (date, optional
> truck, note), adds clients with a number of rolls, and enters the kg later once
> weighed. A day can have several loads. Loads are **not tied to sales** for now.
> Kept: the route algorithm (by road via OpenRouteService), the map, ↑↓ order with
> Saqlash / Qayta hisoblash, and the driver's message. Each load is one route —
> **truck capacity and splitting a load across trucks are still to be decided**
> (crm/routing.py keeps `choose_trucks` / `split_two` for that). Sellers have no
> Yetkazish view for now. Models: `Load`, `LoadItem` (migration 0054); the
> sale-based `DeliveryRoute`, `RouteStop` and `Sale.delivery` were removed.
> Much of the text below describes the earlier sale-based design.


Status: built — trucks and warehouse, routing, the Yetkazish page, saving and hand
corrections, the driver's message and map links, and the sellers' read-only view
of their own stops. Still open: road distances (OSRM), a "deliver first" flag,
time windows.

**Sales are chosen by hand (decided 2026-11-07).** A day's delivery is not the sales
dated that day: on Yetkazish the operator ticks sales in «Yetkazilmagan sotuvlar»
and adds them to the chosen day (`Sale.delivery`). A sale is in at most one
delivery and can be taken out again.

**Open — to discuss at the end:** which sales the list offers. Provisional rule in
`_delivery_candidates`: every real sale not yet in any delivery, dated up to the
chosen day, newest first (200 shown, search for the rest). Options raised: only
sales made after go-live, or a window of recent days.

Hand corrections use ↑ / ↓ / ⇄ buttons rather than drag-and-drop: HTML5 drag is
unreliable on phones, and the project has no drag library.

## Goal

For a day's sales, work out which truck carries which client and in what order the
truck visits them, starting from the warehouse. One truck when the load fits in one,
two when it doesn't. The operator can always correct the result by hand.

## Decisions so far

| Question | Answer |
|---|---|
| Where do trucks start? | The warehouse. |
| How many trucks? | 2. Use 1 if the whole load fits in one truck, otherwise 2. |
| Truck capacity | Stored per truck in kg (to be added). |
| Time windows / "deliver first" | Unknown. Left out of the first version. |
| Client without a saved location | Ask for it before the route is built. |
| Warehouse | One shared physical warehouse for all sellers. Per-seller ombor stock is only bookkeeping. |

Because the warehouse and the trucks are shared, **a day has one plan for the whole
company**. A truck can carry several sellers' goods on the same trip.

## Open questions

1. **Are trucks shared or per seller?** Default: shared, owned by the company.
2. **Do trucks come back to the warehouse?** Default: yes. It only changes which
   client ends up last.
3. **Which sales are delivered on a given day?** Default: real sales (`Sale.real()`)
   dated that day, from every seller. Opening balances never ship.
4. **Zakaz lines** (not yet in stock, `SaleItem.fulfilled_at` is NULL) can't go on
   the truck. Default: count only `fulfilled_kg` toward the load. A sale with
   nothing in stock is left out and listed separately.

## What exists already

- `Client.latitude` / `Client.longitude` with `has_location` (`crm/models.py`).
- The location picker and share window (`static/js/geo.js`,
  `_client_location_modal.html`, `client_location` view). Used to ask for a
  missing location.
- `SaleItem.weight_kg` and `fulfilled_kg` give the load in kg, with grams converted.
- `Sale.objects.visible_to(user)` and `_acting_seller_field` set who sees what, and
  which seller an admin is acting as.
- Leaflet is already vendored in `static/vendor/leaflet`.

## Data model

New tables only. `Sale` is not changed.

- **`Truck`**: `name`, `capacity_kg` (Decimal), `is_active`.
- **`Warehouse`**: a single row with `name`, `latitude`, `longitude`. It's set with
  the existing location picker on the Mashinalar page. It lives in the database
  rather than an environment variable, so an admin can change it without a deploy.
- **`DeliveryRoute`**: `date` (unique), `created_by`, `created_at`, `updated_at`.
  One saved plan per day for the whole company.
- **`RouteStop`**: `route` (FK), `sale` (FK Sale), `truck` (FK Truck), `position`.
  Unique on (`route`, `sale`).

Keeping the plan in its own tables means re-planning a day never touches sales,
payments or reports.

## Algorithm (`crm/routing.py`)

Pure functions with no database access, so they can be unit-tested directly.

1. **Distance.** `distance_km(a, b)` is straight-line haversine distance. It sits
   behind a small interface so real road distances from OSRM can replace it later
   without changing the rest.
2. **Load.** Per stop, the in-stock kg of that sale. Several sales to the same
   client on one day merge into one stop.
3. **Choose trucks.**
   - The smallest active truck that holds the whole load gets one route.
   - Otherwise, the pair of trucks that holds it, preferring the smallest total
     capacity.
   - Otherwise, it's **over capacity**: return how many kg don't fit and stop. The
     operator removes or postpones sales. Nobody is dropped automatically.
   - A single stop heavier than the biggest truck is flagged by name.
4. **Split between two trucks (sweep).** Sort stops by compass angle around the
   warehouse, so neighbouring clients sit next to each other in the list. Try every
   start point and every cut point in that circle. Each cut gives two groups, one
   per side of the city. Keep only cuts where each group fits its truck, and choose
   the one with the shortest combined route. For 20 stops that's about 400 candidate
   splits, which takes milliseconds.
5. **Order each truck.** Nearest-neighbour from the warehouse, then 2-opt (reverse
   any stretch of the route that makes it shorter, and repeat until nothing
   improves). It includes the trip back to the warehouse when trucks return.
6. **Result.** Per truck: ordered stops, kg loaded against capacity, and km.

## Screens

### Mashinalar (admin only)
A simple list with add and edit: name, capacity in kg, active. The warehouse
location is set at the top of the same page.

### Yetkazish (delivery) page
Reached from a "Yetkazish" button on Sotuvlar. Choose a date. Admins and managers
see and plan every seller's sales together. The acting-seller selector doesn't
apply here.

1. **Check.** The page shows the day's sales from all sellers, with the seller's
   name on each, the total kg, and which trucks will be used.
2. **Missing locations first.** Clients without a location are listed at the top.
   Each has a "Joylashuv qo'shish" button that opens the existing location picker.
   "Marshrut tuzish" (build route) stays disabled until the list is empty.
   Partly-zakaz and fully-zakaz sales are listed separately with a short note.
3. **Build.** Shows one column per truck with numbered stops, kg used out of
   capacity, and km. A map shows numbered pins, with a different colour per truck
   and lines for the route.
4. **Adjust.** Drag a stop to reorder it, or move it to the other truck. Kg and km
   update immediately, with a warning if a truck goes over capacity. "Saqlash" saves
   the plan. "Qayta hisoblash" (recalculate) rebuilds it from scratch.
5. **For the driver.** Per truck:
   - A list to share (Telegram or copy) with each stop's number, name, phone,
     address and map link. This reuses the existing share window.
   - "Yo'lga chiqish" (set off) opens the route in a map app. Google Maps takes
     about 9 stops per link, so longer routes are split into consecutive links.
   - Optionally, a printable sheet next to the existing yuk xati.

## Build order

Each step is a small commit with its own tests.

1. `Truck` model, admin list/form, migration.
2. `Warehouse` single row, set with the existing picker on the Mashinalar page.
3. `crm/routing.py`: distance, truck choice, sweep split, nearest-neighbour + 2-opt.
   Unit tests: one truck vs two, over capacity, a stop heavier than a truck, the
   split respecting capacity, 2-opt never making a route longer, merged stops for
   the same client.
4. Yetkazish page, read-only: missing-location gate, computed plan, map.
5. `DeliveryRoute` / `RouteStop`, saving, and manual adjustment.
6. Driver share list and map-app links.
7. Later, optional: OSRM road distances, a "deliver first" flag, time windows.

## Permissions

Only admins and managers build and edit the day's plan, since it mixes every
seller's sales. Sellers get a read-only view of their own stops: which truck, which
position, and the expected order. That keeps `visible_to` intact, so a seller never
sees another seller's clients. Only admins manage trucks and the warehouse.
