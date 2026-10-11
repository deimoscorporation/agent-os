"""
Toolsmith: cria ferramentas a partir de uma descrição em linguagem natural
e valida/audita qualquer especificação de ferramenta antes de salvar.

Especificação (spec) trocada com o editor:
    {"name", "slug", "summary", "description", "icon", "tags",
     "input_schema": {JSON Schema}, "code": "def run(...): ..."}

Para usar um LLM de verdade, substitua `draft_tool` por uma chamada ao
modelo usando `TOOLSMITH_PROMPT` e devolvendo um dict nesse formato.
`generate_tool` e a validação não precisam mudar.
"""

import ast
import re
import time
import unicodedata
from collections.abc import Iterator

from core.llm import LLMError

ENTRYPOINT = "run"
RUNTIME = "python3.11"
CATEGORIES = ("security", "dev", "data", "productivity", "communication", "other")
MAX_CODE_CHARS = 20_000
STEP_DELAY = 0.45

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
PARAM_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
PARAM_TYPES = ["string", "integer", "number", "boolean", "array", "object"]

TOOL_ICONS = [
    "i-wrench", "i-hash", "i-globe", "i-key", "i-shield", "i-mail",
    "i-map-pin", "i-radar", "i-terminal", "i-code", "i-cpu", "i-braces",
]

# Módulos/chamadas que exigem permissão explícita quando a ferramenta rodar em sandbox.
PERMISSION_MODULES = {
    "network": {"urllib", "http", "socket", "requests", "httpx", "ssl", "ftplib", "smtplib"},
    "filesystem": {"pathlib", "shutil", "tempfile", "glob", "io"},
    "process": {"subprocess", "multiprocessing", "signal"},
    "system": {"os", "sys", "ctypes", "platform", "importlib"},
}
PERMISSION_LABELS = {
    "network": "Acesso à rede",
    "filesystem": "Sistema de arquivos",
    "process": "Execução de processos",
    "system": "Acesso ao sistema operacional",
}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "breakpoint"}

# Segredos: o código chama secret("NOME") e o runtime injeta o valor guardado no cofre do workspace.
SECRET_FUNCTION = "secret"
SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
CREDENTIAL_NAME_RE = re.compile(r"(api_?key|token|secret|password|passwd|senha|credential)", re.IGNORECASE)
CREDENTIAL_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

TOOLSMITH_PROMPT = """\
Você é o Toolsmith da Deimos Corp. A partir da descrição do usuário, projete UMA
ferramenta que um agente de IA possa chamar. Responda somente com JSON:
{
  "name": "Nome legível (pt-BR)",
  "slug": "identificador_snake_case",
  "summary": "Uma frase curta para o card",
  "description": "Quando e como um agente deve usar a ferramenta, o que ela retorna",
  "icon": "um de: %(icons)s",
  "category": "um de: security, dev, data, productivity, communication, other",
  "tags": ["até", "cinco", "tags"],
  "input_schema": {"type": "object", "properties": {...}, "required": [...]},
  "code": "código Python 3.11 com def run(<parâmetros do schema>) -> dict"
}
Regras: a função se chama run e cada parâmetro dela tem o mesmo nome de uma propriedade do
schema (os que não estão em "required" precisam de valor padrão); cada propriedade tem "type"
e "description"; use apenas a biblioteca padrão; nada de eval/exec; valide as entradas;
retorne sempre um dict serializável em JSON; timeouts em qualquer acesso à rede;
nunca escreva credenciais no código — leia com secret("NOME_EM_MAIUSCULAS").
""" % {"icons": ", ".join(TOOL_ICONS)}


# --------------------------------------------------------------------------
# Geração
# --------------------------------------------------------------------------

STEPS = [
    ("analyze", "Analisando a descrição"),
    ("draft", "Projetando interface e implementação"),
    ("audit", "Validando e auditando o código"),
]


def generate_tool(description: str, taken_slugs=frozenset(), llm_call=None) -> Iterator[dict]:
    """Gera uma ferramenta emitindo eventos de progresso e, por fim, o rascunho.

    `llm_call(system, user) -> dict` chama o modelo do usuário; sem ele (ou se o modelo
    falhar), a ferramenta sai dos modelos prontos do modo demonstração.
    """
    spec = None
    report = None
    for step_id, label in STEPS:
        yield {"type": "step", "id": step_id, "label": label, "status": "running"}
        time.sleep(STEP_DELAY)
        if step_id == "draft":
            if llm_call:
                try:
                    spec = clean_llm_tool(llm_call(TOOLSMITH_PROMPT, description))
                except (LLMError, ValueError) as exc:
                    yield {
                        "type": "notice",
                        "message": f"O modelo não conseguiu gerar a ferramenta ({exc}) Usei um modelo pronto.",
                    }
            if spec is None:
                spec = draft_tool(description)
            spec["slug"] = unique_slug(spec["slug"], taken_slugs)
        elif step_id == "audit":
            report = validate_tool(spec)
        yield {"type": "step", "id": step_id, "label": label, "status": "done"}
    yield {"type": "draft", "draft": spec, "report": report}


def slugify(text: str, fallback="nova_ferramenta") -> str:
    slug = "_".join(_tokens(text))[:64].strip("_")
    if len(slug) < 3 or not slug[0].isalpha():
        return fallback
    return slug


def clean_llm_tool(data: dict) -> dict:
    """Normaliza a resposta do modelo para o formato de spec. ValueError se faltar o essencial."""
    name = str(data.get("name") or "").strip()[:80]
    code = data.get("code")
    schema = data.get("input_schema")
    if not name or not isinstance(code, str) or not code.strip() or not isinstance(schema, dict):
        raise ValueError("a resposta veio incompleta.")
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    return {
        "name": name,
        "slug": slugify(str(data.get("slug") or name)),
        "summary": str(data.get("summary") or "").strip()[:160],
        "description": str(data.get("description") or data.get("summary") or name).strip(),
        "icon": data.get("icon") if data.get("icon") in TOOL_ICONS else "i-wrench",
        "category": data.get("category") if data.get("category") in CATEGORIES else "other",
        "tags": [str(t).strip()[:24] for t in tags if str(t).strip()][:5],
        "input_schema": schema,
        "code": code.strip() + "\n",
    }


def draft_tool(description: str) -> dict:
    """Modo demonstração: escolhe o modelo mais aderente ou cria um esqueleto."""
    tokens = set(_tokens(description))
    best, best_score = None, 0
    for template in TEMPLATES:
        score = len(tokens & template["keywords"])
        if score > best_score:
            best, best_score = template, score
    if best:
        return {k: v for k, v in best.items() if k != "keywords"} | {"tags": list(best["tags"])}
    return _skeleton(description)


def unique_slug(slug: str, taken) -> str:
    if slug not in taken:
        return slug
    n = 2
    while f"{slug}_{n}" in taken:
        n += 1
    return f"{slug}_{n}"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalize(text))


STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "em", "no", "na",
    "com", "para", "por", "que", "se", "me", "meu", "minha", "ao", "ou", "quero", "crie", "criar",
    "ferramenta", "tool", "deve", "faz", "fazer", "faca", "recebe", "receba", "retorna", "retorne",
    "algo", "isso", "esta", "este", "seja", "sobre", "gere", "gerar", "entre", "partir", "cada",
    "como", "mais", "quando", "qualquer", "dado", "dados",
}


def _skeleton(description: str) -> dict:
    words = [t for t in _tokens(description) if t not in STOPWORDS and not t.isdigit()][:3]
    slug = "_".join(words) or "nova_ferramenta"
    if not slug[0].isalpha():
        slug = "tool_" + slug
    slug = slug[:64]
    if len(slug) < 3:
        slug = "nova_ferramenta"
    name = " ".join(words).capitalize() or "Nova ferramenta"
    clean = " ".join(description.split())
    docstring = clean[:200].replace("\\", "").replace('"', "'")
    return {
        "name": name,
        "slug": slug,
        "summary": clean[:150],
        "description": clean,
        "icon": "i-wrench",
        "category": "other",
        "tags": ["personalizada"],
        "input_schema": {
            "type": "object",
            "properties": {"entrada": {"type": "string", "description": "Dado de entrada da ferramenta."}},
            "required": ["entrada"],
        },
        "code": (
            "def run(entrada: str) -> dict:\n"
            f'    """{docstring}"""\n'
            "    # TODO: implemente a lógica da ferramenta.\n"
            "    # Com um modelo conectado em marketplace/toolsmith.py, este código é gerado automaticamente.\n"
            '    return {"entrada": entrada, "resultado": None}\n'
        ),
    }


# --------------------------------------------------------------------------
# Validação e auditoria
# --------------------------------------------------------------------------

def validate_tool(spec) -> dict:
    """Retorna {"errors": [...], "warnings": [...], "permissions": [...]}."""
    errors, warnings = [], []
    if not isinstance(spec, dict):
        return {"errors": ["Especificação inválida."], "warnings": [], "permissions": [], "secrets": []}

    check_common(spec, errors, TOOL_ICONS)
    properties, required = _check_schema(spec.get("input_schema"), errors, warnings)
    permissions, secrets = _check_code(spec.get("code"), properties, required, errors, warnings)

    return {"errors": errors, "warnings": warnings, "permissions": permissions, "secrets": secrets}


def check_common(spec, errors, icons):
    """Campos de identidade compartilhados por todos os tipos de item."""
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("Informe um nome.")
    elif len(name) > 80:
        errors.append("O nome deve ter no máximo 80 caracteres.")

    slug = spec.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        errors.append("Identificador inválido: use snake_case, de 3 a 64 caracteres, começando com letra.")

    summary = spec.get("summary", "")
    if not isinstance(summary, str) or len(summary) > 160:
        errors.append("O resumo deve ter no máximo 160 caracteres.")

    description = spec.get("description")
    if not isinstance(description, str) or len(description.strip()) < 10:
        errors.append("Escreva uma descrição com pelo menos 10 caracteres.")

    if spec.get("icon") and spec["icon"] not in icons:
        errors.append("Ícone inválido.")

    tags = spec.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 8 or not all(isinstance(t, str) and len(t) <= 24 for t in tags):
        errors.append("Use até 8 tags com no máximo 24 caracteres cada.")


def _check_schema(schema, errors, warnings):
    if not isinstance(schema, dict) or schema.get("type") != "object":
        errors.append('O schema de entrada deve ser um objeto JSON Schema com "type": "object".')
        return {}, []
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        errors.append('"properties" deve ser um objeto.')
        return {}, []
    for pname, pdef in properties.items():
        if not PARAM_RE.match(pname):
            errors.append(f"Parâmetro “{pname}”: use snake_case (letras minúsculas, números e _).")
        if not isinstance(pdef, dict) or pdef.get("type") not in PARAM_TYPES:
            errors.append(f"Parâmetro “{pname}”: tipo inválido.")
            continue
        if not str(pdef.get("description", "")).strip():
            warnings.append(f"Parâmetro “{pname}” sem descrição — agentes usam isso para preencher o valor.")
        if "enum" in pdef and (not isinstance(pdef["enum"], list) or not pdef["enum"]):
            errors.append(f"Parâmetro “{pname}”: a lista de valores permitidos está vazia.")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(r not in properties for r in required):
        errors.append('"required" só pode conter parâmetros definidos em "properties".')
        required = []
    if not properties:
        warnings.append("A ferramenta não recebe parâmetros.")
    return properties, required


def _check_code(code, properties, required, errors, warnings):
    """Audita o código. Retorna (permissões, segredos usados)."""
    if not isinstance(code, str) or not code.strip():
        errors.append("O código da ferramenta está vazio.")
        return [], []
    if len(code) > MAX_CODE_CHARS:
        errors.append(f"O código excede {MAX_CODE_CHARS:,} caracteres.".replace(",", "."))
        return [], []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"Erro de sintaxe na linha {exc.lineno}: {exc.msg}.")
        return [], []

    entry = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == ENTRYPOINT),
        None,
    )
    if entry is None:
        errors.append(f"Defina uma função de nível superior `{ENTRYPOINT}(...)` como ponto de entrada.")
    else:
        _check_signature(entry, properties, required, errors, warnings)

    permissions = set()
    secrets = []
    for node in ast.walk(tree):
        _check_hardcoded_credential(node, errors, warnings)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == SECRET_FUNCTION:
            name = _secret_name(node)
            if name is None:
                errors.append(
                    f'Linha {node.lineno}: use `{SECRET_FUNCTION}("NOME_DO_SEGREDO")` com o nome escrito '
                    "diretamente, em MAIÚSCULAS."
                )
            elif name not in secrets:
                secrets.append(name)
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            modules = []
        for module in modules:
            root = module.split(".")[0]
            for perm, roots in PERMISSION_MODULES.items():
                if root in roots:
                    permissions.add(perm)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                errors.append(f"Linha {node.lineno}: `{node.func.id}()` não é permitido em ferramentas.")
            elif node.func.id == "open":
                permissions.add("filesystem")

    for perm in ("process", "system"):
        if perm in permissions:
            warnings.append(f"Requer permissão sensível: {PERMISSION_LABELS[perm].lower()}.")
    return sorted(permissions), secrets


def _secret_name(call):
    if len(call.args) != 1 or call.keywords:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and SECRET_NAME_RE.match(arg.value):
        return arg.value
    return None


def _check_hardcoded_credential(node, errors, warnings):
    """Credenciais não podem ficar escritas no código: vão para o cofre."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and CREDENTIAL_VALUE_RE.search(node.value):
        errors.append(
            f'Linha {node.lineno}: parece haver uma credencial no código. Guarde-a no cofre e use '
            f'`{SECRET_FUNCTION}("NOME")`.'
        )
        return
    targets = []
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    elif isinstance(node, ast.keyword) and node.arg:
        targets, value = [ast.Name(id=node.arg)], node.value
    for target in targets:
        name = getattr(target, "id", "") or getattr(target, "attr", "")
        if (
            CREDENTIAL_NAME_RE.search(name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and len(value.value) >= 8
        ):
            warnings.append(
                f'Linha {value.lineno}: `{name}` recebe um texto fixo. Se for uma credencial, use '
                f'`{SECRET_FUNCTION}("NOME")`.'
            )


def _check_signature(fn, properties, required, errors, warnings):
    args = fn.args
    positional = args.posonlyargs + args.args
    names = [a.arg for a in positional + args.kwonlyargs]
    no_default = {a.arg for a in positional[: len(positional) - len(args.defaults)]}
    no_default |= {a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None}

    if args.kwarg is None:
        for pname in properties:
            if pname not in names:
                errors.append(f"`{ENTRYPOINT}()` não recebe o parâmetro “{pname}” definido no schema.")
    for arg in names:
        if arg not in properties:
            if arg in no_default:
                errors.append(f"Argumento obrigatório `{arg}` de `{ENTRYPOINT}()` não existe no schema.")
            else:
                warnings.append(f"Argumento `{arg}` de `{ENTRYPOINT}()` não está no schema e usará o valor padrão.")
    for arg in no_default:
        if arg in properties and arg not in required:
            errors.append(f"“{arg}” é opcional no schema, mas `{ENTRYPOINT}()` não define valor padrão.")
    if not any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(fn)):
        warnings.append(f"`{ENTRYPOINT}()` não retorna nenhum valor.")


def build_manifest(spec: dict, report: dict) -> dict:
    return {
        "input_schema": spec["input_schema"],
        "code": spec["code"],
        "entrypoint": ENTRYPOINT,
        "runtime": RUNTIME,
        "permissions": report["permissions"],
        "secrets": report["secrets"],
    }


def schema_params(schema) -> list[dict]:
    """Lista de parâmetros legível para templates."""
    if not isinstance(schema, dict):
        return []
    required = set(schema.get("required", []))
    return [
        {
            "name": name,
            "type": pdef.get("type", "?"),
            "description": pdef.get("description", ""),
            "required": name in required,
            "enum": pdef.get("enum") or [],
        }
        for name, pdef in schema.get("properties", {}).items()
    ]


# --------------------------------------------------------------------------
# Modelos usados no modo demonstração (e pelas ferramentas oficiais)
# --------------------------------------------------------------------------

TEMPLATES = [
    {
        "keywords": {"hash", "hashes", "sha", "sha256", "sha1", "md5", "checksum", "digest", "integridade"},
        "name": "Calcular hash",
        "slug": "calcular_hash",
        "summary": "Calcula o hash criptográfico (MD5, SHA-1, SHA-256, SHA-512) de um texto.",
        "description": (
            "Calcula o hash criptográfico de um texto. Use para verificar integridade, comparar "
            "valores ou gerar identificadores determinísticos. Retorna o algoritmo e o hash em hexadecimal."
        ),
        "icon": "i-hash",
        "category": "security",
        "tags": ["criptografia", "integridade"],
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Texto de entrada a ser processado."},
                "algoritmo": {
                    "type": "string",
                    "description": "Algoritmo de hash.",
                    "enum": ["md5", "sha1", "sha256", "sha512"],
                },
            },
            "required": ["texto"],
        },
        "code": '''\
import hashlib


def run(texto: str, algoritmo: str = "sha256") -> dict:
    if algoritmo not in {"md5", "sha1", "sha256", "sha512"}:
        raise ValueError(f"Algoritmo não suportado: {algoritmo}")
    digest = hashlib.new(algoritmo, texto.encode("utf-8")).hexdigest()
    return {"algoritmo": algoritmo, "hash": digest}
''',
    },
    {
        "keywords": {"cep", "ceps", "logradouro", "viacep", "correios", "bairro"},
        "name": "Consultar CEP",
        "slug": "consultar_cep",
        "summary": "Busca logradouro, bairro, cidade e UF a partir de um CEP brasileiro.",
        "description": (
            "Consulta um CEP brasileiro na API pública ViaCEP e retorna logradouro, bairro, cidade e UF. "
            "Use quando o usuário informar um CEP ou precisar completar um endereço."
        ),
        "icon": "i-map-pin",
        "category": "data",
        "tags": ["brasil", "endereço", "api"],
        "input_schema": {
            "type": "object",
            "properties": {
                "cep": {"type": "string", "description": "CEP com ou sem máscara, ex.: 01310-100."},
            },
            "required": ["cep"],
        },
        "code": '''\
import json
import re
import urllib.request


def run(cep: str) -> dict:
    digits = re.sub(r"\\D", "", cep)
    if len(digits) != 8:
        raise ValueError("O CEP deve ter 8 dígitos.")
    url = f"https://viacep.com.br/ws/{digits}/json/"
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.load(response)
    if data.get("erro"):
        return {"encontrado": False, "cep": digits}
    return {
        "encontrado": True,
        "cep": data["cep"],
        "logradouro": data["logradouro"],
        "bairro": data["bairro"],
        "cidade": data["localidade"],
        "uf": data["uf"],
    }
''',
    },
    {
        "keywords": {"senha", "senhas", "password", "passwords", "forca", "entropia", "credencial"},
        "name": "Avaliar força de senha",
        "slug": "avaliar_senha",
        "summary": "Estima a entropia de uma senha e sugere melhorias.",
        "description": (
            "Avalia a força de uma senha estimando sua entropia em bits e classificando-a como fraca, "
            "média ou forte, com sugestões objetivas de melhoria. A senha não é armazenada."
        ),
        "icon": "i-key",
        "category": "security",
        "tags": ["segurança", "senhas"],
        "input_schema": {
            "type": "object",
            "properties": {
                "senha": {"type": "string", "description": "Senha a ser avaliada."},
            },
            "required": ["senha"],
        },
        "code": '''\
import math
import re

POOLS = [
    (r"[a-z]", 26, "letras minúsculas"),
    (r"[A-Z]", 26, "letras maiúsculas"),
    (r"\\d", 10, "números"),
    (r"[^a-zA-Z\\d]", 33, "símbolos"),
]


def run(senha: str) -> dict:
    charset = 0
    sugestoes = []
    for pattern, size, label in POOLS:
        if re.search(pattern, senha):
            charset += size
        else:
            sugestoes.append(f"Inclua {label}.")
    if len(senha) < 12:
        sugestoes.insert(0, "Use pelo menos 12 caracteres.")

    entropia = round(len(senha) * math.log2(charset), 1) if charset else 0.0
    nivel = "forte" if entropia >= 70 else "média" if entropia >= 45 else "fraca"
    return {"entropia_bits": entropia, "nivel": nivel, "sugestoes": sugestoes}
''',
    },
    {
        "keywords": {"ip", "ips", "ipv4", "ipv6", "cidr", "rede", "subrede"},
        "name": "Analisar endereço IP",
        "slug": "analisar_ip",
        "summary": "Classifica um IPv4/IPv6: privado, público, loopback, multicast ou reservado.",
        "description": (
            "Analisa um endereço IPv4 ou IPv6 e informa versão e classificação (privado, global, loopback, "
            "multicast, reservado). Use na triagem de indicadores de comprometimento e logs de rede."
        ),
        "icon": "i-radar",
        "category": "security",
        "tags": ["rede", "segurança", "soc"],
        "input_schema": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "Endereço IPv4 ou IPv6, ex.: 192.168.0.10."},
            },
            "required": ["ip"],
        },
        "code": '''\
import ipaddress


def run(ip: str) -> dict:
    addr = ipaddress.ip_address(ip.strip())
    return {
        "ip": str(addr),
        "versao": addr.version,
        "privado": addr.is_private,
        "global": addr.is_global,
        "loopback": addr.is_loopback,
        "multicast": addr.is_multicast,
        "reservado": addr.is_reserved,
    }
''',
    },
    {
        "keywords": {"header", "headers", "cabecalho", "cabecalhos", "site", "url", "http", "https", "hsts", "csp"},
        "name": "Verificar headers de segurança",
        "slug": "verificar_headers_seguranca",
        "summary": "Audita os headers HTTP de segurança de um site e atribui uma nota.",
        "description": (
            "Faz uma requisição HEAD para a URL e verifica a presença dos principais headers de segurança "
            "(HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy). "
            "Retorna os presentes, os ausentes e uma nota de 0 a 100."
        ),
        "icon": "i-shield",
        "category": "security",
        "tags": ["web", "segurança", "auditoria"],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL do site, ex.: https://exemplo.com."},
            },
            "required": ["url"],
        },
        "code": '''\
import urllib.request

HEADERS = [
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
]


def run(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "DeimosToolsmith/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        received = {k.lower(): v for k, v in response.headers.items()}
    presentes = {h: received[h] for h in HEADERS if h in received}
    ausentes = [h for h in HEADERS if h not in received]
    return {
        "url": url,
        "nota": round(100 * len(presentes) / len(HEADERS)),
        "presentes": presentes,
        "ausentes": ausentes,
    }
''',
    },
    {
        "keywords": {"reputacao", "abuseipdb", "malicioso", "maliciosos", "blacklist", "denuncias", "abuso"},
        "name": "Consultar reputação de IP",
        "slug": "reputacao_ip",
        "summary": "Consulta no AbuseIPDB se um IP tem denúncias de abuso.",
        "description": (
            "Consulta a reputação de um endereço IP no AbuseIPDB e retorna a pontuação de abuso (0 a 100), "
            "o país e o total de denúncias. Use para avaliar se um IP externo é malicioso. "
            "Requer o segredo ABUSEIPDB_API_KEY no cofre do workspace."
        ),
        "icon": "i-radar",
        "category": "security",
        "tags": ["rede", "threat intel", "api"],
        "input_schema": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "Endereço IPv4 ou IPv6 público."},
                "dias": {"type": "integer", "description": "Janela de análise em dias (1 a 365)."},
            },
            "required": ["ip"],
        },
        "code": '''\
import ipaddress
import json
import urllib.parse
import urllib.request


def run(ip: str, dias: int = 90) -> dict:
    addr = ipaddress.ip_address(ip.strip())
    if not addr.is_global:
        raise ValueError("Informe um IP público.")
    query = urllib.parse.urlencode({"ipAddress": str(addr), "maxAgeInDays": max(1, min(dias, 365))})
    request = urllib.request.Request(
        f"https://api.abuseipdb.com/api/v2/check?{query}",
        headers={"Key": secret("ABUSEIPDB_API_KEY"), "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.load(response)["data"]
    return {
        "ip": data["ipAddress"],
        "pontuacao_abuso": data["abuseConfidenceScore"],
        "pais": data.get("countryCode"),
        "denuncias": data["totalReports"],
    }
''',
    },
    {
        "keywords": {"email", "emails", "mail", "mails"},
        "name": "Extrair e-mails",
        "slug": "extrair_emails",
        "summary": "Encontra endereços de e-mail em um texto livre.",
        "description": (
            "Extrai endereços de e-mail de um texto livre (logs, documentos, páginas). "
            "Pode remover duplicados. Retorna a lista e a contagem."
        ),
        "icon": "i-mail",
        "category": "data",
        "tags": ["texto", "osint"],
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Texto onde procurar e-mails."},
                "unicos": {"type": "boolean", "description": "Remover e-mails duplicados."},
            },
            "required": ["texto"],
        },
        "code": '''\
import re

PATTERN = re.compile(r"[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+")


def run(texto: str, unicos: bool = True) -> dict:
    emails = PATTERN.findall(texto)
    if unicos:
        emails = list(dict.fromkeys(e.lower() for e in emails))
    return {"emails": emails, "total": len(emails)}
''',
    },
]

EXAMPLES = [
    {"label": "Consultar CEP", "icon": "i-map-pin", "prompt": "Uma ferramenta que recebe um CEP e retorna o endereço completo."},
    {"label": "Calcular hash", "icon": "i-hash", "prompt": "Calcular o hash SHA-256 ou MD5 de um texto para verificar integridade."},
    {"label": "Força de senha", "icon": "i-key", "prompt": "Avaliar a força de uma senha e sugerir melhorias."},
    {"label": "Headers de segurança", "icon": "i-shield", "prompt": "Verificar os headers HTTP de segurança de um site e dar uma nota."},
    {"label": "Analisar IP", "icon": "i-radar", "prompt": "Analisar um endereço IP e dizer se é privado, público ou reservado."},
    {"label": "Reputação de IP (usa segredo)", "icon": "i-key", "prompt": "Consultar a reputação de um IP no AbuseIPDB para saber se é malicioso."},
]
