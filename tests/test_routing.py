"""crm/routing.py — choosing trucks, splitting the load, and ordering the stops.
Pure functions, so no database here."""

import random
import time
from dataclasses import dataclass
from decimal import Decimal
from itertools import permutations

import pytest

from crm import routing
from crm.routing import (
    NoTrucks,
    OverCapacity,
    SplitImpossible,
    Stop,
    StopTooHeavy,
    choose_trucks,
    distance_km,
    merge_stops,
    order_stops,
    plan_routes,
)

DEPOT = (41.30, 69.24)  # roughly central Tashkent


@dataclass(frozen=True)
class FakeTruck:
    name: str
    capacity_kg: int


def stop(key, dlat, dlng, kg=100):
    return Stop(key, DEPOT[0] + dlat, DEPOT[1] + dlng, Decimal(kg))


def length(depot, stops, closed=True):
    pts = [depot] + [s.point for s in stops] + ([depot] if closed else [])
    return sum(distance_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def random_stops(n, seed, kg=100):
    rnd = random.Random(seed)
    return [stop(i, rnd.uniform(-0.1, 0.1), rnd.uniform(-0.1, 0.1), kg) for i in range(n)]


# --- distance -----------------------------------------------------------------------

def test_one_degree_of_latitude_is_about_111_km():
    assert distance_km((41.0, 69.0), (42.0, 69.0)) == pytest.approx(111.19, abs=0.1)


def test_distance_to_itself_is_zero():
    assert distance_km(DEPOT, DEPOT) == 0


# --- ordering -----------------------------------------------------------------------

def test_stops_on_a_line_are_visited_in_order():
    stops = [stop("far", 0, 0.03), stop("near", 0, 0.01), stop("mid", 0, 0.02)]
    ordered, _ = order_stops(DEPOT, stops, return_to_depot=False)
    assert [s.key for s in ordered] == ["near", "mid", "far"]


def test_route_length_matches_the_order_it_returns():
    stops = random_stops(12, seed=1)
    for closed in (True, False):
        ordered, km = order_stops(DEPOT, stops, return_to_depot=closed)
        assert km == pytest.approx(length(DEPOT, ordered, closed))
        assert sorted(s.key for s in ordered) == list(range(12))


@pytest.mark.parametrize("seed", range(5))
def test_two_opt_never_does_worse_than_nearest_neighbour(seed):
    stops = random_stops(20, seed)
    # Plain nearest-neighbour, the starting point 2-opt improves on.
    left, here, nn = list(stops), DEPOT, []
    while left:
        nxt = min(left, key=lambda s: distance_km(here, s.point))
        left.remove(nxt)
        nn.append(nxt)
        here = nxt.point
    _, km = order_stops(DEPOT, stops)
    assert km <= length(DEPOT, nn) + 1e-9


@pytest.mark.parametrize("seed", range(5))
def test_small_routes_are_close_to_the_best_possible(seed):
    stops = random_stops(7, seed)
    best = min(length(DEPOT, list(p)) for p in permutations(stops))
    _, km = order_stops(DEPOT, stops)
    assert km <= best * 1.05


def test_far_away_client_is_not_left_stranded():
    # Nearest-neighbour alone visits the cluster, then drives out to the lone
    # client and all the way back. The far client sits just past the cluster's
    # end, so a good route picks them up on the way.
    stops = [stop(i, 0, 0.01 * i) for i in range(1, 6)] + [stop("far", 0.02, 0.055)]
    ordered, _ = order_stops(DEPOT, stops)
    keys = [s.key for s in ordered]
    assert keys.index("far") in (4, 5)


# --- merging and truck choice -------------------------------------------------------

def test_same_client_twice_is_one_stop_with_both_loads():
    merged = merge_stops([stop(1, 0, 0.01, 100), stop(2, 0, 0.02, 50), stop(1, 0, 0.01, 30)])
    assert {s.key: s.kg for s in merged} == {1: Decimal(130), 2: Decimal(50)}


SMALL, MID, BIG = FakeTruck("Labo", 800), FakeTruck("Isuzu", 3000), FakeTruck("Kamaz", 10000)


def test_smallest_truck_that_fits_goes_alone():
    assert choose_trucks(Decimal(700), [BIG, SMALL, MID]) == [SMALL]
    assert choose_trucks(Decimal(2000), [BIG, SMALL, MID]) == [MID]


def test_two_trucks_when_none_holds_it_alone():
    a, b = FakeTruck("A", 1000), FakeTruck("B", 1500)
    assert choose_trucks(Decimal(2200), [a, b]) == [b, a]  # bigger first


def test_smallest_pair_that_fits_is_chosen():
    trucks = [FakeTruck("A", 1000), FakeTruck("B", 1200), FakeTruck("C", 2000)]
    # 2100 kg: A+B = 2200 is the smallest pair that holds it.
    assert {t.name for t in choose_trucks(Decimal(2100), trucks)} == {"A", "B"}


def test_over_capacity_says_how_many_kg_do_not_fit():
    with pytest.raises(OverCapacity) as err:
        choose_trucks(Decimal(5000), [FakeTruck("A", 1000), FakeTruck("B", 1500)])
    assert err.value.excess_kg == Decimal(2500)
    assert "2 500 kg ortiqcha" in str(err.value)


def test_one_truck_too_small_is_over_capacity():
    with pytest.raises(OverCapacity) as err:
        choose_trucks(Decimal(1200), [FakeTruck("A", 1000)])
    assert err.value.excess_kg == Decimal(200)


def test_no_trucks():
    with pytest.raises(NoTrucks):
        choose_trucks(Decimal(1), [])
    with pytest.raises(NoTrucks):
        plan_routes(DEPOT, [stop(1, 0, 0.01)], [])


# --- the whole plan -----------------------------------------------------------------

def test_no_stops_is_an_empty_plan():
    assert plan_routes(DEPOT, [], [MID]).routes == []


def test_load_that_fits_goes_in_one_truck():
    stops = random_stops(20, seed=3, kg=100)  # 2000 kg
    plan = plan_routes(DEPOT, stops, [MID, FakeTruck("Gazel", 1500)])
    assert len(plan.routes) == 1
    route = plan.routes[0]
    assert route.truck == MID
    assert route.kg == Decimal(2000)
    assert sorted(s.key for s in route.stops) == list(range(20))


def test_load_that_does_not_fit_is_split_between_two():
    a, b = FakeTruck("A", 1200), FakeTruck("B", 1200)
    stops = random_stops(20, seed=4, kg=100)  # 2000 kg
    plan = plan_routes(DEPOT, stops, [a, b])
    assert len(plan.routes) == 2
    for route in plan.routes:
        assert route.kg <= route.truck.capacity_kg
        assert route.km == pytest.approx(length(DEPOT, route.stops))
    keys = [s.key for r in plan.routes for s in r.stops]
    assert sorted(keys) == list(range(20))  # everyone, exactly once


def test_split_gives_each_truck_one_side_of_the_city():
    east = [stop(f"e{i}", 0.005 * i, 0.08, 100) for i in range(5)]
    west = [stop(f"w{i}", 0.005 * i, -0.08, 100) for i in range(5)]
    a, b = FakeTruck("A", 600), FakeTruck("B", 600)
    plan = plan_routes(DEPOT, east + west, [a, b])
    sides = [{s.key[0] for s in r.stops} for r in plan.routes]
    assert sorted(map(sorted, sides)) == [["e"], ["w"]]


def test_split_respects_different_capacities():
    big, small = FakeTruck("Big", 1500), FakeTruck("Small", 600)
    stops = random_stops(20, seed=5, kg=100)  # 2000 kg
    plan = plan_routes(DEPOT, stops, [small, big])
    by_truck = {r.truck.name: r.kg for r in plan.routes}
    assert by_truck["Big"] <= 1500 and by_truck["Small"] <= 600
    assert sum(by_truck.values()) == Decimal(2000)


def test_a_stop_heavier_than_any_truck_is_named():
    heavy = stop("heavy", 0, 0.01, 5000)
    with pytest.raises(StopTooHeavy) as err:
        plan_routes(DEPOT, [heavy, stop("ok", 0, 0.02, 100)], [MID])
    assert [s.key for s in err.value.stops] == ["heavy"]


def test_loads_that_cannot_be_divided_are_reported():
    # 18 kg fits in 10 + 10 on paper, but three 6 kg stops can't be divided so.
    stops = [stop(i, 0.01 * i, 0.01, 6) for i in range(3)]
    with pytest.raises(SplitImpossible):
        plan_routes(DEPOT, stops, [FakeTruck("A", 10), FakeTruck("B", 10)])


def test_heaviest_first_packing_keeps_both_trucks_within_capacity():
    # The fallback split_two uses when no arc of the circle fits.
    kg = [Decimal(k) for k in (5, 5, 4, 4)]
    pts = [DEPOT] + [(DEPOT[0] + 0.01 * i, DEPOT[1] + 0.01) for i in range(4)]
    D = routing._matrix(pts, distance_km)
    _, route_a, route_b = routing._pack_heaviest_first(D, kg, 9, 9, True)
    assert sorted(route_a + route_b) == [1, 2, 3, 4]
    assert sum(kg[i - 1] for i in route_a) <= 9
    assert sum(kg[i - 1] for i in route_b) <= 9


def test_heaviest_first_packing_gives_up_when_it_cannot_fit():
    kg = [Decimal(6)] * 3
    pts = [DEPOT] + [(DEPOT[0] + 0.01 * i, DEPOT[1]) for i in range(3)]
    D = routing._matrix(pts, distance_km)
    with pytest.raises(SplitImpossible):
        routing._pack_heaviest_first(D, kg, 10, 10, True)


def test_forty_stops_plan_quickly():
    stops = random_stops(40, seed=6, kg=100)  # 4000 kg across two trucks
    started = time.perf_counter()
    plan = plan_routes(DEPOT, stops, [FakeTruck("A", 2500), FakeTruck("B", 2500)])
    assert time.perf_counter() - started < 5
    assert len(plan.routes) == 2


# --- QA audit regressions -----------------------------------------------------------

def _round(n, kgs, r=0.03):
    import math as m
    return [Stop(i, DEPOT[0] + r * m.sin(m.radians(i * 360 / n)),
                 DEPOT[1] + r * m.cos(m.radians(i * 360 / n)), Decimal(k))
            for i, k in enumerate(kgs)]


def test_another_pair_is_tried_when_the_cheapest_cannot_divide_the_stops():
    # 700 + 800 holds 1 500 kg on paper, but not three 500 kg stops; 1000 + 700 can.
    trucks = [FakeTruck("a", 700), FakeTruck("b", 800), FakeTruck("c", 1000)]
    plan = plan_routes(DEPOT, _round(3, [500, 500, 500]), trucks)
    assert sorted(r.truck.name for r in plan.routes) == ["a", "c"]
    assert all(r.kg <= r.truck.capacity_kg for r in plan.routes)


def test_a_split_the_sweep_misses_is_found_exactly():
    # By direction the weights run 2, 2, 3, 2, 3: no arc fits two 6 kg trucks,
    # but {3, 3} and {2, 2, 2} do.
    a, b = routing.split_two(DEPOT, _round(5, [2, 2, 3, 2, 3]), 6, 6)
    assert sorted([sorted(float(s.kg) for s in a), sorted(float(s.kg) for s in b)]) == [
        [2.0, 2.0, 2.0], [3.0, 3.0]]


def test_truck_pairs_come_cheapest_first():
    trucks = [FakeTruck("a", 700), FakeTruck("b", 800), FakeTruck("c", 1000)]
    pairs = routing.truck_pairs(Decimal(1500), trucks)
    assert [[t.name for t in p] for p in pairs] == [["b", "a"], ["c", "a"], ["c", "b"]]
