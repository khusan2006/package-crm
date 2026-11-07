"""What the driver gets: one truck's stops as a message, and links that open the
route in a map app. Pure functions — the Yetkazish page passes the plan in.

Google Maps takes at most 9 waypoints in a directions link (fewer still in a
phone's browser, though the Maps app reads all 9), so a longer route is split
into consecutive legs, each starting where the previous one ended. Yandex takes
the whole route in one link.
"""

from urllib.parse import quote, urlencode

#: A Google directions link: origin + up to 9 waypoints + destination.
GOOGLE_MAX_WAYPOINTS = 9


def _ll(point):
    lat, lng = point
    return f"{lat:.6f}".rstrip("0").rstrip(".") + "," + f"{lng:.6f}".rstrip("0").rstrip(".")


def _path(depot, points, return_to_depot):
    return [depot] + list(points) + ([depot] if return_to_depot else [])


def google_route_links(depot, points, return_to_depot=True):
    """Directions links covering the whole route, split into legs Google accepts."""
    if not points:
        return []
    path = _path(depot, points, return_to_depot)
    step = GOOGLE_MAX_WAYPOINTS + 1
    links = []
    for start in range(0, len(path) - 1, step):
        leg = path[start:start + step + 1]
        params = {
            "api": "1",
            "origin": _ll(leg[0]),
            "destination": _ll(leg[-1]),
            "travelmode": "driving",
        }
        if len(leg) > 2:
            params["waypoints"] = "|".join(_ll(p) for p in leg[1:-1])
        links.append("https://www.google.com/maps/dir/?" + urlencode(params, quote_via=quote, safe=",|"))
    return links


def yandex_route_link(depot, points, return_to_depot=True):
    """The whole route as one Yandex Maps link (rtext is "lat,lng~lat,lng~…")."""
    if not points:
        return ""
    path = _path(depot, points, return_to_depot)
    return "https://yandex.uz/maps/?rtext=" + "~".join(_ll(p) for p in path) + "&rtt=auto"


def _kg(value):
    return f"{value:,.0f}".replace(",", " ")


def driver_message(title, stops, google_links, yandex_link):
    """The text the driver is sent: every stop in order, then the route links.

    `stops` are dicts with n, name, amount ("12 rulon · 340 kg"), and optionally
    address, phone and map_url."""
    lines = [title, f"{len(stops)} ta manzil", ""]
    for s in stops:
        lines.append(f"{s['n']}. {s['name']} — {s['amount']}")
        for key in ("address", "phone", "map_url"):
            if s.get(key):
                lines.append(f"   {s[key]}")
    if google_links:
        lines.append("")
        if len(google_links) == 1:
            lines.append(f"Marshrut (Google Maps): {google_links[0]}")
        else:
            lines.append("Marshrut (Google Maps), qismlarga bo'lingan:")
            lines.extend(f"{i}-qism: {url}" for i, url in enumerate(google_links, start=1))
    if yandex_link:
        lines.append(f"Marshrut (Yandex): {yandex_link}")
    return "\n".join(lines)


def share_urls(text, link):
    """Telegram and WhatsApp share links for `text`. Telegram shows `url` apart from
    the words, so the route link goes there."""
    return {
        "telegram_url": "https://t.me/share/url?" + urlencode({"url": link, "text": text}, quote_via=quote),
        "whatsapp_url": "https://wa.me/?" + urlencode({"text": text}, quote_via=quote),
    }
