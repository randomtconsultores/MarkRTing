# Etapa 4 — Acesso e dados pessoais

## Estado

Login próprio aprovado pelo utilizador em 16 de outubro de 2026. Este documento regista requisitos e uma proposta técnica; não comprova implementação, migração ou testes. Ramo: feat/etapa4-acesso.

## Requisitos confirmados

- Contas administrativas próprias, separadas dos contactos de marketing.
- Sem registo público de contas administrativas.
- Autenticação, autorização por papéis, sessões revogáveis e proteção das operações administrativas.
- Subscrição, confirmação, cancelamento e eventos conservam os mecanismos próprios; a sessão administrativa não os substitui.

## Proposta para o primeiro incremento

- Argon2id através de biblioteca mantida; não implementar hashing de palavras-passe manualmente. Escolher biblioteca após verificar documentação e compatibilidade. Parâmetros pelo menos equivalentes ao mínimo OWASP, com medição no ambiente de execução.
- Sessões opacas: segredo aleatório no cookie e apenas hash do segredo na base de dados. Estado no servidor para permitir revogação. Não usar JWT nem localStorage neste fluxo web.
- Cookie HttpOnly, Secure por omissão, SameSite explícito, sem Domain. Qualquer exceção HTTP de desenvolvimento deve ser explícita e não permitir abertura acidental em produção.
- Expiração absoluta e por inatividade verificadas no servidor; valores ainda por definir. Nova sessão após login; logout por POST com revogação no servidor. Desativação de conta e alteração de palavra-passe ou papel invalidam sessões existentes.
- Proteção CSRF nos formulários administrativos, incluindo login e logout. SameSite é defesa adicional, não substituto. Validar redirecionamentos para evitar destinos externos.
- Controlo de acesso no servidor antes de operações de negócio. Inventariar todas as rotas, incluindo documentação da API, e testar que novas rotas não ficam abertas por esquecimento.
- Proposta inicial de papéis: admin (operações administrativas) e viewer (consulta, sem mutações). Matriz por rota e gestão de contas ainda por detalhar; negar papéis desconhecidos.
- Criação inicial e recuperação de acesso por comando local controlado, sem credenciais predefinidas nem palavras-passe em argumentos ou logs. Sem recuperação pública por email neste primeiro incremento.
- Erros de login genéricos e limitação de tentativas com estado coerente entre processos. Definir política e limpeza antes de expor login; não depender apenas de contador em memória.
- Persistência e migrações em SQLite e PostgreSQL, respeitando ligações por pedido e preservando os dados existentes.

## Alternativas e compromissos

Identidade externa: não escolhida, por decisão do utilizador. JWT ou sessão apenas no cookie: não propostos, pois a revogação imediata exigiria estado adicional. Sessões no servidor acrescentam consultas e limpeza, mas tornam a revogação explícita.

## Verificação exigida

Credenciais inválidas, conta inexistente ou inativa, duplicados, papéis sem permissão, sessão desconhecida/expirada/revogada, CSRF ausente ou incorreto, logout, redirecionamentos, limitação de tentativas, concorrência e preservação das rotas públicas. Testes de migração nos dois motores e regressão da suite existente. Não declarar testes aprovados sem execução.

## Fora deste incremento

Importação/exportação, remoção/anonimização, retenção geral, remoção de empresas, MFA e recuperação pública por email. A etapa 4 não fica concluída apenas com login. Até a proteção estar implementada e verificada, manter administração em rede privada ou proxy autenticado.

## Referências técnicas

- OWASP Password Storage Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP Authentication Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html

## Ajustes ao limitador — 16 de outubro de 2026

Ramo deste incremento: feat/etapa4-protecao-administrativa. A proposta inicial acima e historica; esta secao regista os ajustes posteriores ao incremento 2/3, sem declarar a integracao web concluida.

- Chave HMAC: LoginAttemptConfig usa field(repr=False) para excluir hmac_key da representacao automatica. Isto nao protege serializacoes explicitas, asdict ou logs manuais: o segredo continua a exigir cuidado operacional.
- Intervalo: a v10 ja publicada usa INTEGER para attempts no PostgreSQL. Em vez de reescrever uma migracao aplicada ou introduzir uma nova migracao apenas para admitir orcamentos superiores a dois mil milhoes, a configuracao e a reserva passam a aceitar limites entre 1 e 2147483647, iguais nos dois motores. O teto e uma restricao tecnica, nao um valor de producao predefinido. Tempos e duracao da janela mantem o intervalo de 64 bits. Nao ha alteracao de esquema nem transformacao de dados existentes.
- Limpeza: cleanup_expired_login_attempts(now, batch_size=1000) e cleanup_expired(now=..., batch_size=1000) processam um lote por chamada, com tamanho entre 1 e 10000. batch_size e keyword-only; as chamadas antigas com apenas now continuam validas, mas deixam de prometer remover todos os expirados numa unica execucao. A selecao ordena por expires_at e bucket_hash e o DELETE revalida a expiracao na condicao externa. O limite controla linhas eliminadas, nao garante tempo maximo da consulta. O operador deve agendar novas chamadas limitadas enquanto houver trabalho; nao ha eliminacao automatica apenas por expirar.

Verificacao deste ajuste: 33 casos aprovados em 0,25 s com SQLite real em ficheiro e o DDL v10 existente, num pacote temporario com fixture minima. Inclui os novos casos focados e os testes existentes de test_login_attempt_limiter.py conferidos pelo hash Git, incluindo 12 ligacoes concorrentes. Nao foi carregada a fixture completa do projeto; PostgreSQL, concorrencia de limpeza com renovacao simultanea e a suite completa nao foram executados aqui. A preservacao de um bucket renovado foi testada sequencialmente. Os 1550 testes reportados antes destes ajustes nao validam esta nova versao.

Validacao focada no ambiente do projeto, com MINI_MARKETO_TEST_PG configurada para testes: .venv/bin/python -m pytest -q -rs tests/test_login_attempt_hardening.py tests/test_login_attempt_limiter.py. A integracao web 3/3 e a conclusao global da etapa 4 continuam pendentes.
