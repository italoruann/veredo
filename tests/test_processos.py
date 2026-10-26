"""Cache de processos: PIDs reaproveitados, limite de tamanho e falhas."""
from types import SimpleNamespace

import psutil
import pytest

from proxy_manager.nucleo import processos


@pytest.fixture(autouse=True)
def _limpar_cache():
    processos.limpar_cache()
    yield
    processos.limpar_cache()


def _processo_falso(nome: str, exe: str, criado_em: float):
    return SimpleNamespace(name=lambda: nome, exe=lambda: exe, create_time=lambda: criado_em)


def test_cache_trocado_quando_pid_e_reaproveitado(monkeypatch):
    chamadas = iter([
        _processo_falso("old.exe", "/bin/old.exe", criado_em=100.0),  # resolução original
        _processo_falso("old.exe", "/bin/old.exe", criado_em=100.0),  # confirma create_time (cache hit)
        _processo_falso("new.exe", "/bin/new.exe", criado_em=200.0),  # create_time mudou: checagem de identidade
        _processo_falso("new.exe", "/bin/new.exe", criado_em=200.0),  # resolução do processo novo
    ])
    monkeypatch.setattr(processos.psutil, "Process", lambda pid: next(chamadas))

    primeira = processos._resolver_processo(pid=4242)
    assert primeira.nome == "old.exe"

    segunda = processos._resolver_processo(pid=4242)
    assert segunda.nome == "old.exe"  # ainda em cache, mesmo processo (create_time bateu)

    terceira = processos._resolver_processo(pid=4242)
    assert terceira.nome == "new.exe"  # PID reciclado: create_time não bate mais


def test_cache_tem_limite(monkeypatch):
    monkeypatch.setattr(processos, "_MAX_CACHE", 3)
    monkeypatch.setattr(
        processos.psutil, "Process",
        lambda pid: _processo_falso(f"proc{pid}.exe", f"/bin/proc{pid}.exe", criado_em=float(pid)),
    )

    for pid in range(1, 6):
        processos._resolver_processo(pid)

    assert len(processos._cache_processos) == 3
    assert set(processos._cache_processos.keys()) == {3, 4, 5}


def test_falha_nao_vai_para_o_cache(monkeypatch):
    def _raise(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(processos.psutil, "Process", _raise)

    info = processos._resolver_processo(pid=999)
    assert info.nome == "pid 999"
    assert 999 not in processos._cache_processos


def _conexao(porta_local, rip, porta_remota, pid):
    return SimpleNamespace(laddr=SimpleNamespace(ip="192.168.0.5", port=porta_local),
                           raddr=SimpleNamespace(ip=rip, port=porta_remota), pid=pid)


def test_transparente_acha_processo_pelo_destino_original(monkeypatch):
    # No modo transparente o socket do app continua apontando pro destino original (o NAT é
    # invisível pra ele), não pra nossa porta transparente.
    conexoes = [
        _conexao(58095, "192.168.0.5", 40000, pid=processos.os.getpid()),  # nosso lado aceito
        _conexao(40000, "142.250.1.1", 443, pid=777),  # socket do Chrome
    ]
    monkeypatch.setattr(processos.psutil, "net_connections", lambda kind: conexoes)
    monkeypatch.setattr(processos, "_resolver_processo",
                        lambda pid: processos.InfoProcesso(pid=pid, nome="chrome"))

    info = processos.processo_da_conexao("192.168.0.5", 40000, 58095,
                                         destino_original=("142.250.1.1", 443))
    assert info.pid == 777

    assert processos.processo_da_conexao("192.168.0.5", 40000, 58095) is processos.DESCONHECIDO


def test_rajada_de_conexoes_compartilha_a_leitura_da_tabela(monkeypatch):
    # Cada leitura da tabela é lenta; numa rajada, quem pediu antes de uma leitura começar
    # aproveita a próxima em vez de ler de novo.
    import threading
    import time

    leituras = []
    conexoes = [_conexao(40000 + i, "1.1.1.1", 443, pid=777) for i in range(20)]

    def ler(kind):
        leituras.append(kind)
        time.sleep(0.2)
        return conexoes

    processos.limpar_cache()
    monkeypatch.setattr(processos.psutil, "net_connections", ler)
    monkeypatch.setattr(processos, "_resolver_processo", lambda pid: processos.InfoProcesso(pid=pid, nome="app"))
    resultados = {}

    def consultar(i):
        resultados[i] = processos.processo_da_conexao("192.168.0.5", 40000 + i, 58080,
                                                      destino_original=("1.1.1.1", 443))

    threads = [threading.Thread(target=consultar, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert all(info.pid == 777 for info in resultados.values()) and len(resultados) == 20
    assert len(leituras) <= 3

    # uma conexão nova, depois da última leitura, nunca usa a tabela antiga
    antes = len(leituras)
    processos.processo_da_conexao("192.168.0.5", 40000, 58080, destino_original=("1.1.1.1", 443))
    assert len(leituras) == antes + 1
