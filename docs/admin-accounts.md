# Administração local de contas

## Estado atual — 20 de outubro de 2026

Gestão local de contas e acesso HTTP administrativo estão integrados em `main`; o PR #27 concluiu a ligação de login/logout, guard, cookies, CSRF e autorização por rota. Esta ferramenta continua a ser uma CLI do operador, não uma interface web de gestão. Não conclui a etapa 4: fluxos e políticas de dados pessoais permanecem pendentes no [roadmap](roadmap.md).

O acesso ao sistema operativo e à base é a fronteira de confiança da CLI. Nunca ligar `AdminAccountService` diretamente a uma rota sem autenticação/autorização. Arranque: [README](../README.md); contrato integrado: [admin-http.md](admin-http.md).

## Operações

- `create`: cria a primeira conta ou outra conta, com papel admin/viewer explícito, sem credenciais predefinidas. Não substitui contas existentes; UNIQUE protege corrida entre verificação e inserção.
- `reset-password`: exige versão observada, altera o hash e revoga todas as sessões da conta na transação. Preserva papel/estado; não reativa ou promove uma conta inativa/viewer.
- `set-role`, `enable`, `disable`: exigem versão observada e revogam sessões. Repetir papel/estado incrementa a versão e revoga por precaução. Conflitos não são repetidos automaticamente.
- `revoke-sessions`: elimina sessões existentes de forma idempotente, sem alterar palavra-passe, papel ou versão. Não impede login novo ou já em curso; usar `disable` para impedir novas emissões.

Pode desativar/despromover o último administrador; não há contagem não atómica que prometa impedir esse caso. Recuperação cabe ao operador autorizado via CLI. O serviço não elimina contas ou contactos de marketing.

## Política de palavras-passe

Mínimo de 15 caracteres Unicode, sem MFA neste fluxo; máximo de 4096 bytes UTF-8, coerente com `passwords.py`. Preserva espaços e Unicode, sem truncagem, remoção de espaços, composição obrigatória ou rotação periódica arbitrária. Palavra-passe não pode ser igual ao nome da conta, ignorando caixa. Nome canónico: 1 a 128 caracteres, UTF-8 válido, minúsculas, sem NUL ou espaços exteriores.

É obrigatória uma lista local não vazia de digests SHA-256 hexadecimais minúsculos, um por linha, para bloquear palavras-passe comuns/comprometidas. SHA-256 é apenas chave de consulta da lista; contas guardam Argon2id com salt. Comparação exata de UTF-8, sem normalização ou consulta externa. Não colocar hashes Argon2 das contas nesse ficheiro.

O operador fornece e atualiza uma lista de origem confiável e abrangência adequada. Uma lista pequena/desatualizada não comprova proteção; não se fornece lista fictícia como adequada a produção. Referência da conceção: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html

## Destino, bootstrap e comando

Inicializar/migrar o esquema pelo arranque controlado da aplicação antes do bootstrap. Para SQLite, a CLI exige um ficheiro existente. Não cria cluster ou base PostgreSQL.

Definir exatamente uma variável de destino: `MINI_MARKETO_DB` (SQLite) ou `MINI_MARKETO_DATABASE_URL` (PostgreSQL). Ambas definidas é erro; não existe fallback silencioso. Usar o mesmo destino administrativo da aplicação. A CLI usa `init_db` existente e pode aplicar migrações pendentes: rever migrações e fazer backup antes de usar numa base existente.

```sh
.venv/bin/python -m mini_marketo.admin_accounts --help
.venv/bin/python -m mini_marketo.admin_accounts create operador --role admin --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts reset-password operador --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts set-role operador --role viewer --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts disable operador --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts enable operador --blocklist /caminho/blocked.sha256
.venv/bin/python -m mini_marketo.admin_accounts revoke-sessions operador --blocklist /caminho/blocked.sha256
```

`/caminho/blocked.sha256` é marcador a substituir pela lista real. Todas as operações exigem terminal interativo, confirmação `ADMIN` e `--blocklist` válido. Palavras-passe são lidas duas vezes por `getpass`; não existem argumentos nem variáveis de ambiente para a palavra-passe da conta. Fallback com eco é rejeitado. Não gravar/capturar terminal durante estas operações.

Argumentos inválidos não são repetidos no erro. Não guardar DSN, palavras-passe ou lista privada da organização no Git. Só é impresso resultado genérico, não registos, hashes, DSNs ou exceções com parâmetros. Retorna 0 em sucesso e 2 em erro/cancelamento. Não há repetição automática; confirmar estado antes de repetir, sobretudo após erro de comunicação posterior ao commit. Confirmação textual não substitui permissões do sistema operativo. A ligação fecha mesmo perante falha de inicialização.

## Histórico e validação posterior

Preparação original: 82 aprovados em 0,85 s num pacote temporário, com SQLite e Argon2id reais, DDL v9 reproduzido e fixtures locais; cópias de `access_repository.py` e `passwords.py` conferidas por hash Git. Uma falha inicial de fixture de captura foi corrigida. Os testes da CLI simulavam terminal, palavra-passe e abertura de ligação: não foi sessão manual com TTY ou suite completa, nem validação PostgreSQL naquele ambiente.

Os testes preparados usam `db` para persistência/despacho SQLite/PostgreSQL e casos sem persistência para política/preflight. Cobrem Unicode/bytes, lista de bloqueio, duplicados/unicidade, estado/papel, versões, revogação, alteração durante hashing, falha de hashing, cancelamento, eco inseguro, fecho e erros sem segredos.

A seleção focada reportada para `e2012d850260c7f5ea74b41adb1d5ef1ea7704ad` antes do PR #27 incluiu `tests/test_admin*.py` e `tests/test_access*.py`, com 999 aprovados com PostgreSQL no conjunto. Não atribuir os 999 apenas à CLI, nem apresentar como ensaio manual de produção. O assistente não executou esses testes; suite completa e validação operacional continuam distintas.

Verificação focada possível no ambiente de testes, sem operações contra dados reais:

```sh
.venv/bin/python -m pytest -q -rs tests/test_admin_account_service.py tests/test_admin_accounts_cli.py
```

A suite completa SQLite/PostgreSQL só deve ser declarada aprovada quando efetivamente executada. Os exemplos documentais não foram executados nesta atualização.

## O que falta na etapa 4

A montagem HTTP e os mecanismos de acesso deixaram de ser pendências. Mantêm-se: ensaio no ambiente de destino, política de durações/orçamentos/segredos e limpeza operacional; importação/exportação sem consentimento implícito; remoção/anonimização e referências; remoção de empresas; política de retenção e execução segura; regressão completa quando exigida pelo âmbito.

Não considerar o merge autorização de exposição pública. Manter as restrições de rede/proxy adequadas até validar o ambiente, sem confundir isso com ausência de autenticação na aplicação. Gestão de dados pessoais não está concluída e novos editores/integrações exigem autorização e revisão próprias.
