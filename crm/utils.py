"""Helpers for rendering create/edit forms either full-page or inside a modal."""

from django.http import HttpResponse
from django.shortcuts import redirect, render


def is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def form_response(request, form, title, invalid=False):
    """Render the modal partial for AJAX requests, otherwise the full page."""
    context = {"form": form, "title": title}
    if is_ajax(request):
        status = 422 if invalid else 200
        return render(request, "_modal.html", context, status=status)
    return render(request, "crm/form.html", context)


def form_success(request, url):
    """Tell an AJAX modal to redirect; otherwise do a normal redirect."""
    if is_ajax(request):
        response = HttpResponse(status=204)
        response["X-Redirect"] = url
        return response
    return redirect(url)
