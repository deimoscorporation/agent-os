"""
Geração de respostas do assistente.

Com credenciais (a chave de API do usuário), `generate_reply` chama a OpenAI e
repassa o texto em streaming. Sem credenciais, responde em modo de demonstração
explicando como configurar a chave.

Parâmetros:
    history      [{"role": "user" | "assistant", "content": str,
                   "attachments": [{"name", "kind": "image" | "file", "text", "path"}]}]
    agent        None ou {"name", "model", "system_prompt", "tools": [definições de tool use]}
    model        id do modelo a usar
    knowledge    None (base desligada) ou os trechos encontrados para a última pergunta:
                 [{"document", "text", "excerpt", ...}]
    credentials  core.llm.Credentials ou None
"""

import base64
import mimetypes
import os
import time
from collections.abc import Iterator

from core import llm

DEMO_DELAY = 0.025

MAX_HISTORY_MESSAGES = 40
MAX_FILE_CHARS = 20_000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024

SYSTEM_PROMPT = (
    "Você é o assistente do Deimos Agent OS, uma plataforma de agentes de IA da Deimos Corp, "
    "empresa de IA avançada e cibersegurança. Responda no idioma do usuário, de forma direta e "
    "precisa, usando Markdown quando ajudar. Se não souber ou não tiver certeza, diga isso."
)


def generate_reply(history: list[dict], agent: dict | None = None, model: str = "",
                   knowledge: list[dict] | None = None, credentials=None) -> Iterator[str]:
    if credentials is None:
        yield from _demo_stream(_demo_reply(history, agent, model, knowledge))
        return
    yield from llm.stream_chat(credentials.api_key, model, build_messages(history, agent, knowledge))


# --------------------------------------------------------------------------
# Montagem do contexto para o modelo
# --------------------------------------------------------------------------

def build_messages(history, agent=None, knowledge=None) -> list[dict]:
    """Converte a conversa para o formato de mensagens da API Chat Completions."""
    system = [agent["system_prompt"] if agent and agent["system_prompt"] else SYSTEM_PROMPT]
    if agent:
        system.append(f"Seu nome é {agent['name']}.")
        if agent["tools"]:
            names = ", ".join(t["name"] for t in agent["tools"])
            system.append(
                f"Suas ferramentas ({names}) ainda não podem ser executadas nesta plataforma. "
                "Não finja tê-las usado: diga qual usaria e o que ela faria."
            )
    if knowledge is not None:
        if knowledge:
            excerpts = "\n\n".join(f"[{k['document']}]\n{k['text']}" for k in knowledge)
            system.append(
                "Trechos da base de conhecimento do usuário relacionados à pergunta. Baseie a resposta "
                "neles, cite entre colchetes o nome do documento usado e, se eles não responderem à "
                f"pergunta, diga isso em vez de inventar.\n\n{excerpts}"
            )
        else:
            system.append(
                "A base de conhecimento do usuário foi consultada e não tem nada relacionado à pergunta. "
                "Avise isso antes de responder com conhecimento geral."
            )

    recent = history[-MAX_HISTORY_MESSAGES:]
    # Só as imagens mais recentes vão para o modelo, para não estourar o contexto.
    with_images = [
        (i, a) for i, m in enumerate(recent) for a in m.get("attachments") or [] if a["kind"] == "image"
    ][-MAX_IMAGES:]
    allowed = {(i, a["path"]) for i, a in with_images}

    messages = [{"role": "system", "content": "\n\n".join(system)}]
    for index, message in enumerate(recent):
        text = message["content"]
        images = []
        for attachment in message.get("attachments") or []:
            if attachment["kind"] == "image":
                url = _image_data_url(attachment["path"]) if (index, attachment["path"]) in allowed else None
                if url:
                    images.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    text += f"\n\n[Imagem anexada: {attachment['name']} — não enviada ao modelo]"
            elif attachment["text"]:
                body = attachment["text"][:MAX_FILE_CHARS]
                cut = " (início do arquivo)" if len(attachment["text"]) > MAX_FILE_CHARS else ""
                text += f"\n\n[Arquivo anexado: {attachment['name']}{cut}]\n{body}"
            else:
                text += f"\n\n[Arquivo anexado sem texto legível: {attachment['name']}]"
        text = text.strip() or "(mensagem sem texto)"
        content = [{"type": "text", "text": text}, *images] if images else text
        messages.append({"role": message["role"], "content": content})
    return messages


def _image_data_url(path):
    try:
        if os.path.getsize(path) > MAX_IMAGE_BYTES:
            return None
        with open(path, "rb") as handle:
            data = base64.b64encode(handle.read()).decode()
    except OSError:
        return None
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{data}"


# --------------------------------------------------------------------------
# Modo de demonstração (sem chave de API)
# --------------------------------------------------------------------------

def _demo_stream(reply):
    for word in reply.split(" "):
        yield word + " "
        time.sleep(DEMO_DELAY)


def _preview(prompt: str) -> str:
    preview = prompt.strip().replace("\n", " ")
    return preview[:140] + "…" if len(preview) > 140 else preview


def _demo_reply(history, agent, model, knowledge) -> str:
    last = history[-1] if history else {"content": "", "attachments": []}
    who = f"Sou **{agent['name']}** e recebi" if agent else "Recebi sua mensagem"
    parts = [
        f"{who}: **“{_preview(last['content']) or 'apenas anexos'}”**.",
        "**Nenhuma chave de API configurada.** Para receber respostas de verdade, cadastre a sua chave "
        f"da OpenAI em [Minha conta](/conta/#openai). O modelo escolhido é `{model}`; enquanto isso, esta "
        "resposta de demonstração mostra o que já chegaria pronto para ele.",
    ]

    attachments = last.get("attachments") or []
    if attachments:
        lines = []
        for a in attachments:
            if a["kind"] == "image":
                lines.append(f"- **{a['name']}** — imagem recebida (será enviada ao modelo com visão).")
            elif a["text"]:
                size = f"{len(a['text']):,}".replace(",", ".")
                lines.append(f"- **{a['name']}** — {size} caracteres lidos. Começa com: "
                             f"*{_preview(a['text'])[:120]}*")
            else:
                lines.append(f"- **{a['name']}** — recebido, mas sem texto extraível.")
        parts.append("**Anexos desta mensagem**\n\n" + "\n".join(lines))

    if knowledge is not None:
        if knowledge:
            quotes = "\n\n".join(f"> **{k['document']}** — {k['excerpt']}" for k in knowledge)
            parts.append(
                f"**Base de conhecimento** — encontrei {len(knowledge)} trecho"
                f"{'s' if len(knowledge) > 1 else ''} relacionado{'s' if len(knowledge) > 1 else ''} à sua pergunta:"
                f"\n\n{quotes}"
            )
        else:
            parts.append("**Base de conhecimento** — não encontrei nada relacionado à sua pergunta nos documentos.")

    if agent:
        if agent["tools"]:
            tool_lines = "\n".join(f"- `{t['name']}` — {t['description'].split('. ')[0]}." for t in agent["tools"])
            parts.append(f"**Ferramentas deste agente**\n\n{tool_lines}")
        else:
            parts.append("Este agente não tem ferramentas ativas no workspace.")
    return "\n\n".join(parts)
