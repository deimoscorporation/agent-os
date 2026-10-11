import json
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import OpenAICredential, User
from core.testing import fake_openai, http_error, sse

from .models import Attachment, Conversation, Message

PASSWORD = "Segredo!Forte42"
MEDIA = tempfile.mkdtemp(prefix="deimos-test-media-")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


@mock.patch("chat.services.DEMO_DELAY", 0)
@override_settings(MEDIA_ROOT=MEDIA)
class ChatTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("user@exemplo.com", PASSWORD, full_name="Usuário Teste")
        self.other = User.objects.create_user("outro@exemplo.com", PASSWORD, full_name="Outro Usuário")
        self.client.force_login(self.user)

    def send(self, message, conversation_id=None, **extra):
        res = self.client.post(
            "/api/chat/",
            json.dumps({"message": message, "conversation_id": conversation_id, **extra}),
            content_type="application/json",
        )
        body = b"".join(res.streaming_content).decode() if res.status_code == 200 else res.content.decode()
        return res, body

    def patch(self, cid, **data):
        return self.client.patch(f"/api/conversations/{cid}/", json.dumps(data), content_type="application/json")

    def upload(self, name="notas.txt", content=b"Reuniao de quinta: revisar o plano de resposta a incidentes."):
        return self.client.post("/api/attachments/", {"file": SimpleUploadedFile(name, content)})


class ChatFlowTests(ChatTestCase):
    def test_index_renders(self):
        res = self.client.get("/")
        self.assertContains(res, "Deimos")
        self.assertContains(res, '<option value="gpt-4.1">')
        self.assertContains(res, "Modo de demonstração")

    def test_full_flow(self):
        res, body = self.send("Olá, Deimos")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Olá, Deimos", body)
        cid = res["X-Conversation-Id"]
        conv = Conversation.objects.get(pk=cid)
        self.assertEqual(conv.title, "Olá, Deimos")
        self.assertEqual(conv.user, self.user)
        self.assertEqual(conv.messages.count(), 2)

        self.send("Segunda", cid)
        self.assertEqual(conv.messages.count(), 4)

        listing = self.client.get("/api/conversations/").json()
        self.assertEqual([c["id"] for c in listing["conversations"]], [cid])

        self.patch(cid, title="Renomeada")
        conv.refresh_from_db()
        self.assertEqual(conv.title, "Renomeada")

        res = self.client.post(f"/api/conversations/{cid}/regenerate/")
        b"".join(res.streaming_content)
        self.assertEqual(conv.messages.count(), 4)
        self.assertEqual(conv.messages.last().role, Message.Role.ASSISTANT)

        self.assertEqual(self.client.get(f"/c/{cid}/").status_code, 200)
        self.client.delete(f"/api/conversations/{cid}/")
        self.assertFalse(Conversation.objects.exists())

    def test_conversations_are_isolated_per_user(self):
        res, _ = self.send("Privado")
        cid = res["X-Conversation-Id"]
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/conversations/{cid}/").status_code, 404)
        self.assertEqual(self.client.get(f"/c/{cid}/").status_code, 404)
        self.assertEqual(self.client.get(f"/c/{cid}/export.md").status_code, 404)
        self.assertEqual(self.client.post(f"/api/conversations/{cid}/share/").status_code, 404)
        self.assertEqual(self.client.get("/api/conversations/").json()["conversations"], [])

    def test_empty_message_rejected(self):
        res = self.client.post("/api/chat/", json.dumps({"message": "  "}), content_type="application/json")
        self.assertEqual(res.status_code, 400)


class ModelSelectionTests(ChatTestCase):
    def test_model_is_chosen_per_conversation_and_can_change(self):
        res, body = self.send("Olá", model="gpt-4o-mini")
        cid = res["X-Conversation-Id"]
        self.assertIn("gpt-4o-mini", body)
        self.assertEqual(Conversation.objects.get(pk=cid).model, "gpt-4o-mini")

        self.assertEqual(self.patch(cid, model="gpt-4.1").status_code, 200)
        _, body = self.send("De novo", cid)
        self.assertIn("gpt-4.1", body)
        self.assertEqual(self.client.get("/api/conversations/").json()["conversations"][0]["model"], "gpt-4.1")

    def test_default_and_invalid_model(self):
        _, body = self.send("Olá")
        self.assertIn("gpt-5-mini", body)
        res, _ = self.send("Olá", model="modelo-inventado")
        self.assertEqual(res.status_code, 400)
        cid = Conversation.objects.get().id
        self.assertEqual(self.patch(cid, model="modelo-inventado").status_code, 400)


class AttachmentTests(ChatTestCase):
    def test_upload_send_and_download(self):
        text_id = self.upload().json()["id"]
        image = self.upload("print.png", PNG).json()
        self.assertEqual(image["kind"], "image")
        self.assertEqual(Attachment.objects.get(pk=text_id).text[:7], "Reuniao")

        res, body = self.send("Resuma o anexo", attachment_ids=[text_id, image["id"]])
        self.assertEqual(res.status_code, 200)
        self.assertIn("notas.txt", body)
        self.assertIn("Reuniao de quinta", body)
        self.assertIn("print.png", body)

        message = Message.objects.filter(role="user").get()
        self.assertEqual(message.attachments.count(), 2)
        detail = self.client.get(f"/api/conversations/{res['X-Conversation-Id']}/").json()
        self.assertEqual([a["name"] for a in detail["messages"][0]["attachments"]], ["notas.txt", "print.png"])

        # Imagem abre no navegador; outros arquivos sempre baixam, sem execução de conteúdo.
        img = self.client.get(image["url"])
        self.assertEqual(img["Content-Type"], "image/png")
        self.assertIn("inline", img["Content-Disposition"])
        self.assertEqual(img["X-Content-Type-Options"], "nosniff")
        doc = self.client.get(f"/attachments/{text_id}/")
        self.assertEqual(doc["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", doc["Content-Disposition"])

    def test_message_can_be_only_attachments(self):
        attachment = self.upload().json()
        res, _ = self.send("", attachment_ids=[attachment["id"]])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Conversation.objects.get().title, "notas.txt")

    def test_attachments_are_private_and_single_use(self):
        attachment = self.upload().json()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(attachment["url"]).status_code, 404)
        res, _ = self.send("Roubando anexo", attachment_ids=[attachment["id"]])
        self.assertEqual(res.status_code, 400)

        self.client.force_login(self.user)
        self.send("Uso legítimo", attachment_ids=[attachment["id"]])
        res, _ = self.send("Reuso", attachment_ids=[attachment["id"]])
        self.assertEqual(res.status_code, 400)
        res, _ = self.send("Lixo", attachment_ids=["nao-e-uuid"])
        self.assertEqual(res.status_code, 400)

    def test_upload_validation(self):
        cases = [
            ("script.exe", b"MZ" + b"0" * 30),
            ("falsa.png", b"<html><script>alert(1)</script></html>"),
            ("imagem.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
            ("vazio.txt", b""),
        ]
        for name, content in cases:
            with self.subTest(name=name):
                self.assertEqual(self.upload(name, content).status_code, 400)
        self.assertFalse(Attachment.objects.exists())
        res, _ = self.send("Muitos", attachment_ids=["x"] * 6)
        self.assertEqual(res.status_code, 400)

    def test_unsent_attachment_can_be_removed_and_files_are_cleaned(self):
        attachment = self.upload().json()
        path = Attachment.objects.get(pk=attachment["id"]).file.path
        self.assertEqual(self.client.delete(attachment["url"]).status_code, 200)
        with self.assertRaises(FileNotFoundError):
            open(path)

        # Excluir a conversa apaga também os arquivos dos anexos.
        sent = self.upload().json()
        res, _ = self.send("Com anexo", attachment_ids=[sent["id"]])
        path = Attachment.objects.get(pk=sent["id"]).file.path
        self.assertEqual(self.client.delete(sent["url"]).status_code, 400)  # já faz parte de uma mensagem
        self.client.delete(f"/api/conversations/{res['X-Conversation-Id']}/")
        self.assertFalse(Attachment.objects.exists())
        with self.assertRaises(FileNotFoundError):
            open(path)


class KnowledgeChatTests(ChatTestCase):
    POLICY = (
        "Política de senhas\n\nTodas as senhas devem ter no mínimo 14 caracteres.\n\n"
        "A troca é obrigatória a cada 180 dias."
    )

    def setUp(self):
        super().setUp()
        file = SimpleUploadedFile("politica.md", self.POLICY.encode())
        self.assertEqual(self.client.post("/api/knowledge/", {"file": file}).status_code, 201)

    def test_answer_cites_sources_when_enabled(self):
        res, body = self.send("Quantos caracteres a senha precisa ter?", use_knowledge=True)
        cid = res["X-Conversation-Id"]
        self.assertIn("Base de conhecimento", body)
        self.assertIn("14 caracteres", body)
        reply = self.client.get(f"/api/conversations/{cid}/").json()["messages"][-1]
        self.assertEqual(reply["sources"][0]["document"], "politica.md")
        self.assertIn("14 caracteres", reply["sources"][0]["excerpt"])

        # Pergunta sem relação: avisa que não achou e não cita fontes.
        _, body = self.send("Qual a capital da Austrália?", cid)
        self.assertIn("não encontrei nada", body)
        self.assertEqual(self.client.get(f"/api/conversations/{cid}/").json()["messages"][-1]["sources"], [])

    def test_disabled_by_default_and_toggle(self):
        res, body = self.send("Quantos caracteres a senha precisa ter?")
        cid = res["X-Conversation-Id"]
        self.assertNotIn("Base de conhecimento", body)
        self.assertEqual(self.patch(cid, use_knowledge=True).status_code, 200)
        _, body = self.send("E a troca de senha?", cid)
        self.assertIn("180 dias", body)
        self.assertEqual(self.patch(cid, use_knowledge="sim").status_code, 400)


class OpenAIChatTests(ChatTestCase):
    """Com a chave do usuário, a resposta vem da OpenAI (aqui simulada)."""

    KEY = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789ABCD"

    def setUp(self):
        super().setUp()
        OpenAICredential.store(self.user, self.KEY, ["gpt-5-mini", "gpt-4o-mini"])

    def test_reply_is_streamed_from_openai_and_saved(self):
        with fake_openai(sse("A capital ", "é **Paris**.")) as calls:
            res, body = self.send("Qual a capital da França?", model="gpt-4o-mini")
        self.assertEqual(body, "A capital é **Paris**.")
        self.assertEqual(Message.objects.get(role="assistant").content, "A capital é **Paris**.")

        call = calls[0]
        self.assertEqual(call["url"], "https://api.openai.test/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {self.KEY}")
        self.assertEqual(call["payload"]["model"], "gpt-4o-mini")
        self.assertTrue(call["payload"]["stream"])
        messages = call["payload"]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Deimos Agent OS", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "user", "content": "Qual a capital da França?"})

        # A conversa seguinte leva o histórico completo.
        with fake_openai(sse("Cerca de 2 milhões.")) as calls:
            self.send("E a população?", res["X-Conversation-Id"])
        self.assertEqual([m["role"] for m in calls[0]["payload"]["messages"]], ["system", "user", "assistant", "user"])

    def test_attachments_and_knowledge_reach_the_model(self):
        doc = SimpleUploadedFile("politica.md", "Senhas devem ter no mínimo 14 caracteres.\n\nTroca a cada 180 dias.".encode())
        self.client.post("/api/knowledge/", {"file": doc})
        text_id = self.upload().json()["id"]
        image_id = self.upload("print.png", PNG).json()["id"]

        with fake_openai(sse("ok")) as calls:
            self.send("Quantos caracteres a senha precisa?", attachment_ids=[text_id, image_id], use_knowledge=True)
        system, user = calls[0]["payload"]["messages"]
        self.assertIn("[politica.md]", system["content"])
        self.assertIn("14 caracteres", system["content"])
        text_part, image_part = user["content"]
        self.assertIn("[Arquivo anexado: notas.txt]", text_part["text"])
        self.assertIn("Reuniao de quinta", text_part["text"])
        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/png;base64,"))
        # A resposta continua citando as fontes usadas.
        self.assertEqual(Message.objects.get(role="assistant").sources[0]["document"], "politica.md")

    def test_openai_error_is_shown_but_not_saved(self):
        with fake_openai(http_error(401)):
            res, body = self.send("Olá")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Não foi possível obter a resposta", body)
        self.assertIn("/conta/#openai", body)
        self.assertNotIn(self.KEY, body)
        self.assertFalse(Message.objects.filter(role="assistant").exists())
        # A pergunta ficou salva: dá para corrigir a chave e gerar de novo.
        with fake_openai(sse("Agora foi.")):
            regen = self.client.post(f"/api/conversations/{res['X-Conversation-Id']}/regenerate/")
            self.assertEqual(b"".join(regen.streaming_content).decode(), "Agora foi.")
        self.assertEqual(Message.objects.filter(role="assistant").count(), 1)

    def test_model_outside_the_key_is_rejected_or_replaced(self):
        res, _ = self.send("Olá", model="gpt-4.1")  # a chave não tem acesso
        self.assertEqual(res.status_code, 400)
        # Conversa antiga com modelo que a chave não alcança: usa o padrão da chave.
        conversation = Conversation.objects.create(user=self.user, title="Antiga", model="gpt-4.1")
        with fake_openai(sse("ok")) as calls:
            self.send("Olá", str(conversation.id))
        self.assertEqual(calls[0]["payload"]["model"], "gpt-5-mini")

    def test_each_user_uses_only_their_own_key(self):
        self.client.force_login(self.other)
        with fake_openai():  # qualquer chamada à OpenAI falharia o teste
            _, body = self.send("Olá")
        self.assertIn("Nenhuma chave de API configurada", body)


class ShareExportTests(ChatTestCase):
    def setUp(self):
        super().setUp()
        attachment = self.upload().json()
        res, _ = self.send("Pergunta **importante**", attachment_ids=[attachment["id"]])
        self.cid = res["X-Conversation-Id"]
        self.attachment_url = attachment["url"]

    def test_share_link_is_public_readonly_and_revocable(self):
        url = f"/api/conversations/{self.cid}/share/"
        data = self.client.post(url).json()
        self.assertTrue(data["shared"])
        self.assertEqual(self.client.post(url).json()["url"], data["url"])  # idempotente
        path = "/s/" + data["url"].split("/s/")[1]

        anonymous = self.client_class()
        page = anonymous.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Pergunta **importante**")
        self.assertContains(page, "Somente leitura")
        self.assertContains(page, "notas.txt")
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")
        # O link não dá acesso aos arquivos nem a outras áreas.
        self.assertNotContains(page, self.attachment_url)
        self.assertEqual(anonymous.get(self.attachment_url).status_code, 302)
        self.assertEqual(anonymous.get(f"/api/conversations/{self.cid}/").status_code, 302)

        self.assertFalse(self.client.delete(url).json()["shared"])
        self.assertEqual(anonymous.get(path).status_code, 404)
        self.assertEqual(anonymous.get("/s/token-inexistente/").status_code, 404)

    def test_shared_page_escapes_message_content(self):
        self.send("</script><script>alert('xss')</script>", self.cid)
        path = "/s/" + self.client.post(f"/api/conversations/{self.cid}/share/").json()["url"].split("/s/")[1]
        page = self.client_class().get(path).content.decode()
        self.assertNotIn("<script>alert('xss')</script>", page)

    def test_export_markdown_and_json(self):
        md = self.client.get(f"/c/{self.cid}/export.md")
        self.assertEqual(md["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn('filename="pergunta-importante.md"', md["Content-Disposition"])
        text = md.content.decode()
        self.assertIn("# Pergunta **importante**", text)
        self.assertIn("## Você", text)
        self.assertIn("## Assistente Deimos", text)
        self.assertIn("**Anexos:** notas.txt", text)

        data = json.loads(self.client.get(f"/c/{self.cid}/export.json").content)
        self.assertEqual([m["role"] for m in data["messages"]], ["user", "assistant"])
        self.assertEqual(data["messages"][0]["attachments"], ["notas.txt"])
        self.assertEqual(self.client.get(f"/c/{self.cid}/export.pdf").status_code, 404)
