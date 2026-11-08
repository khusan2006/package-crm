"""crm/route_share.py — the driver's message and the map links for one truck."""

from urllib.parse import parse_qs, unquote, urlparse

from crm.route_share import (
    GOOGLE_MAX_WAYPOINTS,
    driver_message,
    google_route_links,
    share_urls,
    yandex_route_link,
)

DEPOT = (41.3, 69.24)


def pts(n):
    return [(41.3 + i / 1000, 69.25 + i / 1000) for i in range(1, n + 1)]


def legs(links):
    """Each Google link as its full list of points: origin, waypoints, destination."""
    out = []
    for url in links:
        q = parse_qs(urlparse(url).query)
        mid = q["waypoints"][0].split("|") if "waypoints" in q else []
        out.append([q["origin"][0]] + mid + [q["destination"][0]])
    return out


def ll(p):
    return f"{p[0]:.6f}".rstrip("0").rstrip(".") + "," + f"{p[1]:.6f}".rstrip("0").rstrip(".")


def test_short_route_is_one_google_link_from_and_back_to_the_warehouse():
    links = google_route_links(DEPOT, pts(3))
    assert len(links) == 1
    assert legs(links) == [[ll(DEPOT)] + [ll(p) for p in pts(3)] + [ll(DEPOT)]]
    assert "travelmode=driving" in links[0]


def test_long_route_is_split_into_legs_that_join_up():
    stops = pts(20)
    links = google_route_links(DEPOT, stops)
    parts = legs(links)
    assert len(parts) == 3
    for part in parts:
        assert len(part) - 2 <= GOOGLE_MAX_WAYPOINTS
    for a, b in zip(parts, parts[1:]):
        assert a[-1] == b[0]  # each leg starts where the last one ended
    walked = parts[0] + [p for part in parts[1:] for p in part[1:]]
    assert walked == [ll(DEPOT)] + [ll(p) for p in stops] + [ll(DEPOT)]


def test_open_route_ends_at_the_last_client():
    links = google_route_links(DEPOT, pts(2), return_to_depot=False)
    assert legs(links)[0][-1] == ll(pts(2)[-1])


def test_no_stops_no_links():
    # A warehouse → warehouse "route" would be a pointless link.
    assert google_route_links(DEPOT, []) == []
    assert yandex_route_link(DEPOT, []) == ""


def test_yandex_link_has_the_whole_route_in_order():
    url = yandex_route_link(DEPOT, pts(12))
    rtext = unquote(parse_qs(urlparse(url).query)["rtext"][0]).split("~")
    assert rtext == [ll(DEPOT)] + [ll(p) for p in pts(12)] + [ll(DEPOT)]
    assert url.endswith("&rtt=auto")


def test_driver_message_lists_the_stops_in_order():
    stops = [
        {"n": 1, "name": "Anvar", "amount": "12 rulon · 450 kg", "address": "Chilonzor 5",
         "phone": "+998 90 111 22 33", "map_url": "https://www.google.com/maps?q=41.3,69.25"},
        {"n": 2, "name": "Botir", "amount": "3 rulon", "address": "", "phone": ""},
    ]
    text = driver_message("Yuk #4 — 07.11.2026 — Isuzu", stops,
                          ["https://g/1", "https://g/2"], "https://y/1")
    lines = text.splitlines()
    assert lines[0] == "Yuk #4 — 07.11.2026 — Isuzu"
    assert lines[1] == "2 ta manzil"
    assert "1. Anvar — 12 rulon · 450 kg" in lines and "2. Botir — 3 rulon" in lines
    assert lines.index("1. Anvar — 12 rulon · 450 kg") < lines.index("2. Botir — 3 rulon")
    assert "   Chilonzor 5" in lines and "   +998 90 111 22 33" in lines
    assert "1-qism: https://g/1" in lines and "2-qism: https://g/2" in lines
    assert "Marshrut (Yandex): https://y/1" in lines


def test_one_google_link_is_not_called_a_part():
    text = driver_message("Yuk #1", [], ["https://g/1"], "")
    assert "Marshrut (Google Maps): https://g/1" in text
    assert "qism" not in text


def test_share_urls_carry_the_whole_text():
    urls = share_urls("Salom & xayr\n1. Anvar", "https://g/1")
    tg = parse_qs(urlparse(urls["telegram_url"]).query)
    assert tg["url"] == ["https://g/1"] and tg["text"] == ["Salom & xayr\n1. Anvar"]
    wa = parse_qs(urlparse(urls["whatsapp_url"]).query)
    assert wa["text"] == ["Salom & xayr\n1. Anvar"]


def test_telegram_can_have_its_own_text_and_link():
    urls = share_urls("google text", "https://g/1", telegram_text="yandex text",
                      telegram_link="https://y/1")
    tg = parse_qs(urlparse(urls["telegram_url"]).query)
    assert tg["url"] == ["https://y/1"] and tg["text"] == ["yandex text"]
    assert parse_qs(urlparse(urls["whatsapp_url"]).query)["text"] == ["google text"]


# --- QA audit regressions -----------------------------------------------------------

from crm.route_share import TELEGRAM_MAX, fit_for_telegram  # noqa: E402


def test_a_name_with_line_breaks_cannot_fake_a_line():
    text = driver_message("Yuk #1", [{"n": 1, "name": "Anvar\nMarshrut (Yandex): https://evil",
                                      "amount": "1 rulon", "address": "Chilonzor\r\n5"}], [], "")
    assert "\nMarshrut (Yandex): https://evil" not in text
    assert "1. Anvar Marshrut (Yandex): https://evil — 1 rulon" in text
    assert "   Chilonzor 5" in text


def test_a_long_load_fits_in_one_telegram_message():
    stops = [{"n": n, "name": f"Мижоз номи анча узун {n}", "amount": "12 rulon · 340 kg",
              "address": "Тошкент ш., Чилонзор тумани, 19-мавзе, 45-уй, 12-хонадон",
              "phone": "+998 90 123 45 67", "map_url": f"https://yandex.uz/maps/?pt=69.{n},41.{n}"}
             for n in range(1, 41)]
    text = fit_for_telegram("Yuk #1 — 08.11.2026", stops, "https://yandex.uz/maps/?rtext=x")
    assert len(text) <= TELEGRAM_MAX
    assert "Marshrut (Yandex): https://yandex.uz/maps/?rtext=x" in text  # the route stays
    assert "40. Мижоз номи анча узун 40" in text  # every stop stays


def test_a_short_load_keeps_everything_for_telegram():
    stops = [{"n": 1, "name": "A", "amount": "1 rulon", "address": "Chilonzor", "phone": "+998",
              "map_url": "https://yandex.uz/maps/?pt=1,2"}]
    text = fit_for_telegram("Yuk #1", stops, "https://y/1")
    assert "Chilonzor" in text and "https://yandex.uz/maps/?pt=1,2" in text
