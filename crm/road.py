"""Driving distances and road paths from OpenRouteService (OpenStreetMap roads).

The delivery planner (crm/routing.py) takes its distance as a function; this module
supplies one that reads real driving distances instead of straight lines, and the
road path a truck takes so the map can draw it along the streets.

Only coordinates are sent — never a name, phone or address. Answers are cached for
a day, keyed by the coordinates, so reopening the Yetkazish page doesn't ask again.
Without a key (OPEN_ROUTE), or when the service fails, `RoadError` is raised and the
caller falls back to straight lines: the plan is never blocked by it.
"""

import hashlib
import json
import logging
import math
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache

from .routing import distance_km

log = logging.getLogger(__name__)

API = "https://api.openrouteservice.org/v2"
#: Per request. A page asks at most three times (one matrix, a path per truck), so
#: even a hanging service costs well under the web worker's 30 s.
TIMEOUT = 6  # seconds
CACHE_SECONDS = 24 * 60 * 60
#: After a failure, skip the service for this long: pages go straight to straight
#: lines instead of each waiting out the timeout again.
DOWN_SECONDS = 5 * 60
_DOWN_KEY = "road:down"
#: The public matrix endpoint takes at most 3 500 pairs per request: 59² = 3 481.
MAX_MATRIX_POINTS = 59
#: The public directions endpoint takes at most 50 points per request.
MAX_PATH_POINTS = 50


class RoadError(Exception):
    """Road distances aren't available — no key, too many points, or the service failed."""


def enabled():
    return bool(getattr(settings, "OPEN_ROUTE_API_KEY", ""))


def _cache_key(kind, points):
    raw = ";".join(f"{lat:.6f},{lng:.6f}" for lat, lng in points)
    return f"road:{kind}:{hashlib.sha1(raw.encode()).hexdigest()}"


def _post(path, body):
    if cache.get(_DOWN_KEY):
        raise RoadError("OpenRouteService yaqinda javob bermadi — biroz kutiladi")
    try:
        return _send(path, body)
    except RoadError as err:
        log.warning("OpenRouteService failed, straight lines for %s s: %s", DOWN_SECONDS, err)
        cache.set(_DOWN_KEY, True, DOWN_SECONDS)
        raise


def _send(path, body):
    request = urllib.request.Request(
        f"{API}{path}", data=json.dumps(body).encode(), method="POST",
        headers={
            "Authorization": settings.OPEN_ROUTE_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json, application/geo+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        raise RoadError(f"OpenRouteService {path}: HTTP {err.code}") from err
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        raise RoadError(f"OpenRouteService {path}: {err}") from err


def matrix_km(points):
    """Driving distance in km between every pair of (lat, lng) points, by road.

    A pair the service can't route (a point off any road) gets the straight-line
    distance instead, so one odd location doesn't throw the whole plan back."""
    if not enabled():
        raise RoadError("OPEN_ROUTE kaliti yo'q")
    if len(points) > MAX_MATRIX_POINTS:
        raise RoadError(f"{len(points)} nuqta — bitta so'rovga {MAX_MATRIX_POINTS} tadan ko'p")
    key = _cache_key("matrix", points)
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = _post("/matrix/driving-car", {
        "locations": [[lng, lat] for lat, lng in points],
        "metrics": ["distance"], "units": "km",
    })
    try:
        rows = data["distances"]
        result = [
            [_km(rows[i][j]) if rows[i][j] is not None else distance_km(points[i], points[j])
             for j in range(len(points))]
            for i in range(len(points))
        ]
    except (KeyError, IndexError, TypeError, ValueError) as err:
        raise RoadError(f"OpenRouteService matrix: kutilmagan javob ({err})") from err
    cache.set(key, result, CACHE_SECONDS)
    return result


def _km(value):
    """A distance cell as a float — never a string or NaN to blow up later."""
    km = float(value)
    if not math.isfinite(km) or km < 0:
        raise ValueError(value)
    return km


def distance_for(points):
    """A distance function for crm/routing.py over these points, by road.

    Road distances differ a little by direction (one-way streets); the planner's
    2-opt step assumes a route reversed is as long as before, so each pair gets the
    average of its two directions. Raises RoadError when roads aren't available."""
    m = matrix_km(points)
    index = {p: i for i, p in enumerate(points)}

    def dist(a, b):
        i, j = index.get(a), index.get(b)
        if i is None or j is None:
            return distance_km(a, b)
        return (m[i][j] + m[j][i]) / 2

    return dist


def path(points):
    """The road a truck drives through `points` in order, as [[lat, lng], …] for the
    map — or None when it isn't available (the map then draws straight lines)."""
    if not enabled() or len(points) < 2 or len(points) > MAX_PATH_POINTS:
        return None
    key = _cache_key("path", points)
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        data = _post("/directions/driving-car/geojson",
                     {"coordinates": [[lng, lat] for lat, lng in points]})
        line = [[lat, lng] for lng, lat, *_ in data["features"][0]["geometry"]["coordinates"]]
    except (RoadError, KeyError, IndexError, TypeError, ValueError):
        return None
    cache.set(key, line, CACHE_SECONDS)
    return line
