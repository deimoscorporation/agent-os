from urllib.error import URLError

from django.test import TestCase

from core.testing import fake_openai, http_error, models_reply

from .models import OpenAICredential, User

PASSWORD = "Segredo!Forte42"
OPENAI_KEY = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789ABCD"


class RegisterTests(TestCase):
    def register(self, **overrides):
        data = {
            "full_name": "Maria Silva",
            "email": "Maria@Exemplo.com",
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        data.update(overrides)
        return self.client.post("/cadastro/", data)

    def test_register_creates_account_and_logs_in(self):
        res = self.register()
        self.assertRedirects(res, "/")
        user = User.objects.get()
        self.assertEqual(user.email, "maria@exemplo.com")
        self.assertEqual(user.full_name, "Maria Silva")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertContains(self.client.get("/"), "Maria Silva")

    def test_register_validations(self):
        cases = [
            ({"full_name": "Maria"}, "Informe nome e sobrenome."),
            ({"password2": "outra-coisa-99"}, "As senhas não coincidem."),
            ({"password1": "123", "password2": "123"}, "muito curta"),
            ({"password1": "12345678", "password2": "12345678"}, "inteiramente numérica"),
            ({"email": "invalido"}, "endereço de email válido"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                res = self.register(**overrides)
                self.assertEqual(res.status_code, 200)
                self.assertContains(res, message)
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_email_is_rejected_case_insensitively(self):
        User.objects.create_user("maria@exemplo.com", PASSWORD, full_name="Maria Silva")
        res = self.register(email="MARIA@exemplo.com")
        self.assertContains(res, "Já existe uma conta com este e-mail.")
        self.assertEqual(User.objects.count(), 1)


class LoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("joao@exemplo.com", PASSWORD, full_name="João Souza")

    def test_pages_require_login(self):
        for url in ["/", "/marketplace/", "/workspace/", "/conta/", "/api/conversations/"]:
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), f"/entrar/?next={url}")
        self.assertEqual(self.client.get("/entrar/").status_code, 200)
        self.assertEqual(self.client.get("/cadastro/").status_code, 200)

    def test_login_and_next_redirect(self):
        res = self.client.post("/entrar/?next=/workspace/", {
            "email": "JOAO@exemplo.com", "password": PASSWORD, "next": "/workspace/", "remember": "on",
        })
        self.assertRedirects(res, "/workspace/")
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_login_without_remember_expires_with_browser(self):
        self.client.post("/entrar/", {"email": "joao@exemplo.com", "password": PASSWORD})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_wrong_password(self):
        res = self.client.post("/entrar/", {"email": "joao@exemplo.com", "password": "errada"})
        self.assertContains(res, "E-mail ou senha incorretos.")
        self.assertRedirects(self.client.get("/"), "/entrar/?next=/")

    def test_open_redirect_is_blocked(self):
        res = self.client.post("/entrar/", {
            "email": "joao@exemplo.com", "password": PASSWORD, "next": "https://malicioso.example/",
        })
        self.assertRedirects(res, "/")

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/sair/").status_code, 405)
        self.assertRedirects(self.client.post("/sair/"), "/entrar/")
        self.assertRedirects(self.client.get("/"), "/entrar/?next=/")

    def test_logged_user_skips_auth_pages(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/entrar/"), "/")
        self.assertRedirects(self.client.get("/cadastro/"), "/")


class AccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana@exemplo.com", PASSWORD, full_name="Ana Lima")
        self.client.force_login(self.user)

    def test_update_name(self):
        res = self.client.post("/conta/", {"form": "profile", "profile-full_name": "Ana Lima Costa"})
        self.assertRedirects(res, "/conta/")
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Ana Lima Costa")
        self.assertEqual(self.user.initials, "AC")

    def test_change_password_keeps_session(self):
        new = "OutraSenha!Forte77"
        res = self.client.post("/conta/", {
            "form": "password",
            "password-old_password": PASSWORD,
            "password-new_password1": new,
            "password-new_password2": new,
        })
        self.assertRedirects(res, "/conta/")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new))
        self.assertEqual(self.client.get("/conta/").status_code, 200)

    def test_openai_key_is_validated_encrypted_and_never_shown(self):
        page = self.client.get("/conta/")
        self.assertContains(page, "Não configurada")

        with fake_openai(models_reply("gpt-4o-mini", "gpt-5", "text-embedding-3-small")) as calls:
            res = self.client.post("/conta/", {"form": "openai", "api_key": f"  {OPENAI_KEY} "})
        self.assertRedirects(res, "/conta/")
        self.assertEqual(calls[0]["headers"]["Authorization"], f"Bearer {OPENAI_KEY}")

        credential = OpenAICredential.objects.get(user=self.user)
        self.assertNotIn(OPENAI_KEY, credential.ciphertext)
        self.assertEqual(credential.reveal(), OPENAI_KEY)
        self.assertEqual(credential.available_models, ["gpt-5", "gpt-4o-mini"])
        self.assertIsNotNone(credential.validated_at)

        page = self.client.get("/conta/").content.decode()
        self.assertIn("Configurada", page)
        self.assertIn("••••••••" + OPENAI_KEY[-4:], page)
        self.assertNotIn(OPENAI_KEY, page)
        # O seletor do chat passa a mostrar os modelos da chave.
        chat = self.client.get("/")
        self.assertContains(chat, '<option value="gpt-4o-mini">')
        self.assertNotContains(chat, '<option value="gpt-4.1">')
        self.assertNotContains(chat, "Modo de demonstração")

    def test_invalid_openai_key_is_rejected(self):
        with fake_openai(http_error(401)):
            res = self.client.post("/conta/", {"form": "openai", "api_key": OPENAI_KEY})
        self.assertContains(res, "inválida ou foi revogada")
        self.assertNotContains(res, OPENAI_KEY)
        with fake_openai():  # formato errado nem chega a chamar a OpenAI
            res = self.client.post("/conta/", {"form": "openai", "api_key": "curta"})
            self.assertContains(res, "Cole a chave completa")
            res = self.client.post("/conta/", {"form": "openai", "api_key": "sk-com espaco no meio 123456789"})
            self.assertContains(res, "Cole a chave completa")
        self.assertFalse(OpenAICredential.objects.exists())

    def test_openai_key_is_kept_when_validation_cannot_run(self):
        with fake_openai(URLError("sem rede")):
            self.client.post("/conta/", {"form": "openai", "api_key": OPENAI_KEY})
        credential = OpenAICredential.objects.get()
        self.assertIsNone(credential.validated_at)
        self.assertEqual(credential.reveal(), OPENAI_KEY)
        self.assertContains(self.client.get("/conta/"), "Ainda não validada")

    def test_replace_and_remove_openai_key(self):
        other_key = "sk-outra-chave-0000000000000000000000WXYZ"
        with fake_openai(models_reply("gpt-5"), models_reply("gpt-5-mini")):
            self.client.post("/conta/", {"form": "openai", "api_key": OPENAI_KEY})
            self.client.post("/conta/", {"form": "openai", "api_key": other_key})
        self.assertEqual(OpenAICredential.objects.get().reveal(), other_key)
        self.assertRedirects(self.client.post("/conta/", {"form": "openai-remove"}), "/conta/")
        self.assertFalse(OpenAICredential.objects.exists())

    def test_change_password_requires_current(self):
        res = self.client.post("/conta/", {
            "form": "password",
            "password-old_password": "errada",
            "password-new_password1": "OutraSenha!Forte77",
            "password-new_password2": "OutraSenha!Forte77",
        })
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
