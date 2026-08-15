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


def _readable(field, value):
    """One side of a change, as a person would read it: a choice's label rather than
    its code, "ha"/"yo'q" for a checkbox, an em dash for nothing at all."""
    if value is True:
        return "ha"
    if value is False:
        return "yo'q"
    if value in (None, "", []):
        return "—"
    choices = getattr(field, "choices", None)
    if choices:
        # Works for both plain choices and a ModelChoiceField, whose iterator keys
        # compare equal to the raw pk that `initial` holds.
        return str(dict(choices).get(value, value))
    return str(value)


def form_changes(form):
    """What a bound form actually changed, as "Maydon: oldin → hozir, …".

    An audit line that says only "yangilandi" answers none of the questions the trail
    is kept for — which price moved, who the client was handed to, whose account was
    switched off. Read from `changed_data`, so an untouched field is never listed."""
    return ", ".join(
        f"{form.fields[name].label or name}: "
        f"{_readable(form.fields[name], form.initial.get(name))} → "
        f"{_readable(form.fields[name], form.cleaned_data.get(name))}"
        for name in form.changed_data
        if name in form.fields
    )
