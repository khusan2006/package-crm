"""crm/road.py — driving distances and road paths from OpenRouteService.
The service is always mocked here; tests never reach the network."""

import io
import json
import urllib.error

import pytest
from django.core.cache import cache

from crm import road
from crm.routing import distance_km

A, B, C = (41.30, 69.24), (41.3643, 69.2856), (41.2257, 69.2213)


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def key(settings):
    settings.OPEN_ROUTE_API_KEY = "test-key"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def ors(monkeypatch):
    """Record requests and answer them from `ors.answers` by endpoint."""
    class Fake:
        calls = []
        answers = {}

        def __call__(self, request, timeout):
            body = json.loads(request.data)
            self.calls.append((request.full_url, body, dict(request.headers)))
            answer = self.answers[request.full_url.rsplit("/v2", 1)[1]]
            if isinstance(answer, Exception):
                raise answer
            return FakeResponse(json.dumps(answer).encode())

    fake = Fake()
    fake.calls, fake.answers = [], {}
    monkeypatch.setattr(road.urllib.request, "urlopen", fake)
    return fake


def test_without_a_key_roads_are_off(ors):
    assert not road.enabled()
    with pytest.raises(road.RoadError):
        road.matrix_km([A, B])
    assert road.path([A, B]) is None
    assert ors.calls == []


def test_matrix_sends_lng_lat_and_the_key_and_is_cached(key, ors):
    ors.answers["/matrix/driving-car"] = {"distances": [[0, 10.2], [9.9, 0]]}
    assert road.matrix_km([A, B]) == [[0, 10.2], [9.9, 0]]
    url, body, headers = ors.calls[0]
    assert body["locations"] == [[69.24, 41.30], [69.2856, 41.3643]]
    assert body["units"] == "km" and headers["Authorization"] == "test-key"
    # Asked again, answered from the cache.
    road.matrix_km([A, B])
    assert len(ors.calls) == 1


def test_unroutable_pair_falls_back_to_a_straight_line(key, ors):
    ors.answers["/matrix/driving-car"] = {"distances": [[0, None], [9.9, 0]]}
    m = road.matrix_km([A, B])
    assert m[0][1] == pytest.approx(distance_km(A, B))


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
    urllib.error.URLError("no network"),
    TimeoutError(),
])
def test_service_failure_is_a_road_error(key, ors, failure):
    ors.answers["/matrix/driving-car"] = failure
    with pytest.raises(road.RoadError):
        road.matrix_km([A, B])


def test_a_strange_answer_is_a_road_error(key, ors):
    ors.answers["/matrix/driving-car"] = {"error": "quota"}
    with pytest.raises(road.RoadError):
        road.matrix_km([A, B])


def test_too_many_points_for_one_request(key, ors):
    points = [(41 + i / 1000, 69.0) for i in range(road.MAX_MATRIX_POINTS + 1)]
    with pytest.raises(road.RoadError):
        road.matrix_km(points)
    assert ors.calls == []


def test_distance_averages_the_two_directions(key, ors):
    ors.answers["/matrix/driving-car"] = {"distances": [[0, 10, 4], [12, 0, 7], [4, 9, 0]]}
    dist = road.distance_for([A, B, C])
    assert dist(A, B) == dist(B, A) == 11
    assert dist(B, C) == 8


def test_path_comes_back_as_lat_lng(key, ors):
    ors.answers["/directions/driving-car/geojson"] = {"features": [{"geometry": {
        "type": "LineString", "coordinates": [[69.24, 41.30], [69.25, 41.31], [69.2856, 41.3643]]}}]}
    assert road.path([A, B]) == [[41.30, 69.24], [41.31, 69.25], [41.3643, 69.2856]]
    road.path([A, B])
    assert len(ors.calls) == 1  # cached


def test_path_failure_just_means_no_path(key, ors):
    ors.answers["/directions/driving-car/geojson"] = urllib.error.URLError("down")
    assert road.path([A, B]) is None


# --- QA audit regressions -----------------------------------------------------------

def test_after_a_failure_the_service_is_left_alone_for_a_while(key, ors):
    ors.answers["/matrix/driving-car"] = urllib.error.URLError("down")
    with pytest.raises(road.RoadError):
        road.matrix_km([A, B])
    calls = len(ors.calls)
    with pytest.raises(road.RoadError):
        road.matrix_km([A, C])  # different points, same outage
    assert road.path([A, B]) is None
    assert len(ors.calls) == calls  # nobody waited on the timeout again


def test_distances_that_are_not_numbers_are_refused(key, ors):
    ors.answers["/matrix/driving-car"] = {"distances": [[0, "ten"], [9.9, 0]]}
    with pytest.raises(road.RoadError):
        road.matrix_km([A, B])


def test_the_timeout_leaves_room_under_the_web_worker():
    # A page asks at most three times; gunicorn kills a worker at 30 s.
    assert road.TIMEOUT * 3 < 30
