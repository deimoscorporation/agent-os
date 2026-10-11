import json
import logging
import uuid

from django.db import transaction
from django.db.models import F, Q
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core import llm
from core.api import ApiError, api_view, json_body, require_admin
from workspaces.models import Membership
from workspaces.services import secret_names

from . import specs
from .drafts import draft_item
from .models import Installation, Item, PublicationRequest, Review
from .toolsmith import EXAMPLES, PARAM_TYPES, PERMISSION_LABELS, TOOL_ICONS, generate_tool, schema_params

logger = logging.getLogger(__name__)

KIND_ORDER = [Item.Kind.TOOL, Item.Kind.AGENT, Item.Kind.WORKFLOW, Item.Kind.SKILL]
KIND_PLURALS = {
    Item.Kind.TOOL: "Ferramentas",
    Item.Kind.AGENT: "Agentes",
    Item.Kind.WORKFLOW: "Workflows",
    Item.Kind.SKILL: "Skills",
}
Pending = PublicationRequest.Status.PENDING


def _kind(value):
    if value not in Item.Kind.values:
        raise ApiError("Tipo de item inválido.")
    return Item.Kind(value)


def _org_ids(user):
    return Membership.objects.filter(user=user).values("workspace_id")


def _visible_item(request, pk):
    """Público, do workspace ativo ou compartilhado em uma organização do usuário."""
    visible = (
        Q(visibility=Item.Visibility.PUBLIC)
        | Q(workspace=request.workspace)
        | Q(visibility=Item.Visibility.ORGANIZATION, workspace__in=_org_ids(request.user))
    )
    if request.user.is_staff:
        visible |= Q(publication_requests__status=Pending)
    item = Item.objects.filter(visible, pk=pk).distinct().first()
    if item is None:
        raise Http404
    return item


def _own_item(request, pk):
    return get_object_or_404(Item, pk=pk, workspace=request.workspace)


def _editable_item(request, pk):
    item = _own_item(request, pk)
    if not item.can_edit(request.membership):
        raise ApiError("Você só pode alterar itens que criou. Peça a um admin do workspace.", status=403)
    return item


def _workspace_refs(workspace, exclude=None):
    """Itens instalados no workspace, por tipo: {"tool": {slug: nome}, ...}."""
    refs = {k.value: {} for k in Item.Kind}
    installs = Installation.objects.filter(workspace=workspace).select_related("item")
    if exclude is not None:
        installs = installs.exclude(item=exclude)
    for inst in installs:
        refs[inst.item.kind][inst.item.slug] = inst.item.name
    return refs


def _full_report(workspace, kind, spec, exclude=None):
    refs = _workspace_refs(workspace, exclude)
    report = specs.validate_spec(kind, spec, refs)
    if isinstance(spec, dict) and spec.get("slug") in refs[kind]:
        report["errors"].append(f"Já existe {specs.KIND_NOUNS[kind]} “{spec['slug']}” no seu workspace.")
    report["permission_labels"] = {p: PERMISSION_LABELS[p] for p in report["permissions"]}
    stored = secret_names(workspace) if report["secrets"] else set()
    report["secrets_status"] = [{"name": n, "configured": n in stored} for n in report["secrets"]]
    return report


def _editor_url(item):
    return reverse("marketplace:item_edit", args=[item.id])


def _cancel_pending(item):
    item.publication_requests.filter(status=Pending).update(
        status=PublicationRequest.Status.CANCELED, decided_at=timezone.now(),
    )


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------

def catalog(request):
    workspace = request.workspace
    items = list(
        Item.objects.filter(
            Q(visibility=Item.Visibility.PUBLIC)
            | Q(visibility=Item.Visibility.ORGANIZATION, workspace__in=_org_ids(request.user))
        ).select_related("workspace")
    )
    installed = set(Installation.objects.filter(workspace=workspace).values_list("item_id", flat=True))
    kinds = [
        {"value": k.value, "label": KIND_PLURALS[k], "count": sum(1 for i in items if i.kind == k)}
        for k in KIND_ORDER
    ]
    return render(request, "marketplace/catalog.html", {
        "section": "marketplace",
        "items": items,
        "installed": installed,
        "kinds": kinds,
        "categories": Item.Category.choices,
        "total": len(items),
        "has_org_items": any(i.is_org_shared for i in items),
    })


def item_detail(request, pk):
    item = _visible_item(request, pk)
    workspace = request.workspace
    is_owner = item.is_owned_by(workspace)
    installation = Installation.objects.filter(workspace=workspace, item=item).first()
    reviews = list(item.reviews.select_related("user")[:30])

    # Cada um vê a versão que receberia; a equipe de revisão vê a versão enviada.
    shown_version = item.available_version(workspace)
    pending = item.pending_request()
    reviewing = bool(request.user.is_staff and pending and not is_owner)
    if reviewing:
        shown_version = pending.version
    manifest = item.manifest_at(shown_version)

    context = {
        "section": "marketplace",
        "item": item,
        "manifest": manifest,
        "shown_version": shown_version,
        "installation": installation,
        "is_owner": is_owner,
        "can_edit": item.can_edit(request.membership),
        "reviewing": pending if reviewing else None,
        "versions": item.versions.all()[:8],
        "reviews": reviews,
        "my_review": next((r for r in reviews if r.user_id == request.user.id), None),
        "can_review": installation is not None and not is_owner,
        "stars": range(1, 6),
    }
    if item.kind == Item.Kind.TOOL:
        stored = secret_names(workspace)
        context.update(
            params=schema_params(manifest.get("input_schema")),
            permissions=[PERMISSION_LABELS.get(p, p) for p in manifest.get("permissions", [])],
            tool_secrets=[{"name": n, "configured": n in stored} for n in manifest.get("secrets", [])],
            definition=json.dumps(item.tool_definition(shown_version), ensure_ascii=False, indent=2),
        )
    return render(request, "marketplace/item_detail.html", context)


def workspace(request):
    ws = request.workspace
    installs = list(Installation.objects.filter(workspace=ws).select_related("item"))
    stored = secret_names(ws)
    for inst in installs:
        inst.can_edit = inst.item.can_edit(request.membership)
        inst.missing_secrets = [n for n in inst.manifest.get("secrets", []) if n not in stored]
    groups = [
        {
            "kind": k.value,
            "label": KIND_PLURALS[k],
            "noun": k.label.lower(),
            "installs": [i for i in installs if i.item.kind == k],
        }
        for k in KIND_ORDER
    ]
    return render(request, "marketplace/workspace.html", {
        "section": "workspace",
        "groups": groups,
        "total": len(installs),
        "created": sum(1 for i in installs if i.item.is_owned_by(ws)),
        "updates": sum(1 for i in installs if i.update_available),
        "agent_tools": sum(1 for i in installs if i.item.kind == Item.Kind.TOOL and i.enabled),
    })


def item_new(request, kind):
    if kind not in Item.Kind.values:
        raise Http404
    return _render_editor(request, Item.Kind(kind), None)


def item_edit(request, pk):
    item = _own_item(request, pk)
    if not item.can_edit(request.membership):
        return redirect("marketplace:item_detail", pk=item.id)
    return _render_editor(request, Item.Kind(item.kind), item)


def _render_editor(request, kind, item):
    mode = "edit" if item else "create"
    context = {"section": "workspace", "mode": mode, "item": item, "kind": kind, "kind_label": kind.label,
               "categories": Item.Category.choices}
    config = {"mode": mode, "kind": kind.value, "categories": Item.Category.choices}
    if item:
        context.update(
            pending=item.pending_request(),
            last_rejection=item.publication_requests.filter(
                status=PublicationRequest.Status.REJECTED,
            ).order_by("-decided_at").first(),
        )

    if kind == Item.Kind.TOOL:
        config.update(item=specs.item_spec(item) if item else None, icons=TOOL_ICONS, types=PARAM_TYPES)
        context.update(examples=EXAMPLES, editor_config=config)
        return render(request, "marketplace/tool_editor.html", context)

    refs = _workspace_refs(request.workspace, exclude=item)
    models = llm.models_for(request.user)
    spec = specs.item_spec(item) if item else specs.blank_spec(kind, llm.default_model(models))
    if spec.get("model") and spec["model"] not in models:
        models = [spec["model"], *models]  # modelo escolhido por outra pessoa continua selecionável
    config.update(
        item=spec,
        icons=specs.ICONS,
        refs={k.value: [{"slug": s, "name": n} for s, n in sorted(refs[k].items())] for k in specs.REF_KINDS},
    )
    context.update(editor_config=config, models=models)
    return render(request, "marketplace/item_editor.html", context)


def review_queue(request):
    if not request.user.is_staff:
        raise Http404
    requests = PublicationRequest.objects.select_related("item", "item__workspace", "requested_by", "reviewer")
    return render(request, "marketplace/review_queue.html", {
        "section": "review",
        "pending": requests.filter(status=Pending),
        "decided": requests.exclude(status=Pending).order_by("-decided_at")[:20],
    })


# --------------------------------------------------------------------------
# API — marketplace e workspace
# --------------------------------------------------------------------------

@require_POST
@api_view
def install(request, pk):
    require_admin(request)
    workspace = request.workspace
    item = _visible_item(request, pk)
    if Installation.objects.filter(workspace=workspace, item=item).exists():
        return JsonResponse({"installed": True})
    if item.slug in _workspace_refs(workspace)[item.kind]:
        raise ApiError(f"Já existe {specs.KIND_NOUNS[item.kind]} “{item.slug}” no seu workspace.", status=409)
    with transaction.atomic():
        Installation.objects.create(
            workspace=workspace, item=item, installed_by=request.user,
            installed_version=item.available_version(workspace),
        )
        Item.objects.filter(pk=item.pk).update(install_count=F("install_count") + 1)
    return JsonResponse({"installed": True})


@require_POST
@api_view
def uninstall(request, pk):
    require_admin(request)
    item = get_object_or_404(Item, pk=pk)
    if item.is_owned_by(request.workspace):
        raise ApiError("Itens criados neste workspace são removidos excluindo o item.")
    with transaction.atomic():
        deleted, _ = Installation.objects.filter(workspace=request.workspace, item=item).delete()
        if deleted:
            Item.objects.filter(pk=item.pk, install_count__gt=0).update(install_count=F("install_count") - 1)
    return JsonResponse({"installed": False})


@require_http_methods(["PATCH"])
@api_view
def installation_update(request, pk):
    require_admin(request)
    installation = get_object_or_404(Installation, workspace=request.workspace, item_id=pk)
    enabled = json_body(request).get("enabled")
    if not isinstance(enabled, bool):
        raise ApiError("Informe enabled: true/false.")
    installation.enabled = enabled
    installation.save(update_fields=["enabled"])
    return JsonResponse({"enabled": installation.enabled})


@require_POST
@api_view
def installation_upgrade(request, pk):
    """Traz a instalação para a versão mais recente disponível para este workspace."""
    require_admin(request)
    installation = get_object_or_404(
        Installation.objects.select_related("item"), workspace=request.workspace, item_id=pk,
    )
    installation.installed_version = installation.available_version
    installation.save(update_fields=["installed_version"])
    return JsonResponse({"installed_version": installation.installed_version})


@require_GET
def workspace_tools(request):
    """Ferramentas ativas do workspace no formato de tool use — consumido por agentes."""
    installs = Installation.objects.filter(
        workspace=request.workspace, enabled=True, item__kind=Item.Kind.TOOL,
    ).select_related("item")
    return JsonResponse({"tools": [i.item.tool_definition(i.installed_version) for i in installs]})


@require_GET
def workspace_agents(request):
    """Agentes ativos do workspace, para escolher com quem conversar no chat."""
    installs = Installation.objects.filter(
        workspace=request.workspace, enabled=True, item__kind=Item.Kind.AGENT,
    ).select_related("item")
    return JsonResponse({"agents": [
        {"id": str(i.item.id), "name": i.item.name, "summary": i.item.summary, "model": i.manifest.get("model", "")}
        for i in installs
    ]})


@require_http_methods(["POST", "DELETE"])
@api_view
def review(request, pk):
    item = _visible_item(request, pk)
    if item.is_owned_by(request.workspace):
        raise ApiError("Você não pode avaliar um item do seu próprio workspace.", status=403)

    if request.method == "DELETE":
        Review.objects.filter(item=item, user=request.user).delete()
    else:
        if not Installation.objects.filter(workspace=request.workspace, item=item).exists():
            raise ApiError("Instale o item antes de avaliar.", status=403)
        data = json_body(request)
        rating, comment = data.get("rating"), data.get("comment", "")
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ApiError("Dê uma nota de 1 a 5.")
        if not isinstance(comment, str) or len(comment) > 1000:
            raise ApiError("O comentário deve ter no máximo 1000 caracteres.")
        Review.objects.update_or_create(
            item=item, user=request.user, defaults={"rating": rating, "comment": comment.strip()},
        )
    item.refresh_rating()
    return JsonResponse({"rating_avg": item.rating_avg, "rating_count": item.rating_count})


# --------------------------------------------------------------------------
# API — criação e edição de itens
# --------------------------------------------------------------------------

def _llm_call(request):
    """Função que chama o modelo com a chave do usuário, ou None se não houver chave."""
    credentials = llm.credentials_for(request.user, request.workspace)
    if credentials is None:
        return None
    model = credentials.pick_model()
    return lambda system, user: llm.complete_json(credentials.api_key, model, system, user)


def _description(request):
    description = str(json_body(request).get("description", "")).strip()
    if len(description) < 10:
        raise ApiError("Descreva com pelo menos 10 caracteres.")
    if len(description) > 2000:
        raise ApiError("A descrição deve ter no máximo 2000 caracteres.")
    return description


@require_POST
@api_view
def tool_generate(request):
    description = _description(request)
    workspace = request.workspace
    taken = set(_workspace_refs(workspace)[Item.Kind.TOOL])
    llm_call = _llm_call(request)

    def stream():
        try:
            for event in generate_tool(description, taken, llm_call):
                if event["type"] == "draft":
                    event["report"] = _full_report(workspace, Item.Kind.TOOL, event["draft"])
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception:
            logger.exception("Falha ao gerar ferramenta")
            yield json.dumps({"type": "error", "message": "Não foi possível gerar a ferramenta."}) + "\n"

    response = StreamingHttpResponse(stream(), content_type="application/x-ndjson; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_POST
@api_view
def item_draft(request):
    """Rascunho de agente, skill ou workflow a partir de uma descrição."""
    kind = _kind(json_body(request).get("kind"))
    if kind == Item.Kind.TOOL:
        raise ApiError("Ferramentas são geradas em /api/tools/generate/.")
    description = _description(request)
    workspace = request.workspace
    installed = [
        {"kind": i.item.kind, "slug": i.item.slug, "name": i.item.name, "summary": i.item.summary,
         "tags": i.item.tags}
        for i in Installation.objects.filter(workspace=workspace).select_related("item")
    ]
    taken = {i["slug"] for i in installed if i["kind"] == kind}
    spec, notice = draft_item(kind, description, installed, taken, _llm_call(request))
    if kind == Item.Kind.AGENT:
        spec["model"] = llm.default_model(llm.models_for(request.user))
    return JsonResponse({"spec": spec, "notice": notice, "report": _full_report(workspace, kind, spec)})


@require_POST
@api_view
def item_validate(request):
    data = json_body(request)
    kind = _kind(data.get("kind"))
    exclude = None
    if data.get("item_id"):
        try:
            item_id = uuid.UUID(str(data["item_id"]))
        except ValueError:
            raise ApiError("item_id inválido.")
        exclude = _own_item(request, item_id)
    return JsonResponse(_full_report(request.workspace, kind, data.get("spec"), exclude))


@require_POST
@api_view
def item_create(request):
    data = json_body(request)
    kind = _kind(data.get("kind"))
    spec = data.get("spec")
    workspace = request.workspace
    report = _full_report(workspace, kind, spec)
    if report["errors"]:
        raise ApiError("Corrija os erros antes de instalar.", report=report)
    item = Item(
        kind=kind, source=Item.Source.USER, workspace=workspace, created_by=request.user, install_count=1,
        author=workspace.name if workspace.is_organization else request.user.full_name,
    )
    specs.apply_spec(item, spec, report)
    with transaction.atomic():
        item.save()
        item.record_version("Versão inicial")
        Installation.objects.create(
            workspace=workspace, item=item, installed_by=request.user, installed_version=item.version,
        )
    return JsonResponse({"item": specs.item_spec(item), "url": _editor_url(item)}, status=201)


@require_http_methods(["PUT", "DELETE"])
@api_view
def item_api(request, pk):
    item = _editable_item(request, pk)
    if request.method == "DELETE":
        item.delete()
        return JsonResponse({"deleted": True})
    data = json_body(request)
    spec = data.get("spec")
    report = _full_report(request.workspace, Item.Kind(item.kind), spec, exclude=item)
    if report["errors"]:
        raise ApiError("Corrija os erros antes de salvar.", report=report)
    changelog = str(data.get("changelog") or "").strip()[:300] or "Atualização"
    specs.apply_spec(item, spec, report)
    item.bump_version()
    with transaction.atomic():
        item.save()
        item.record_version(changelog)
        # O workspace dono sempre usa a versão mais recente do que ele mesmo criou.
        Installation.objects.filter(workspace=request.workspace, item=item).update(installed_version=item.version)
    return JsonResponse({"item": specs.item_spec(item), "report": report})


# --------------------------------------------------------------------------
# API — compartilhamento e revisão
# --------------------------------------------------------------------------

@require_POST
@api_view
def item_share(request, pk):
    """Alterna entre privado e visível na organização (marketplace privado)."""
    require_admin(request)
    item = _own_item(request, pk)
    visibility = json_body(request).get("visibility")
    if visibility == Item.Visibility.ORGANIZATION:
        if not request.workspace.is_organization:
            raise ApiError("Só itens de uma organização podem ser compartilhados com a equipe.")
        if item.is_public:
            raise ApiError("O item já é público.")
    elif visibility == Item.Visibility.PRIVATE:
        # Sair do marketplace público também cancela qualquer revisão em andamento.
        _cancel_pending(item)
        item.approved_version = ""
    else:
        raise ApiError("Visibilidade inválida.")
    item.visibility = visibility
    item.save(update_fields=["visibility", "approved_version", "updated_at"])
    return JsonResponse({"visibility": item.visibility, "status": item.sharing_status()})


@require_http_methods(["POST", "DELETE"])
@api_view
def item_submit(request, pk):
    """Envia a versão atual para a fila de revisão do marketplace público (ou cancela o envio)."""
    require_admin(request)
    item = _own_item(request, pk)
    if request.method == "DELETE":
        _cancel_pending(item)
        return JsonResponse({"status": item.sharing_status()})
    if item.pending_request():
        raise ApiError("Já existe uma versão deste item em revisão.")
    if item.is_public and item.approved_version == item.version:
        raise ApiError("Esta versão já está publicada.")
    PublicationRequest.objects.create(item=item, version=item.version, requested_by=request.user)
    return JsonResponse({"status": item.sharing_status()}, status=201)


@require_POST
@api_view
def review_decide(request, pk):
    if not request.user.is_staff:
        raise Http404
    publication = get_object_or_404(PublicationRequest.objects.select_related("item"), pk=pk, status=Pending)
    data = json_body(request)
    decision = data.get("decision")
    note = str(data.get("note") or "").strip()[:500]
    if decision not in ("approve", "reject"):
        raise ApiError("Decisão inválida.")
    if decision == "reject" and not note:
        raise ApiError("Explique o motivo da rejeição para o autor.")

    item = publication.item
    with transaction.atomic():
        publication.status = (
            PublicationRequest.Status.APPROVED if decision == "approve" else PublicationRequest.Status.REJECTED
        )
        publication.reviewer = request.user
        publication.note = note
        publication.decided_at = timezone.now()
        publication.save()
        if decision == "approve":
            item.visibility = Item.Visibility.PUBLIC
            item.approved_version = publication.version
            item.save(update_fields=["visibility", "approved_version", "updated_at"])
    return JsonResponse({"status": publication.status})
