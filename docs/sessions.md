# Servico de sessoes administrativas

Complementa decisoes-etapa4.md e passwords.md. Nao implementa login, autorizacao de rotas ou transporte por cookies.

## Contrato e decisoes

SessionService recebe um repositorio por pedido e limites explicitos de duracao absoluta e inatividade em segundos. Nao existem valores por omissao; ambos devem ser inteiros positivos, com inatividade nao superior a duracao absoluta. O relogio e fornecido como segundos Unix UTC, dentro do intervalo BIGINT positivo ou zero. Os valores finais para producao continuam por definir na configuracao da aplicacao.

- issue so deve ser chamado pela futura camada de autenticacao apos verificar credenciais. Recebe o identificador e a credential_version observada nessa verificacao; nao deve receber uma versao mais recente obtida depois. O repositorio revalida conta ativa e versao na insercao. Identificador e versao, por si so, nao autenticam o chamador.
- Cada emissao usa secrets.token_urlsafe(32): 32 bytes aleatorios, codificados num token URL-safe de 43 caracteres. Apenas o digest SHA-256 hexadecimal segue para o repositorio. IssuedSession omite o token do repr; o chamador continua responsavel por nao o registar nem devolver em URLs. A expiracao absoluta e fixada na emissao.
- resolve rejeita formatos invalidos antes de aceder ao repositorio. touch_valid_session atualiza last_seen_at apenas se a sessao existir, nao estiver expirada por nenhum dos limites e a conta continuar ativa com a mesma versao. Validacao e atualizacao usam um UPDATE condicionado, seguido de leitura na mesma transacao. A expiracao absoluta nunca e alterada.
- O limite de inatividade e exclusivo: no instante last_seen_at + idle_seconds, a sessao ja expirou. O limite absoluto tambem e exclusivo. Uma hora inferior a atividade guardada e rejeitada; pedidos concorrentes com timestamps mais antigos podem ser rejeitados em vez de recuarem a atividade.
- revoke e idempotente e nao recria sessoes. Falhas de persistencia propagam-se, sem converter falha da base de dados em sessao valida. Uma colisao de token falha pela restricao de unicidade, sem substituir uma sessao existente.

Os dois repositorios conservam o restante comportamento. Nao ha alteracoes de esquema, dependencias ou dados existentes. Nao e introduzida uma interface abstrata adicional: ambos os repositorios implementam o mesmo contrato usado pelo servico.

## Verificacao

48 casos aprovados em 0,07 s num pacote temporario com SQLite e o DDL de acesso v9 reproduzido, fora da instalacao completa do repositorio. As versoes-base dos dois repositorios foram conferidas pelo hash Git dos ficheiros antes de acrescentar o metodo. Os testes preparados para o repositorio usam a fixture db existente, parametrizada para SQLite/PostgreSQL; os casos independentes de persistencia correm uma vez.

Cobertura: digest sem token em claro, emissao distinta, limites e renovacao, ausencia de renovacao de sessoes invalidas, conta inexistente/inativa ou versao obsoleta, alteracoes de utilizador, revalidacao mesmo sem eliminacao da sessao, revogacao isolada/idempotente, entradas invalidas, overflow, falha da base de dados, rollback da atividade, tamanho solicitado ao gerador, colisao e falha fechada do gerador.

A suite completa, a execucao em PostgreSQL e corridas reais entre ligacoes concorrentes nao foram verificadas neste ambiente. Nao afirmar que esses testes passaram. O comportamento durante revogacao concorrente exige testes especificos antes da exposicao do login; uma resolucao nao substitui autorizacao transacional de operacoes de negocio.

## Continuidade

Ainda faltam coordenacao da verificacao de credenciais, tratamento de conta inexistente com custo adequado, limitacao de tentativas coerente entre processos, politica de palavras-passe, bootstrap administrativo, cookies, CSRF e autorizacao. A administracao continua a exigir rede privada ou proxy autenticado. Nenhuma rota e adicionada ou protegida neste incremento.
