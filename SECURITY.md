# Política de segurança

O Deimos Agent OS guarda chaves de API, segredos de equipes e documentos enviados por usuários. Levamos relatos de segurança a sério.

## Como relatar uma vulnerabilidade

**Não abra uma issue pública.** Envie os detalhes em privado por um destes canais:

- o recurso **"Report a vulnerability"** na aba *Security* do repositório no GitHub; ou
- e-mail para **seguranca@SEU-DOMINIO** (substitua pelo contato real da equipe mantenedora).

Inclua, se possível:

- o que é afetado e qual o impacto (por exemplo: "um membro consegue ler segredos de outro workspace");
- os passos para reproduzir, com requisições ou código de exemplo;
- a versão ou o commit testado.

## O que esperar

- Confirmação de recebimento em até **3 dias úteis**.
- Uma avaliação inicial em até **10 dias úteis**, com a gravidade e os próximos passos.
- Aviso quando a correção for publicada. Com a sua permissão, damos crédito pela descoberta.

Pedimos que não divulgue o problema publicamente antes da correção e que teste apenas em instalações suas, sem acessar dados de terceiros.

## Versões com suporte

Enquanto o projeto não tem versões estáveis numeradas, as correções de segurança são aplicadas apenas no branch `main`.

## Escopo

São especialmente relevantes relatos sobre:

- acesso a conversas, anexos, documentos, itens ou segredos de outro usuário ou workspace;
- exposição do valor de segredos do cofre ou de chaves de API (em respostas, páginas, logs ou erros);
- contorno dos papéis de organização (membro agindo como admin) ou da fila de revisão do marketplace;
- contorno da auditoria de código das ferramentas;
- execução de script por conteúdo de usuário (XSS), inclusive na página pública de conversa compartilhada;
- problemas no envio de arquivos (tipos disfarçados, leitura de caminhos fora da pasta de mídia).

Fora de escopo: instalações rodando com `DJANGO_DEBUG=true` ou com a chave de desenvolvimento, ataques que exigem acesso ao servidor, e problemas em serviços de terceiros (como a própria OpenAI).

## Para quem opera uma instalação

- Rode com `DJANGO_DEBUG=false` e defina `DJANGO_SECRET_KEY` e `DEIMOS_SECRETS_KEY` próprias — a aplicação se recusa a iniciar em produção sem elas.
- Sirva somente por HTTPS.
- Faça backup do banco, da pasta `media/` e da `DEIMOS_SECRETS_KEY`, guardando a chave separada dos dados.
- As ferramentas do marketplace **não são executadas** pela plataforma hoje. Se você adicionar execução, faça-o em um ambiente isolado, respeitando as permissões e os segredos declarados por cada ferramenta.
