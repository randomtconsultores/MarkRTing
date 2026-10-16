# Administracao local de contas

Este incremento entrega gestao de contas pelo operador local; nao e uma interface web e nao conclui a etapa 4. O acesso ao sistema operativo e a base de dados e a fronteira de confianca. Nunca ligar AdminAccountService diretamente a uma rota sem autenticacao e autorizacao.

## Operacoes

- create cria a primeira conta ou uma conta adicional, com papel admin/viewer explicito, sem credenciais predefinidas. Conta existente nunca e substituida; a restricao UNIQUE continua a proteger a corrida entre verificacao e insercao.
- reset-password exige a versao observada, altera o hash e revoga todas as sessoes dessa conta na transacao do repositorio. Conserva papel e estado: nao reativa nem promove uma conta inativa/viewer.
- set-role, enable e disable exigem versao observada e revogam sessoes. Mesmo uma repeticao do estado/papel atual incrementa versao e revoga por precaucao. Conflitos nao sao repetidos automaticamente.
- revoke-sessions elimina as sessoes existentes e e idempotente, sem alterar palavra-passe, papel ou versao. Nao impede novo login, incluindo um login em curso; usar disable para impedir novas emissoes.

As operacoes locais podem desativar ou despromover o ultimo administrador. Nao se introduz uma contagem nao atomica que prometa impedir esse caso; a recuperacao e feita pelo operador autorizado via comando local. O servico nao elimina contas nem contactos de marketing.

## Politica de palavras-passe

Minimo de 15 caracteres Unicode, pois este fluxo nao tem MFA; maximo de 4096 bytes UTF-8, coerente com passwords.py. Espacos e Unicode sao preservados, sem truncagem, remocao de espacos, composicao obrigatoria ou rotacao periodica arbitraria. A palavra-passe nao pode ser igual ao nome da conta, ignorando caixa. O nome segue o contrato canonico da autenticacao: 1 a 128 caracteres, UTF-8 valido, minusculas, sem NUL ou espacos exteriores.

E obrigatoria uma lista local nao vazia de digests SHA-256 hexadecimais minusculos, um por linha, para bloquear palavras-passe comuns/comprometidas. O SHA-256 serve apenas de chave de consulta da lista; as contas continuam a guardar Argon2id com salt. Comparacao exata de UTF-8, sem normalizacao ou consulta externa. O operador fornece e mantem uma lista de origem confiavel, abrangencia adequada e atualizada; uma lista pequena ou desatualizada nao comprova protecao contra palavras-passe comprometidas. Nao e fornecida uma lista ficticia como se fosse adequada a producao. Nao usar os hashes Argon2 das contas neste ficheiro.

Referencia: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html

## Comando

Usar .venv/bin/python -m mini_marketo.admin_accounts --help. As palavras-passe sao lidas duas vezes por getpass num terminal interativo; nao existem argumentos ou variaveis de ambiente para a palavra-passe da conta. O fallback de getpass com eco e rejeitado. Argumentos invalidos nao sao repetidos no erro para evitar mostrar um segredo passado acidentalmente. Nao usar capturas ou gravacao de terminal para estas operacoes.

Todas as operacoes exigem terminal interativo, confirmacao ADMIN e --blocklist valido. Define exatamente uma variavel de destino: MINI_MARKETO_DB para um ficheiro SQLite existente, ou MINI_MARKETO_DATABASE_URL para PostgreSQL. Nao existe fallback silencioso de destino e definir ambas e erro. O comando usa init_db existente: pode aplicar migracoes pendentes; fazer copia de seguranca e rever migracoes antes de usar numa base existente. Nao e um comando de criacao de cluster ou de base PostgreSQL.

Exemplos, depois de configurar o destino correto e a lista local:

```sh
.venv/bin/python -m mini_marketo.admin_accounts create operador --role admin --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts reset-password operador --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts set-role operador --role viewer --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts disable operador --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts enable operador --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts revoke-sessions operador --blocklist /caminho/blocked.sha256
```

Nao copiar um destino de testes para producao sem o rever. Nao guardar DSN, palavras-passe ou a lista de bloqueio privada da organizacao no Git. O comando imprime apenas resultado generico, nao registos, hashes, DSNs ou excecoes com parametros. Retorna 0 em sucesso e 2 em erro/cancelamento. Falhas nao provocam repeticao automatica; confirmar o estado antes de repetir, sobretudo se houver erro de comunicacao apos commit. A confirmacao textual nao substitui permissoes do sistema operativo. A ligacao fecha tambem perante falha de inicializacao.

## Verificacao deste incremento

82 casos aprovados em 0,85 s num pacote temporario, com repositorio SQLite e Argon2id reais, DDL de acesso v9 reproduzido e fixtures locais. Copias de access_repository.py e passwords.py conferidas pelos hashes Git. Os testes do comando simulam terminal, introducao de palavras-passe e abertura da ligacao; nao foi uma sessao manual com TTY nem uma execucao completa do projeto. Uma falha inicial de fixture de captura de terminal foi corrigida antes desta execucao aprovada.

Os testes preparados usam db do projeto para correr casos de persistencia e despacho do comando em SQLite/PostgreSQL; os testes de politica e preflight correm sem persistencia. Cobrem Unicode/bytes, bloqueio, duplicados e corrida de unicidade, estado/papel, revogacao isolada, versao obsoleta, alteracao durante hashing, falha de hashing, cancelamento, eco inseguro, fecho de ligacoes e supressao de segredos nos erros.

PostgreSQL, migracoes reais pelo comando e regressao completa ainda nao executados neste ambiente. Validar na base de testes, com MINI_MARKETO_TEST_PG exportada, sem executar operacoes administrativas contra dados reais:

```sh
.venv/bin/python -m pytest -q -rs tests/test_admin_account_service.py tests/test_admin_accounts_cli.py
.venv/bin/python -m pytest -q -rs
```

## Criterios ainda necessarios para fechar a etapa 4

1. Login/logout web, cookies seguros, CSRF, redirecionamentos locais seguros e matriz admin/viewer por rota, incluindo documentacao de API. Negacao por omissao e preservacao das rotas publicas verificadas.
2. Limitacao de tentativas coerente entre processos, politica de limpeza e duracoes de sessao configuradas; ensaio no ambiente de destino.
3. Importacao/exportacao com validacao, duplicados e relatorio de erros, sem consentimento implicito.
4. Remocao/anonimizacao considerando referencias de campanhas, consentimento e supressoes; decisao sobre remocao de empresas.
5. Politica explicita de retencao, execucao segura e testes de preservacao de dados. Prazos de conservacao nao serao inventados a partir de requisitos tecnicos.
6. Suite completa SQLite/PostgreSQL, testes de acesso negado e verificacao operacional relevante. Testes aprovados nao substituem os requisitos anteriores.

A administracao continua a exigir rede privada ou proxy autenticado. Nao marcar etapa 4 como concluida nem iniciar edicao publica da etapa 5 com base apenas neste incremento.
