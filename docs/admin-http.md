# Integracao HTTP administrativa — tres commits

Ramo: feat/etapa4-integracao-http. Nao fazer merge como protecao concluida antes da integracao e validacao finais.

1. Parser limitado de formularios de login/logout e testes, sem montar rotas.
2. Router de login/logout, formulario, sessoes, cookies, CSRF e limitador, com testes; ainda sem montagem na aplicacao publica.
3. Ligacao conjunta do router e do guard global a aplicacao, tokens nos formularios administrativos e testes das rotas protegidas e publicas.

## Commit 1 — parser

read_login_form recebe o receive ASGI, Content-Type e limites obrigatorios max_bytes/max_fields. Limita os bytes durante a leitura, sem confiar em Content-Length, e limita os campos na descodificacao. Aceita apenas application/x-www-form-urlencoded, com charset UTF-8 opcional, e corpo ASCII com valores UTF-8 percent-encoded. Preserva espacos e restantes valores descodificados; rejeita nomes vazios, nomes descodificados duplicados, percent-encoding invalido, UTF-8 invalido e desconexao antes do fim. Nao serve para formularios de negocio com campos multivalor.

LoginFormError tem status_code 400 (formulario invalido), 413 (corpo excessivo) ou 415 (tipo nao suportado); nao inclui valores recebidos na mensagem. O futuro handler deve converter estes erros em respostas HTTP e validar campos obrigatorios, origem, CSRF e credenciais. A leitura consome o fluxo: nao o ler novamente sem partilha do resultado ou replay controlado. Limites de tempo e de ligacoes pertencem tambem ao servidor/proxy; o teto de bytes nao os substitui.

Verificacao realizada: 24 testes aprovados em 0,03 s, num pacote temporario com o modulo real e mensagens ASGI simuladas, sem FastAPI ou base de dados. Nao foram executados testes HTTP de routers, SQLite/PostgreSQL ou a suite completa. No projeto, validar apenas com .venv/bin/python -m pytest -q -rs tests/test_login_form.py.

Este commit nao modifica main.py, nao cria endpoints, nao autentica pedidos e nao instala protecao nas rotas. A administracao deve continuar em rede privada ou atras de proxy autenticado enquanto faltar a integracao final.

## Commit 2 — router isolado

create_access_router(repo_factory, web_config=..., login_config=..., max_form_bytes=..., clock=...) devolve um APIRouter com GET/HEAD /login, POST /login e POST /logout, excluidos do esquema OpenAPI. Recebe configuracao explicita e rejeita a mesma chave para CSRF e HMAC de tentativas. O limite max_form_bytes e obrigatorio e aplica-se ao corpo codificado, nao apenas a palavra-passe descodificada; o valor usado nos testes nao e uma politica de producao predefinida.

O formulario autonomo usa Jinja2 com escape, nonce de pre-login e CSRF vinculado. Os POST verificam origem, cookies sem nomes duplicados, campos admitidos e CSRF antes das operacoes de acesso. O login usa o IP do scope ASGI, nao le X-Forwarded-For diretamente; configurar corretamente os proxies confiaveis continua obrigatorio. Uma recusa do limitador devolve 429 com Retry-After antes do hashing; falhas de credenciais usam 401 generico e falhas dos servicos de acesso usam 503 sem detalhes internos. Nao ha reset dos contadores no sucesso.

A fabrica abre e fecha o repositorio dentro de cada chamada sincrona em run_in_threadpool. Ligacoes e hashing dos pedidos nao correm diretamente no event loop. Um sucesso emite nova sessao, revoga o token anterior fornecido pelo browser, limpa cookies antigos antes de definir o novo e usa safe_redirect. O logout exige CSRF da finalidade logout e sessao/conta ativa, papel conhecido e versao coerente antes de revogar e limpar cookies. A emissao e a revogacao anterior continuam a usar as transacoes dos servicos existentes, nao uma nova transacao atomica conjunta; uma falha apos emissao pode deixar uma sessao sem cookie entregue, a expirar/limpar. Isto nao autoriza acesso numa resposta de falha. A limpeza limitada e a sua execucao operacional ficam para o commit 3.

Os testes preparados usam as fixtures reais SQLite/PostgreSQL, Argon2id e chamadas ASGI a uma FastAPI isolada. Cobrem os fluxos de acesso, recusas antes de persistencia/hashing, hashing em worker, cookies, rotacao, logout, expiracao/revogacao e escape do destino no formulario. Ainda nao verificam a protecao das rotas de negocio.

Por pedido do utilizador, nao foram executados testes, recolha, compilacao/verificacao de sintaxe ou renderizacao de templates nesta preparacao do HTTP 2/3. Nao se transportam resultados de um router anterior para esta implementacao. Toda a verificacao deste incremento fica para o Copilot, no ambiente do projeto e sobre o SHA efetivamente publicado. Com MINI_MARKETO_TEST_PG configurada para testes, executar apenas .venv/bin/python -m pytest -q -rs tests/test_admin_router.py tests/test_login_form.py, sem a suite completa. Reportar resultados reais, skips e casos PostgreSQL; nao expor credenciais.

O router nao esta montado em main.py; a aplicacao atual nao passa a ter login ou guard por importar o modulo. A montagem conjunta, o guard global e os tokens nos formularios administrativos continuam no commit 3. Nao abrir PR intermedio nem fazer merge como integracao concluida antes desse passo e da validacao.

## Commit 3a — guard isolado

`AdminAccessGuard` e um middleware ASGI independente; nao esta montado em `main.py`. Recebe `repo_factory`, `WebSecurityConfig`, `max_body_bytes`, `max_form_fields` e, opcionalmente, um relogio. Ambos os limites do corpo sao obrigatorios e nao definem valores de producao. As rotas sao classificadas exclusivamente por `access_policy`: rotas publicas seguem para os seus validadores proprios, rotas nao classificadas sao recusadas, e leituras/edicoes/escritas exigem sessao persistida, conta ativa, papel permitido e versao de credenciais coincidente. Erros de persistencia falham fechados com 503.

Nas mutacoes classificadas, o guard valida origem, cookie de sessao unico e um unico campo CSRF de finalidade administrativa antes de encaminhar o pedido. A leitura e limitada a formularios `application/x-www-form-urlencoded`; todos os campos, incluindo campos de negocio repetidos, sao aceites no encaminhamento. As mensagens ASGI lidas sao reproduzidas sem alteracao para o handler. `login` e `logout` passam intactos para o router de acesso, que conserva a responsabilidade pelas suas verificacoes de origem, formulario, CSRF e sessao; assim, o corpo nao e consumido duas vezes. A montagem conjunta e os tokens nos templates pertencem a 3b/3c.

Os testes `tests/test_admin_guard.py` exercitam o middleware com as fixtures de persistencia SQLite e PostgreSQL, incluindo autorizacao, sessoes invalidadas, CSRF/origem, rotas publicas, recusas sem operacoes de negocio, falha de persistencia e replay do corpo.

Validacao reportada pelo utilizador para a versao final preparada no Copilot: 73 aprovados, 0 falhados, 0 ignorados e 0 erros em 11,89 s, executando tests/test_admin_guard.py e tests/test_admin_router.py sobre a base 0aa2add79ef85e075a7f6a61f970aed7c70d5aa8 com as alteracoes locais. Foram executados 36 casos PostgreSQL, 36 SQLite e 1 sem base de dados; git diff --check passou. Inclui a verificacao do tamanho antes de acumular o chunk e logs fixos sem traceback, com testes de chunks invalidos e marcadores sensiveis em falhas de relogio e persistencia. Este assistente nao executou testes nem a suite completa. main.py permanece inalterado.

## Commit 3b — formularios administrativos

Cada formulario administrativo de mutacao usa um unico campo hidden `csrf_token` de finalidade `admin`. O logout e exclusivamente um formulario `POST /logout`, com token de finalidade `logout`; nao e apresentado como ligacao GET. Os formularios publicos de subscricao, confirmacao e cancelamento e os eventos mantem os seus mecanismos proprios, sem receber tokens administrativos.

### Contrato de contexto para 3c

Para cada pagina administrativa, 3c fornece `admin_access` no contexto Jinja, construido exclusivamente depois de validar a sessao, a conta ativa e a versao de credenciais no servidor. A forma minima e um mapping com:

- `role`: exatamente `admin` ou `viewer`, derivado da conta validada;
- `admin_csrf_token`: token emitido por `CsrfTokens.issue(session_token, "admin", now=...)`; obrigatorio para o papel `admin`;
- `logout_csrf_token`: token emitido por `CsrfTokens.issue(session_token, "logout", now=...)`; obrigatorio para `admin` e `viewer`.

`session_token` acima e apenas o binding passado ao servico no servidor e nunca integra o mapping nem e exposto ao template. 3c nao pode aceitar papel, identidade ou tokens de CSRF de campos, cookies ou outros dados controlados pelo cliente. Os templates nao geram tokens nem presumem utilizador autenticado: se faltar `admin_access`, o papel nao for conhecido ou faltar o token exigido, ocultam os formularios/controlos correspondentes. `viewer` recebe consultas e logout, mas nao recebe controlos de mutacao. Ate 3c fornecer este contexto a partir do guard/sessao validados, as mutacoes administrativas permanecem ocultas; nao existe bypass de producao para os testes antigos.

`tests/test_admin_templates.py` verifica renderizacao dos formularios administrativos e publicos com tokens reais de teste, escape, ausencia de segredos, contexto ausente e o metodo do endpoint de logout.
