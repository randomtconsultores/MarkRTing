# Decisões de arquitetura

## Etapa 1 - Contactos e empresas

- **Camadas**: domínio (regras puras, sem framework) -> serviço -> repositório (SQL) -> web (FastAPI/Jinja2).
- **Integridade em duas camadas**: o serviço valida e dá mensagens claras; a base de dados garante com UNIQUE, CHECK e chaves estrangeiras. Alternativa rejeitada: validar apenas em Python (falha com pedidos concorrentes).
- **Transações**: cada escrita usa `with conn` (commit/rollback). Quando existirem operações compostas, a transação passa para o serviço.
- **Ligação SQLite única** com `check_same_thread=False`: suficiente para a v1; rever para ligação por pedido ou PostgreSQL.
- **Sem interface abstrata de repositório**: os testes usam SQLite em memória. Só se abstrai com uma segunda implementação real.
- **Pendente**: política ao apagar empresas (atualmente ON DELETE SET NULL), paginação, consentimento (finalidade, origem, momento, histórico).
