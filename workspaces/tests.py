import json

from django.test import TestCase

from accounts.models import User

from .models import Invitation, Membership, Secret, Workspace
from .services import personal_workspace, reveal_secret, secrets_for

PASSWORD = "Segredo!Forte42"


def post_json(client, url, data=None, method="post"):
    return getattr(client, method)(url, json.dumps(data or {}), content_type="application/json")


class WorkspaceTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin@exemplo.com", PASSWORD, full_name="Ana Admin")
        self.member = User.objects.create_user("membro@exemplo.com", PASSWORD, full_name="Marcos Membro")
        self.outsider = User.objects.create_user("fora@exemplo.com", PASSWORD, full_name="Fábio Fora")
        self.client.force_login(self.admin)

    def create_org(self, name="Deimos Lab"):
        res = post_json(self.client, "/api/organizations/", {"name": name})
        self.assertEqual(res.status_code, 201, res.content)
        return Workspace.objects.get(pk=res.json()["id"])

    def switch(self, workspace):
        return self.client.post("/workspaces/switch/", {"workspace_id": str(workspace.id)})

    def add_member(self, org, user=None, role="member"):
        user = user or self.member
        res = post_json(self.client, "/api/organization/members/", {"email": user.email, "role": role})
        self.assertEqual(res.status_code, 201, res.content)
        return Membership.objects.get(workspace=org, user=user)


class OrganizationTests(WorkspaceTestCase):
    def test_every_user_gets_a_personal_workspace(self):
        workspace = Workspace.objects.get(owner=self.admin)
        self.assertTrue(workspace.is_personal)
        self.assertTrue(Membership.objects.get(workspace=workspace, user=self.admin).is_admin)
        self.assertContains(self.client.get("/workspace/"), "Workspace pessoal")

    def test_create_organization_makes_it_active(self):
        org = self.create_org()
        self.assertTrue(org.is_organization)
        page = self.client.get("/workspace/")
        self.assertContains(page, "Deimos Lab")
        self.assertContains(page, "Organização · Admin")
        # O pessoal continua existindo e dá para voltar.
        self.switch(personal_workspace(self.admin))
        self.assertContains(self.client.get("/workspace/"), "Workspace pessoal")

    def test_cannot_switch_to_foreign_workspace(self):
        org = self.create_org()
        self.client.force_login(self.outsider)
        self.assertEqual(self.switch(org).status_code, 404)
        self.assertNotContains(self.client.get("/workspace/"), "Deimos Lab")

    def test_add_existing_user_and_shared_view(self):
        org = self.create_org()
        self.add_member(org)
        self.client.force_login(self.member)
        self.switch(org)
        page = self.client.get("/equipe/")
        self.assertContains(page, "Ana Admin")
        self.assertContains(page, "você é membro")

    def test_invitation_is_accepted_on_register(self):
        org = self.create_org()
        res = post_json(self.client, "/api/organization/members/", {"email": "Nova@Exemplo.com", "role": "admin"})
        self.assertTrue(res.json()["invited"])
        self.assertContains(self.client.get("/equipe/"), "nova@exemplo.com")

        self.client.logout()
        self.client.post("/cadastro/", {
            "full_name": "Nova Pessoa", "email": "nova@exemplo.com", "password1": PASSWORD, "password2": PASSWORD,
        })
        new_user = User.objects.get(email="nova@exemplo.com")
        self.assertTrue(Membership.objects.get(workspace=org, user=new_user).is_admin)
        self.assertFalse(Invitation.objects.exists())

    def test_member_cannot_manage_team(self):
        org = self.create_org()
        mine = self.add_member(org)
        admin_membership = Membership.objects.get(workspace=org, user=self.admin)
        self.client.force_login(self.member)
        self.switch(org)
        self.assertEqual(post_json(self.client, "/api/organization/members/", {"email": "x@exemplo.com"}).status_code, 403)
        self.assertEqual(post_json(self.client, f"/api/organization/members/{mine.id}/", {"role": "admin"}, "patch").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/organization/members/{admin_membership.id}/").status_code, 403)
        self.assertEqual(post_json(self.client, "/api/organization/", {"name": "Hackeada"}, "patch").status_code, 403)
        self.assertEqual(self.client.delete("/api/organization/").status_code, 403)
        org.refresh_from_db()
        self.assertEqual(org.name, "Deimos Lab")

    def test_member_can_leave_but_last_admin_cannot(self):
        org = self.create_org()
        mine = self.add_member(org)
        admin_membership = Membership.objects.get(workspace=org, user=self.admin)

        # Único admin não pode sair nem se rebaixar.
        self.assertEqual(self.client.delete(f"/api/organization/members/{admin_membership.id}/").status_code, 400)
        res = post_json(self.client, f"/api/organization/members/{admin_membership.id}/", {"role": "member"}, "patch")
        self.assertEqual(res.status_code, 400)

        # Depois de promover outra pessoa, pode.
        post_json(self.client, f"/api/organization/members/{mine.id}/", {"role": "admin"}, "patch")
        self.assertEqual(self.client.delete(f"/api/organization/members/{admin_membership.id}/").status_code, 200)
        self.assertFalse(Membership.objects.filter(workspace=org, user=self.admin).exists())
        # Quem saiu volta para o workspace pessoal.
        self.assertContains(self.client.get("/workspace/"), "Workspace pessoal")

    def test_personal_workspace_has_no_team_api(self):
        self.assertEqual(post_json(self.client, "/api/organization/members/", {"email": "x@exemplo.com"}).status_code, 400)
        self.assertEqual(self.client.delete("/api/organization/").status_code, 400)
        self.assertTrue(Workspace.objects.filter(owner=self.admin).exists())

    def test_delete_organization(self):
        org = self.create_org()
        self.assertEqual(self.client.delete("/api/organization/").status_code, 200)
        self.assertFalse(Workspace.objects.filter(pk=org.pk).exists())
        self.assertEqual(self.client.get("/workspace/").status_code, 200)


class SecretVaultTests(WorkspaceTestCase):
    VALUE = "sk-live-abcdef1234567890XYZW"

    def set(self, name="ABUSEIPDB_API_KEY", value=None, **extra):
        return post_json(self.client, "/api/secrets/", {"name": name, "value": value or self.VALUE, **extra})

    def test_value_is_encrypted_and_never_returned(self):
        res = self.set(description="Chave do AbuseIPDB")
        self.assertEqual(res.status_code, 201)
        self.assertNotIn(self.VALUE, res.content.decode())

        secret = Secret.objects.get()
        self.assertNotIn(self.VALUE, secret.ciphertext)
        self.assertEqual(secret.hint, "XYZW")
        self.assertEqual(reveal_secret(secret), self.VALUE)

        page = self.client.get("/segredos/").content.decode()
        self.assertIn("ABUSEIPDB_API_KEY", page)
        self.assertIn("••••••••XYZW", page)
        self.assertNotIn(self.VALUE, page)

    def test_replace_and_delete(self):
        self.set()
        self.set(value="outro-valor-bem-comprido-9999")
        self.assertEqual(Secret.objects.count(), 1)
        self.assertEqual(reveal_secret(Secret.objects.get()), "outro-valor-bem-comprido-9999")
        self.assertEqual(self.client.delete("/api/secrets/ABUSEIPDB_API_KEY/").status_code, 200)
        self.assertFalse(Secret.objects.exists())

    def test_validation(self):
        self.assertEqual(self.set(name="minusculo").status_code, 400)
        self.assertEqual(self.set(name="COM ESPACO").status_code, 400)
        self.assertEqual(post_json(self.client, "/api/secrets/", {"name": "VAZIO", "value": ""}).status_code, 400)
        # Valores curtos não deixam pista do conteúdo.
        self.set(name="PIN", value="1234")
        self.assertEqual(Secret.objects.get(name="PIN").hint, "")

    def test_secrets_are_isolated_per_workspace(self):
        self.set()
        org = self.create_org()  # passa a ser o workspace ativo
        self.assertContains(self.client.get("/segredos/"), "Nenhum segredo guardado neste workspace.")
        self.assertEqual(self.client.delete("/api/secrets/ABUSEIPDB_API_KEY/").status_code, 404)
        self.assertEqual(secrets_for(org, ["ABUSEIPDB_API_KEY"]), {})
        self.assertEqual(secrets_for(personal_workspace(self.admin), ["ABUSEIPDB_API_KEY"]), {"ABUSEIPDB_API_KEY": self.VALUE})

    def test_member_sees_names_but_cannot_change(self):
        org = self.create_org()
        self.set()
        self.add_member(org)
        self.client.force_login(self.member)
        self.switch(org)
        page = self.client.get("/segredos/")
        self.assertContains(page, "ABUSEIPDB_API_KEY")
        self.assertNotContains(page, 'id="secret-form"')
        self.assertEqual(self.set(value="tentativa-de-troca-000000").status_code, 403)
        self.assertEqual(self.client.delete("/api/secrets/ABUSEIPDB_API_KEY/").status_code, 403)
        self.assertEqual(reveal_secret(Secret.objects.get()), self.VALUE)
