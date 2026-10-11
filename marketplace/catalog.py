"""Itens oficiais da Deimos Corp publicados no marketplace (sincronizados após cada migrate)."""

from core.llm import default_model

from .toolsmith import ENTRYPOINT, RUNTIME, TEMPLATES, validate_tool

# Agentes oficiais usam o modelo padrão da instância; no chat, vale o que a chave de cada usuário alcança.
DEFAULT_MODEL = default_model()

OFFICIAL_TOOLS = ["verificar_headers_seguranca", "analisar_ip", "calcular_hash", "avaliar_senha"]

OFFICIAL_ITEMS = [
    {
        "kind": "agent",
        "slug": "analista_soc",
        "category": "security",
        "featured": True,
        "name": "Analista SOC",
        "summary": "Triagem de alertas e investigação de incidentes guiada por MITRE ATT&CK.",
        "description": (
            "Agente especializado em operações de segurança. Recebe alertas, correlaciona indicadores, "
            "classifica a severidade e sugere ações de contenção seguindo playbooks baseados em MITRE ATT&CK."
        ),
        "icon": "i-shield",
        "tags": ["soc", "incidentes", "blue team"],
        "manifest": {
            "model": DEFAULT_MODEL,
            "system_prompt": (
                "Você é um analista SOC sênior da Deimos Corp. Para cada alerta: extraia indicadores, "
                "verifique-os com as ferramentas disponíveis, mapeie táticas MITRE ATT&CK, classifique a "
                "severidade (baixa/média/alta/crítica) e proponha ações de contenção. Seja objetivo."
            ),
            "tools": ["analisar_ip", "verificar_headers_seguranca", "calcular_hash"],
        },
    },
    {
        "kind": "agent",
        "slug": "revisor_codigo_seguro",
        "category": "dev",
        "name": "Revisor de código seguro",
        "summary": "Revisa diffs procurando vulnerabilidades OWASP Top 10 e segredos expostos.",
        "description": (
            "Analisa trechos de código e pull requests em busca de injeções, falhas de autenticação, "
            "exposição de dados sensíveis e configurações inseguras, explicando o risco e a correção."
        ),
        "icon": "i-code",
        "tags": ["appsec", "owasp", "code review"],
        "manifest": {
            "model": DEFAULT_MODEL,
            "system_prompt": (
                "Você revisa código com foco em segurança. Aponte cada problema com arquivo/linha, "
                "categoria OWASP, impacto e uma correção concreta. Não invente problemas."
            ),
            "tools": ["calcular_hash"],
        },
    },
    {
        "kind": "agent",
        "slug": "pesquisador_tecnico",
        "category": "productivity",
        "name": "Pesquisador técnico",
        "summary": "Pesquisa e sintetiza documentação técnica em relatórios objetivos.",
        "description": "Agente generalista para pesquisa técnica, comparação de soluções e redação de relatórios.",
        "icon": "i-search",
        "tags": ["pesquisa", "relatórios"],
        "manifest": {
            "model": DEFAULT_MODEL,
            "system_prompt": "Você pesquisa temas técnicos e entrega sínteses claras, citando as fontes usadas.",
            "tools": [],
        },
    },
    {
        "kind": "workflow",
        "slug": "triagem_incidentes",
        "category": "security",
        "featured": True,
        "name": "Triagem de incidentes",
        "summary": "Do alerta ao relatório: coleta indicadores, classifica e gera o resumo executivo.",
        "description": "Workflow de ponta a ponta para triagem de alertas de segurança.",
        "icon": "i-radar",
        "tags": ["soc", "automação"],
        "manifest": {
            "trigger": "Novo alerta recebido",
            "steps": [
                {"name": "Extrair indicadores do alerta", "uses": "agent:analista_soc"},
                {"name": "Analisar IPs envolvidos", "uses": "tool:analisar_ip"},
                {"name": "Classificar severidade", "uses": "agent:analista_soc"},
                {"name": "Gerar resumo executivo", "uses": "skill:resumo_executivo"},
            ],
        },
    },
    {
        "kind": "workflow",
        "slug": "auditoria_web_lote",
        "category": "security",
        "name": "Auditoria web em lote",
        "summary": "Audita os headers de segurança de uma lista de domínios e consolida as notas.",
        "description": "Executa a verificação de headers para vários domínios e produz um ranking.",
        "icon": "i-globe",
        "tags": ["web", "auditoria"],
        "manifest": {
            "trigger": "Lista de domínios",
            "steps": [
                {"name": "Verificar headers de cada domínio", "uses": "tool:verificar_headers_seguranca"},
                {"name": "Consolidar notas e ausências", "uses": "agent:pesquisador_tecnico"},
                {"name": "Redigir recomendações", "uses": "skill:resumo_executivo"},
            ],
        },
    },
    {
        "kind": "skill",
        "slug": "modelagem_ameacas_stride",
        "category": "security",
        "name": "Modelagem de ameaças STRIDE",
        "summary": "Ensina o agente a modelar ameaças de um sistema usando STRIDE.",
        "description": "Conjunto de instruções para conduzir uma modelagem de ameaças estruturada.",
        "icon": "i-shield",
        "tags": ["threat modeling", "arquitetura"],
        "manifest": {
            "instructions": (
                "1. Peça o diagrama ou a descrição dos componentes e fluxos de dados.\n"
                "2. Para cada componente, avalie Spoofing, Tampering, Repudiation, Information disclosure, "
                "Denial of service e Elevation of privilege.\n"
                "3. Classifique cada ameaça por probabilidade e impacto.\n"
                "4. Proponha mitigações priorizadas e indique riscos residuais."
            ),
        },
    },
    {
        "kind": "skill",
        "slug": "resumo_executivo",
        "category": "communication",
        "name": "Resumo executivo",
        "summary": "Transforma achados técnicos em um resumo claro para a liderança.",
        "description": "Padroniza relatórios executivos: contexto, impacto, decisão necessária e próximos passos.",
        "icon": "i-book",
        "tags": ["comunicação", "relatórios"],
        "manifest": {
            "instructions": (
                "Escreva no máximo 200 palavras, nesta ordem: o que aconteceu, impacto para o negócio, "
                "decisão necessária e próximos passos com responsáveis. Evite jargão."
            ),
        },
    },
]


def official_tools():
    by_slug = {t["slug"]: t for t in TEMPLATES}
    for slug in OFFICIAL_TOOLS:
        spec = {k: v for k, v in by_slug[slug].items() if k != "keywords"}
        report = validate_tool(spec)
        assert not report["errors"], (slug, report["errors"])
        yield {
            "kind": "tool",
            "slug": slug,
            "name": spec["name"],
            "summary": spec["summary"],
            "description": spec["description"],
            "icon": spec["icon"],
            "category": spec["category"],
            "featured": slug == OFFICIAL_TOOLS[0],
            "tags": list(spec["tags"]),
            "manifest": {
                "input_schema": spec["input_schema"],
                "code": spec["code"],
                "entrypoint": ENTRYPOINT,
                "runtime": RUNTIME,
                "permissions": report["permissions"],
                "secrets": report["secrets"],
            },
        }


def sync_official_items(Item, ItemVersion):
    """Cria/atualiza os itens oficiais. Idempotente."""
    for data in [*official_tools(), *OFFICIAL_ITEMS]:
        defaults = {k: v for k, v in data.items() if k not in ("kind", "slug")}
        defaults.setdefault("featured", False)
        defaults.update(author="Deimos Corp", visibility="public")
        item, _ = Item.objects.update_or_create(
            source="official", kind=data["kind"], slug=data["slug"], defaults=defaults,
        )
        if item.approved_version != item.version:
            item.approved_version = item.version
            item.save(update_fields=["approved_version"])
        if not ItemVersion.objects.filter(item=item).exists():
            ItemVersion.objects.create(
                item=item, version=item.version, changelog="Versão inicial", manifest=item.manifest,
            )
