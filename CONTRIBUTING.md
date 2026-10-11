# Como contribuir

Obrigado por querer melhorar o Deimos Agent OS. Este guia explica como preparar o ambiente, o que esperamos de uma contribuição e como ela é revisada.

## Antes de começar

- **Bugs e ideias**: abra uma issue usando os modelos do repositório. Para mudanças grandes, combine a abordagem na issue antes de escrever código — evita retrabalho.
- **Vulnerabilidades de segurança**: não abra issue pública. Siga a [política de segurança](SECURITY.md).
- Ao participar, você concorda com o [código de conduta](CODE_OF_CONDUCT.md).

## Preparando o ambiente

```bash
git clone https://github.com/deimoscorporation/agent-os.git
cd agent-os
make install
make migrate
make run
```

Nenhuma chave de API é necessária para desenvolver: sem chave, o chat funciona em modo de demonstração, e os testes simulam a OpenAI.

## Fazendo uma mudança

1. Crie um branch a partir de `main`: `git checkout -b corrige-busca-vazia`.
2. Faça a mudança com testes que a cubram.
3. Rode `make test` e `make check` — os dois precisam passar.
4. Abra o pull request descrevendo **o que** mudou e **por quê**.

### O que esperamos do código

- **Testes**: toda correção vem com um teste que falhava antes; toda funcionalidade, com testes do caminho feliz e dos casos de erro. Os testes ficam em `tests.py` de cada app.
- **Sem rede nos testes**: use `core.testing.fake_openai` para simular a OpenAI.
- **Migrações**: se mudou um modelo, inclua a migração (`python manage.py makemigrations`). Migrações que removem colunas precisam preservar os dados existentes.
- **Estilo**: siga o que já existe no arquivo — nomes em inglês no código, textos de interface e comentários em português, linhas de até 120 caracteres. Comentários explicam o *porquê*, não o *o quê*.
- **Dependências**: o projeto tem poucas, de propósito. Proponha uma dependência nova na issue antes de adicioná-la.
- **Front-end**: HTML de templates Django, CSS e JavaScript puros, sem etapa de build.

### Cuidados de segurança

Este projeto guarda chaves de API e executa lógica sobre conteúdo enviado por usuários. Ao contribuir:

- **Nunca** devolva o valor de um segredo em uma resposta, página, log ou mensagem de erro. O cofre e as chaves de API são só de escrita.
- **Todo acesso a dados confere o dono.** Consultas a conversas, itens, anexos, documentos e segredos filtram pelo usuário ou workspace da requisição. Inclua um teste mostrando que outro usuário recebe 404.
- **Arquivos enviados não são servidos direto do disco.** Passam por uma view que confere a permissão.
- **Conteúdo de usuário é tratado como texto.** Em templates, confie no escape automático; no JavaScript, use `textContent` — nunca monte HTML concatenando dados.
- **Ações restritas** (instalar, compartilhar, gerenciar segredos e membros) conferem o papel no servidor, não apenas escondem o botão.

## Revisão

Um mantenedor revisa o pull request, pode pedir ajustes e faz o merge quando os testes passam e a mudança está clara. Pull requests pequenos e focados são revisados mais rápido.

## Licença das contribuições

Ao enviar uma contribuição, você concorda que ela seja distribuída sob a [licença MIT](LICENSE) do projeto.
