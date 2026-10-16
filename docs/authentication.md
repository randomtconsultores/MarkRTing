# Coordenacao da autenticacao administrativa

Complementa decisoes-etapa4.md, passwords.md e sessions.md. Este incremento liga verificacao de palavras-passe a emissao de sessoes, sem adicionar rotas, cookies, migracoes ou dependencias.

## Contrato

AuthenticationService recebe o repositorio por pedido e duracoes explicitas, construindo SessionService com o mesmo repositorio. authenticate recebe username, password e now em segundos Unix UTC. Devolve IssuedSession numa autenticacao bem-sucedida ou None para falha de credenciais, conta ausente/inativa, papel desconhecido ou versao entretanto alterada. Falhas de infraestrutura propagam-se; nao sao convertidas em acesso aceite ou confundidas com credenciais incorretas. Tempos invalidos ou expiracao fora de BIGINT provocam ValueError antes da consulta.

O username tem de ser texto UTF-8 canonico, nao vazio, ate 128 caracteres, sem NUL, sem espacos exteriores e em minusculas. Nao ha normalizacao silenciosa; a criacao de contas tera de respeitar esse contrato. A palavra-passe segue o contrato de passwords.py sem remocao de espacos, normalizacao ou truncagem.

A palavra-passe e verificada antes de rejeitar conta inativa. Conta inexistente ou username invalido usa um hash Argon2id de substituicao, gerado uma vez por processo a partir de segredo aleatorio descartado, nao a cada pedido. Nao corresponde a qualquer conta e nunca permite autenticar uma conta ausente. Uma falha ao gerar esse hash impede a importacao do modulo em vez de ativar fallback inseguro.

Esta verificacao reduz a diferenca de trabalho entre conta existente e ausente para palavras-passe validas em formato; nao garante tempo constante. Entradas invalidas, hashes armazenados corrompidos, parametros Argon2 diferentes e consultas podem ter tempos distintos. Nao e substituto para limitacao de tentativas nem uma prova de resistencia a enumeracao.

A emissao recebe a credential_version do registo imutavel cuja palavra-passe foi verificada. Nao se volta a consultar a conta para obter uma versao mais recente depois da verificacao. A insercao condicionada do repositorio decide se a conta continua ativa com essa versao. Cada sucesso gera sessao nova; falhas nao revogam sessoes existentes. O retorno nao inclui palavra-passe ou hash e o token e omitido do repr de IssuedSession.

Nao ha rehash automatico: atualizar password_hash atraves do repositorio existente incrementa credential_version e revoga sessoes. A politica para rehash transparente e a sua coordenacao transacional ficam explicitas como pendentes, em vez de alterar esse comportamento silenciosamente.

## Testes e verificacao

37 testes unitarios aprovados em 0,46 s num pacote temporario, com Argon2id e SessionService reais e um repositorio de teste que regista chamadas e simula a verificacao de versao/estado na insercao. Foram reproduzidas apenas as dataclasses de acesso necessarias nesse pacote; nao foram executados metodos de repositorios reais. As copias de passwords.py e session_service.py foram conferidas pelos respetivos hashes Git. Esta execucao nao e a suite completa nem validacao de persistencia.

Os testes unitarios cobrem sucesso admin/viewer, falhas genericas, entrada invalida, verificacao de substituicao, conta inativa, papel desconhecido, hash corrompido, versao observada, alteracoes durante verificacao, limites temporais, propagacao de falhas e nova sessao por sucesso.

Foram ainda preparados 10 casos de integracao por motor usando a fixture db existente: emissao/resolucao admin/viewer, persistencia apenas do digest, credenciais incorretas/ausentes/inativas, preservacao de sessao numa falha, alteracoes de credenciais/papel/estado entre verificacao e emissao e hash corrompido. A alteracao durante verificacao nestes testes e injetada de forma deterministica, nao executada por threads; os intercalamentos reais de persistencia estao nos testes do incremento anterior.

Integracao SQLite/PostgreSQL e regressao completa deste incremento ainda nao executadas neste ambiente. Para validar, com MINI_MARKETO_TEST_PG exportada para a base de testes:

```sh
: "${MINI_MARKETO_TEST_PG:?Define e exporta a ligacao PostgreSQL de testes}"
.venv/bin/python -m pytest -q -rs tests/test_authentication_service.py tests/test_authentication_integration.py tests/test_passwords.py tests/test_session_service.py tests/test_access_repository.py tests/test_session_concurrency.py
.venv/bin/python -m pytest -q -rs
```

## Antes de expor login

Continuam obrigatorios limitacao de tentativas com estado coerente entre processos, politica de criacao de palavras-passe, bootstrap administrativo controlado, configuracao das duracoes, cookies seguros, CSRF, redirecionamentos seguros e autorizacao das rotas. O hashing sincrono nao deve correr diretamente no event loop assincrono. Este servico nao protege nenhuma rota existente e nao autoriza operacoes de negocio. Manter administracao em rede privada ou proxy autenticado. A etapa 4 permanece em curso.
