from django.db.models import Sum
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.api import ApiError, api_view

from .extract import DOCUMENT_EXTENSIONS, TEXT_EXTENSIONS, UnsupportedFile, check_file, content_type_for
from .models import Document
from .search import index_document, search

MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_DOCUMENTS_PER_WORKSPACE = 200
ALLOWED = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS


def _can_delete(request, document):
    return request.membership.is_admin or document.uploaded_by_id == request.user.id


def index(request):
    documents = list(request.workspace.documents.select_related("uploaded_by"))
    for document in documents:
        document.can_delete = _can_delete(request, document)
    totals = request.workspace.documents.aggregate(chunks=Sum("chunk_count"), chars=Sum("char_count"))
    return render(request, "knowledge/index.html", {
        "section": "knowledge",
        "documents": documents,
        "chunks": totals["chunks"] or 0,
        "chars": totals["chars"] or 0,
        "extensions": ", ".join(sorted(f".{e}" for e in ("pdf", "docx", "txt", "md", "csv", "json"))),
        "max_mb": MAX_DOCUMENT_BYTES // (1024 * 1024),
    })


@require_POST
@api_view
def upload(request):
    upload_file = request.FILES.get("file")
    if upload_file is None:
        raise ApiError("Envie um arquivo.")
    if request.workspace.documents.count() >= MAX_DOCUMENTS_PER_WORKSPACE:
        raise ApiError(f"Limite de {MAX_DOCUMENTS_PER_WORKSPACE} documentos por workspace atingido.")
    try:
        check_file(upload_file, ALLOWED, MAX_DOCUMENT_BYTES)
        document = index_document(request.workspace, request.user, upload_file)
    except UnsupportedFile as exc:
        raise ApiError(str(exc))
    return JsonResponse({"id": str(document.id), "name": document.name, "chunks": document.chunk_count}, status=201)


@require_http_methods(["DELETE"])
@api_view
def document(request, pk):
    doc = get_object_or_404(Document, pk=pk, workspace=request.workspace)
    if not _can_delete(request, doc):
        raise ApiError("Só quem enviou o documento ou um admin pode excluí-lo.", status=403)
    doc.delete()
    return JsonResponse({"deleted": True})


@require_GET
def download(request, pk):
    doc = get_object_or_404(Document, pk=pk, workspace=request.workspace)
    response = FileResponse(doc.file.open("rb"), as_attachment=True, filename=doc.name,
                            content_type=content_type_for(doc.name))
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
@api_view
def search_api(request):
    """Testa a busca: o mesmo ranqueamento usado nas conversas."""
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        raise ApiError("Digite pelo menos 2 caracteres.")
    results = search(request.workspace, query[:500], limit=5)
    return JsonResponse({"results": [
        {k: r[k] for k in ("document_id", "document", "position", "excerpt", "score")} for r in results
    ]})
