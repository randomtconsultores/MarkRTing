# Concorrencia dos repositorios de sessoes

Complementa sessions.md. Este incremento acrescenta apenas testes e documentacao; nao altera codigo de producao, esquema, dependencias, rotas ou dados existentes.

## Escopo

O ficheiro tests/test_session_concurrency.py usa a fixture db existente, parametrizada para SQLite e PostgreSQL. Cada operacao concorrente abre e fecha a sua propria ligacao, criada na thread que a utiliza, para a mesma base/ficheiro ou schema de teste.

ScheduledConnection delega SQL e transacoes reais. Eventos controlam pontos de execucao, sem sleeps nem simulacao dos resultados da base de dados. A primeira operacao e suspensa depois de executar a instrucao escolhida, antes do commit. A segunda sinaliza que chegou a chamada SQL enquanto a primeira permanece suspensa; so entao a primeira e libertada. O teste nao consulta as tabelas internas de locks e nao prova que o motor ja colocou a segunda instrucao em espera nesse instante. Verifica os resultados e invariantes para estes intercalamentos controlados, nao todos os escalonamentos possiveis.

As ligacoes de teste usam busy_timeout de 5 s em SQLite e lock_timeout de 5 s/statement_timeout de 8 s em PostgreSQL. Os eventos e futures tem limites de espera. Estas opcoes sao locais as ligacoes dos testes, nao configuracao de producao. As ligacoes fecham em finally e a primeira operacao e libertada mesmo perante falha na coordenacao.

## Casos

- Revogacao individual, alteracao de palavra-passe, alteracao de papel e desativacao antes da renovacao: a resolucao posterior nao devolve nem recria a sessao.
- Resolucao antes de revogacao individual: pode devolver uma sessao valida antes da revogacao, mas esta continua a eliminar a sessao para os pedidos seguintes. Uma resolucao nao autoriza indefinidamente uma operacao de negocio.
- Duas renovacoes, nas duas ordens temporais: last_seen_at nunca recua e expires_at nao muda.
- Alteracao de credenciais antes de criacao com versao antiga: nao e criada sessao.
- Criacao antes de alteracao de credenciais: a criacao pode concluir, mas a alteracao posterior revoga tambem essa sessao.

Sao 9 casos por motor, 18 quando ambos estao configurados. Os testes usam digests e hashes ficticios explicitamente de teste; nao fazem verificacao de palavras-passe nem exercitam uma rota HTTP.

## Verificacao realizada

9 casos executados com duas ligacoes SQLite reais em ficheiro, repetidos 10 vezes: 90 execucoes aprovadas. Cada repeticao demorou entre 0,08 e 0,10 s. Execucao isolada num pacote temporario com o DDL de acesso v9 reproduzido; o ficheiro SQLite do repositorio foi conferido pelo hash Git 2d140d54ad3695b46478dace353c88471dd457d6. Nao foi uma execucao da suite completa ou da fixture completa do projeto.

PostgreSQL e regressao completa ainda nao executados neste ambiente. O sucesso local nao comprova esses resultados nem constitui uma prova geral de ausencia de corridas. O registo de verificacao original em sessions.md permanece historico; este documento acrescenta a cobertura preparada neste incremento.

## Validar no ambiente do projeto

Usar a base PostgreSQL de testes ja configurada, nunca producao. A fixture cria e elimina schemas temporarios. MINI_MARKETO_TEST_PG tem de estar exportada no ambiente dos comandos; nao guardar credenciais no repositorio.

```sh
: "${MINI_MARKETO_TEST_PG:?Define e exporta a ligacao PostgreSQL de testes}"
.venv/bin/python -m pytest -q -rs tests/test_session_concurrency.py tests/test_session_service.py tests/test_access_repository.py
.venv/bin/python -m pytest -q -rs
```

Sem MINI_MARKETO_TEST_PG, a fixture ignora PostgreSQL; isso nao conta como validacao desse motor. Ainda faltam autenticacao coordenada, limitacao de tentativas entre processos, bootstrap administrativo e protecoes web. Manter administracao em rede privada ou proxy autenticado ate implementacao e verificacao dessas protecoes.
