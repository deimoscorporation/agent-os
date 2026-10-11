"""Utilitários das views JSON."""

import json

from django.http import Http404, JsonResponse


class ApiError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def api_view(fn):
    def wrapper(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except ApiError as exc:
            return JsonResponse({"error": str(exc), **exc.extra}, status=exc.status)
        except Http404:
            return JsonResponse({"error": "Não encontrado."}, status=404)
    wrapper.__name__ = fn.__name__
    return wrapper


def json_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        raise ApiError("JSON inválido.")
    if not isinstance(data, dict):
        raise ApiError("JSON inválido.")
    return data


def require_admin(request):
    if not request.membership.is_admin:
        raise ApiError("Apenas administradores do workspace podem fazer isso.", status=403)
