# Deimos Agent OS

**Deimos Agent OS** é uma plataforma open source para criar, organizar e usar agentes de IA. Reúne um chat com streaming, anexos e base de conhecimento própria; um marketplace para instalar ou publicar ferramentas, agentes, skills e workflows; e workspaces compartilhados para times, com papéis e cofre de segredos criptografado.

Cada pessoa usa a **própria chave de API da OpenAI** — a plataforma não revende nem intermedia o uso de modelos. Ferramentas podem ser geradas a partir de uma descrição em linguagem natural e passam por auditoria automática de código antes de serem instaladas; itens só chegam ao marketplace público depois de revisão.

Feito em Django, sem build de front-end, pensado para rodar na sua própria infraestrutura. Desenvolvido pela Deimos Corp — Advanced AI & CyberSecurity.

> **Estado do projeto:** em desenvolvimento ativo. Veja [o que ainda não funciona](#limitações-conhecidas) antes de usar em produção.

## Funcionalidades

- **Chat** — respostas em streaming, Markdown com realce de código, anexos (imagens, PDF, DOCX, texto), escolha de modelo, histórico, exportação (Markdown/JSON) e link público somente leitura.
- **Base de conhecimento** — documentos por workspace; ao ativar na conversa, o assistente consulta os trechos relevantes e cita as fontes.
- **Marketplace** — catálogo de ferramentas, agentes, skills e workflows, com categorias, avaliações, histórico de versões e aviso de atualização.
- **Criação assistida** — descreva o que precisa e o sistema gera a ferramenta (parâmetros + código Python), o agente, a skill ou o workflow.
- **Auditoria de ferramentas** — o código é analisado antes de instalar: bloqueia `eval`/`exec`, detecta permissões (rede, arquivos, processos) e credenciais escritas no código.
- **Organizações** — workspaces compartilhados, papéis de admin e membro, convites por e-mail e marketplace privado da empresa.
- **Cofre de segredos** — chaves de API criptografadas por workspace; as ferramentas leem com `secret("NOME")` e o valor nunca aparece no código nem na interface.
- **Revisão** — itens só entram no marketplace público depois de aprovados pela equipe da instância.

## Começando

Requisitos: Python 3.11 ou mais novo.

```bash
git clone https://github.com/deimoscorporation/agent-os.git
cd agent-os

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # opcional em desenvolvimento
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Abra <http://localhost:8000>, crie sua conta e cadastre sua chave em **Minha conta › Chave da OpenAI**. Sem chave, o chat responde em modo de demonstração.

Se preferir, `make install && make migrate && make run` faz o mesmo. `make help` lista os atalhos.

Para acessar o `/admin/` e a fila de revisão do marketplace, crie uma conta de equipe:

```bash
.venv/bin/python manage.py createsuperuser
```

## Chave da OpenAI

A plataforma procura a chave nesta ordem:

1. a chave cadastrada pela pessoa em **Minha conta**;
2. o segredo `OPENAI_API_KEY` no **cofre** do workspace ativo (uma chave para toda a organização);
3. a variável de ambiente `OPENAI_API_KEY` da instância (opcional).

As chaves são guardadas criptografadas, usadas apenas nas requisições de quem as cadastrou e nunca são exibidas de volta. Os modelos oferecidos nos seletores são os que a própria chave alcança.

`OPENAI_BASE_URL` permite apontar para qualquer servidor compatível com a API da OpenAI.

## Configuração

Tudo o que muda entre ambientes vem de variáveis de ambiente (ou de um arquivo `.env` na raiz). A lista completa, com comentários, está em [`.env.example`](.env.example).

| Variável | Para que serve |
|---|---|
| `DJANGO_DEBUG` | `true` em desenvolvimento (padrão), `false` em produção |
| `DJANGO_SECRET_KEY` | Chave do Django. Obrigatória em produção |
| `DEIMOS_SECRETS_KEY` | Chave que criptografa o cofre e as chaves de API. Obrigatória em produção |
| `DJANGO_ALLOWED_HOSTS` | Domínios que servem a aplicação |
| `OPENAI_BASE_URL`, `OPENAI_MODELS`, `OPENAI_API_KEY` | Opções da integração com a OpenAI |

### Produção

O servidor `runserver` é só para desenvolvimento. Para publicar:

- defina `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY`, `DEIMOS_SECRETS_KEY`, `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS`;
- rode a aplicação com um servidor WSGI (por exemplo, gunicorn) atrás de um proxy com HTTPS;
- rode `python manage.py collectstatic` e sirva a pasta `staticfiles/` pelo proxy;
- faça backup do banco, da pasta `media/` **e da `DEIMOS_SECRETS_KEY`** — sem ela, os segredos salvos não podem ser lidos;
- o banco padrão é SQLite; para mais de um processo ou muitos usuários, configure PostgreSQL em `core/settings.py`.

## Limitações conhecidas

- **Ferramentas e workflows ainda não são executados.** Eles ficam definidos, auditados e instaláveis, mas rodar código gerado por IA exige um ambiente isolado (sandbox) que ainda não faz parte do projeto. No chat, os agentes respondem com suas instruções e modelo, mas não chamam ferramentas.
- **A busca na base de conhecimento é por palavras-chave** (BM25), não por significado. PDFs escaneados não são lidos.
- **Não há recuperação de senha nem confirmação de e-mail**, porque dependem de um serviço de envio de e-mail.
- **Sem limites de uso por usuário** (requisições, armazenamento).

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `accounts/` | Contas, login, cadastro e a chave de API de cada usuário |
| `workspaces/` | Workspaces pessoais, organizações, papéis e cofre de segredos |
| `chat/` | Conversas, anexos, compartilhamento e exportação |
| `knowledge/` | Base de conhecimento: extração de texto e busca |
| `marketplace/` | Catálogo, editores, auditoria de ferramentas e fila de revisão |
| `core/` | Configurações, cliente da OpenAI (`llm.py`) e criptografia (`crypto.py`) |
| `templates/`, `static/` | Layout compartilhado, estilos e scripts |

## Testes

```bash
.venv/bin/python manage.py test
```

Os testes simulam a API da OpenAI: nenhuma chamada sai para a rede e nenhuma chave é necessária.

## Contribuindo

Contribuições são bem-vindas. Leia o [guia de contribuição](CONTRIBUTING.md) e o [código de conduta](CODE_OF_CONDUCT.md). Para relatar uma vulnerabilidade, siga a [política de segurança](SECURITY.md) — não abra uma issue pública.

## Licença

O código é distribuído sob a [licença MIT](LICENSE).

O nome **Deimos**, os logotipos e a identidade visual em `static/deimos/img/` são marcas da Deimos Corp e **não** estão cobertos pela licença do código. Se você redistribuir uma versão modificada, use nome e marca próprios.
