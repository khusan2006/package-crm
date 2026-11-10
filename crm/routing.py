"""Delivery route planning: which truck carries which stop, and in what order.

Pure functions with no database access — the delivery page turns sales into
`Stop`s and trucks into anything with a `capacity_kg`, and gets a `Plan` back.
Design: docs/specs/2026-11-07-delivery-route-design.md.

The steps, as `plan_routes` runs them:

1. Choose trucks. The smallest truck that holds the whole load goes alone;
   otherwise the pair of trucks that holds it, preferring the smallest total
   capacity. If even that is not enough, nothing is planned — the caller is told
   how many kg are over, and a person decides what waits.
2. Split between two trucks (sweep). Stops are sorted by compass direction from
   the warehouse, so neighbours sit next to each other. Every way of cutting that
   circle into two arcs is tried, and the shortest pair of routes whose loads fit
   wins: each truck gets one side of the city.
3. Order each truck's stops: nearest-neighbour from the warehouse, then 2-opt —
   reverse any stretch of the route that makes it shorter, until nothing does.

Distances are straight-line (haversine). Every function takes the distance as a
parameter, so road distances (OSRM) can replace it later without changing the rest.
"""

import math
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from itertools import combinations

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Stop:
    """One delivery address. `key` identifies it to the caller (e.g. a client pk)."""

    key: object
    lat: float
    lng: float
    kg: Decimal

    @property
    def point(self):
        return (self.lat, self.lng)


@dataclass
class TruckRoute:
    truck: object
    stops: list
    kg: Decimal
    km: float


@dataclass
class Plan:
    routes: list

    @property
    def km(self):
        return sum(r.km for r in self.routes)


def _kg(value):
    """1 350 — a space between thousands, as the rest of the app writes kg."""
    return f"{value:,.0f}".replace(",", " ")


class RoutingError(Exception):
    """A day that can't be planned automatically. The message is shown to the operator."""


class NoTrucks(RoutingError):
    def __init__(self):
        super().__init__("Faol mashina yo'q. Avval Mashinalar sahifasida mashina qo'shing.")


class OverCapacity(RoutingError):
    def __init__(self, excess_kg):
        self.excess_kg = excess_kg
        super().__init__(
            f"Yuk mashinalarga sig'maydi: {_kg(excess_kg)} kg ortiqcha. "
            f"Ba'zi sotuvlarni keyingi kunga qoldiring."
        )


class StopTooHeavy(RoutingError):
    def __init__(self, stops, capacity_kg):
        self.stops = stops
        super().__init__(
            f"{len(stops)} ta manzilning yuki eng katta mashinadan ({_kg(capacity_kg)} kg) "
            f"og'ir — ularni bo'lib yuborish kerak."
        )


class SplitImpossible(RoutingError):
    def __init__(self):
        super().__init__(
            "Yukni ikki mashinaga sig'adigan qilib bo'lib bo'lmadi. "
            "Ba'zi sotuvlarni keyingi kunga qoldiring."
        )


def distance_km(a, b):
    """Straight-line (great-circle) distance between two (lat, lng) points."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def merge_stops(stops):
    """One stop per key: several sales to the same client on a day are one visit."""
    merged = {}
    for stop in stops:
        if stop.key in merged:
            prev = merged[stop.key]
            merged[stop.key] = Stop(prev.key, prev.lat, prev.lng, prev.kg + stop.kg)
        else:
            merged[stop.key] = stop
    return list(merged.values())


def choose_trucks(total_kg, trucks):
    """The trucks that go: one if any holds the whole load, else the best pair.

    One truck: the smallest that fits, keeping the bigger ones free. Two: the pair
    with the smallest total capacity that fits. Raises OverCapacity otherwise."""
    trucks = sorted(trucks, key=lambda t: t.capacity_kg)
    if not trucks:
        raise NoTrucks()
    for truck in trucks:
        if truck.capacity_kg >= total_kg:
            return [truck]
    pairs = truck_pairs(total_kg, trucks)
    if pairs:
        return pairs[0]
    biggest = sum(t.capacity_kg for t in trucks[-2:])
    raise OverCapacity(total_kg - biggest)


def truck_pairs(total_kg, trucks):
    """Every pair of trucks that holds `total_kg` between them, smallest total
    capacity first, each pair bigger truck first ("Mashina 1" on screen). The first
    is the one to use; the rest are for when its stops can't be divided to fit."""
    pairs = [p for p in combinations(trucks, 2)
             if p[0].capacity_kg + p[1].capacity_kg >= total_kg]
    pairs.sort(key=lambda p: (p[0].capacity_kg + p[1].capacity_kg,
                              -max(p[0].capacity_kg, p[1].capacity_kg)))
    return [sorted(p, key=lambda t: t.capacity_kg, reverse=True) for p in pairs]


def _matrix(points, dist):
    return [[dist(a, b) for b in points] for a in points]


def _order(D, idxs, closed):
    """Order node indices `idxs` from depot node 0: nearest-neighbour, then 2-opt.
    Returns (ordered indices, route length). `closed` adds the drive back."""
    left = list(idxs)
    tour = [0]
    while left:
        here = tour[-1]
        nxt = min(left, key=lambda j: D[here][j])
        left.remove(nxt)
        tour.append(nxt)
    if closed:
        tour.append(0)

    # 2-opt: reverse tour[i..k] when that shortens it. Node 0 at the start (and the
    # end, when closed) stays put.
    last = len(tour) - (2 if closed else 1)
    improved = True
    while improved:
        improved = False
        for i in range(1, last):
            for k in range(i + 1, last + 1):
                a, b = tour[i - 1], tour[i]
                c = tour[k]
                if k + 1 < len(tour):
                    d = tour[k + 1]
                    delta = D[a][c] + D[b][d] - D[a][b] - D[c][d]
                else:  # open route: nothing after the last stop
                    delta = D[a][c] - D[a][b]
                if delta < -1e-9:
                    tour[i:k + 1] = reversed(tour[i:k + 1])
                    improved = True

    length = sum(D[tour[n]][tour[n + 1]] for n in range(len(tour) - 1))
    stops = tour[1:-1] if closed else tour[1:]
    return stops, length


def order_stops(depot, stops, return_to_depot=True, dist=distance_km):
    """`stops` in visiting order from `depot`, and the route length in km."""
    D = _matrix([depot] + [s.point for s in stops], dist)
    order, length = _order(D, range(1, len(stops) + 1), return_to_depot)
    return [stops[i - 1] for i in order], length


def _bearing(depot, stop):
    """Compass direction of a stop from the depot, for the sweep."""
    dy = stop.lat - depot[0]
    dx = (stop.lng - depot[1]) * math.cos(math.radians(depot[0]))
    return math.atan2(dy, dx)


def split_two(depot, stops, cap_a, cap_b, return_to_depot=True, dist=distance_km):
    """Split `stops` between truck A and truck B so both loads fit and the two
    routes together are as short as the sweep can find. Returns two ordered lists.

    Every arc of the direction-sorted circle is tried for truck A, with the rest
    going to B. If no arc fits (an awkward mix of weights), fall back to packing
    the heaviest stops first into whichever truck has more room left."""
    by_angle = sorted(stops, key=lambda s: _bearing(depot, s))
    n = len(by_angle)
    D = _matrix([depot] + [s.point for s in by_angle], dist)
    kg = [s.kg for s in by_angle]

    best = None
    for start in range(n):
        for size in range(1, n):
            a = [(start + j) % n for j in range(size)]
            a_kg = sum(kg[i] for i in a)
            if a_kg > cap_a or sum(kg) - a_kg > cap_b:
                continue
            in_a = set(a)
            b = [i for i in range(n) if i not in in_a]
            route_a, len_a = _order(D, [i + 1 for i in a], return_to_depot)
            route_b, len_b = _order(D, [i + 1 for i in b], return_to_depot)
            if best is None or len_a + len_b < best[0] - 1e-9:
                best = (len_a + len_b, route_a, route_b)

    if best is None and n <= EXACT_SPLIT_MAX:
        best = _exact_split(D, kg, cap_a, cap_b, return_to_depot)
    if best is None:
        best = _pack_heaviest_first(D, kg, cap_a, cap_b, return_to_depot)
    _, route_a, route_b = best
    return [by_angle[i - 1] for i in route_a], [by_angle[i - 1] for i in route_b]


#: Up to this many stops, a split the sweep can't find is searched for exactly
#: (every way of dividing them: 2^16 = 65 536 sums, milliseconds).
EXACT_SPLIT_MAX = 16
#: How many of the splits that fit get their routes measured, most even first.
EXACT_SPLIT_TRIES = 40


def _exact_split(D, kg, cap_a, cap_b, closed):
    """When no arc of the circle fits: try every division of the stops, keep the
    ones whose loads fit both trucks, and measure the most even of them. None if
    none fits."""
    n = len(kg)
    total = sum(kg)
    fits = []
    for mask in range(1, (1 << n) - 1):
        a_kg = sum(kg[i] for i in range(n) if mask >> i & 1)
        if a_kg <= cap_a and total - a_kg <= cap_b:
            fits.append((abs(a_kg / cap_a - (total - a_kg) / cap_b), mask))
    if not fits:
        return None
    best = None
    for _, mask in sorted(fits)[:EXACT_SPLIT_TRIES]:
        route_a, len_a = _order(D, [i + 1 for i in range(n) if mask >> i & 1], closed)
        route_b, len_b = _order(D, [i + 1 for i in range(n) if not mask >> i & 1], closed)
        if best is None or len_a + len_b < best[0] - 1e-9:
            best = (len_a + len_b, route_a, route_b)
    return best


def _pack_heaviest_first(D, kg, cap_a, cap_b, closed):
    room = [cap_a, cap_b]
    groups = ([], [])
    for i in sorted(range(len(kg)), key=lambda i: kg[i], reverse=True):
        side = 0 if room[0] >= room[1] else 1
        if kg[i] > room[side]:
            raise SplitImpossible()
        room[side] -= kg[i]
        groups[side].append(i + 1)
    route_a, len_a = _order(D, groups[0], closed)
    route_b, len_b = _order(D, groups[1], closed)
    return len_a + len_b, route_a, route_b


def build_route(truck, stops, depot, closed=True, dist=distance_km):
    """A TruckRoute for `stops` in exactly the order given — used for a plan the
    operator has adjusted by hand, so its kg and km are measured, not re-optimised."""
    D = _matrix([depot] + [s.point for s in stops], dist)
    length = 0.0
    path = [0] + list(range(1, len(stops) + 1)) + ([0] if closed else [])
    for n in range(len(path) - 1):
        length += D[path[n]][path[n + 1]]
    return TruckRoute(truck, stops, sum((s.kg for s in stops), Decimal("0")), length)


def plan_routes(depot, stops, trucks, return_to_depot=True, dist=distance_km, prefer_two=False):
    """The whole day: choose trucks, split if two, order each truck's stops.

    `depot` is the warehouse as (lat, lng); `trucks` are the active trucks (any
    objects with `capacity_kg`). By default one truck goes when it holds everything
    (fill one first); `prefer_two` sends two in parallel, each taking one side of the
    city, to finish sooner — falling back to the default when there aren't two trucks
    or the stops can't be divided. Raises a RoutingError when the day can't be
    planned automatically; its message says why, for the operator."""
    stops = merge_stops(stops)
    if not stops:
        return Plan(routes=[])
    if not trucks:
        raise NoTrucks()
    biggest = max(t.capacity_kg for t in trucks)
    too_heavy = [s for s in stops if s.kg > biggest]
    if too_heavy:
        raise StopTooHeavy(too_heavy, biggest)

    total = sum((s.kg for s in stops), Decimal("0"))
    if prefer_two and len(trucks) >= 2 and len(stops) >= 2:
        for pair in truck_pairs(total, trucks):
            try:
                groups = split_two(depot, stops, pair[0].capacity_kg, pair[1].capacity_kg,
                                   return_to_depot, dist)
            except SplitImpossible:
                continue
            return Plan(routes=[build_route(t, g, depot, return_to_depot, dist)
                                for t, g in zip(pair, groups)])
    chosen = choose_trucks(total, sorted(trucks, key=lambda t: t.capacity_kg))
    if len(chosen) == 1:
        ordered, _ = order_stops(depot, stops, return_to_depot, dist)
        return Plan(routes=[build_route(chosen[0], ordered, depot, return_to_depot, dist)])
    # Two trucks: the cheapest pair whose stops can actually be divided to fit —
    # 700 + 800 kg holds 1 500 kg on paper, but not three 500 kg stops.
    for pair in truck_pairs(total, trucks):
        try:
            groups = split_two(depot, stops, pair[0].capacity_kg, pair[1].capacity_kg,
                               return_to_depot, dist)
        except SplitImpossible:
            continue
        return Plan(routes=[build_route(t, g, depot, return_to_depot, dist)
                            for t, g in zip(pair, groups)])
    raise SplitImpossible()


#: Arrival times are estimates until the trucks report GPS: an average city speed
#: (40 km/h with the traffic lights — the client's own figure), a fixed time at
#: every stop (unloading, taking the money), and — when distances are straight
#: lines, not roads — a detour factor, as streets are longer than the crow flies.
ETA_SPEED_KMH = 40
ETA_STOP_MINUTES = 15
ETA_DETOUR = 1.3


def arrival_times(depot, points, start, dist=distance_km, detour=1.0):
    """When a truck that leaves `depot` at `start` reaches each of `points`, visited
    in order, and when it is back at the depot: ([datetime, …], datetime)."""
    def drive(a, b):
        return timedelta(hours=dist(a, b) * detour / ETA_SPEED_KMH)

    times, at, here = [], start, depot
    for point in points:
        at += drive(here, point)
        times.append(at)
        at += timedelta(minutes=ETA_STOP_MINUTES)
        here = point
    return times, at + drive(here, depot)
