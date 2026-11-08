# Yetkazish: loads that become sales — spec

Status: built 2026-11-08 (commit b3f16a4 and follow-ups), not yet deployed.

Choices made while building, to confirm with the client:
- A line's price may be 0 (a free line becomes a 0-so'm sale line).
- Any seller can reorder, save or reset the route and switch the truck strategy of
  a load, including loads that hold other sellers' stops (load-level, shared).
- A seller sees other sellers' stops but gets no links into those clients' pages
  (location, sale), which they couldn't open. Builds on the loads feature
(`Load`, `LoadItem`, route by road, trucks chosen from kg).

## What the client asked for

1. **Every load row becomes a sale.** The day before delivery, sellers enter each
   client and the items agreed on the call — products and rolls, everything except
   kg. On delivery day the goods are weighed at the warehouse before the truck
   leaves; the seller enters kg and presses ✓, and that client's row turns into a real
   sale, linked to the delivery.
2. **Some stops are only to collect money**, or to deliver and collect.
3. **Back to the suggested route** after changing the order by hand.
4. **Choose how trucks are used:** fill one truck first, or split across two to
   finish faster.

## Decisions

| Question | Answer |
|---|---|
| Several products per client in one delivery? | Yes. |
| Price | From the product's catalogue price, editable per line (like the sale form). |
| Who enters loads | Sellers (and admins/managers). |
| Whose sale | The client's own seller. |
| When it becomes a sale | Per client: each row's ✓ once its kg is in. |
| Truck plan before weighing | Preliminary — estimated the day before, re-planned from real kg. |
| Money collection details (recorded as payment?) | Deferred — to ask the client. For now a collect stop is on the route with the client's current debt shown. |
| What a seller sees of other sellers' rows | Everything in the load (clients, items, route); adds, edits and confirms only their own clients' rows. |
| Kg estimate before weighing | From past sales: average kg per roll of that product, over sale lines that recorded rolls. |
| Debt term of a load's sale | The standard default; edited later on the sale if needed. |

## Data

- **`LoadItem`** (one stop: a client in a load) gains:
  - `purpose`: `deliver` / `collect` / `both`.
  - `sale` (FK Sale, null): set when the row is confirmed (✓). A confirmed row is
    locked; it shows a link to its sale.
  - `rolls` and `kg` move down to lines (below); the stop's totals are derived.
- **`LoadLine`** (new): `item` (FK LoadItem), `product`, `rolls`, `price`, `kg`
  (null until weighed). Several per stop. A collect-only stop has none.
- **`Load`** gains `strategy`: `fill` (one truck first, today's behaviour) / `fast`
  (two trucks in parallel when two exist).
- Existing loads: each `LoadItem` becomes a stop with one line (no product yet)
  — see open question A.

## Confirming a row (✓) → sale

Done by one shared function, also used by the sale form after this change, so a
load's sale is exactly like one typed into Sotuvlar:

- `Sale(date = load date, client, sales_rep = client's seller, debt term = default,
  note = "Yuk #N")`.
- One `SaleItem` per line: product, kg, rolls, price, tannarx = product's cost.
- Then the same steps as the sale form: mark fulfilled, spend the client's
  advance, recompute debt deadlines, audit log.
- Every line needs kg first; otherwise ✓ is refused with the line named.
- Undo: the sale is edited or deleted in Sotuvlar like any other; deleting it
  unlocks the row again.

## Trucks

- **Before weighing** (preliminary): kg is estimated per line from the product's
  past sales — average kg per roll across previous sale lines that recorded rolls.
  The route page says "taxminiy" (estimated) wherever this is used.
- **After weighing**: real kg, as now.
- **Strategy**: `fill` = smallest truck that fits, a second only when it doesn't
  (today). `fast` = two trucks split by area whenever two active trucks exist and
  each half fits, even if one truck could take everything.

## Route

- A **"Tavsiya etilgan tartib"** button is always there: it puts the suggested order
  back on screen (saved or not); saving is still a separate step.
- Collect-only stops are on the route; the driver message shows "pul olish" and the
  client's current debt for them.

## Permissions

Sellers can open Yetkazish, create loads, see every row and the route, and add,
edit and confirm only their **own** clients' rows. Admins and managers see and edit
everything, as now.

## Open questions

- **A. Existing loads on production**: there are none yet (0 loads on 2026-11-08),
  so the data change can start fresh.
- **C. Money collection** (deferred to the client): should the collected amount be
  recorded as a payment, and when?
