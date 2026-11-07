from functools import wraps

from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Allow access only to users whose role is in `roles`. Assumes login is already enforced."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def payroll_required(view_func):
    """Allow access only to users who may open «Xodimlar oyligi» — see
    `User.can_see_payroll`. Assumes login is already enforced."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.can_see_payroll:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper
