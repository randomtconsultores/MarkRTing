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
