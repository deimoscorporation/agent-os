"""
Especificações editáveis de cada tipo de item.

Uma spec é o que o editor envia: campos de identidade (name, slug, summary,
description, icon, category, tags) mais os campos do tipo. Aqui ficam a
validação, a conversão spec -> manifest e o caminho inverso.
"""

import re

from core import llm

from . import toolsmith
from .models import Item

ICONS = toolsmith.TOOL_ICONS + ["i-bot", "i-workflow", "i-book", "i-search", "i-sparkles", "i-chat"]

# Tipos que um agente ou workflow pode referenciar.
REF_KINDS = [Item.Kind.TOOL, Item.Kind.AGENT, Item.Kind.SKILL]
USES_RE = re.compile(r"^(tool|agent|skill):([a-z][a-z0-9_]{2,63})$")
MAX_TEXT = 20_000
MAX_STEPS = 20

KIND_NOUNS = {
    Item.Kind.TOOL: "uma ferramenta",
    Item.Kind.AGENT: "um agente",
    Item.Kind.WORKFLOW: "um workflow",
    Item.Kind.SKILL: "uma skill",
}

BLANK_SPECS = {
    Item.Kind.AGENT: {
        "icon": "i-bot",
        "model": "",  # preenchido em blank_spec com o modelo padrão
        "system_prompt": "",
        "tools": [],
    },
    Item.Kind.SKILL: {"icon": "i-book", "instructions": ""},
    Item.Kind.WORKFLOW: {"icon": "i-workflow", "trigger": "", "steps": []},
}


def blank_spec(kind, model=""):
    base = {"name": "", "slug": "", "summary": "", "description": "", "category": "other", "tags": []}
    spec = base | BLANK_SPECS[kind]
    if kind == Item.Kind.AGENT:
        spec["model"] = model or llm.default_model()
    return spec


def validate_spec(kind, spec, refs) -> dict:
    """refs: {"tool": {slug, ...}, "agent": {...}, "skill": {...}} instalados no workspace."""
    if kind == Item.Kind.TOOL:
        report = toolsmith.validate_tool(spec)
    elif not isinstance(spec, dict):
        report = {"errors": ["Especificação inválida."], "warnings": [], "permissions": [], "secrets": []}
    else:
        errors, warnings = [], []
        toolsmith.check_common(spec, errors, ICONS)
        _CHECKS[kind](spec, refs, errors, warnings)
        report = {"errors": errors, "warnings": warnings, "permissions": [], "secrets": []}

    if isinstance(spec, dict) and spec.get("category") not in Item.Category.values:
        report["errors"].append("Escolha uma categoria.")
    return report


def _text(spec, field, label, errors, minimum=20):
    value = spec.get(field)
    if not isinstance(value, str) or len(value.strip()) < minimum:
        errors.append(f"{label}: escreva pelo menos {minimum} caracteres.")
    elif len(value) > MAX_TEXT:
        errors.append(f"{label}: máximo de {MAX_TEXT:,} caracteres.".replace(",", "."))


def _check_agent(spec, refs, errors, warnings):
    # Qualquer id de modelo é aceito: quem usa o agente pode ter acesso a modelos diferentes do autor.
    # Se a chave de quem conversa não tiver esse modelo, o chat usa o padrão dela.
    model = spec.get("model")
    if not isinstance(model, str) or not llm.MODEL_ID_RE.match(model):
        errors.append("Escolha um modelo válido.")
    _text(spec, "system_prompt", "Instruções do agente", errors)
    tools = spec.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools) or len(set(tools)) != len(tools):
        errors.append("Lista de ferramentas inválida.")
        return
    for slug in tools:
        if slug not in refs["tool"]:
            errors.append(f"A ferramenta “{slug}” não está instalada no seu workspace.")
    if not tools:
        warnings.append("O agente não usa nenhuma ferramenta — só conseguirá conversar.")


def _check_skill(spec, refs, errors, warnings):
    _text(spec, "instructions", "Instruções da skill", errors)


def _check_workflow(spec, refs, errors, warnings):
    trigger = spec.get("trigger")
    if not isinstance(trigger, str) or not trigger.strip():
        errors.append("Descreva o gatilho que inicia o workflow.")
    elif len(trigger) > 200:
        errors.append("O gatilho deve ter no máximo 200 caracteres.")

    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("Adicione pelo menos uma etapa.")
        return
    if len(steps) > MAX_STEPS:
        errors.append(f"Um workflow pode ter no máximo {MAX_STEPS} etapas.")
        return
    for n, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            errors.append(f"Etapa {n}: formato inválido.")
            continue
        name = step.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"Etapa {n}: informe um nome.")
        elif len(name) > 120:
            errors.append(f"Etapa {n}: o nome deve ter no máximo 120 caracteres.")
        match = USES_RE.match(step.get("uses") or "") if isinstance(step.get("uses"), str) else None
        if not match:
            errors.append(f"Etapa {n}: escolha o que será executado.")
        elif match.group(2) not in refs[match.group(1)]:
            errors.append(f"Etapa {n}: “{match.group(2)}” não está instalado no seu workspace.")


_CHECKS = {
    Item.Kind.AGENT: _check_agent,
    Item.Kind.SKILL: _check_skill,
    Item.Kind.WORKFLOW: _check_workflow,
}


def build_manifest(kind, spec, report) -> dict:
    if kind == Item.Kind.TOOL:
        return toolsmith.build_manifest(spec, report)
    if kind == Item.Kind.AGENT:
        return {"model": spec["model"], "system_prompt": spec["system_prompt"].strip(), "tools": spec.get("tools", [])}
    if kind == Item.Kind.SKILL:
        return {"instructions": spec["instructions"].strip()}
    return {
        "trigger": spec["trigger"].strip(),
        "steps": [{"name": s["name"].strip(), "uses": s["uses"]} for s in spec["steps"]],
    }


def apply_spec(item, spec, report):
    item.name = spec["name"].strip()
    item.slug = spec["slug"]
    item.summary = spec.get("summary", "").strip()
    item.description = spec["description"].strip()
    item.icon = spec.get("icon") or Item.DEFAULT_ICONS[item.kind]
    item.category = spec["category"]
    item.tags = [t.strip() for t in spec.get("tags", []) if t.strip()]
    item.manifest = build_manifest(item.kind, spec, report)


def item_spec(item) -> dict:
    spec = {
        "id": str(item.id),
        "kind": item.kind,
        "name": item.name,
        "slug": item.slug,
        "summary": item.summary,
        "description": item.description,
        "icon": item.icon_id,
        "category": item.category,
        "tags": item.tags,
        "version": item.version,
        "visibility": item.visibility,
        "status": item.sharing_status(),
    }
    manifest = item.manifest
    if item.kind == Item.Kind.TOOL:
        spec["input_schema"] = manifest.get("input_schema", {"type": "object", "properties": {}})
        spec["code"] = manifest.get("code", "")
    elif item.kind == Item.Kind.AGENT:
        spec.update(model=manifest.get("model", ""), system_prompt=manifest.get("system_prompt", ""),
                    tools=manifest.get("tools", []))
    elif item.kind == Item.Kind.SKILL:
        spec["instructions"] = manifest.get("instructions", "")
    else:
        spec.update(trigger=manifest.get("trigger", ""), steps=manifest.get("steps", []))
    return spec
