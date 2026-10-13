# Decisões de arquitetura

## Etapa 1 - Contactos e empresas

- **Camadas**: domínio (regras puras, sem framework) -> serviço -> repositório (SQL) -> web (FastAPI/Jinja2).
- **Integridade em duas camadas**: o serviço valida e dá mensagens claras; a base de dados garante com UNIQUE, CHECK e chaves estrangeiras. Alternativa rejeitada: validar apenas em Python (falha com pedidos concorrentes).
- **Transações**: cada escrita usa `with conn` (commit/rollback). Quando existirem operações compostas, a transação passa para o serviço.
- **Ligação SQLite única** com `check_same_thread=False`: suficiente para a v1. Substituída por uma ligação por pedido (ver «Correção - Ligação por pedido»).
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

**Lição: migrar dados reais, não só dados de teste**

Na primeira execução sobre a base do Codespace, a migração falhou: existia um contacto com `company_id = 9999`, uma empresa inexistente. O SQLite da linha de comandos tem `foreign_keys` desligado por omissão, pelo que a chave estrangeira só protege as ligações que a ativam. A transação fez rollback e a base ficou intacta na v1, mas a mensagem era um `IntegrityError` genérico.

Decisão: antes de migrar, procurar contactos órfãos e abortar com `MigrationError`, listando os ids afetados e sem alterar a base de dados. Alternativa rejeitada: pôr `company_id` a NULL automaticamente, porque apagaria informação sem o utilizador saber.

A migração copia agora os contactos com `ORDER BY id`, para que os ids de `contact_emails` sigam a ordem dos contactos e não a ordem de leitura de um índice.

## Experiência 2 - PostgreSQL

Pergunta: quanto código muda ao acrescentar PostgreSQL como segunda base de dados?

**O que não muda**

- `domain/` e `services/`: nenhuma linha. O serviço só conhece os métodos do repositório e as exceções do domínio.

**O que é novo ou muda**

- `postgres_database.py` (ligação e esquema) e `PostgresContactRepository`: SQL e tratamento de erros próprios do PostgreSQL.
- `main.py`: `build_service_factory` escolhe o backend (`MINI_MARKETO_DATABASE_URL`), e `create_app` aceita um serviço já construído.
- Testes: a fixture `service` (em `conftest.py`) corre os testes do serviço e da web contra os dois backends. Os testes que dependem de SQL específico ficam em `test_sqlite_schema.py` e `test_postgres_schema.py`.

**Decisões**

- Dois repositórios sem interface abstrata (Protocol/ABC): o contrato é o conjunto de testes executado contra ambos. Reavaliar se surgir uma terceira implementação ou um erro de contrato que os testes não apanhem.
- `psycopg` 3 em `autocommit`, com cada escrita em `conn.transaction()`. Em vez de `with conn:`, que no psycopg 3 fecha a ligação à saída do bloco.
- Erros distinguidos por `UniqueViolation` e pelo nome da restrição (`contact_emails_email_key`), em vez de procurar texto na mensagem como no SQLite.
- Esquema: `IDENTITY`, `TIMESTAMPTZ`, `BOOLEAN`. A versão fica na tabela `schema_version`, e um bloqueio consultivo (`pg_advisory_xact_lock`) evita criar o esquema em simultâneo a partir de dois processos.
- Contrato de datas: os dois repositórios devolvem `created_at` como `datetime` ingénuo em UTC, porque a interface mostra UTC.
- Ordenação de empresas por `lower(name)`. No SQLite, `COLLATE NOCASE` só trata ASCII; no PostgreSQL, `lower()` depende da locale. É uma diferença conhecida.
- Isolamento dos testes: um schema por teste (`search_path`), criado e apagado no fim.

**Limitações conhecidas**

- Uma única ligação partilhada entre threads: resolvida em «Correção - Ligação por pedido».
- Não existe script de cópia de SQLite para PostgreSQL.
- A migração de esquema em PostgreSQL só deteta uma versão diferente e aborta.

**Validado**

- Testes do serviço, da web e de esquema com SQLite e PostgreSQL: 86 aprovados (PR #2).

## Correção - Ligação por pedido

**Problema, reproduzido antes de corrigir**

Um teste com 16 threads sobre um serviço com uma única ligação falhou nos dois backends:

- SQLite: `InterfaceError('bad parameter or other API misuse')`; uma violação de unicidade a surgir como `DatabaseError` genérico; um `DuplicateEmailError` para um email que nenhuma thread tinha criado; e um erro de integridade. O rollback de uma thread desfez a transação de outra.
- PostgreSQL: `InFailedSqlTransaction: current transaction is aborted` com a ligação em `[INERROR]`. Uma violação de unicidade numa thread deixou a ligação partilhada em transação falhada para todas as outras.

**Decisão**

- Uma ligação por pedido. Uma dependência do FastAPI (`get_service`) abre a ligação, constrói o serviço e fecha-a no fim, nos dois backends. O esquema é criado ou migrado uma vez, no arranque.
- SQLite com `timeout=30`: com várias ligações, os escritores esperam pelo bloqueio de escrita do ficheiro.
- `create_app(service=...)` mantém-se para os testes, que partilham um serviço; `:memory:` deixa de servir com `db_path`, porque cada ligação veria uma base diferente.
- `requires-python` baixa de `>=3.13` para `>=3.12`, porque o ambiente de desenvolvimento (container Alpine) usa Python 3.12 e os testes foram validados nessa versão. Contraria o «Python 3.13+» do plano inicial.

**Alternativas rejeitadas**

- `Lock` global à volta do repositório: serializa tudo e mantém o estado partilhado, escondendo o problema em vez de o resolver.
- Pool de ligações: aceita, mas adiada. O pool reduz o custo de abrir ligações, mas não resolve o problema de concorrência; acrescentaria uma dependência sem necessidade medida.

**Teste**

- `tests/test_concurrency.py`: em cada ronda, 12 threads fazem pedidos HTTP a criar o mesmo email ao mesmo tempo. Exige exatamente um sucesso por ronda, 422 «Já existe» para os restantes, nenhum erro 500, e uma listagem final com um contacto por ronda.

**Validado**

- Antes da correção, o teste de concorrência falhou nos dois backends (commit `5123901`).
- Depois da correção, o teste de concorrência passa em SQLite e em PostgreSQL, e a suíte completa dá 88 testes aprovados, com os dois backends ativos, em Python 3.12.
