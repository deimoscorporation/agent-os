"""
Cliente da OpenAI (API Chat Completions) usando apenas a biblioteca padrão.

Cada usuário usa a própria chave de API. A ordem de busca é:
  1. a chave cadastrada pelo usuário em "Minha conta";
  2. o segredo OPENAI_API_KEY no cofre do workspace ativo (uma chave para a equipe);
  3. a variável de ambiente OPENAI_API_KEY da instância (opcional).

O endereço da API vem de settings.OPENAI_BASE_URL, então qualquer servidor
compatível com a API da OpenAI também funciona.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

TIMEOUT = 90
WORKSPACE_SECRET = "OPENAI_API_KEY"
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")

# /v1/models devolve de tudo; ficam só os modelos de conversa.
_CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
_NOT_CHAT = (
    "audio", "realtime", "tts", "transcribe", "whisper", "image", "embedding", "moderation",
    "search", "instruct", "dall-e", "codex", "computer-use",
)


class LLMError(Exception):
    """Falha ao falar com a OpenAI, com mensagem pronta para mostrar ao usuário."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass
class Credentials:
    api_key: str
    source: str  # "user", "workspace" ou "instance"
    models: list = field(default_factory=list)

    def pick_model(self, wanted=""):
        """O modelo pedido, se esta chave tiver acesso a ele; senão o padrão."""
        available = self.models or configured_models()
        return wanted if wanted in available else default_model(available)


def configured_models() -> list[str]:
    return list(settings.OPENAI_MODELS)


def default_model(models=None) -> str:
    models = models or configured_models()
    for preferred in configured_models():
        if preferred in models:
            return preferred
    return models[0]


def models_for(user) -> list[str]:
    """Modelos que aparecem nos seletores: os da chave do usuário, ou a lista padrão."""
    credential = getattr(user, "openai_credential", None) if user.is_authenticated else None
    return (credential and credential.available_models) or configured_models()


def credentials_for(user, workspace=None):
    """Credenciais para chamar a OpenAI em nome do usuário, ou None (modo demonstração)."""
    credential = getattr(user, "openai_credential", None)
    if credential:
        return Credentials(credential.reveal(), "user", credential.available_models)
    if workspace is not None:
        from workspaces.services import secrets_for

        key = secrets_for(workspace, [WORKSPACE_SECRET]).get(WORKSPACE_SECRET)
        if key:
            return Credentials(key, "workspace")
    if settings.OPENAI_API_KEY:
        return Credentials(settings.OPENAI_API_KEY, "instance")
    return None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _open(api_key, path, payload=None):
    request = Request(
        settings.OPENAI_BASE_URL.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        return urlopen(request, timeout=TIMEOUT)
    except HTTPError as exc:
        raise _from_http_error(exc, payload) from None
    except (URLError, TimeoutError, OSError):
        raise LLMError("Não foi possível conectar à OpenAI. Verifique a conexão do servidor.") from None


def _from_http_error(exc, payload):
    detail = ""
    try:
        detail = json.loads(exc.read().decode("utf-8", "replace"))["error"]["message"]
    except Exception:
        pass
    status = exc.code
    if status == 401:
        message = "A chave de API da OpenAI é inválida ou foi revogada."
    elif status == 403:
        message = "Esta chave de API não tem permissão para essa operação."
    elif status == 404:
        model = (payload or {}).get("model", "")
        message = f"O modelo “{model}” não está disponível para esta chave de API." if model else "Recurso não encontrado."
    elif status == 429:
        message = "Limite de uso ou cota da sua conta OpenAI atingido. Verifique o faturamento da conta."
    elif status >= 500:
        message = "A OpenAI está indisponível no momento. Tente de novo em instantes."
    else:
        message = f"A OpenAI recusou a requisição: {detail[:300]}" if detail else "A OpenAI recusou a requisição."
    return LLMError(message, status)


def list_models(api_key) -> list[str]:
    """Modelos de conversa a que a chave tem acesso. Serve também para validar a chave."""
    with _open(api_key, "/models") as response:
        data = json.load(response)
    ids = [m.get("id", "") for m in data.get("data", [])]
    chat = [
        i for i in ids
        if MODEL_ID_RE.match(i) and i.startswith(_CHAT_PREFIXES) and not any(word in i for word in _NOT_CHAT)
    ]
    preferred = [m for m in configured_models() if m in chat]
    return (preferred + sorted(set(chat) - set(preferred), reverse=True))[:40]


def stream_chat(api_key, model, messages):
    """Gera os pedaços de texto da resposta conforme chegam."""
    response = _open(api_key, "/chat/completions", {"model": model, "messages": messages, "stream": True})
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if event.get("error"):
                raise LLMError(f"A OpenAI interrompeu a resposta: {str(event['error'].get('message', ''))[:300]}")
            for choice in event.get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


def complete_json(api_key, model, system, user) -> dict:
    """Uma resposta única em JSON (usada para gerar ferramentas, agentes, skills e workflows)."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    with _open(api_key, "/chat/completions", payload) as response:
        data = json.load(response)
    try:
        result = json.loads(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise LLMError("O modelo devolveu uma resposta que não é JSON válido.") from None
    if not isinstance(result, dict):
        raise LLMError("O modelo devolveu uma resposta em formato inesperado.")
    return result
