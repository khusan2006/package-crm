import os
import re

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils import timezone

register = template.Library()

# Audit summaries embed the amount as "… — 1,210,000 so'm …". Pull it out so the
# reports feed can show it in a dedicated, colour-coded Summa column, and strip it
# from the descriptive text so the amount isn't printed twice.
_MONEY_RE = re.compile(r"\s*—\s*([\d,]+)\s*so['’]m")


@register.filter
def money_of(summary):
    """The so'm amount embedded in an audit summary, e.g. "1,210,000" (or "")."""
    m = _MONEY_RE.search(summary or "")
    return m.group(1) if m else ""


@register.filter
def without_money(summary):
    """The audit summary with its "— <amount> so'm" chunk removed and spaces tidied,
    so the Tafsilot column reads cleanly next to the Summa column."""
    text = _MONEY_RE.sub(" ", summary or "")
    return re.sub(r"\s{2,}", " ", text).strip()


@register.filter
def timeago_uz(value):
    """Uzbek relative time for a past date: "Bugun", "Kecha", "N kun oldin",
    "N oy oldin", "N yil oldin". Blank for None (e.g. a client with no sales)."""
    if not value:
        return ""
    days = (timezone.localdate() - value).days
    if days <= 0:
        return "Bugun"
    if days == 1:
        return "Kecha"
    if days < 30:
        return f"{days} kun oldin"
    if days < 365:
        return f"{days // 30} oy oldin"
    return f"{days // 365} yil oldin"


@register.inclusion_tag("crm/_deadline_badge.html")
def deadline_badge(deadline):
    """Render a "X kun qoldi / X kun o'tgan / Bugun" chip for a debt deadline."""
    if not deadline:
        return {"days": None}
    days = (deadline - timezone.localdate()).days
    return {"days": days, "overdue_by": -days if days < 0 else 0}


@register.simple_tag
def static_v(path):
    """Static URL with a ?v=<mtime> cache-buster so browsers refetch on change."""
    url = static(path)
    abs_path = finders.find(path)
    if abs_path:
        try:
            return f"{url}?v={int(os.path.getmtime(abs_path))}"
        except OSError:
            pass
    return url


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    """Build a ?query string for pagination that preserves current filters."""
    params = context["request"].GET.copy()
    params["page"] = page_number
    return "?" + params.urlencode()


@register.simple_tag(takes_context=True)
def qs_replace(context, **kwargs):
    """Replace given query params (preserving the rest); empty value drops the
    param; pagination is always reset."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in ("", None):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop("page", None)
    return "?" + params.urlencode()
