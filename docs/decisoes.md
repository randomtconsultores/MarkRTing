# Decisões de arquitetura

## Etapa 1 - Contactos e empresas

- **Camadas**: domínio (regras puras, sem framework) -> serviço -> repositório (SQL) -> web (FastAPI/Jinja2).
- **Integridade em duas camadas**: o serviço valida e dá mensagens claras; a base de dados garante com UNIQUE, CHECK e chaves estrangeiras. Alternativa rejeitada: validar apenas em Python (falha com pedidos concorrentes).
- **Transações**: cada escrita usa `with conn` (commit/rollback). Quando existirem operações compostas, a transação passa para o serviço.
- **Ligação SQLite única** com `check_same_thread=False`: suficiente para a v1; rever para ligação por pedido ou PostgreSQL.
- **Sem interface abstrata de repositório**: os testes usam SQLite em memória. Só se abstrai com uma segunda implementação real.
- **Datas em UTC**: `created_at` é guardado e mostrado em UTC (cabeçalho da coluna indica-o). Conversão para hora local fica para quando for necessário.
- **Pendente**: política ao apagar empresas (atualmente ON DELETE SET NULL), paginação, consentimento (finalidade, origem, momento, histórico).

## Experiência 1 - Emails múltiplos por contacto

Requisito novo: um contacto pode ter vários emails, um deles principal.

**O que mudou**

- Persistência: o email sai de `contacts` para a tabela `contact_emails` (relacionamento 1-N). `email` continua UNIQUE globalmente (um email não pode pertencer a dois contactos). Um índice parcial `WHERE is_primary = 1` garante no máximo um email principal por contacto.
- Migração: `PRAGMA user_version` identifica a versão do esquema. Uma BD v1 é migrada numa única transação (renomear, recriar, copiar, apagar); cada email antigo passa a principal. Uma BD nova nasce já na v2.
- Repositório: SQL de emails; a listagem carrega todos os emails numa só consulta (evita N+1).
- Serviço: `add_email`, `set_primary_email`, `remove_email`. Regras: não se remove o email principal nem o único email.
- Web: três rotas novas e o template da listagem.

**O que não mudou**

- Normalização e validação do email (`domain`), a assinatura de `create_contact` e os testes anteriores (`Contact.email` passou a propriedade que devolve o email principal).

**Alternativas consideradas**

- Coluna JSON/texto com vários emails em `contacts`: rejeitada, perde a unicidade garantida pela BD.
- Principal como coluna `primary_email_id` em `contacts`: rejeitada, cria referência circular entre tabelas.
- Email não único entre contactos: rejeitada, reintroduz duplicados de pessoas.
