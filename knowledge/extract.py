"""Extração de texto de arquivos enviados (anexos do chat e base de conhecimento)."""

import html
import io
import re
import unicodedata
import zipfile

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "log", "yaml", "yml", "xml", "html", "htm",
    "py", "js", "ts", "sql", "sh", "ini", "toml", "rst", "css",
}
DOCUMENT_EXTENSIONS = {"pdf", "docx"}

CONTENT_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
# Assinaturas dos formatos binários: a extensão sozinha não basta.
MAGIC = {
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "gif": [b"GIF87a", b"GIF89a"],
    "webp": [b"RIFF"],
    "pdf": [b"%PDF-"],
    "docx": [b"PK\x03\x04"],
}

MAX_TEXT_CHARS = 400_000
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150


class UnsupportedFile(Exception):
    pass


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(extension(name), "text/plain; charset=utf-8")


def check_file(upload, allowed, max_bytes):
    """Valida extensão, tamanho e assinatura. Devolve a extensão."""
    ext = extension(upload.name)
    if ext not in allowed:
        raise UnsupportedFile(f"Tipo de arquivo não suportado (.{ext or '?'}).")
    if upload.size > max_bytes:
        raise UnsupportedFile(f"Arquivo grande demais (máximo de {max_bytes // (1024 * 1024)} MB).")
    if upload.size == 0:
        raise UnsupportedFile("O arquivo está vazio.")
    head = upload.read(12)
    upload.seek(0)
    signatures = MAGIC.get(ext)
    if signatures and not any(head.startswith(s) for s in signatures):
        raise UnsupportedFile("O conteúdo do arquivo não corresponde à extensão.")
    if ext == "webp" and head[8:12] != b"WEBP":
        raise UnsupportedFile("O conteúdo do arquivo não corresponde à extensão.")
    if ext in TEXT_EXTENSIONS and b"\x00" in head and head[:2] not in (b"\xff\xfe", b"\xfe\xff"):
        raise UnsupportedFile("O arquivo não parece ser texto.")
    return ext


def extract_text(upload, name: str) -> str:
    """Texto do arquivo, ou "" quando não há o que extrair (imagens, PDFs escaneados)."""
    ext = extension(name)
    upload.seek(0)
    try:
        if ext in TEXT_EXTENSIONS:
            text = _decode(upload.read())
        elif ext == "pdf":
            text = _pdf_text(upload)
        elif ext == "docx":
            text = _docx_text(upload)
        else:
            text = ""
    except UnsupportedFile:
        raise
    except Exception as exc:  # arquivo corrompido, PDF protegido etc.
        raise UnsupportedFile("Não foi possível ler o conteúdo do arquivo.") from exc
    finally:
        upload.seek(0)
    text = text.replace("\x00", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_TEXT_CHARS]


def _decode(data: bytes) -> str:
    # UTF-16 só quando há BOM: sem ele, texto latin-1 seria "decodificado" como lixo.
    encodings = ["utf-16"] if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ["utf-8-sig", "latin-1"]
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _pdf_text(upload) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(upload.read()))
    if reader.is_encrypted:
        raise UnsupportedFile("O PDF está protegido por senha.")
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(upload) -> str:
    with zipfile.ZipFile(io.BytesIO(upload.read())) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Divide em trechos de ~`size` caracteres, respeitando parágrafos e com sobreposição."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        while len(paragraph) > size:  # parágrafo gigante: corta em frases/palavras
            cut = max(paragraph.rfind(". ", 0, size), paragraph.rfind(" ", 0, size))
            cut = cut + 1 if cut > size // 2 else size
            paragraphs_piece, paragraph = paragraph[:cut].strip(), paragraph[cut:].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraphs_piece)
        if current and len(current) + len(paragraph) + 2 > size:
            chunks.append(current)
            current = current[-overlap:].split(" ", 1)[-1] if overlap else ""
        current = f"{current}\n\n{paragraph}".strip() if current else paragraph
    if current:
        chunks.append(current)
    return chunks


STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "com", "para", "por", "que", "se", "ao", "aos", "ou", "como", "mais", "mas", "foi", "ser", "sao", "tem",
    "qual", "quais", "quando", "onde", "sobre", "isso", "esta", "este", "essa", "esse", "the", "and", "of", "to",
    "in", "is", "for", "me", "meu", "minha", "eu", "voce", "ele", "ela", "nao", "sim", "ja", "pelo", "pela",
}


def tokens(text: str) -> list[str]:
    """Palavras normalizadas (sem acento, minúsculas), sem stopwords."""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c)).lower()
    return [t for t in re.findall(r"[a-z0-9]+", normalized) if len(t) > 1 and t not in STOPWORDS]
