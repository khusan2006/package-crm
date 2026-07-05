from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    """Build a ?query string for pagination that preserves current filters."""
    params = context["request"].GET.copy()
    params["page"] = page_number
    return "?" + params.urlencode()


@register.simple_tag(takes_context=True)
def qs_replace(context, **kwargs):
    """Replace given query params (preserving the rest); resets pagination."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        params[key] = value
    params.pop("page", None)
    return "?" + params.urlencode()
