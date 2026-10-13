# Mini-Marketo

Plataforma simplificada de automação de marketing em Python (FastAPI + Jinja2 + SQLite ou PostgreSQL), desenvolvida por etapas como exercício de arquitetura. As decisões estão em [docs/decisoes.md](docs/decisoes.md).

## Executar com SQLite (por omissão)

    pip install -e ".[dev]"
    uvicorn mini_marketo.main:app_factory --factory --reload

A base de dados fica em `data/mini_marketo.db` (ignorada pelo Git).

## Executar com PostgreSQL

    pip install -e ".[dev,postgres]"
    export MINI_MARKETO_DATABASE_URL="postgresql://utilizador:palavra-passe@localhost/mini_marketo"
    uvicorn mini_marketo.main:app_factory --factory --reload

A base de dados tem de existir; o esquema é criado no arranque.

## Testes

    pytest -v

Os testes do serviço e da web correm contra SQLite e contra PostgreSQL. Sem a variável `MINI_MARKETO_TEST_PG`, os testes de PostgreSQL são ignorados. Cada teste usa um schema próprio, criado e apagado no fim.

O projeto exige Python 3.12 ou superior.

### PostgreSQL de testes em Alpine (validado)

Num container Alpine, sem Docker nem serviços de sistema, o cluster fica no diretório do utilizador. Estes comandos foram executados no projeto, pela ordem indicada:

    sudo apk update
    sudo apk add postgresql postgresql-client
    mkdir -p /home/vscode/.local/share/postgresql/markrting
    initdb -D /home/vscode/.local/share/postgresql/markrting
    pg_ctl \
      -D /home/vscode/.local/share/postgresql/markrting \
      -l /home/vscode/.local/share/postgresql/markrting/server.log \
      -o "-h 127.0.0.1 -p 5432 -k /tmp" \
      start
    psql -h 127.0.0.1 -p 5432 -d postgres \
      -c "CREATE USER mm WITH PASSWORD 'mm' CREATEDB;"
    psql -h 127.0.0.1 -p 5432 -d postgres \
      -c "CREATE DATABASE mini_marketo_test OWNER mm;"
    pg_isready -h 127.0.0.1 -p 5432

Para correr os testes com PostgreSQL:

    pip install -e ".[dev,postgres]"
    MINI_MARKETO_TEST_PG="postgresql://mm:mm@localhost:5432/mini_marketo_test" \
      .venv/bin/python -m pytest -q

A opção `-k /tmp` define a pasta do socket Unix; a ligação de teste usa TCP. As credenciais `mm`/`mm` são descartáveis e servem só para testes locais. Para desligar o servidor:

    pg_ctl -D /home/vscode/.local/share/postgresql/markrting stop

### PostgreSQL de testes em Debian/Ubuntu (não validado neste projeto)

    sudo apt-get update && sudo apt-get install -y postgresql
    sudo service postgresql start
    sudo -u postgres psql -c "CREATE USER mm WITH PASSWORD 'mm' CREATEDB;"
    sudo -u postgres psql -c "CREATE DATABASE mini_marketo_test OWNER mm;"
    export MINI_MARKETO_TEST_PG="postgresql://mm:mm@localhost/mini_marketo_test"
    pip install -e ".[dev,postgres]"
    pytest -v
