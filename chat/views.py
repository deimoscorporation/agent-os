import json
import secrets
from datetime import timedelta

from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core import llm
from core.api import ApiError, api_view, json_body
from knowledge.extract import (
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    TEXT_EXTENSIONS,
    UnsupportedFile,
    check_file,
    content_type_for,
    extract_text,
)
from knowledge.search import search
from marketplace.models import Installation, Item

from .models import Attachment, Conversation, Message
from .services import generate_reply

TITLE_MAX = 60
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5
ATTACHMENT_EXTENSIONS = IMAGE_EXTENSIONS | TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS
KNOWLEDGE_RESULTS = 4
ORPHAN_ATTACHMENT_AGE = timedelta(days=1)


def _get_conversation(request, pk):
    return get_object_or_404(Conversation, pk=pk, user=request.user)


def _installed_agent(workspace, agent_id):
    """Instalação ativa de um agente no workspace, ou None."""
    try:
        return Installation.objects.select_related("item").filter(
            workspace=workspace, item_id=agent_id, item__kind=Item.Kind.AGENT, enabled=True,
        ).first()
    except (ValueError, ValidationError):
        return None


def _agent_context(conversation, workspace):
    """Instruções e ferramentas do agente da conversa, prontas para o modelo."""
    if not conversation.agent_id:
        return None
    agent = conversation.agent
    installation = _installed_agent(workspace, agent.id)
    manifest = installation.manifest if installation else agent.manifest
    wanted = manifest.get("tools", [])
    tools = Installation.objects.filter(
        workspace=workspace, enabled=True, item__kind=Item.Kind.TOOL, item__slug__in=wanted,
    ).select_related("item")
    return {
        "name": agent.name,
        "model": manifest.get("model", ""),
        "system_prompt": manifest.get("system_prompt", ""),
        "tools": [t.item.tool_definition(t.installed_version) for t in tools],
    }


def _history(conversation):
    messages = conversation.messages.prefetch_related("attachments")
    return [
        {
            "role": m.role,
            "content": m.content,
            "attachments": [
                {"name": a.name, "kind": "image" if a.is_image else "file", "text": a.text, "path": a.file.path}
                for a in m.attachments.all()
            ],
        }
        for m in messages
    ]


def _stream_reply(conversation, request):
    workspace = request.workspace
    history = _history(conversation)
    agent = _agent_context(conversation, workspace)
    credentials = llm.credentials_for(request.user, workspace)
    wanted = (agent and agent["model"]) or conversation.model
    # Se a chave não tiver acesso ao modelo pedido (ex.: agente criado por outra pessoa), usa o padrão.
    model = credentials.pick_model(wanted) if credentials else wanted or llm.default_model(llm.models_for(request.user))

    knowledge = None
    if conversation.use_knowledge:
        question = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        knowledge = search(workspace, question, limit=KNOWLEDGE_RESULTS)
    sources = [
        {"document_id": k["document_id"], "document": k["document"], "excerpt": k["excerpt"]}
        for k in knowledge or []
    ]

    def stream():
        parts = []
        failed = False
        try:
            for chunk in generate_reply(history, agent=agent, model=model, knowledge=knowledge,
                                        credentials=credentials):
                parts.append(chunk)
                yield chunk
        except llm.LLMError as exc:
            # O erro aparece na conversa, mas não entra no histórico enviado ao modelo.
            failed = True
            yield ("\n\n" if parts else "") + f"**Não foi possível obter a resposta.** {exc}"
            if exc.status in (401, 403):
                yield " Revise a chave em [Minha conta](/conta/#openai)."
        finally:
            # Persiste mesmo se o cliente interromper a geração.
            content = "".join(parts).strip()
            if content and not failed:
                Message.objects.create(
                    conversation=conversation, role=Message.Role.ASSISTANT, content=content, sources=sources,
                )
                conversation.save(update_fields=["updated_at"])

    response = StreamingHttpResponse(stream(), content_type="text/plain; charset=utf-8")
    response["X-Conversation-Id"] = str(conversation.id)
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _detail(conversation):
    messages = conversation.messages.prefetch_related("attachments")
    return {"conversation": conversation.as_dict(), "messages": [m.as_dict() for m in messages]}


# --------------------------------------------------------------------------
# Páginas e conversas
# --------------------------------------------------------------------------

def index(request, pk=None):
    if pk is not None and not Conversation.objects.filter(pk=pk, user=request.user).exists():
        raise Http404
    models = llm.models_for(request.user)
    return render(request, "chat/index.html", {
        "initial_conversation": str(pk) if pk else "",
        "section": "chat",
        "models": models,
        "default_model": llm.default_model(models),
        "has_key": llm.credentials_for(request.user, request.workspace) is not None,
        "has_knowledge": request.workspace.documents.exists(),
    })


@require_GET
def conversation_list(request):
    conversations = Conversation.objects.filter(user=request.user).select_related("agent")[:100]
    return JsonResponse({"conversations": [c.as_dict() for c in conversations]})


@require_http_methods(["GET", "PATCH", "DELETE"])
def conversation_detail(request, pk):
    conversation = _get_conversation(request, pk)
    if request.method == "DELETE":
        conversation.delete()
        return JsonResponse({"ok": True})
    if request.method == "PATCH":
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return HttpResponseBadRequest("JSON inválido")
        fields = ["updated_at"]
        if "title" in data:
            title = str(data["title"] or "").strip()
            if not title:
                return HttpResponseBadRequest("Título vazio")
            conversation.title = title[:120]
            fields.append("title")
        if "model" in data:
            if data["model"] not in llm.models_for(request.user):
                return HttpResponseBadRequest("Modelo inválido")
            conversation.model = data["model"]
            fields.append("model")
        if "use_knowledge" in data:
            if not isinstance(data["use_knowledge"], bool):
                return HttpResponseBadRequest("use_knowledge deve ser true/false")
            conversation.use_knowledge = data["use_knowledge"]
            fields.append("use_knowledge")
        conversation.save(update_fields=fields)
    return JsonResponse(_detail(conversation))


@require_POST
def send_message(request):
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        return HttpResponseBadRequest("JSON inválido")
    text = (payload.get("message") or "").strip()

    attachment_ids = payload.get("attachment_ids") or []
    if not isinstance(attachment_ids, list) or len(attachment_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        return HttpResponseBadRequest(f"Envie no máximo {MAX_ATTACHMENTS_PER_MESSAGE} anexos por mensagem")
    try:
        # Só anexos do próprio usuário que ainda não foram usados em outra mensagem.
        attachments = list(Attachment.objects.filter(pk__in=attachment_ids, user=request.user, message__isnull=True))
    except (ValueError, ValidationError):
        return HttpResponseBadRequest("Anexo inválido")
    if len(attachments) != len(set(map(str, attachment_ids))):
        return HttpResponseBadRequest("Anexo não encontrado")
    if not text and not attachments:
        return HttpResponseBadRequest("Mensagem vazia")

    if payload.get("conversation_id"):
        conversation = _get_conversation(request, payload["conversation_id"])
    else:
        title = (text or attachments[0].name).replace("\n", " ")
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX].rstrip() + "…"
        agent = None
        if payload.get("agent_id"):
            installation = _installed_agent(request.workspace, payload["agent_id"])
            if installation is None:
                return HttpResponseBadRequest("Agente não disponível neste workspace")
            agent = installation.item
        model = payload.get("model") or ""
        if model and model not in llm.models_for(request.user):
            return HttpResponseBadRequest("Modelo inválido")
        conversation = Conversation.objects.create(
            user=request.user, title=title, agent=agent, model=model,
            use_knowledge=payload.get("use_knowledge") is True,
        )

    message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=text)
    Attachment.objects.filter(pk__in=[a.pk for a in attachments]).update(message=message)
    return _stream_reply(conversation, request)


@require_POST
def regenerate(request, pk):
    conversation = _get_conversation(request, pk)
    last = conversation.messages.last()
    if last and last.role == Message.Role.ASSISTANT:
        last.delete()
    if not conversation.messages.exists():
        return HttpResponseBadRequest("Nada para regenerar")
    return _stream_reply(conversation, request)


# --------------------------------------------------------------------------
# Anexos
# --------------------------------------------------------------------------

@require_POST
@api_view
def attachment_upload(request):
    upload = request.FILES.get("file")
    if upload is None:
        raise ApiError("Envie um arquivo.")
    try:
        check_file(upload, ATTACHMENT_EXTENSIONS, MAX_ATTACHMENT_BYTES)
        text = extract_text(upload, upload.name)
    except UnsupportedFile as exc:
        raise ApiError(str(exc))
    # Anexos enviados e nunca usados em uma mensagem não ficam acumulando no disco.
    Attachment.objects.filter(
        user=request.user, message__isnull=True, created_at__lt=timezone.now() - ORPHAN_ATTACHMENT_AGE,
    ).delete()
    attachment = Attachment.objects.create(
        user=request.user, name=upload.name[:200], file=upload, size=upload.size, text=text,
    )
    return JsonResponse(attachment.as_dict(), status=201)


@require_http_methods(["GET", "DELETE"])
def attachment(request, pk):
    """Os arquivos nunca são servidos direto do disco: só o dono acessa."""
    item = get_object_or_404(Attachment, pk=pk, user=request.user)
    if request.method == "DELETE":
        if item.message_id:
            return HttpResponseBadRequest("O anexo já faz parte de uma mensagem")
        item.delete()
        return JsonResponse({"deleted": True})
    # Só imagens abrem no navegador; o resto é sempre download.
    response = FileResponse(
        item.file.open("rb"), as_attachment=not item.is_image, filename=item.name,
        content_type=content_type_for(item.name) if item.is_image else "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, max-age=3600"
    return response


# --------------------------------------------------------------------------
# Compartilhar e exportar
# --------------------------------------------------------------------------

@require_http_methods(["POST", "DELETE"])
@api_view
def share(request, pk):
    conversation = _get_conversation(request, pk)
    if request.method == "DELETE":
        conversation.share_token = None
    elif not conversation.share_token:
        conversation.share_token = secrets.token_urlsafe(24)
    conversation.save(update_fields=["share_token"])
    url = request.build_absolute_uri(f"/s/{conversation.share_token}/") if conversation.share_token else None
    return JsonResponse({"shared": bool(conversation.share_token), "url": url})


def _speaker(conversation, message):
    if message.role == Message.Role.USER:
        return "Você"
    return conversation.agent.name if conversation.agent_id else "Assistente Deimos"


@login_not_required
@require_GET
def shared(request, token):
    """Página pública, somente leitura. Anexos aparecem só pelo nome: os arquivos continuam privados."""
    conversation = get_object_or_404(Conversation.objects.select_related("user", "agent"), share_token=token)
    messages = [
        {
            "role": m.role,
            "speaker": "Autor" if m.role == Message.Role.USER else _speaker(conversation, m),
            "content": m.content,
            "attachments": [a.name for a in m.attachments.all()],
            "sources": [s["document"] for s in m.sources],
        }
        for m in conversation.messages.prefetch_related("attachments")
    ]
    response = render(request, "chat/shared.html", {
        "conversation": conversation,
        "shared_messages": messages,
        "author": conversation.user.get_short_name(),
    })
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_GET
def export(request, pk, fmt):
    conversation = _get_conversation(request, pk)
    messages = list(conversation.messages.prefetch_related("attachments"))
    filename = slugify(conversation.title)[:60] or "conversa"

    if fmt == "json":
        data = {
            "title": conversation.title,
            "exported_at": timezone.now().isoformat(),
            "agent": conversation.agent.name if conversation.agent_id else None,
            "model": conversation.model or llm.default_model(llm.models_for(request.user)),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat(),
                    "attachments": [a.name for a in m.attachments.all()],
                    "sources": m.sources,
                }
                for m in messages
            ],
        }
        body, content_type = json.dumps(data, ensure_ascii=False, indent=2), "application/json; charset=utf-8"
    elif fmt == "md":
        lines = [
            f"# {conversation.title}",
            "",
            f"_Exportado do Deimos Agent OS em {timezone.localtime().strftime('%d/%m/%Y %H:%M')}_",
        ]
        for m in messages:
            lines += ["", f"## {_speaker(conversation, m)}", "", m.content]
            names = [a.name for a in m.attachments.all()]
            if names:
                lines += ["", "**Anexos:** " + ", ".join(names)]
            if m.sources:
                lines += ["", "**Fontes:** " + ", ".join(dict.fromkeys(s["document"] for s in m.sources))]
        body, content_type = "\n".join(lines) + "\n", "text/markdown; charset=utf-8"
    else:
        raise Http404

    response = HttpResponse(body, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}.{fmt}"'
    return response
