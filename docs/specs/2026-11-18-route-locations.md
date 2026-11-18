# Yetkazish: do'konlar, Teskari, Zarur and «$ Yo'l-yo'lakay»

Client request, 2026-11-18. Four problems with the delivery route:

1. **A client with 2–3 do'konlar.** Some clients have shops in two or three places.
   It should be possible to add more locations to a client, each with a note.
2. **Reverse the route.** Say the route is 1-2-3-4-5 and the driver wants to start
   from the far end (5-4-3-2-1). One button should do it.
3. **An urgent client.** Partway through 1-2-3-4-5, the owner of stop 3 says it's
   urgent. That stop should come first and the rest should be worked out again from
   there.
4. **Money on the way.** If the route passes a debtor, show that place with a «$»
   icon so the driver can collect on the way.

## Decisions

These are the defaults chosen while building. Change them if the client decides
otherwise.

| Question | Answer |
|---|---|
| Where are the extra shops kept? | The main joylashuv stays on `Client` (`latitude`, `longitude`), with a new `location_note`. Other shops are `ClientLocation` rows: `note` plus a point. |
| Can a shop exist without a main location? | No. The form asks for the main one first. |
| Which shop does a delivery go to? | `LoadItem.location`. Empty means the main joylashuv. A «Do'kon» box appears on the day row and in «Mijoz qo'shish», only for a client with more than one place. |
| Two do'konlar of one client on one day | Yes: «Mijoz qo'shish» takes the client in two rows with different do'konlar (the same do'kon twice is refused). Each row becomes a stop of its own; the second goes into the day's next Yuk (a client is once per Yuk). |
| A shop is removed from the client | Its stops go back to the main joylashuv (`SET_NULL`). The client form updates kept shops in place, so their stops keep pointing at them. |
| Teskari | Reverses that vehicle's card and saves at once. «Zarur» stops stay first. |
| Zarur | `LoadItem.urgent_at`. The pressed stop goes first on its truck, ahead of earlier urgent ones (the newest is first). The rest of that truck is ordered on from it. Pressing it saves the order. Pressing it again clears the mark and keeps the order. |
| Zarur on an unsaved suggestion | The suggestion keeps urgent stops first too, including after the money car rearranges the trucks. |
| Zarur and another day | Moving the stop to another day clears it. |
| Who sees «$»? | The route, so admins and managers. Debtors are anyone with `Sale.outstanding()` and no stop on the day that isn't cancelled. |
| Which money is asked for? | Only what is due by the route's day: receipts past `debt_deadline`, or due that very day (client request, 2026-11-18). A debt still inside its term isn't offered. The card shows that amount and «muddati N kun o'tgan» / «muddati shu kuni», plus «jami qarz» when more is owed. |
| How near is "on the way"? | Adding them costs at most `ON_WAY_KM = 2` km (straight line, about 2.6 km by road), cheapest insertion. At most `ON_WAY_MAX = 12` are shown, nearest first. A client with several places is offered at the place that costs least. |
| «+ Marshrutga» | Adds a money-only stop (`collect`, by truck) for that client and place at the cheapest point on a vehicle's route, then saves the order as shown. |

## Where it lives

- `crm/models.py`: `MapPoint` (the shared map properties), `Client.location_note`,
  `ClientLocation`, `LoadItem.location`, `LoadItem.urgent_at`, `LoadItem.place`,
  `LoadItem.place_note`. Migration 0070.
- `crm/forms.py`: `ShopsField` and `ShopsWidget` (rows of a note plus a location
  picker), and `ClientForm.save_shops`.
- `crm/routing.py`: `order_from`, `urgent_first`, `cheapest_insert`, `on_way`.
- `crm/views.py`:
  - `_shop_of`, `_shop_choices` and `place_<pk>` / `new_place` on the day form.
  - `_press_urgent`, `_press_on_way` and the `why=reverse` audit note in
    `_save_load_order_checked`.
  - `_urgent_groups` in the suggestion, and `_on_way` for the route cards.
- Templates:
  - `_load_route.html`, `_route_stop_place.html`, `_route_on_way.html` for the route.
  - `_load_stop.html` and `_day_row.html` for the «Do'kon» box and the «Zarur» badge.
  - `_load_new_row.html` for «Mijoz qo'shish».
  - `widgets/shops.html` for the client form.
- JS:
  - `route.js`: Teskari.
  - `geo.js`: the «$» and «Zarur» pins, and `geo:enhance` for new picker rows.
  - `load.js`: the «Do'kon» box in «Mijoz qo'shish».
  - `base.html`: «+ Do'kon».

## Not done

- The driver's message lists only the stops on the route. Debtors on the way reach
  the driver only once they are added with «+ Marshrutga».
- Arrival times still count from the warehouse. Without GPS the page doesn't know
  where the truck is when «Zarur» is pressed mid-route.
