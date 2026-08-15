"""Helpers for rendering create/edit forms either full-page or inside a modal."""

from django.http import HttpResponse
from django.shortcuts import redirect, render

# Month names spelled out, because a native <input type="month"> paints its label in
# the browser's locale ("Август" on a Russian Windows) — the one place the Uzbek UI
# stopped being Uzbek. Lives here so the payroll page and its form share one list.
UZ_MONTH_NAMES = [
    "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
]


def uz_month(year, month):
    return f"{UZ_MONTH_NAMES[month - 1]} {year}"


def is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def form_response(request, form, title, invalid=False, modal_template="_modal.html", **extra):
    """Render the modal partial for AJAX requests, otherwise the full page.
    `extra` is merged into the template context (e.g. datalist suggestions)."""
    context = {"form": form, "title": title, **extra}
    if is_ajax(request):
        status = 422 if invalid else 200
        return render(request, modal_template, context, status=status)
    return render(request, "crm/form.html", context)


def form_success(request, url):
    """Tell an AJAX modal to redirect; otherwise do a normal redirect."""
    if is_ajax(request):
        response = HttpResponse(status=204)
        response["X-Redirect"] = url
        return response
    return redirect(url)


def form_reload(request, fallback_url):
    """For in-place actions (edit/delete/settle): an AJAX modal reloads the page
    it was opened from (204 with no X-Redirect); otherwise redirect to a fallback."""
    if is_ajax(request):
        return HttpResponse(status=204)
    return redirect(fallback_url)


def render_confirm(request, title, message, confirm_label, confirm_class=""):
    """Render a confirm dialog as a modal partial (AJAX) or a full page."""
    context = {
        "title": title,
        "message": message,
        "confirm_label": confirm_label,
        "confirm_class": confirm_class,
    }
    template = "_confirm_modal.html" if is_ajax(request) else "crm/confirm.html"
    return render(request, template, context)
