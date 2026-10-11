from .services import user_workspaces


def workspace(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    context = {
        "workspace": request.workspace,
        "membership": request.membership,
        "workspaces": user_workspaces(request.user),
    }
    if request.user.is_staff:
        from marketplace.models import PublicationRequest

        context["pending_reviews"] = PublicationRequest.objects.filter(
            status=PublicationRequest.Status.PENDING,
        ).count()
    return context
