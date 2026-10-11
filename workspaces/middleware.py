from django.utils.functional import SimpleLazyObject

from .services import active_membership


class WorkspaceMiddleware:
    """Expõe request.membership e request.workspace (workspace ativo do usuário logado)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            request.membership = SimpleLazyObject(lambda: active_membership(request))
            request.workspace = SimpleLazyObject(lambda: request.membership.workspace)
        return self.get_response(request)
