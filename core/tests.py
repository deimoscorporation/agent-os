from urllib.error import URLError

from django.test import SimpleTestCase, TestCase

from accounts.models import OpenAICredential, User
from workspaces.services import personal_workspace, set_secret

from . import crypto, llm
from .testing import fake_openai, http_error, json_reply, models_reply, sse

KEY = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789ABCD"


class CryptoTests(SimpleTestCase):
    def test_roundtrip_and_hint(self):
        token = crypto.encrypt(KEY)
        self.assertNotIn(KEY, token)
        self.assertEqual(crypto.decrypt(token), KEY)
        self.assertEqual(crypto.hint(KEY), "ABCD")
        self.assertEqual(crypto.hint("curto"), "")
        with self.assertRaises(ValueError):
            crypto.decrypt("lixo")


class LLMClientTests(SimpleTestCase):
    def test_list_models_keeps_only_chat_models_with_preferred_first(self):
        body = models_reply(
            "gpt-4o-mini", "text-embedding-3-small", "gpt-5", "whisper-1", "gpt-4o-audio-preview",
            "dall-e-3", "o3-mini", "gpt-5-mini", "gpt-4o-realtime-preview", "tts-1", "id com espaço",
        )
        with fake_openai(body) as calls:
            models = llm.list_models(KEY)
        self.assertEqual(models[:3], ["gpt-5-mini", "gpt-5", "gpt-4o-mini"])
        self.assertEqual(set(models), {"gpt-5-mini", "gpt-5", "gpt-4o-mini", "o3-mini"})
        self.assertEqual(calls[0]["url"], "https://api.openai.test/v1/models")
        self.assertEqual(calls[0]["headers"]["Authorization"], f"Bearer {KEY}")

    def test_stream_chat_yields_text_and_sends_payload(self):
        with fake_openai(sse("Olá", ", ", "mundo")) as calls:
            text = "".join(llm.stream_chat(KEY, "gpt-5", [{"role": "user", "content": "oi"}]))
        self.assertEqual(text, "Olá, mundo")
        self.assertEqual(calls[0]["payload"], {
            "model": "gpt-5", "messages": [{"role": "user", "content": "oi"}], "stream": True,
        })

    def test_errors_become_friendly_messages_without_leaking_the_key(self):
        cases = [
            (http_error(401), "inválida ou foi revogada", 401),
            (http_error(429), "cota", 429),
            (http_error(404), "não está disponível", 404),
            (http_error(500), "indisponível", 500),
            (http_error(400, "max tokens"), "max tokens", 400),
            (URLError("sem rede"), "conectar", None),
        ]
        for error, expected, status in cases:
            with self.subTest(status=status), fake_openai(error):
                with self.assertRaises(llm.LLMError) as caught:
                    list(llm.stream_chat(KEY, "gpt-5", []))
                self.assertIn(expected, str(caught.exception))
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn(KEY, str(caught.exception))

    def test_complete_json(self):
        with fake_openai(json_reply({"name": "Teste"})) as calls:
            self.assertEqual(llm.complete_json(KEY, "gpt-5", "sistema", "pedido"), {"name": "Teste"})
        self.assertEqual(calls[0]["payload"]["response_format"], {"type": "json_object"})
        with fake_openai(b'{"choices": [{"message": {"content": "isto nao e json"}}]}'):
            with self.assertRaises(llm.LLMError):
                llm.complete_json(KEY, "gpt-5", "s", "u")

    def test_pick_model_falls_back_when_key_has_no_access(self):
        creds = llm.Credentials(KEY, "user", ["gpt-4o-mini", "gpt-4.1"])
        self.assertEqual(creds.pick_model("gpt-4.1"), "gpt-4.1")
        self.assertEqual(creds.pick_model("gpt-5"), "gpt-4.1")  # primeiro preferido que a chave alcança
        self.assertEqual(creds.pick_model(""), "gpt-4.1")
        self.assertEqual(llm.Credentials(KEY, "workspace").pick_model("modelo-x"), "gpt-5-mini")


class CredentialResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana@exemplo.com", "Segredo!Forte42", full_name="Ana Lima")
        self.workspace = personal_workspace(self.user)

    def test_no_key_means_demo_mode(self):
        self.assertIsNone(llm.credentials_for(self.user, self.workspace))
        self.assertEqual(llm.models_for(self.user), ["gpt-5-mini", "gpt-5", "gpt-4.1", "gpt-4o-mini"])

    def test_user_key_wins_over_workspace_and_instance(self):
        set_secret(self.workspace, "OPENAI_API_KEY", "sk-do-workspace-000000000000", self.user)
        creds = llm.credentials_for(self.user, self.workspace)
        self.assertEqual((creds.api_key, creds.source), ("sk-do-workspace-000000000000", "workspace"))

        with self.settings(OPENAI_API_KEY="sk-da-instancia-00000000000"):
            self.assertEqual(llm.credentials_for(self.user, None).source, "instance")

        OpenAICredential.store(self.user, KEY, ["gpt-4.1"])
        self.user.refresh_from_db()
        creds = llm.credentials_for(self.user, self.workspace)
        self.assertEqual((creds.api_key, creds.source, creds.models), (KEY, "user", ["gpt-4.1"]))
        self.assertEqual(llm.models_for(self.user), ["gpt-4.1"])
