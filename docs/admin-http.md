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
