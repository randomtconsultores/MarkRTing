# Palavras-passe administrativas — incremento Argon2id

Complementa os requisitos em decisoes-etapa4.md. Este incremento implementa apenas as primitivas de palavras-passe; nao implementa autenticacao nem altera rotas, sessoes, esquema ou dados existentes.

## Decisao

Usar argon2-cffi >=23.1.0 em vez de implementar criptografia ou salts manualmente. O modulo mini_marketo.passwords cria hashes Argon2id com memoria de 19456 KiB (19 MiB), duas iteracoes, paralelismo 1, salt aleatorio de 16 bytes e digest de 32 bytes. Os parametros ficam explicitos para nao depender de alteracoes dos defaults da biblioteca. Correspondem ao minimo OWASP de 19 MiB, duas iteracoes e paralelismo 1; e necessario medir custo e capacidade no servidor de destino antes de expor login.

## Contrato

- hash_password recebe texto UTF-8 nao vazio, ate 4096 bytes, e devolve o hash codificado com os parametros e o salt. Nao remove espacos, nao normaliza Unicode e nao trunca a entrada. Falhas de hashing propagam-se, sem fallback inseguro.
- verify_password devolve False para entrada invalida, hash invalido, variante diferente de Argon2id ou palavra-passe incorreta. Apenas aceita hashes provenientes de armazenamento confiavel, nunca hashes fornecidos no pedido. Nao regista segredos.
- password_needs_rehash deve ser chamado apenas apos verificacao bem-sucedida. Deteta diferencas de parametros, nao apenas parametros mais fracos, e nao reescreve hashes automaticamente. Entradas invalidas provocam ValueError.
- O limite de 4096 bytes e uma defesa de recursos, nao uma politica completa de criacao de palavras-passe. Comprimento minimo e verificacao de palavras-passe comprometidas ficam para o fluxo de criacao/alteracao de contas.

## Integracao ainda pendente

O servico de autenticacao tera de tratar contas inexistentes/inativas e erros genericos, limitar tentativas entre processos e executar hashing fora do event loop assincrono. Nao se deve ligar diretamente este modulo a uma rota de login. A atualizacao de hashes tera de respeitar credential_version e as regras de revogacao transacional dos repositorios. Permanecem por implementar tokens, atividade das sessoes, bootstrap administrativo, cookies, CSRF e autorizacao. A administracao deve continuar em rede privada ou atras de proxy autenticado.

## Verificacao realizada

23 testes isolados aprovados em 0,56 s com Python 3.12.8, argon2-cffi 23.1.0 e pytest 8.3.5. Foram executados sobre o modulo preparado num pacote temporario, nao sobre uma instalacao completa do repositorio. Cobrem parametros, salts distintos, correspondencia/incorrecao, Unicode, espacos, limites em bytes, entradas e hashes invalidos, outra variante Argon2, rehash e propagacao de falha de hashing. A suite completa, integracao com SQLite/PostgreSQL e medicao no servidor de destino nao foram executadas neste incremento. O tempo dos testes nao e um benchmark de producao.

## Fontes

- https://argon2-cffi.readthedocs.io/en/stable/api.html
- https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
