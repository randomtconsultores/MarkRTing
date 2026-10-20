import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from admin_app_support import TestClient, create_app

WORKERS = 12
ROUNDS = 8


@pytest.fixture(params=["sqlite", "postgres"])
def app(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        return create_app(tmp_path / "concurrency.db")
    url = request.getfixturevalue("pg_url")
    schema = request.getfixturevalue("pg_schema")
    # O libpq lê PGOPTIONS: isola o teste no schema próprio, sem tocar no código da app.
    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    return create_app(database_url=url)


def test_concurrent_requests_do_not_interfere(app):
    """Em cada ronda, várias threads criam o mesmo email ao mesmo tempo (pedidos HTTP).

    Tem de haver exatamente um sucesso por ronda; os outros pedidos recebem 422
    «Já existe». Nenhum pode falhar com erro 500 nem afetar pedidos de outras threads.
    """
    barriers = [threading.Barrier(WORKERS) for _ in range(ROUNDS)]
    successes = [[] for _ in range(ROUNDS)]
    problems = []

    def worker(n):
        client = TestClient(app)
        for rnd in range(ROUNDS):
            barriers[rnd].wait(timeout=30)
            try:
                r = client.post(
                    "/contacts",
                    data={"first_name": f"W{n}", "last_name": "R", "email": f"race{rnd}@a.pt"},
                )
            except Exception as exc:
                problems.append(f"ronda {rnd}, worker {n}: {exc!r}")
                continue
            if r.status_code == 200:
                successes[rnd].append(n)
            elif not (r.status_code == 422 and "Já existe" in r.text):
                problems.append(f"ronda {rnd}, worker {n}: HTTP {r.status_code}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(worker, n) for n in range(WORKERS)]
        for future in futures:
            future.result(timeout=120)

    assert not problems, problems[:5]
    assert [len(s) for s in successes] == [1] * ROUNDS
    page = TestClient(app).get("/contacts").text
    assert f"Contactos ({ROUNDS})" in page
    assert page.count("<li>") == ROUNDS
