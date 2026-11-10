"""The seller an admin/manager is currently working as — the «Sotuvchi» switch in the
top bar.

Writing for a seller used to mean picking them again on every form; someone entering
a whole day of Kamola's sales picked Kamola forty times. The switch is picked once and
kept in the session, and every form that asks whose name a record goes in (sale,
qarzdor, chiqim, new mijoz) opens with that seller already chosen — still changeable
on the form itself for the odd one that belongs to someone else."""

from .forms import sellers_queryset

SESSION_KEY = "acting_seller"


def current_acting_seller(request):
    """The seller picked in the top bar, or None — also when the user may not act for
    sellers at all, or the one picked has since been deactivated."""
    user = request.user
    if not user.is_authenticated or not user.can_see_all_records:
        return None
    pk = request.session.get(SESSION_KEY)
    if not pk:
        return None
    return sellers_queryset().filter(pk=pk).first()


def acting_seller(request):
    """Template context for the top-bar switch: the sellers to offer and the one
    picked. Empty for sellers, who only ever write in their own name."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.can_see_all_records:
        return {}
    current = current_acting_seller(request)
    return {
        "acting_sellers": sellers_queryset(),
        "acting_seller_id": current.pk if current else None,
    }
