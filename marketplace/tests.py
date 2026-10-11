import json
from unittest import mock

from django.test import TestCase

from accounts.models import OpenAICredential, User
from core.testing import fake_openai, http_error, json_reply
from chat.models import Conversation
from workspaces.models import Membership, Workspace
from workspaces.services import create_organization, personal_workspace, set_secret

from .models import Installation, Item, PublicationRequest, Review
from .toolsmith import draft_tool, validate_tool

PASSWORD = "Segredo!Forte42"


def post_json(client, url, data=None, method="post"):
    return getattr(client, method)(url, json.dumps(data or {}), content_type="application/json")


class ToolsmithTests(TestCase):
    def test_templates_match_description(self):
        self.assertEqual(draft_tool("Recebe um CEP e devolve o endereço")["slug"], "consultar_cep")
        self.assertEqual(draft_tool("Analisar um endereço IP e dizer se é privado")["slug"], "analisar_ip")
        self.assertEqual(draft_tool("Calcular o hash SHA-256 de um texto")["slug"], "calcular_hash")
        self.assertEqual(draft_tool("Consultar a reputação de um IP no AbuseIPDB")["slug"], "reputacao_ip")

    def test_unknown_description_produces_valid_skeleton(self):
        spec = draft_tool('Converter temperaturas entre Celsius e "Fahrenheit" \\ rapidamente')
        report = validate_tool(spec)
        self.assertEqual(report["errors"], [])
        self.assertEqual(spec["slug"], "converter_temperaturas_celsius")

    def test_validation_catches_problems(self):
        spec = draft_tool("Calcular hash de um texto")
        spec["code"] = "import subprocess\n\ndef run(texto):\n    return eval(texto)\n"
        report = validate_tool(spec)
        self.assertIn("process", report["permissions"])
        self.assertTrue(any("eval" in e for e in report["errors"]))
        # "algoritmo" está no schema mas run() não o recebe
        self.assertTrue(any("algoritmo" in e for e in report["errors"]))

    def test_validation_rejects_syntax_errors_and_bad_slug(self):
        spec = draft_tool("Calcular hash de um texto")
        spec["slug"] = "Hash Tool"
        spec["code"] = "def run(:\n"
        errors = validate_tool(spec)["errors"]
        self.assertTrue(any("Identificador" in e for e in errors))
        self.assertTrue(any("sintaxe" in e for e in errors))

    def test_validation_handles_garbage(self):
        self.assertTrue(validate_tool(None)["errors"])
        self.assertTrue(validate_tool({"input_schema": "x", "code": 3})["errors"])

    def test_secrets_are_detected_from_code(self):
        spec = draft_tool("Consultar a reputação de um IP no AbuseIPDB")
        report = validate_tool(spec)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["secrets"], ["ABUSEIPDB_API_KEY"])
        self.assertEqual(report["permissions"], ["network"])

    def test_hardcoded_credentials_are_rejected(self):
        spec = draft_tool("Calcular hash de um texto")
        base = spec["code"]
        cases = {
            'CHAVE = "sk-abcdefghijklmnopqrstuvwxyz123456"\n': "errors",
            'api_key = "valor-fixo-qualquer"\n': "warnings",
        }
        for prefix, bucket in cases.items():
            with self.subTest(prefix=prefix):
                spec["code"] = prefix + base
                report = validate_tool(spec)
                self.assertTrue(any("secret(" in message for message in report[bucket]), report)

    def test_secret_name_must_be_a_literal(self):
        spec = draft_tool("Calcular hash de um texto")
        for call in ("secret(nome)", 'secret("minusculo")', 'secret("A", "B")'):
            with self.subTest(call=call):
                spec["code"] = f"def run(texto, algoritmo='sha256'):\n    nome = 'X'\n    return {{'v': {call}}}\n"
                report = validate_tool(spec)
                self.assertTrue(any("NOME_DO_SEGREDO" in e for e in report["errors"]), report)
                self.assertEqual(report["secrets"], [])


@mock.patch("marketplace.toolsmith.STEP_DELAY", 0)
@mock.patch("chat.services.DEMO_DELAY", 0)
class MarketplaceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("autor@exemplo.com", PASSWORD, full_name="Autora Teste")
        self.other = User.objects.create_user("outro@exemplo.com", PASSWORD, full_name="Outro Usuário")
        self.staff = User.objects.create_user("equipe@deimos.test", PASSWORD, full_name="Equipe Deimos", is_staff=True)
        self.client.force_login(self.user)

    def login(self, user, workspace=None):
        self.client.force_login(user)
        if workspace:
            res = self.client.post("/workspaces/switch/", {"workspace_id": str(workspace.id)})
            self.assertEqual(res.status_code, 302)

    def generate(self, description):
        res = post_json(self.client, "/api/tools/generate/", {"description": description})
        self.assertEqual(res.status_code, 200)
        events = [json.loads(line) for line in b"".join(res.streaming_content).decode().splitlines()]
        self.assertEqual(events[-1]["type"], "draft")
        return events

    def create(self, kind, spec):
        return post_json(self.client, "/api/items/", {"kind": kind, "spec": spec})

    def create_tool(self, description="Calcular hash de um texto"):
        res = self.create("tool", self.generate(description)[-1]["draft"])
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()["item"]

    def save(self, item_id, **changes):
        item = Item.objects.get(pk=item_id)
        from .specs import item_spec
        spec = item_spec(item) | changes
        res = post_json(self.client, f"/api/items/{item_id}/", {"spec": spec, "changelog": "Mudança"}, "put")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["item"]

    def approve(self, item_id, decision="approve", note=""):
        """Aprova como equipe e volta para o usuário que estava logado."""
        request = PublicationRequest.objects.get(item_id=item_id, status="pending")
        client = self.client_class()
        client.force_login(self.staff)
        return post_json(client, f"/api/review/{request.id}/", {"decision": decision, "note": note})

    def publish(self, item_id):
        self.assertEqual(post_json(self.client, f"/api/items/{item_id}/submit/").status_code, 201)
        self.assertEqual(self.approve(item_id).status_code, 200)

    def common(self, **extra):
        spec = {
            "name": "Item de teste", "slug": "item_de_teste", "summary": "Resumo",
            "description": "Descrição suficientemente longa.", "icon": "i-bot",
            "category": "security", "tags": ["teste"],
        }
        spec.update(extra)
        return spec

    def agent_spec(self, **extra):
        spec = self.common(
            name="Agente de hashes", slug="agente_hashes", model="gpt-5-mini",
            system_prompt="Você calcula e compara hashes para verificar integridade.", tools=[],
        )
        spec.update(extra)
        return spec


class ToolFlowTests(MarketplaceTestCase):
    def test_official_catalog_is_seeded(self):
        official = Item.objects.filter(source="official")
        self.assertEqual(set(official.values_list("kind", flat=True)), {"tool", "agent", "workflow", "skill"})
        self.assertTrue(official.filter(featured=True).exists())
        self.assertTrue(all(i.versions.count() == 1 and i.approved_version == i.version for i in official))
        self.assertContains(self.client.get("/marketplace/"), "Analista SOC")

    def test_generate_create_edit_delete(self):
        events = self.generate("Uma ferramenta que recebe um CEP e retorna o endereço")
        self.assertEqual([e["status"] for e in events if e["type"] == "step"].count("done"), 3)
        draft = events[-1]["draft"]
        self.assertEqual(events[-1]["report"]["errors"], [])
        self.assertEqual(events[-1]["report"]["permissions"], ["network"])

        res = self.create("tool", draft)
        self.assertEqual(res.status_code, 201)
        tool_id = res.json()["item"]["id"]
        item = Item.objects.get(pk=tool_id)
        self.assertEqual(item.workspace, personal_workspace(self.user))
        self.assertEqual(item.created_by, self.user)
        self.assertEqual(item.author, "Autora Teste")
        self.assertEqual(item.manifest["entrypoint"], "run")
        self.assertEqual(item.visibility, "private")
        self.assertEqual(item.category, "data")

        # Disponível para agentes no formato tool use
        tools = self.client.get("/api/workspace/tools/").json()["tools"]
        self.assertEqual(tools, [item.tool_definition()])
        self.assertEqual(set(tools[0]), {"name", "description", "input_schema"})

        # Mesmo identificador não pode ser criado de novo; a geração sugere outro
        self.assertEqual(self.create("tool", draft).status_code, 400)
        self.assertEqual(self.generate("Consultar CEP de novo")[-1]["draft"]["slug"], "consultar_cep_2")

        # Editar incrementa a versão, registra o histórico e atualiza a instalação do dono
        draft["summary"] = "Novo resumo"
        res = post_json(self.client, f"/api/items/{tool_id}/", {"spec": draft, "changelog": "Resumo novo"}, "put")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["item"]["version"], "1.0.1")
        self.assertEqual(
            list(item.versions.values_list("version", "changelog")),
            [("1.0.1", "Resumo novo"), ("1.0.0", "Versão inicial")],
        )
        self.assertEqual(Installation.objects.get(item=item).installed_version, "1.0.1")

        # Desativar remove a ferramenta da lista dos agentes
        post_json(self.client, f"/api/workspace/items/{tool_id}/", {"enabled": False}, "patch")
        self.assertEqual(self.client.get("/api/workspace/tools/").json()["tools"], [])

        self.assertEqual(self.client.get(f"/workspace/items/{tool_id}/").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/items/{tool_id}/").status_code, 200)
        self.assertFalse(Item.objects.filter(pk=tool_id).exists())

    def test_invalid_spec_is_rejected(self):
        draft = self.generate("Calcular hash de um texto")[-1]["draft"]
        draft["code"] = "def run(texto):\n    return exec(texto)\n"
        res = self.create("tool", draft)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.json()["report"]["errors"])
        self.assertFalse(Item.objects.filter(source="user").exists())

    def test_install_and_uninstall_official_item(self):
        agent = Item.objects.get(source="official", slug="analista_soc")
        self.assertEqual(self.client.post(f"/api/marketplace/{agent.id}/install/").status_code, 200)
        installation = Installation.objects.get()
        self.assertEqual(installation.workspace, personal_workspace(self.user))
        self.assertEqual(installation.installed_version, agent.version)
        agent.refresh_from_db()
        self.assertEqual(agent.install_count, 1)
        self.assertContains(self.client.get("/workspace/"), "Analista SOC")
        self.client.post(f"/api/marketplace/{agent.id}/uninstall/")
        self.assertEqual(Installation.objects.count(), 0)

    def test_slug_conflict_on_install(self):
        self.create_tool("Analisar um IP")
        official = Item.objects.get(source="official", slug="analisar_ip")
        self.assertEqual(self.client.post(f"/api/marketplace/{official.id}/install/").status_code, 409)

    def test_private_items_are_isolated(self):
        tool_id = self.create_tool()["id"]
        self.login(self.other)
        self.assertEqual(self.client.get(f"/marketplace/{tool_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/workspace/items/{tool_id}/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/items/{tool_id}/").status_code, 404)
        self.assertEqual(self.client.post(f"/api/marketplace/{tool_id}/install/").status_code, 404)
        self.assertEqual(post_json(self.client, f"/api/items/{tool_id}/submit/").status_code, 404)
        self.assertEqual(self.client.get("/api/workspace/tools/").json()["tools"], [])

    def test_tool_reports_secret_status_from_vault(self):
        draft = self.generate("Consultar a reputação de um IP no AbuseIPDB")[-1]
        self.assertEqual(draft["report"]["secrets_status"], [{"name": "ABUSEIPDB_API_KEY", "configured": False}])
        tool = self.create("tool", draft["draft"]).json()["item"]
        self.assertEqual(Item.objects.get(pk=tool["id"]).manifest["secrets"], ["ABUSEIPDB_API_KEY"])

        self.assertContains(self.client.get("/workspace/"), "Falta no cofre: ABUSEIPDB_API_KEY")
        self.assertContains(self.client.get("/segredos/"), "1 segredo faltando")

        set_secret(personal_workspace(self.user), "ABUSEIPDB_API_KEY", "valor-secreto-123456", self.user)
        self.assertNotContains(self.client.get("/workspace/"), "Falta no cofre")
        report = post_json(self.client, "/api/items/validate/", {"kind": "tool", "spec": draft["draft"], "item_id": tool["id"]}).json()
        self.assertEqual(report["secrets_status"], [{"name": "ABUSEIPDB_API_KEY", "configured": True}])
        self.assertNotContains(self.client.get(f"/workspace/items/{tool['id']}/"), "valor-secreto-123456")

    def test_pages_render(self):
        urls = ["/workspace/", "/marketplace/?kind=agent", "/", "/conta/", "/equipe/", "/segredos/"]
        urls += [f"/workspace/new/{kind}/" for kind in ("tool", "agent", "workflow", "skill")]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/workspace/new/invalido/").status_code, 404)
        for item in Item.objects.all():
            self.assertEqual(self.client.get(f"/marketplace/{item.id}/").status_code, 200, item.slug)


class AgentSkillWorkflowTests(MarketplaceTestCase):
    def test_agent_uses_workspace_tools(self):
        self.create_tool()  # calcular_hash
        spec = self.agent_spec(tools=["calcular_hash", "nao_instalada"])
        res = self.create("agent", spec)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(any("nao_instalada" in e for e in res.json()["report"]["errors"]))

        spec["tools"] = ["calcular_hash"]
        res = self.create("agent", spec)
        self.assertEqual(res.status_code, 201, res.content)
        agent = Item.objects.get(pk=res.json()["item"]["id"])
        self.assertEqual(agent.manifest, {
            "model": "gpt-5-mini",
            "system_prompt": "Você calcula e compara hashes para verificar integridade.",
            "tools": ["calcular_hash"],
        })
        self.assertContains(self.client.get(f"/workspace/items/{agent.id}/"), "agente_hashes")
        self.assertContains(self.client.get(f"/marketplace/{agent.id}/"), "calcular_hash")

    def test_agent_validation(self):
        spec = self.common(model="modelo invalido!", system_prompt="curto", tools=[])
        report = post_json(self.client, "/api/items/validate/", {"kind": "agent", "spec": spec}).json()
        self.assertTrue(any("modelo" in e for e in report["errors"]))
        self.assertTrue(any("Instruções do agente" in e for e in report["errors"]))
        spec.update(model="gpt-4o-mini", system_prompt="Instruções longas o bastante para valer.")
        report = post_json(self.client, "/api/items/validate/", {"kind": "agent", "spec": spec}).json()
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["warnings"])

    def test_skill_and_workflow(self):
        self.create_tool()
        skill = self.common(name="Resumo técnico", slug="resumo_tecnico", icon="i-book",
                            instructions="1. Leia tudo.\n2. Resuma em cinco linhas objetivas.")
        self.assertEqual(self.create("skill", skill).status_code, 201)

        workflow = self.common(
            name="Verificar e resumir", slug="verificar_resumir", icon="i-workflow", trigger="Arquivo recebido",
            steps=[
                {"name": "Calcular hash", "uses": "tool:calcular_hash"},
                {"name": "Resumir", "uses": "skill:resumo_tecnico"},
                {"name": "Quebrada", "uses": "agent:inexistente"},
            ],
        )
        res = self.create("workflow", workflow)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(len(res.json()["report"]["errors"]), 1)
        self.assertIn("Etapa 3", res.json()["report"]["errors"][0])

        workflow["steps"].pop()
        res = self.create("workflow", workflow)
        self.assertEqual(res.status_code, 201, res.content)
        item = Item.objects.get(pk=res.json()["item"]["id"])
        self.assertEqual([s["uses"] for s in item.manifest["steps"]], ["tool:calcular_hash", "skill:resumo_tecnico"])
        self.assertContains(self.client.get(f"/marketplace/{item.id}/"), "Arquivo recebido")

        workflow.update(slug="vazio", steps=[])
        self.assertEqual(self.create("workflow", workflow).status_code, 400)

    def test_invalid_kind_and_category(self):
        self.assertEqual(self.create("plugin", self.common()).status_code, 400)
        spec = self.common(category="inexistente", instructions="Instruções longas o bastante para valer.")
        res = self.create("skill", spec)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Escolha uma categoria.", res.json()["report"]["errors"])

    def test_drafts_from_description(self):
        official = Item.objects.get(source="official", slug="verificar_headers_seguranca")
        self.client.post(f"/api/marketplace/{official.id}/install/")

        res = post_json(self.client, "/api/items/draft/", {
            "kind": "agent", "description": "Auditar os headers de segurança de um site e explicar os riscos",
        })
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data["report"]["errors"], [])
        self.assertEqual(data["spec"]["tools"], ["verificar_headers_seguranca"])
        self.assertEqual(data["spec"]["category"], "security")
        self.assertIn("verificar_headers_seguranca", data["spec"]["system_prompt"])
        # O rascunho gerado pode ser criado sem ajustes.
        self.assertEqual(self.create("agent", data["spec"]).status_code, 201)

        workflow = post_json(self.client, "/api/items/draft/", {
            "kind": "workflow", "description": "Verificar os headers de um site e analisar os riscos",
        }).json()
        self.assertEqual(workflow["report"]["errors"], [])
        self.assertEqual(workflow["spec"]["steps"][0]["uses"], "tool:verificar_headers_seguranca")
        self.assertTrue(any(s["uses"].startswith("agent:") for s in workflow["spec"]["steps"]))

        skill = post_json(self.client, "/api/items/draft/", {
            "kind": "skill", "description": "Como escrever um relatório de incidente para a diretoria",
        }).json()
        self.assertEqual(skill["report"]["errors"], [])

        self.assertEqual(post_json(self.client, "/api/items/draft/", {"kind": "tool", "description": "x" * 20}).status_code, 400)
        self.assertEqual(post_json(self.client, "/api/items/draft/", {"kind": "agent", "description": "curta"}).status_code, 400)

    def test_chat_with_agent(self):
        self.create_tool()
        agent = self.create("agent", self.agent_spec(tools=["calcular_hash"])).json()["item"]
        self.assertEqual(
            [a["id"] for a in self.client.get("/api/workspace/agents/").json()["agents"]], [agent["id"]],
        )

        res = post_json(self.client, "/api/chat/", {"message": "Olá", "agent_id": agent["id"]})
        body = b"".join(res.streaming_content).decode()
        self.assertIn("Agente de hashes", body)
        self.assertIn("calcular_hash", body)
        conversation = Conversation.objects.get()
        self.assertEqual(str(conversation.agent_id), agent["id"])
        listed = self.client.get("/api/conversations/").json()["conversations"][0]
        self.assertEqual(listed["agent"], {"id": agent["id"], "name": "Agente de hashes"})

        # Agentes de outro workspace não podem ser usados.
        self.login(self.other)
        res = post_json(self.client, "/api/chat/", {"message": "Olá", "agent_id": agent["id"]})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(post_json(self.client, "/api/chat/", {"message": "Olá", "agent_id": "lixo"}).status_code, 400)


class LLMGenerationTests(MarketplaceTestCase):
    """Com a chave do usuário, ferramentas e rascunhos são escritos pelo modelo (aqui simulado)."""

    KEY = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    TOOL = {
        "name": "Converter temperatura",
        "slug": "Converter Temperatura!",
        "summary": "Converte entre Celsius e Fahrenheit.",
        "description": "Converte um valor de temperatura entre Celsius e Fahrenheit e devolve o resultado.",
        "icon": "icone-que-nao-existe",
        "category": "categoria-inventada",
        "tags": ["conversão", "temperatura"],
        "input_schema": {
            "type": "object",
            "properties": {
                "valor": {"type": "number", "description": "Temperatura a converter."},
                "para": {"type": "string", "description": "Escala de destino.", "enum": ["c", "f"]},
            },
            "required": ["valor"],
        },
        "code": 'def run(valor: float, para: str = "f") -> dict:\n'
                '    resultado = valor * 9 / 5 + 32 if para == "f" else (valor - 32) * 5 / 9\n'
                '    return {"resultado": round(resultado, 2), "escala": para}',
    }

    def setUp(self):
        super().setUp()
        OpenAICredential.store(self.user, self.KEY, ["gpt-5-mini"])

    def test_tool_is_written_by_the_model_and_still_audited(self):
        with fake_openai(json_reply(self.TOOL)) as calls:
            events = self.generate("Converter temperaturas entre Celsius e Fahrenheit")
        draft, report = events[-1]["draft"], events[-1]["report"]
        self.assertEqual(calls[0]["payload"]["model"], "gpt-5-mini")
        self.assertIn("Toolsmith", calls[0]["payload"]["messages"][0]["content"])
        # Campos fora do padrão são normalizados; o código passa pela mesma auditoria.
        self.assertEqual((draft["slug"], draft["icon"], draft["category"]), ("converter_temperatura", "i-wrench", "other"))
        self.assertEqual(report["errors"], [])
        self.assertEqual(self.create("tool", draft).status_code, 201)

    def test_dangerous_model_output_is_blocked_by_the_audit(self):
        bad = self.TOOL | {"code": "import subprocess\n\ndef run(valor, para='f'):\n    return eval(para)\n"}
        with fake_openai(json_reply(bad)):
            events = self.generate("Converter temperaturas entre Celsius e Fahrenheit")
        self.assertTrue(any("eval" in e for e in events[-1]["report"]["errors"]))
        self.assertEqual(self.create("tool", events[-1]["draft"]).status_code, 400)

    def test_falls_back_to_templates_when_the_model_fails(self):
        for response in (http_error(429), json_reply({"name": "Incompleta"})):
            with self.subTest(response=type(response).__name__), fake_openai(response):
                events = self.generate("Calcular o hash SHA-256 de um texto")
                notices = [e for e in events if e["type"] == "notice"]
                self.assertEqual(len(notices), 1)
                self.assertEqual(events[-1]["draft"]["slug"], "calcular_hash")

    def test_agent_draft_keeps_only_installed_tools(self):
        self.create_tool()  # calcular_hash, pelo modo sem modelo abaixo
        reply = {
            "name": "Guardião de integridade", "slug": "guardiao_integridade",
            "summary": "Confere a integridade de arquivos.", "description": "Compara hashes e aponta divergências.",
            "category": "security", "tags": ["hash"],
            "system_prompt": "Você verifica a integridade de arquivos comparando hashes e explica o resultado.",
            "tools": ["calcular_hash", "ferramenta_inventada"],
        }
        with fake_openai(json_reply(reply)) as calls:
            data = post_json(self.client, "/api/items/draft/", {
                "kind": "agent", "description": "Um agente que confere a integridade de arquivos",
            }).json()
        self.assertIn("tool:calcular_hash", calls[0]["payload"]["messages"][0]["content"])
        self.assertEqual(data["spec"]["name"], "Guardião de integridade")
        self.assertEqual(data["spec"]["tools"], ["calcular_hash"])
        self.assertEqual(data["spec"]["model"], "gpt-5-mini")
        self.assertIsNone(data["notice"])
        self.assertEqual(data["report"]["errors"], [])

        with fake_openai(http_error(500)):
            data = post_json(self.client, "/api/items/draft/", {
                "kind": "skill", "description": "Como escrever um relatório de incidente",
            }).json()
        self.assertIn("não conseguiu gerar", data["notice"])
        self.assertEqual(data["report"]["errors"], [])

    def create_tool(self, description="Calcular hash de um texto"):
        # Usa os modelos prontos, sem passar pelo modelo simulado.
        with mock.patch("marketplace.views._llm_call", return_value=None):
            return super().create_tool(description)


class ReviewTests(MarketplaceTestCase):
    def setUp(self):
        super().setUp()
        self.item = Item.objects.get(source="official", slug="analista_soc")
        self.url = f"/api/marketplace/{self.item.id}/review/"

    def test_review_requires_installation(self):
        self.assertEqual(post_json(self.client, self.url, {"rating": 5}).status_code, 403)

    def test_review_lifecycle_updates_average(self):
        self.client.post(f"/api/marketplace/{self.item.id}/install/")
        self.assertEqual(post_json(self.client, self.url, {"rating": 9}).status_code, 400)
        self.assertEqual(post_json(self.client, self.url, {"rating": True}).status_code, 400)
        self.assertEqual(post_json(self.client, self.url, {"rating": 5, "comment": "Excelente"}).status_code, 200)
        post_json(self.client, self.url, {"rating": 4, "comment": "Muito bom"})
        self.assertEqual(Review.objects.count(), 1)

        self.login(self.other)
        self.client.post(f"/api/marketplace/{self.item.id}/install/")
        post_json(self.client, self.url, {"rating": 2})
        self.item.refresh_from_db()
        self.assertEqual((self.item.rating_avg, self.item.rating_count), (3.0, 2))
        page = self.client.get(f"/marketplace/{self.item.id}/")
        self.assertContains(page, "Muito bom")
        self.assertContains(page, "Autora Teste")

        self.client.delete(self.url)
        self.item.refresh_from_db()
        self.assertEqual((self.item.rating_avg, self.item.rating_count), (4.0, 1))

    def test_author_cannot_review_own_item(self):
        tool = self.create_tool()
        res = post_json(self.client, f"/api/marketplace/{tool['id']}/review/", {"rating": 5})
        self.assertEqual(res.status_code, 403)


class PublicationReviewTests(MarketplaceTestCase):
    def test_item_only_becomes_public_after_approval(self):
        tool = self.create_tool()
        submit = f"/api/items/{tool['id']}/submit/"
        self.assertEqual(post_json(self.client, submit).status_code, 201)
        self.assertEqual(post_json(self.client, submit).status_code, 400)  # já está na fila

        item = Item.objects.get(pk=tool["id"])
        self.assertEqual(item.visibility, "private")
        self.assertEqual(item.sharing_status(), "Em revisão para o marketplace")
        self.assertNotContains(self.client.get("/marketplace/"), f"/marketplace/{tool['id']}/")

        # Enquanto está em revisão, outros usuários não veem.
        self.login(self.other)
        self.assertEqual(self.client.get(f"/marketplace/{tool['id']}/").status_code, 404)
        self.assertEqual(self.client.get("/marketplace/revisao/").status_code, 404)
        request = PublicationRequest.objects.get()
        self.assertEqual(post_json(self.client, f"/api/review/{request.id}/", {"decision": "approve"}).status_code, 404)

        # A equipe vê o conteúdo e aprova.
        self.login(self.staff)
        self.assertContains(self.client.get("/marketplace/revisao/"), "Calcular hash")
        self.assertContains(self.client.get(f"/marketplace/{tool['id']}/"), "Você está revisando a v1.0.0")
        self.assertEqual(post_json(self.client, f"/api/review/{request.id}/", {"decision": "approve"}).status_code, 200)

        item.refresh_from_db()
        self.assertEqual((item.visibility, item.approved_version), ("public", "1.0.0"))
        self.login(self.other)
        self.assertEqual(self.client.post(f"/api/marketplace/{tool['id']}/install/").status_code, 200)

    def test_rejection_requires_a_note_and_keeps_item_private(self):
        tool = self.create_tool()
        post_json(self.client, f"/api/items/{tool['id']}/submit/")
        self.assertEqual(self.approve(tool["id"], "reject").status_code, 400)
        self.assertEqual(self.approve(tool["id"], "reject", "Falta tratar erros de rede.").status_code, 200)
        item = Item.objects.get(pk=tool["id"])
        self.assertEqual(item.visibility, "private")
        self.assertContains(self.client.get(f"/workspace/items/{tool['id']}/"), "Falta tratar erros de rede.")
        # Pode corrigir e enviar de novo.
        self.save(tool["id"], summary="Corrigida")
        self.assertEqual(post_json(self.client, f"/api/items/{tool['id']}/submit/").status_code, 201)

    def test_cancel_submission_and_unpublish(self):
        tool = self.create_tool()
        submit = f"/api/items/{tool['id']}/submit/"
        post_json(self.client, submit)
        self.assertEqual(self.client.delete(submit).status_code, 200)
        self.assertFalse(PublicationRequest.objects.filter(status="pending").exists())

        self.publish(tool["id"])
        res = post_json(self.client, f"/api/items/{tool['id']}/share/", {"visibility": "private"})
        self.assertEqual(res.status_code, 200)
        item = Item.objects.get(pk=tool["id"])
        self.assertEqual((item.visibility, item.approved_version), ("private", ""))
        self.login(self.other)
        self.assertEqual(self.client.get(f"/marketplace/{tool['id']}/").status_code, 404)

    def test_public_item_updates_need_new_approval(self):
        tool = self.create_tool()
        self.publish(tool["id"])

        self.login(self.other)
        self.client.post(f"/api/marketplace/{tool['id']}/install/")
        installation = Installation.objects.get(workspace=personal_workspace(self.other))
        self.assertEqual(installation.installed_version, "1.0.0")
        original = self.client.get("/api/workspace/tools/").json()["tools"][0]

        # O autor publica uma nova versão com outro schema.
        self.login(self.user)
        self.save(tool["id"], input_schema={
            "type": "object",
            "properties": {"texto": {"type": "string", "description": "Entrada."},
                           "algoritmo": {"type": "string", "description": "Novo texto."}},
            "required": ["texto"],
        })
        item = Item.objects.get(pk=tool["id"])
        self.assertEqual((item.version, item.approved_version), ("1.0.1", "1.0.0"))

        # Sem aprovação, quem instalou não vê atualização nem recebe o conteúdo novo.
        self.login(self.other)
        installation.refresh_from_db()
        self.assertFalse(installation.update_available)
        self.assertEqual(self.client.get("/api/workspace/tools/").json()["tools"][0], original)
        self.assertNotContains(self.client.get(f"/marketplace/{tool['id']}/"), "Novo texto.")

        # Aprovada a v1.0.1, aparece o aviso; o conteúdo só muda quando o admin atualiza.
        self.login(self.user)
        self.publish(tool["id"])
        self.login(self.other)
        installation.refresh_from_db()
        self.assertTrue(installation.update_available)
        self.assertContains(self.client.get("/workspace/"), "v1.0.1 disponível")
        self.assertEqual(self.client.get("/api/workspace/tools/").json()["tools"][0], original)

        self.assertEqual(self.client.post(f"/api/workspace/items/{tool['id']}/upgrade/").status_code, 200)
        updated = self.client.get("/api/workspace/tools/").json()["tools"][0]
        self.assertEqual(updated["input_schema"]["properties"]["algoritmo"]["description"], "Novo texto.")
        self.assertNotContains(self.client.get("/workspace/"), "disponível</span>")


class OrganizationMarketplaceTests(MarketplaceTestCase):
    def setUp(self):
        super().setUp()
        self.org = create_organization(self.user, "Deimos Lab")
        self.member = User.objects.create_user("membro@exemplo.com", PASSWORD, full_name="Marcos Membro")
        Membership.objects.create(workspace=self.org, user=self.member, role="member")
        self.login(self.user, self.org)

    def test_org_workspace_is_shared(self):
        tool = self.create_tool()
        item = Item.objects.get(pk=tool["id"])
        self.assertEqual((item.workspace, item.author), (self.org, "Deimos Lab"))

        self.login(self.member, self.org)
        self.assertContains(self.client.get("/workspace/"), "Calcular hash")
        self.assertEqual(len(self.client.get("/api/workspace/tools/").json()["tools"]), 1)
        # No workspace pessoal do membro, nada.
        self.login(self.member, personal_workspace(self.member))
        self.assertEqual(self.client.get("/api/workspace/tools/").json()["tools"], [])

    def test_member_permissions(self):
        admin_tool = self.create_tool()
        official = Item.objects.get(source="official", slug="analista_soc")
        self.client.post(f"/api/marketplace/{official.id}/install/")

        self.login(self.member, self.org)
        # Não instala, remove, desativa nem atualiza.
        other = Item.objects.get(source="official", slug="resumo_executivo")
        self.assertEqual(self.client.post(f"/api/marketplace/{other.id}/install/").status_code, 403)
        self.assertEqual(self.client.post(f"/api/marketplace/{official.id}/uninstall/").status_code, 403)
        self.assertEqual(post_json(self.client, f"/api/workspace/items/{official.id}/", {"enabled": False}, "patch").status_code, 403)
        self.assertEqual(self.client.post(f"/api/workspace/items/{official.id}/upgrade/").status_code, 403)
        # Não altera o que não criou, nem compartilha.
        self.assertEqual(self.client.delete(f"/api/items/{admin_tool['id']}/").status_code, 403)
        self.assertEqual(post_json(self.client, f"/api/items/{admin_tool['id']}/", {"spec": {}}, "put").status_code, 403)
        self.assertRedirects(self.client.get(f"/workspace/items/{admin_tool['id']}/"), f"/marketplace/{admin_tool['id']}/")
        self.assertEqual(post_json(self.client, f"/api/items/{admin_tool['id']}/share/", {"visibility": "organization"}).status_code, 403)
        self.assertEqual(post_json(self.client, f"/api/items/{admin_tool['id']}/submit/").status_code, 403)

        # Mas cria e edita o que é seu; o admin também pode editar.
        mine = self.create("agent", self.agent_spec()).json()["item"]
        self.save(mine["id"], summary="Editado pelo membro")
        self.login(self.user, self.org)
        self.save(mine["id"], summary="Editado pelo admin")
        self.assertEqual(self.client.delete(f"/api/items/{mine['id']}/").status_code, 200)

    def test_private_marketplace_of_the_organization(self):
        tool = self.create_tool()
        detail = f"/marketplace/{tool['id']}/"

        # Antes de compartilhar, o item não aparece no catálogo de ninguém.
        self.login(self.member, personal_workspace(self.member))
        self.assertNotContains(self.client.get("/marketplace/"), detail)

        self.login(self.user, self.org)
        res = post_json(self.client, f"/api/items/{tool['id']}/share/", {"visibility": "organization"})
        self.assertEqual(res.json()["status"], "Visível na organização")

        # Membros veem no marketplace e instalam no workspace pessoal.
        self.login(self.member, personal_workspace(self.member))
        catalog = self.client.get("/marketplace/")
        self.assertContains(catalog, detail)
        self.assertContains(catalog, "Só da organização")
        self.assertEqual(self.client.post(f"/api/marketplace/{tool['id']}/install/").status_code, 200)
        self.assertEqual(len(self.client.get("/api/workspace/tools/").json()["tools"]), 1)

        # Quem é de fora não vê nem instala.
        self.login(self.other)
        self.assertNotContains(self.client.get("/marketplace/"), detail)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.post(f"/api/marketplace/{tool['id']}/install/").status_code, 404)

        # Dentro da organização, atualizações chegam sem revisão — mas com aviso.
        self.login(self.user, self.org)
        self.save(tool["id"], summary="Versão interna nova")
        self.login(self.member, personal_workspace(self.member))
        self.assertContains(self.client.get("/workspace/"), "v1.0.1 disponível")

    def test_personal_items_cannot_be_shared_with_organization(self):
        self.login(self.user, personal_workspace(self.user))
        tool = self.create_tool()
        res = post_json(self.client, f"/api/items/{tool['id']}/share/", {"visibility": "organization"})
        self.assertEqual(res.status_code, 400)

    def test_deleting_organization_removes_its_items(self):
        tool = self.create_tool()
        self.assertEqual(self.client.delete("/api/organization/").status_code, 200)
        self.assertFalse(Item.objects.filter(pk=tool["id"]).exists())
        self.assertFalse(Workspace.objects.filter(pk=self.org.pk).exists())
