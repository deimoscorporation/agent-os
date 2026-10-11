import io
import json
import shutil
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import User
from workspaces.models import Membership
from workspaces.services import create_organization, personal_workspace

from .extract import UnsupportedFile, check_file, chunk_text, extract_text, tokens
from .models import Chunk, Document
from .search import search

PASSWORD = "Segredo!Forte42"
MEDIA = tempfile.mkdtemp(prefix="deimos-test-media-")

POLITICA = (
    "Política de senhas da Deimos Corp\n\n"
    "Todas as senhas devem ter no mínimo 14 caracteres e ser trocadas a cada 180 dias.\n\n"
    "O uso de autenticação em dois fatores é obrigatório para acessos administrativos.\n\n"
    "Incidentes de segurança devem ser reportados ao SOC em até 30 minutos pelo canal #incidentes."
)
FERIAS = (
    "Manual de férias\n\n"
    "Cada colaborador tem direito a 30 dias de férias por ano, que podem ser divididos em até três períodos.\n\n"
    "O pedido deve ser feito com 45 dias de antecedência no sistema de RH."
)


def text_file(name, content):
    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/plain")


def docx_file(name, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f'<?xml version="1.0"?><w:document><w:body>{body}</w:body></w:document>')
    return SimpleUploadedFile(name, buffer.getvalue())


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


class ExtractTests(TestCase):
    def test_text_and_docx(self):
        self.assertIn("14 caracteres", extract_text(text_file("p.md", POLITICA), "p.md"))
        latin = SimpleUploadedFile("l.txt", "ação e reação".encode("latin-1"))
        self.assertEqual(extract_text(latin, "l.txt"), "ação e reação")
        docx = docx_file("d.docx", ["Primeiro parágrafo &amp; mais.", "Segundo."])
        self.assertEqual(extract_text(docx, "d.docx"), "Primeiro parágrafo & mais.\nSegundo.")

    def test_check_file_rejects_disguised_and_oversized(self):
        allowed = {"png", "txt", "pdf"}
        with self.assertRaises(UnsupportedFile):
            check_file(SimpleUploadedFile("virus.exe", b"MZ"), allowed, 1000)
        with self.assertRaises(UnsupportedFile):  # .png que não é PNG
            check_file(SimpleUploadedFile("foto.png", b"<script>alert(1)</script>"), allowed, 1000)
        with self.assertRaises(UnsupportedFile):  # "PDF" falso
            check_file(SimpleUploadedFile("doc.pdf", b"nao sou pdf"), allowed, 1000)
        with self.assertRaises(UnsupportedFile):
            check_file(SimpleUploadedFile("grande.txt", b"x" * 2000), allowed, 1000)
        with self.assertRaises(UnsupportedFile):
            check_file(SimpleUploadedFile("vazio.txt", b""), allowed, 1000)
        self.assertEqual(check_file(SimpleUploadedFile("ok.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20), allowed, 1000), "png")

    def test_corrupted_documents_raise_clean_error(self):
        with self.assertRaises(UnsupportedFile):
            extract_text(SimpleUploadedFile("x.pdf", b"%PDF-1.4 lixo"), "x.pdf")
        with self.assertRaises(UnsupportedFile):
            extract_text(SimpleUploadedFile("x.docx", b"PK\x03\x04 lixo"), "x.docx")

    def test_chunking(self):
        text = "\n\n".join(f"Parágrafo {n} " + "palavra " * 40 for n in range(12))
        chunks = chunk_text(text, size=600, overlap=100)
        self.assertGreater(len(chunks), 4)
        self.assertTrue(all(len(c) <= 800 for c in chunks))
        self.assertIn("Parágrafo 0", chunks[0])
        self.assertIn("Parágrafo 11", chunks[-1])
        # Parágrafo único maior que o limite também é dividido.
        self.assertGreater(len(chunk_text("palavra " * 500, size=600)), 4)

    def test_tokens_normalize(self):
        self.assertEqual(tokens("Qual é a POLÍTICA de senhas?"), ["politica", "senhas"])


@override_settings(MEDIA_ROOT=MEDIA)
class KnowledgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana@exemplo.com", PASSWORD, full_name="Ana Lima")
        self.other = User.objects.create_user("fora@exemplo.com", PASSWORD, full_name="Fábio Fora")
        self.client.force_login(self.user)

    def upload(self, file):
        return self.client.post("/api/knowledge/", {"file": file})

    def test_upload_indexes_and_search_ranks(self):
        self.assertEqual(self.upload(text_file("politica.md", POLITICA)).status_code, 201)
        self.assertEqual(self.upload(text_file("ferias.txt", FERIAS)).status_code, 201)
        self.assertEqual(Document.objects.count(), 2)
        self.assertGreaterEqual(Chunk.objects.count(), 2)

        workspace = personal_workspace(self.user)
        results = search(workspace, "Quantos caracteres uma senha precisa ter?")
        self.assertEqual(results[0]["document"], "politica.md")
        self.assertIn("14 caracteres", results[0]["text"])
        self.assertEqual(search(workspace, "quantos dias de férias por ano")[0]["document"], "ferias.txt")
        self.assertEqual(search(workspace, "receita de bolo de cenoura"), [])
        self.assertEqual(search(workspace, "de a o"), [])  # só stopwords

        api = self.client.get("/api/knowledge/search/", {"q": "autenticação em dois fatores"}).json()
        self.assertEqual(api["results"][0]["document"], "politica.md")
        self.assertNotIn("text", api["results"][0])

        page = self.client.get("/conhecimento/")
        self.assertContains(page, "politica.md")
        self.assertContains(page, "ferias.txt")

    def test_rejects_unsupported_and_empty_documents(self):
        for file in (
            SimpleUploadedFile("foto.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50),  # imagem não entra na base
            SimpleUploadedFile("app.exe", b"MZ" + b"0" * 50),
            text_file("curto.txt", "oi"),
        ):
            with self.subTest(name=file.name):
                res = self.upload(file)
                self.assertEqual(res.status_code, 400)
                self.assertTrue(res.json()["error"])
        self.assertFalse(Document.objects.exists())

    def test_knowledge_is_isolated_per_workspace(self):
        doc_id = self.upload(text_file("politica.md", POLITICA)).json()["id"]
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get("/conhecimento/"), "politica.md")
        self.assertEqual(self.client.get("/api/knowledge/search/", {"q": "senhas"}).json()["results"], [])
        self.assertEqual(self.client.get(f"/conhecimento/{doc_id}/arquivo/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/knowledge/{doc_id}/").status_code, 404)

    def test_download_and_delete_removes_file(self):
        doc_id = self.upload(text_file("politica.md", POLITICA)).json()["id"]
        document = Document.objects.get(pk=doc_id)
        path = document.file.path
        res = self.client.get(f"/conhecimento/{doc_id}/arquivo/")
        self.assertEqual(b"".join(res.streaming_content).decode(), POLITICA)
        self.assertIn("attachment", res["Content-Disposition"])

        self.assertEqual(self.client.delete(f"/api/knowledge/{doc_id}/").status_code, 200)
        self.assertFalse(Document.objects.exists())
        self.assertFalse(Chunk.objects.exists())
        with self.assertRaises(FileNotFoundError):
            open(path)

    def test_organization_base_is_shared_and_members_delete_only_their_own(self):
        org = create_organization(self.user, "Deimos Lab")
        member = User.objects.create_user("membro@exemplo.com", PASSWORD, full_name="Marcos Membro")
        Membership.objects.create(workspace=org, user=member, role="member")
        self.client.post("/workspaces/switch/", {"workspace_id": str(org.id)})
        admin_doc = self.upload(text_file("politica.md", POLITICA)).json()["id"]

        self.client.force_login(member)
        self.client.post("/workspaces/switch/", {"workspace_id": str(org.id)})
        self.assertContains(self.client.get("/conhecimento/"), "politica.md")
        own_doc = self.upload(text_file("ferias.txt", FERIAS)).json()["id"]
        self.assertEqual(self.client.delete(f"/api/knowledge/{admin_doc}/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/knowledge/{own_doc}/").status_code, 200)
        # No workspace pessoal do membro a base é outra.
        self.client.post("/workspaces/switch/", {"workspace_id": str(personal_workspace(member).id)})
        self.assertEqual(self.client.get("/api/knowledge/search/", {"q": "senhas"}).json()["results"], [])
