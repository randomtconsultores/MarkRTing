# Etapa 4 — Acesso e dados pessoais

## Estado atual — 20 de outubro de 2026

O login próprio, aprovado em 16 de outubro de 2026, evoluiu de proposta para implementação integrada. O PR #27, de `feat/etapa4-integracao-http` para `main`, concluiu a ligação HTTP de autenticação, sessões, autorização admin/viewer, CSRF, limitação persistente de tentativas e gestão local de contas já preparada nos incrementos anteriores.

A etapa 4 permanece em curso. Importação/exportação, remoção/anonimização, retenção geral e remoção de empresas continuam pendentes. Integração e testes não autorizam automaticamente exposição pública ou tratamento de dados reais.

A descrição histórica dos primeiros incrementos e respetivos resultados não deve ser interpretada como uma lista atual de funcionalidades em falta. Contratos detalhados: [passwords.md](passwords.md), [sessions.md](sessions.md), [authentication.md](authentication.md), [web-security.md](web-security.md), [admin-protection.md](admin-protection.md), [admin-accounts.md](admin-accounts.md) e [admin-http.md](admin-http.md).

## Requisitos confirmados

- Contas administrativas próprias, separadas dos contactos de marketing; sem registo público administrativo.
- Autenticação, autorização por papéis, sessões revogáveis e proteção das operações administrativas.
- Subscrição, confirmação, cancelamento e eventos conservam mecanismos próprios; a sessão administrativa não os substitui.

## Decisões implementadas para acesso

- Hashing Argon2id através de biblioteca, sem implementação manual. Parâmetros e limites constam de `passwords.md`; medições no ambiente de execução continuam relevantes.
- Sessões opacas: segredo aleatório no cookie e apenas digest na base de dados. Estado no servidor para revogação; sem JWT nem localStorage neste fluxo.
- Cookies HttpOnly, SameSite explícito, Path=/, sem Domain e Secure em HTTPS. HTTP apenas em desenvolvimento local explicitamente configurado, com nomes de cookies distintos.
- Expiração absoluta e por inatividade no servidor, com durações obrigatórias de configuração. Nova sessão após login; logout exclusivamente por POST, com revogação e limpeza de cookies.
- Alterações de palavra-passe, papel ou estado por operações de gestão invalidam sessões existentes. A versão de credenciais é verificada no acesso.
- CSRF vinculado, com finalidades distintas login/admin/logout; verificação de origem e redirecionamentos locais seguros. SameSite é defesa adicional, não substituto.
- Matriz explícita por método/caminho e recusa por omissão, incluindo documentação da API. `admin` pode editar/escrever; `viewer` consulta e termina sessão, sem mutações administrativas.
- Bootstrap e recuperação por comando local controlado, sem credenciais predefinidas nem palavras-passe em argumentos ou logs; sem recuperação pública por email neste incremento.
- Erros de login genéricos e reserva persistente/transacional dos limites por conta, IP e instalação antes do hashing. As chaves CSRF e HMAC de tentativas têm de ser independentes.
- Persistência SQLite/PostgreSQL com migrações existentes e ligações abertas/fechadas por operação. Acesso e hashing dos pedidos são coordenados em workers, não diretamente no event loop.
- Router e guard montados conjuntamente em `create_app`. Configuração ausente/inválida impede o arranque de toda a aplicação, incluindo páginas públicas; não se inicia uma versão desprotegida.
- Contexto `admin_access` criado por pedido após validar sessão/conta, sem expor o token bruto. Formulários administrativos recebem CSRF; páginas públicas não recebem esse contexto.

## Alternativas e compromissos

Identidade externa não escolhida, por decisão do utilizador. JWT ou sessão apenas no cookie não escolhidos: revogação imediata exigiria estado adicional. Sessões no servidor acrescentam consultas e limpeza, mas tornam a revogação explícita.

O operador continua a definir política de durações, orçamentos de tentativas, origem, TLS/proxy, distribuição e rotação de segredos. Implementação de um carregador de configuração não define essa política. A indisponibilidade das rotas públicas perante erro de configuração é consequência deliberada da aplicação ASGI conjunta.

Rehash transparente e a sua coordenação transacional permanecem uma decisão técnica pendente descrita em `authentication.md`; não confundir com reset de palavra-passe, que já existe. MFA e recuperação pública por email continuam fora do incremento de acesso.

## Ajustes históricos ao limitador — 16 de outubro de 2026

Ramo original: `feat/etapa4-protecao-administrativa`. À data, estes ajustes não concluíam a integração web; essa pendência foi posteriormente resolvida pelo PR #27.

- `LoginAttemptConfig` usa `field(repr=False)` para excluir `hmac_key` do repr. Não protege serializações explícitas, `asdict` ou logs manuais.
- A migração v10 publicada usa INTEGER para `attempts` no PostgreSQL. Configuração e reserva aceitam 1 a 2147483647 tentativas, iguais nos dois motores, sem reescrever migração aplicada nem criar alteração de esquema desnecessária. Este teto é uma restrição técnica, não uma política de produção. Tempos e duração conservam o intervalo de 64 bits validado.
- `cleanup_expired_login_attempts(now, batch_size=1000)` e `cleanup_expired(now=..., batch_size=1000)` processam um lote por chamada, de 1 a 10000; `batch_size` é keyword-only. Chamadas antigas com apenas `now` continuam válidas, mas não prometem remover todos os expirados de uma vez. Seleção por `expires_at` e `bucket_hash`, com revalidação da expiração no DELETE externo.
- O limite controla linhas eliminadas, não tempo máximo da consulta. O operador agenda novas chamadas enquanto houver trabalho. Expiração não apaga automaticamente os registos. O 3c acrescentou limpeza limitada de sessões e o comando `admin_maintenance`, sem scheduler nem limpeza por pedido.

Resultado histórico desse ajuste: 33 aprovados em 0,25 s, com SQLite real em ficheiro e DDL v10, em pacote temporário com fixture mínima; incluía 12 ligações concorrentes. Cópias dos testes existentes conferidas por hash Git. Não carregou a fixture completa nem validou PostgreSQL, limpeza com renovação concorrente ou suite completa. Preservação de bucket renovado foi testada sequencialmente. Os 1550 testes reportados antes desse ajuste não validavam a nova versão.

Comando focado então indicado: `.venv/bin/python -m pytest -q -rs tests/test_login_attempt_hardening.py tests/test_login_attempt_limiter.py`, com PostgreSQL de testes configurado.

## Validação da integração HTTP

Validação reportada pelo utilizador no Copilot para `e2012d850260c7f5ea74b41adb1d5ef1ea7704ad`, antes do merge: worktree isolado, Python da `.venv` e imports do checkout, sem alterações de código e com workspace original e `poetry.lock` preservados.

- `pytest -q tests/test_admin_guard_response_errors.py`: 2 aprovados em 0,06 s.
- `pytest -q tests/test_access*.py tests/test_login*.py tests/test_admin*.py tests/test_authentication*.py tests/test_session*.py tests/test_web*.py`: 999 aprovados com PostgreSQL em 69,36 s; já inclui os dois de regressão.
- `git diff --check f11204e9bd67a249e16eee7b7c7ac5c7ffb35d6c`: passou sem erros.

Os comandos pytest acima são a forma abreviada reportada e foram executados com Python da `.venv`. Não foi a suite completa, nem a mesma seleção anterior de 920 testes, nem validação sobre o merge ou este commit documental. O assistente não executou os testes reportados. O guard foi corrigido para relançar exceções após `http.response.start`, sem tentar emitir outra resposta.

## Condições ainda pendentes

- Definir e implementar fluxos de importação/exportação com validação, duplicados, permissões e relatório de erros, sem consentimento implícito.
- Definir remoção/anonimização e conservação de referências a campanhas, consentimentos e supressões; decidir remoção de empresas.
- Aprovar política de retenção e execução segura, com preservação dos dados válidos. Não inventar prazos a partir de requisitos técnicos.
- Concluir verificações operacionais do ambiente de destino, incluindo browser/proxy/TLS, política de segredos, recuperação e manutenção agendada.
- Executar a regressão completa SQLite/PostgreSQL quando se pretender declarar essa cobertura; não substituir por contagens de seleções focadas.

Próximo incremento proposto: inventariar o ciclo de vida dos dados e registar alternativas/decisões antes de implementar eliminação. Não altera por si só dados existentes. Ver [roadmap.md](roadmap.md).

## Referências técnicas da conceção original

- OWASP Password Storage Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP Authentication Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
