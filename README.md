# Mini-Marketo

Plataforma simplificada de automação de marketing em Python (FastAPI + Jinja2 + SQLite ou PostgreSQL), desenvolvida por etapas como exercício de arquitetura. Exige Python 3.12 ou superior.

## Estado do projeto — 20 de outubro de 2026

A subscrição pública foi integrada pelo PR #15 e o acesso HTTP administrativo pelo PR #27. A aplicação já monta login/logout, sessões, autorização admin/viewer, guard e CSRF. A etapa 4 continua em curso: importação/exportação, remoção/anonimização, retenção geral e remoção de empresas não ficam concluídas com a autenticação.

Código integrado não equivale a autorização para produção. Consultar [roadmap](docs/roadmap.md), [decisões da etapa 4](docs/decisoes-etapa4.md) e [integração HTTP](docs/admin-http.md). As decisões iniciais estão em [docs/decisoes.md](docs/decisoes.md).

## Preparar o ambiente

A partir da raiz do repositório, criar uma `.venv` apenas se ainda não existir e instalar o projeto nesse ambiente:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Não substituir uma `.venv` existente sem necessidade. Para PostgreSQL, instalar também o extra correspondente:

```sh
.venv/bin/python -m pip install -e ".[dev,postgres]"
```

## Configuração administrativa obrigatória

`create_app` exige todas estas variáveis, sem segredos ou limites por omissão:

| Variável | Significado |
| --- | --- |
| `MINI_MARKETO_ADMIN_ORIGIN` | Origem administrativa confiável, com esquema e porta, sem caminho/query/fragmento |
| `MINI_MARKETO_ADMIN_CSRF_KEY` | Segredo aleatório com pelo menos 32 bytes UTF-8 |
| `MINI_MARKETO_ADMIN_SESSION_ABSOLUTE_SECONDS` | Duração absoluta positiva da sessão |
| `MINI_MARKETO_ADMIN_SESSION_IDLE_SECONDS` | Inatividade positiva, não superior à duração absoluta |
| `MINI_MARKETO_ADMIN_CSRF_SECONDS` | Validade positiva do CSRF, não superior à duração absoluta |
| `MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY` | Segredo aleatório independente da chave CSRF, pelo menos 32 bytes UTF-8 |
| `MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS` | Janela positiva de limitação de tentativas |
| `MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT` | Limite positivo por conta |
| `MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT` | Limite positivo por IP |
| `MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT` | Limite positivo da instalação |

As durações são em segundos. Os limites de tentativas não podem exceder 2147483647. Configuração ausente/inválida impede o arranque de toda a aplicação, incluindo subscrição, cancelamento e outras rotas públicas; não existe fallback para administração sem proteção. Não publicar chaves ou DSNs no Git, em relatórios ou logs.

### Exemplo apenas para desenvolvimento local

Os valores seguintes são exemplos locais, não uma política de produção aprovada. Gerar as duas chaves na preparação do ambiente e conservar os mesmos valores entre processos e reinícios; não repetir a geração a cada arranque. A aplicação não carrega automaticamente um ficheiro `.env`.

```sh
export MINI_MARKETO_ADMIN_ORIGIN="http://127.0.0.1:8000"
export MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL=1
export MINI_MARKETO_ADMIN_CSRF_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
export MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
export MINI_MARKETO_ADMIN_SESSION_ABSOLUTE_SECONDS=3600
export MINI_MARKETO_ADMIN_SESSION_IDLE_SECONDS=1800
export MINI_MARKETO_ADMIN_CSRF_SECONDS=900
export MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS=60
export MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT=5
export MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT=30
export MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT=100
```

HTTP só é admitido com a exceção explícita e host literal `localhost`, `127.0.0.1` ou `::1`. Usar no browser exatamente a origem configurada. Para HTTPS, configurar a origem real e deixar `MINI_MARKETO_ADMIN_ALLOW_HTTP_LOCAL` ausente ou a `0`; não ativar a exceção em produção. Rever TLS, proxies confiáveis e limites de corpo/tempo antes da exposição.

## Executar com SQLite

Depois de configurar o acesso, escolher o destino SQLite. Não executar estes comandos de seleção de destino sobre um ambiente de produção sem o rever:

```sh
unset MINI_MARKETO_DATABASE_URL
export MINI_MARKETO_DB="data/mini_marketo.db"
.venv/bin/python -m uvicorn mini_marketo.main:app_factory --factory --host 127.0.0.1 --port 8000 --reload
```

A base SQLite por omissão é `data/mini_marketo.db`, ignorada pelo Git. O arranque inicializa/migra o esquema; numa base existente, rever migrações e fazer cópia de segurança antes. `--reload` destina-se a desenvolvimento.

## Executar com PostgreSQL

Depois de instalar o extra e configurar o acesso, selecionar a base correta:

```sh
unset MINI_MARKETO_DB
export MINI_MARKETO_DATABASE_URL="postgresql://utilizador:palavra-passe@localhost/mini_marketo"
.venv/bin/python -m uvicorn mini_marketo.main:app_factory --factory --host 127.0.0.1 --port 8000 --reload
```

O DSN acima é um modelo a substituir, não credenciais fornecidas. A base PostgreSQL tem de existir; a aplicação inicializa/migra o esquema. Web e processos de envio devem apontar para os destinos e segredos apropriados. Configuração pública de subscrição/envio é separada da configuração administrativa; consultar [decisões de subscrição](docs/decisoes-subscricao.md).

## Criar a primeira conta

Não existem contas ou palavras-passe predefinidas, nem registo público administrativo. Inicializar primeiro o esquema pelo arranque da aplicação, num ambiente local/controlado, e parar o servidor se necessário para o bootstrap. Escolher exatamente uma variável de destino para o comando: `MINI_MARKETO_DB` ou `MINI_MARKETO_DATABASE_URL`.

Fornecer uma lista local não vazia e confiável de digests SHA-256 de palavras-passe bloqueadas. O caminho seguinte é um marcador a substituir:

```sh
.venv/bin/python -m mini_marketo.admin_accounts create operador --role admin --blocklist /caminho/blocked.sha256
```

O comando exige terminal interativo, confirmação `ADMIN` e introdução da palavra-passe duas vezes por `getpass`. Não passar a palavra-passe em argumentos ou variáveis de ambiente. A política exige pelo menos 15 caracteres e no máximo 4096 bytes UTF-8. Consultar [administração de contas](docs/admin-accounts.md) para preparação da lista, recuperação e revogação.

Após arrancar novamente, abrir `/login`. Sem sessão, as páginas administrativas são recusadas; não confundir essa recusa com uma instalação que oferece consultas públicas.

## Manutenção

Com exatamente um destino de base explicitamente configurado e revisto:

```sh
.venv/bin/python -m mini_marketo.admin_maintenance --batch-size 1000
```

Remove no máximo um lote por tabela de sessões/tentativas expiradas. Não é scheduler nem política geral de retenção de contactos, consentimentos ou pedidos de subscrição. Repetições são responsabilidade do operador. Consultar [integração HTTP](docs/admin-http.md).

## Testes e limites da validação

```sh
.venv/bin/python -m pytest -v
```

Os testes de persistência e web usam SQLite e PostgreSQL. Sem `MINI_MARKETO_TEST_PG`, os casos PostgreSQL são ignorados; os testes usam schemas temporários. Nunca usar uma base de produção como destino de testes.

Validação focada reportada pelo utilizador no Copilot para `e2012d850260c7f5ea74b41adb1d5ef1ea7704ad`, antes do merge do PR #27: 999 aprovados com PostgreSQL em 69,36 s, incluindo os dois testes de regressão do guard. Estes dois também passaram isoladamente em 0,06 s. `git diff --check` desde `f11204e9bd67a249e16eee7b7c7ac5c7ffb35d6c` passou. Não foi a suite completa nem uma execução sobre o commit documental ou sobre o merge. O assistente não executou esses testes nem os novos exemplos de arranque.

### PostgreSQL de testes em Alpine — procedimento histórico validado

Num container Alpine, sem Docker nem serviços de sistema, foi utilizado um cluster no diretório do utilizador:

```sh
sudo apk update
sudo apk add postgresql postgresql-client
mkdir -p /home/vscode/.local/share/postgresql/markrting
initdb -D /home/vscode/.local/share/postgresql/markrting
pg_ctl -D /home/vscode/.local/share/postgresql/markrting -l /home/vscode/.local/share/postgresql/markrting/server.log -o "-h 127.0.0.1 -p 5432 -k /tmp" start
psql -h 127.0.0.1 -p 5432 -d postgres -c "CREATE USER mm WITH PASSWORD 'mm' CREATEDB;"
psql -h 127.0.0.1 -p 5432 -d postgres -c "CREATE DATABASE mini_marketo_test OWNER mm;"
pg_isready -h 127.0.0.1 -p 5432
MINI_MARKETO_TEST_PG="postgresql://mm:mm@localhost:5432/mini_marketo_test" .venv/bin/python -m pytest -q
```

Não repetir `initdb` sobre um cluster existente. `-k /tmp` define a pasta do socket; os testes acima ligam por TCP. `mm`/`mm` são credenciais descartáveis apenas de testes locais. Para desligar:

```sh
pg_ctl -D /home/vscode/.local/share/postgresql/markrting stop
```

### PostgreSQL de testes em Debian/Ubuntu — não validado neste projeto

```sh
sudo apt-get update && sudo apt-get install -y postgresql
sudo service postgresql start
sudo -u postgres psql -c "CREATE USER mm WITH PASSWORD 'mm' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE mini_marketo_test OWNER mm;"
export MINI_MARKETO_TEST_PG="postgresql://mm:mm@localhost/mini_marketo_test"
.venv/bin/python -m pip install -e ".[dev,postgres]"
.venv/bin/python -m pytest -v
```
