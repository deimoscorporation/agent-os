"""
Rascunhos de agentes, skills e workflows a partir de uma descrição.

Com a chave de API do usuário, o modelo escreve o rascunho; a resposta é
filtrada para conter só itens realmente instalados no workspace. Sem chave (ou
se o modelo falhar), o rascunho é montado por regras simples.
"""

from .models import Item
from core.llm import LLMError

from .specs import blank_spec
from .toolsmith import CATEGORIES, STOPWORDS, _tokens, slugify, unique_slug

CATEGORY_KEYWORDS = {
    "security": {"seguranca", "vulnerabilidade", "vulnerabilidades", "ameaca", "ameacas", "incidente", "incidentes",
                 "ataque", "soc", "auditoria", "auditar", "senha", "malware", "phishing", "risco", "riscos"},
    "dev": {"codigo", "bug", "bugs", "deploy", "api", "teste", "testes", "revisar", "review", "git", "programacao"},
    "data": {"dados", "planilha", "relatorio", "relatorios", "metricas", "sql", "csv", "analise", "dashboard"},
    "communication": {"email", "emails", "resumo", "resumir", "comunicado", "mensagem", "cliente", "clientes", "texto"},
    "productivity": {"tarefa", "tarefas", "agenda", "organizar", "pesquisa", "pesquisar", "planejar", "documento"},
}

EXTRA_STOPWORDS = {"agente", "skill", "workflow", "fluxo", "ajuda", "ajudar", "usando", "usar", "capaz"}


def draft_item(kind, description, installed, taken_slugs=frozenset(), llm_call=None):
    """Devolve (spec, aviso). Com `llm_call`, o modelo do usuário escreve o rascunho.

    installed: itens do workspace como dicts {"kind", "slug", "name", "summary", "tags"}.
    Se o modelo falhar, cai no rascunho por regras e devolve um aviso para a interface.
    """
    spec = _draft_by_rules(kind, description, installed, taken_slugs)
    if llm_call is None:
        return spec, None
    try:
        data = llm_call(_llm_prompt(kind, installed), description)
    except LLMError as exc:
        return spec, f"O modelo não conseguiu gerar o rascunho ({exc}) Usei um ponto de partida padrão."
    return _merge_llm(kind, spec, data, installed, taken_slugs), None


def _llm_prompt(kind, installed):
    def listing(k):
        items = [f"{k}:{i['slug']} — {i['name']}: {i['summary']}" for i in installed if i["kind"] == k]
        return "\n".join(items) or "(nenhum instalado)"

    common = (
        f"Você projeta um item do tipo “{Item.Kind(kind).label}” para a plataforma Deimos Agent OS a partir "
        "da descrição do usuário. Responda somente com JSON, em português, com os campos: "
        '"name" (curto), "slug" (snake_case), "summary" (até 160 caracteres), "description", '
        f'"category" (um de: {", ".join(CATEGORIES)}), "tags" (até 5)'
    )
    if kind == Item.Kind.AGENT:
        return (
            f'{common}, "system_prompt" (instruções completas do agente: papel, objetivos, regras e formato '
            'das respostas) e "tools" (lista de slugs, escolhidos SOMENTE entre as ferramentas abaixo; '
            f"vazia se nenhuma servir).\n\nFerramentas instaladas:\n{listing('tool')}"
        )
    if kind == Item.Kind.SKILL:
        return f'{common} e "instructions" (passo a passo, critérios de qualidade e formato de saída).'
    return (
        f'{common}, "trigger" (o que inicia o workflow) e "steps" (lista ordenada de {{"name", "uses"}}, '
        'em que "uses" é exatamente um dos identificadores abaixo, no formato tipo:slug).\n\n'
        f"Itens instalados:\n{listing('tool')}\n{listing('agent')}\n{listing('skill')}"
    )


def _merge_llm(kind, spec, data, installed, taken_slugs):
    """Aproveita da resposta do modelo só o que é válido; o resto fica do rascunho por regras."""
    def text(key, limit):
        value = data.get(key)
        return value.strip()[:limit] if isinstance(value, str) and value.strip() else None

    for key, limit in (("name", 80), ("summary", 160), ("description", 4000)):
        spec[key] = text(key, limit) or spec[key]
    spec["slug"] = unique_slug(slugify(str(data.get("slug") or spec["name"]), spec["slug"]), taken_slugs)
    if data.get("category") in CATEGORIES:
        spec["category"] = data["category"]
    if isinstance(data.get("tags"), list):
        spec["tags"] = [str(t).strip()[:24] for t in data["tags"] if str(t).strip()][:5]

    available = {f"{i['kind']}:{i['slug']}" for i in installed}
    if kind == Item.Kind.AGENT:
        spec["system_prompt"] = text("system_prompt", 20_000) or spec["system_prompt"]
        tools = data.get("tools") if isinstance(data.get("tools"), list) else []
        spec["tools"] = list(dict.fromkeys(t for t in tools if f"tool:{t}" in available))
    elif kind == Item.Kind.SKILL:
        spec["instructions"] = text("instructions", 20_000) or spec["instructions"]
    else:
        spec["trigger"] = text("trigger", 200) or spec["trigger"]
        steps = data.get("steps") if isinstance(data.get("steps"), list) else []
        valid = [
            {"name": str(s.get("name") or "").strip()[:120], "uses": s.get("uses")}
            for s in steps
            if isinstance(s, dict) and s.get("uses") in available and not str(s.get("uses")).startswith("workflow:")
        ]
        spec["steps"] = [s for s in valid if s["name"]][:20] or spec["steps"]
    return spec


def _draft_by_rules(kind, description, installed, taken_slugs=frozenset()) -> dict:
    clean = " ".join(description.split())
    tokens = [t for t in _tokens(clean) if t not in STOPWORDS and t not in EXTRA_STOPWORDS and not t.isdigit()]
    words = tokens[:3]
    slug = "_".join(words)[:64]
    if len(slug) < 3 or not slug[0].isalpha():
        slug = f"novo_{kind}"

    spec = blank_spec(kind)
    spec.update(
        name=" ".join(words).capitalize() or f"Novo {Item.Kind(kind).label.lower()}",
        slug=unique_slug(slug, taken_slugs),
        summary=clean[:157] + "…" if len(clean) > 158 else clean,
        description=clean,
        category=_guess_category(set(tokens)),
        tags=words[:2],
    )

    matches = {k: _match(set(tokens), [i for i in installed if i["kind"] == k]) for k in ("tool", "agent", "skill")}
    if kind == Item.Kind.AGENT:
        tools = [t["slug"] for t in matches["tool"][:5]]
        spec.update(tools=tools, system_prompt=_agent_prompt(spec["name"], clean, tools))
    elif kind == Item.Kind.SKILL:
        spec["instructions"] = _skill_instructions(clean)
    else:
        spec.update(trigger="Quando solicitado", steps=_workflow_steps(matches, installed))
    return spec


def _guess_category(tokens):
    scores = {category: len(tokens & words) for category, words in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def _match(tokens, items):
    """Itens cujo nome/resumo/tags compartilham palavras com a descrição, do mais ao menos aderente."""
    scored = []
    for item in items:
        haystack = set(_tokens(" ".join([item["name"], item["slug"].replace("_", " "), item["summary"], *item["tags"]])))
        score = len(tokens & haystack)
        if score:
            scored.append((score, item))
    return [item for _, item in sorted(scored, key=lambda pair: -pair[0])]


def _agent_prompt(name, description, tools):
    tool_line = (
        f"Ferramentas disponíveis: {', '.join(tools)}. Use-as sempre que precisar de dados reais."
        if tools else "Você não tem ferramentas: trabalhe apenas com o que o usuário fornecer."
    )
    return (
        f"Você é {name}, um agente da plataforma Deimos.\n\n"
        f"Objetivo\n{description}\n\n"
        "Como trabalhar\n"
        "1. Entenda o pedido e pergunte o que estiver faltando antes de agir.\n"
        f"2. {tool_line}\n"
        "3. Explique o raciocínio de forma objetiva e diga de onde veio cada informação.\n"
        "4. Se não tiver certeza, diga isso em vez de supor.\n\n"
        "Formato da resposta\n"
        "Comece pela conclusão; depois detalhe em tópicos curtos."
    )


def _skill_instructions(description):
    return (
        f"Objetivo\n{description}\n\n"
        "Passo a passo\n"
        "1. Levante o contexto necessário e confirme as premissas.\n"
        "2. Execute a tarefa seguindo os critérios abaixo.\n"
        "3. Revise o resultado antes de entregar.\n\n"
        "Critérios de qualidade\n"
        "- Clareza: frases curtas, sem jargão desnecessário.\n"
        "- Precisão: não invente dados; sinalize incertezas.\n"
        "- Utilidade: termine com próximos passos concretos.\n\n"
        "Formato de saída\n"
        "Título, resumo em até três linhas e lista de pontos principais."
    )


def _workflow_steps(matches, installed):
    steps = [{"name": f"Executar {t['name']}", "uses": f"tool:{t['slug']}"} for t in matches["tool"][:3]]
    for kind, verb in (("agent", "Analisar com"), ("skill", "Aplicar")):
        candidates = matches[kind] or [i for i in installed if i["kind"] == kind]
        if candidates:
            steps.append({"name": f"{verb} {candidates[0]['name']}", "uses": f"{kind}:{candidates[0]['slug']}"})
    return steps
