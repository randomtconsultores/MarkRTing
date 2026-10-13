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

Os testes do serviço e da web correm contra SQLite e contra PostgreSQL. Sem a variável `MINI_MARKETO_TEST_PG`, os testes de PostgreSQL são ignorados.

Para os correr num ambiente sem Docker (por exemplo, um Codespace):

    sudo apt-get update && sudo apt-get install -y postgresql
    sudo service postgresql start
    sudo -u postgres psql -c "CREATE USER mm WITH PASSWORD 'mm' CREATEDB;"
    sudo -u postgres psql -c "CREATE DATABASE mini_marketo_test OWNER mm;"
    export MINI_MARKETO_TEST_PG="postgresql://mm:mm@localhost/mini_marketo_test"
    pip install -e ".[dev,postgres]"
    pytest -v

Cada teste usa um schema próprio, criado e apagado no fim.
