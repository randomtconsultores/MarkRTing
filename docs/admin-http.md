# Integração HTTP administrativa

## Estado atual — 20 de outubro de 2026

Integração concluída e incorporada em `main` pelo PR #27, a partir de `feat/etapa4-integracao-http`. O plano original foi dividido em parser, router isolado e integração 3a/3b/3c; as notas de preparação abaixo são históricas, não pendências atuais de montagem.

SHA validado antes do merge: `e2012d850260c7f5ea74b41adb1d5ef1ea7704ad`. Não equivale a fecho global da etapa 4 ou autorização para produção. Arranque: [README](../README.md). Gestão de contas: [admin-accounts.md](admin-accounts.md).

## Componentes e responsabilidades

`create_app` monta sempre `create_access_router` e `AdminAccessGuard` conjuntamente. A matriz `access_policy` classifica os métodos/caminhos; não há autorização pública por importar módulos. Rotas não classificadas são recusadas. Documentação `/docs`, `/redoc` e `/openapi.json` exige sessão administrativa; políticas de HEAD não criam handlers onde não existem.

O router expõe GET/HEAD `/login`, POST `/login` e POST `/logout`, excluídos do esquema OpenAPI. Recebe repositório por fábrica, configuração web, configuração do limitador, limite do corpo e relógio opcional. CSRF e HMAC de tentativas usam chaves independentes. Login/logout pertencem ao router; o guard não consome esses corpos.

O guard protege consultas/edições/mutações antes de operações de negócio. Exige sessão persistida, conta ativa, papel permitido e versão de credenciais coincidente. `viewer` consulta e termina sessão; só `admin` edita/escreve. Erros de persistência, relógio ou emissão de contexto falham fechados com 503. Caminhos desconhecidos são recusados por omissão.

## Parser e formulários de acesso

`read_login_form` recebe o `receive` ASGI, Content-Type e limites obrigatórios `max_bytes`/`max_fields`. Limita bytes durante a leitura, sem confiar em Content-Length, e campos na descodificação. Aceita apenas `application/x-www-form-urlencoded`, com charset UTF-8 opcional e corpo ASCII contendo valores UTF-8 percent-encoded.

Preserva espaços e valores descodificados. Rejeita nomes vazios/duplicados, percent-encoding ou UTF-8 inválidos e desconexão prematura. Não serve para formulários de negócio multivalor. `LoginFormError` distingue 400, 413 e 415 sem incluir entradas na mensagem. A leitura consome o fluxo: não repetir sem partilha/replay controlado. Limites de tempo e de ligações também pertencem ao servidor/proxy.

O router admite apenas campos previstos e valida origem, cookies sem nomes duplicados e CSRF antes das operações de acesso. Login usa nonce de pré-login e CSRF próprio; falha de credenciais tem erro genérico 401. Limitação responde 429 com Retry-After antes do hashing; falhas dos serviços respondem 503 sem detalhes internos. Não se reiniciam contadores por um login bem-sucedido.

O IP é o do scope ASGI; não se lê X-Forwarded-For diretamente. O operador deve configurar os proxies confiáveis. Um login bem-sucedido emite sessão nova, revoga o token anterior fornecido pelo browser, limpa cookies antes de definir o novo e aplica `safe_redirect`. Emissão e revogação anterior usam as transações existentes, não uma transação conjunta: falha depois da emissão pode deixar uma sessão sem cookie entregue, a expirar/limpar; não autoriza acesso numa resposta de falha.

Logout é exclusivamente POST com finalidade CSRF `logout`, sessão/conta válidas, papel conhecido e versão coerente antes de revogar e limpar cookies. Não há ligação GET de logout.

## Corpos administrativos e replay

Nas mutações classificadas, o guard valida origem, cookie de sessão único e um único `csrf_token` de finalidade `admin`. O corpo é limitado a `application/x-www-form-urlencoded`; campos de negócio repetidos são preservados. Mensagens ASGI aceites são reproduzidas sem alteração para o handler, sem consumir novamente o corpo no router de acesso.

Limites fixos deliberados em `main.py`:

- Formulário de acesso: 4096 bytes; o parser de login admite até 4 campos, o de logout apenas o CSRF previsto pelo router.
- Corpo administrativo: 4 MiB e 128 campos descodificados.

As 10 linhas iniciais de critérios de segmentos ficam abaixo desse teto. Campanhas admitem `body_text` até 100000 e `body_html` até 200000 caracteres; UTF-8 percent-encoded pode ocupar cerca de 3,6 MB nesses dois campos, deixando margem dentro dos 4 MiB. Limites do proxy devem ser coordenados com estes limites e os tamanhos efetivos dos formulários.

## Contexto dos templates

Depois de resolver sessão e validar conta, papel e versão de credenciais, o guard coloca um mapping por pedido em `request.state.admin_access`:

- `role`: exatamente `admin` ou `viewer`, da conta validada.
- `admin_csrf_token`: emitido por `CsrfTokens.issue(session_token, "admin", now=...)` para admin; vazio para viewer.
- `logout_csrf_token`: emitido com finalidade `logout`, para ambos os papéis.

O token bruto de sessão é apenas binding no servidor; nunca integra o mapping ou HTML. Contexto não é construído com papel, identidade ou tokens fornecidos pelo cliente. Não há cache de autorização entre pedidos nem variáveis Jinja globais mutáveis por utilizador.

Páginas administrativas e re-renderizações com erros recebem o contexto validado. A aplicação recusa contexto administrativo indisponível; isoladamente, os templates ocultam controlos quando faltam contexto/papel/token necessário. Ocultar controlos não substitui autorização no servidor. Cada formulário administrativo de mutação tem um único CSRF admin e o logout tem um único CSRF logout.

Formulários públicos de subscrição, confirmação e cancelamento não recebem CSRF administrativo e mantêm os próprios mecanismos. Eventos conservam autenticação própria; classificação pública não significa aceitação sem validação.

## Respostas privadas e falhas

Respostas administrativas recebem Cache-Control=no-store, Referrer-Policy=no-referrer, X-Content-Type-Options=nosniff e X-Frame-Options=DENY. Guard e router aplicam as proteções nas suas responsabilidades; estes cabeçalhos não substituem escape, TLS ou autorização.

Se o handler falhar antes de iniciar a resposta, o guard devolve 500 genérico. Depois de `http.response.start`, regista a mensagem fixa e relança a exceção, sem tentar emitir outra resposta. O teste de regressão verifica ambos os casos e ausência do marcador sensível no log produzido pelo guard; não comprova sanitização de logs de todos os servidores/proxies externos.

## Configuração obrigatória

A configuração vem de objetos confiáveis injetados em `create_app` ou de:

- `WebSecurityConfig.from_env`: `MINI_MARKETO_ADMIN_ORIGIN`, `MINI_MARKETO_ADMIN_CSRF_KEY`, `MINI_MARKETO_ADMIN_SESSION_ABSOLUTE_SECONDS`, `MINI_MARKETO_ADMIN_SESSION_IDLE_SECONDS`, `MINI_MARKETO_ADMIN_CSRF_SECONDS`.
- `LoginAttemptConfig.from_env`: `MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY`, `MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS`, `MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT`, `MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT`, `MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT`.

Sem valores de produção predefinidos nem geração silenciosa de chaves. As chaves são independentes. HTTP só é admitido nos hosts locais literais suportados e com `MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL=1`; HTTPS exige a exceção desligada. Durações, orçamentos e segredos são responsabilidade do operador.

Configuração ausente/inválida interrompe `create_app` antes de servir pedidos. Como páginas públicas e administração usam a mesma aplicação ASGI, esse erro indisponibiliza também as rotas públicas, em vez de iniciar uma aplicação desprotegida.

## Persistência e manutenção

`build_access_repository_factory` usa as migrações existentes e abre uma ligação nova por operação síncrona, SQLite ou PostgreSQL, fechada no mesmo contexto. As chamadas de acesso e hashing dos pedidos correm em workers. Serviços de domínio injetados exigem também uma `access_repo_factory` explícita; não se escolhe silenciosamente outra base administrativa.

Selecionar exatamente um destino explícito de base e revê-lo antes de executar:

```sh
.venv/bin/python -m mini_marketo.admin_maintenance --batch-size 1000
```

Cada execução remove no máximo um lote por tabela de sessões e tentativas expiradas, entre 1 e 10000. Agendamento externo e repetições são responsabilidade do operador. O teto limita linhas, não promete tempo máximo de consulta. Não há scheduler nem limpeza ilimitada por pedido. Sessões expiradas são recusadas mesmo antes da limpeza; revogação por gestão de conta é distinta da expiração. Esta manutenção não elimina contactos, histórico de consentimento ou pedidos de subscrição.

## Histórico de preparação e validação

- Parser: 24 aprovados em 0,03 s num pacote temporário com módulo real e mensagens ASGI simuladas, sem FastAPI/base de dados; não era teste HTTP ou suite completa. Comando de projeto então indicado: `.venv/bin/python -m pytest -q -rs tests/test_login_form.py`.
- Router isolado: por pedido do utilizador, o assistente não executou testes, recolha, compilação ou renderização nessa preparação. Os testes preparados cobriam SQLite/PostgreSQL, Argon2id, chamadas ASGI, workers, cookies, rotação, logout e redirecionamentos; a montagem ainda estava pendente nesse momento. Comando então indicado: `tests/test_admin_router.py tests/test_login_form.py` com Python da `.venv` e PostgreSQL de testes configurado.
- Guard 3a: utilizador reportou 73 aprovados em 11,89 s para `tests/test_admin_guard.py tests/test_admin_router.py`, sobre a base `0aa2add79ef85e075a7f6a61f970aed7c70d5aa8` e alterações locais; 36 PostgreSQL, 36 SQLite e 1 sem base, sem falhas/skips/erros. Diff-check passou; incluía limite antes de acumular chunk e logs fixos sem traceback. Não era suite completa.
- Templates 3b: utilizador reportou 8 aprovados em 1,22 s em `f11204e9bd67a249e16eee7b7c7ac5c7ffb35d6c`, com worktree isolado e imports desse checkout. Diff-check desde `9561e02971fcdc0b6575d6b4ac1f5f1e5f89ed52` passou. Sem persistência nesses testes de renderização.
- Integração 3c: preparação reportada com 920 aprovados; seleção focada posterior no SHA publicado `31106dbb137a857d53b0e19f52a7ebdf1e51378f` teve 997 aprovados com PostgreSQL. Nessa versão identificaram-se exceção suprimida após o início da resposta e linha em branco extra na documentação; foram corrigidas antes da validação final.

### Referência final antes do merge do PR #27

Validação reportada pelo utilizador no Copilot em `e2012d850260c7f5ea74b41adb1d5ef1ea7704ad`, com worktree isolado, Python da `.venv`, imports do checkout, sem alterar código e preservando workspace original e `poetry.lock`:

```sh
pytest -q tests/test_admin_guard_response_errors.py
# 2 aprovados em 0,06 s
pytest -q tests/test_access*.py tests/test_login*.py tests/test_admin*.py tests/test_authentication*.py tests/test_session*.py tests/test_web*.py
# 999 aprovados com PostgreSQL em 69,36 s, incluindo os dois anteriores
 git diff --check f11204e9bd67a249e16eee7b7c7ac5c7ffb35d6c
# passou sem erros
```

O espaço antes de `git` acima é apenas indentação de apresentação no bloco shell. Os comandos pytest são a forma abreviada reportada e usaram Python da `.venv`. A seleção de 999 difere da anterior de 920; não é suite completa. O assistente não executou os testes reportados nem novos testes para esta revisão documental. A validação não é sobre o merge, este commit documental ou um ambiente de produção.

Permanecem as verificações operacionais e as políticas/fluxos de dados da etapa 4. Ver [roadmap.md](roadmap.md).
