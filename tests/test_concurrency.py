import threading
from concurrent.futures import ThreadPoolExecutor

from mini_marketo.domain.contact import DuplicateEmailError

WORKERS = 16
PER_WORKER = 10


def test_concurrent_writes_on_shared_connection(service):
    """Várias threads a escrever sobre o mesmo serviço (e, logo, a mesma ligação).

    Cada thread cria contactos únicos e, entre eles, provoca uma violação real de
    unicidade (contornando a verificação do serviço, como numa corrida entre dois
    pedidos). Com uma ligação partilhada, o rollback de uma thread pode desfazer a
    transação de outra.
    """
    service.create_contact("Base", "Contacto", "base@a.pt")
    barrier = threading.Barrier(WORKERS)
    problems = []

    def worker(n):
        barrier.wait(timeout=30)
        for i in range(PER_WORKER):
            key = n * 1000 + i
            try:
                service.create_contact("Ana", f"N{key}", f"u{key}@a.pt")
            except Exception as exc:
                problems.append(f"criar {key}: {exc!r}")
            try:
                service.repo.add_contact("base@a.pt", "Dup", f"D{key}")
            except DuplicateEmailError:
                pass
            except Exception as exc:
                problems.append(f"duplicado {key}: {exc!r}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(worker, n) for n in range(WORKERS)]
        for future in futures:
            future.result(timeout=60)

    contacts = service.list_contacts()
    assert not problems, problems[:5]
    assert len(contacts) == 1 + WORKERS * PER_WORKER
    assert all(len(c.emails) == 1 for c in contacts)
