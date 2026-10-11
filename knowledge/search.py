"""
Busca na base de conhecimento.

Ranqueamento BM25 por palavras-chave, calculado em memória sobre os trechos do
workspace — suficiente para bases pequenas e sem dependências. Para bases
grandes ou busca semântica, troque `search` por uma consulta a um índice de
embeddings mantendo o mesmo formato de retorno.
"""

import math
from collections import Counter

from .extract import chunk_text, extract_text, tokens
from .models import Chunk, Document

K1 = 1.5
B = 0.75
MAX_CHUNKS_SCANNED = 20_000
EXCERPT_CHARS = 320


def index_document(workspace, user, upload) -> Document:
    """Extrai o texto, divide em trechos e grava. Levanta UnsupportedFile se não houver texto."""
    from .extract import UnsupportedFile

    text = extract_text(upload, upload.name)
    if len(text) < 20:
        raise UnsupportedFile("Não encontrei texto neste arquivo (PDFs escaneados não são lidos).")
    pieces = chunk_text(text)
    document = Document.objects.create(
        workspace=workspace, uploaded_by=user, name=upload.name[:200], file=upload,
        size=upload.size, char_count=len(text), chunk_count=len(pieces),
    )
    Chunk.objects.bulk_create(
        Chunk(document=document, position=n, text=piece) for n, piece in enumerate(pieces)
    )
    return document


def search(workspace, query: str, limit: int = 4) -> list[dict]:
    """Trechos mais relevantes: [{"document_id", "document", "position", "text", "excerpt", "score"}]."""
    terms = list(dict.fromkeys(tokens(query)))
    if not terms:
        return []
    rows = list(
        Chunk.objects.filter(document__workspace=workspace)
        .values_list("id", "text", "position", "document_id", "document__name")[:MAX_CHUNKS_SCANNED]
    )
    if not rows:
        return []

    counted = [(row, Counter(tokens(row[1]))) for row in rows]
    lengths = [sum(c.values()) for _, c in counted]
    average = (sum(lengths) / len(lengths)) or 1
    total = len(counted)
    idf = {}
    for term in terms:
        df = sum(1 for _, counts in counted if term in counts)
        idf[term] = math.log(1 + (total - df + 0.5) / (df + 0.5)) if df else 0

    scored = []
    for (row, counts), length in zip(counted, lengths):
        score = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if tf:
                score += idf[term] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda pair: -pair[0])
    return [
        {
            "document_id": str(document_id),
            "document": name,
            "position": position,
            "text": text,
            "excerpt": _excerpt(text, terms),
            "score": round(score, 3),
        }
        for score, (_, text, position, document_id, name) in scored[:limit]
    ]


def _excerpt(text: str, terms: list[str]) -> str:
    """Recorte do trecho em torno da primeira palavra buscada."""
    flat = " ".join(text.split())
    if len(flat) <= EXCERPT_CHARS:
        return flat
    lowered = flat.lower()
    hits = [lowered.find(t) for t in terms if lowered.find(t) >= 0]
    start = max(0, (min(hits) if hits else 0) - EXCERPT_CHARS // 4)
    start = flat.rfind(" ", 0, start) + 1 if start else 0
    piece = flat[start:start + EXCERPT_CHARS]
    return ("… " if start else "") + piece.rsplit(" ", 1)[0] + " …"
