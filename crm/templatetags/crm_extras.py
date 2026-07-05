import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


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
