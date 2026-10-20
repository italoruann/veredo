"""Modo transparente no Linux com subprocess e socket simulados."""
import socket
import struct
import subprocess

import pytest

from proxy_manager.nucleo.transparente import linux_nftables as mod
from proxy_manager.nucleo.transparente.linux_nftables import ModoTransparenteLinux, destino_original


class _ProcessoConcluidoFalso:
    def __init__(self, returncode: int):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


@pytest.fixture()
def root_falso(monkeypatch):
    # os.geteuid só existe em POSIX — raising=False permite rodar esses testes também no
    # Windows (onde o atributo nem existe no módulo os de verdade).
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(mod, "which", lambda nome: f"/usr/sbin/{nome}")


@pytest.fixture()
def comandos_registrados(monkeypatch):
    chamadas: list[list[str]] = []

    def run_falso(cmd, check=False, capture_output=False, timeout=None):
        chamadas.append(cmd)
        return _ProcessoConcluidoFalso(0)

    monkeypatch.setattr(mod.subprocess, "run", run_falso)
    return chamadas


def test_iniciar_recusa_sem_root(monkeypatch):
    monkeypatch.setattr(mod.os, "geteuid", lambda: 1000, raising=False)
    backend = ModoTransparenteLinux(porta_transparente=58095)

    ok, mensagem = backend.iniciar()

    assert ok is False
    assert "root" in mensagem.lower()


def test_iniciar_aplica_regras_nftables(root_falso, comandos_registrados):
    backend = ModoTransparenteLinux(porta_transparente=58095)

    ok, mensagem = backend.iniciar()

    assert ok is True
    assert "58095" in mensagem
    # a regra de redirect pra porta certa precisa ter sido aplicada
    chamadas_redirecionar = [c for c in comandos_registrados if "redirect" in c]
    assert chamadas_redirecionar, "deveria ter aplicado uma regra de redirect"
    assert any(f":{58095}" in c for c in chamadas_redirecionar[0])
    # a exclusão do próprio tráfego (evita loop) precisa existir
    chamadas_dono = [c for c in comandos_registrados if "skuid" in c]
    assert chamadas_dono, "deveria excluir o próprio uid do redirect pra evitar loop"
    # QUIC não é interceptável: precisa ser rejeitado pra não vazar direto
    chamadas_rejeitar = [c for c in comandos_registrados if "reject" in c]
    assert any("udp" in c and "443" in c for c in chamadas_rejeitar), "QUIC deveria ser rejeitado"
    # TCP de qualquer família (IPv4 e IPv6) é redirecionado; nenhum TCP é rejeitado
    assert any("tcp" in c and "nfproto" not in c for c in chamadas_redirecionar)
    assert not any("tcp" in c for c in chamadas_rejeitar)
    # sem DNS pelo proxy, a porta 53 não é desviada
    assert not any("53" in c for c in comandos_registrados)
    # tudo isolado numa tabela própria (nunca mexe em OUTPUT/chains do sistema)
    assert all("OUTPUT" not in c for c in comandos_registrados)


def test_parar_remove_tabela_e_pode_repetir(root_falso, comandos_registrados):
    backend = ModoTransparenteLinux(porta_transparente=58095)
    backend.iniciar()
    comandos_registrados.clear()

    ok, mensagem = backend.parar()
    assert ok is True
    assert comandos_registrados == [["nft", "delete", "table", "inet", mod.NOME_TABELA]]

    # chamar stop() de novo sem um start() no meio não deve tentar rodar comandos de novo
    comandos_registrados.clear()
    ok2, mensagem2 = backend.parar()
    assert ok2 is True
    assert comandos_registrados == []


def test_iniciar_falha_sem_excecao_se_nft_der_erro(root_falso, monkeypatch):
    def run_falso(cmd, check=False, capture_output=False, timeout=None):
        if check and "redirect" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"nft: Could not process rule: No such file or directory")
        return _ProcessoConcluidoFalso(0)

    monkeypatch.setattr(mod.subprocess, "run", run_falso)
    backend = ModoTransparenteLinux(porta_transparente=58095)

    ok, mensagem = backend.iniciar()

    assert ok is False
    assert "nftables" in mensagem.lower() or "No such file" in mensagem


def test_destino_original_le_so_original_dst():
    class _SocketFalso:
        def getsockopt(self, nivel, optname, buflen):
            assert optname == 80
            # struct sockaddr_in: family(2) + port(2, network order) + ip(4) + padding(8)
            return struct.pack("!HH4s8x", socket.AF_INET, 443, socket.inet_aton("203.0.113.10"))

    class _EscritorFalso:
        def get_extra_info(self, nome):
            return _SocketFalso() if nome == "socket" else None

    host, port = destino_original(_EscritorFalso())
    assert host == "203.0.113.10"
    assert port == 443


def test_destino_original_none_sem_socket():
    class _EscritorFalso:
        def get_extra_info(self, nome):
            return None

    assert destino_original(_EscritorFalso()) is None


def test_iniciar_explica_como_instalar_nft(root_falso, monkeypatch):
    monkeypatch.setattr(mod, "which", lambda nome: None)
    ok, mensagem = mod.ModoTransparenteLinux(58095).iniciar()
    assert ok is False
    assert "apt install nftables" in mensagem


def test_dns_pelo_proxy_desvia_porta_53_antes_da_excecao_de_loopback():
    comandos = ModoTransparenteLinux(58095, porta_dns=58053).comandos("1000")
    nat = [c for c in comandos if "output" in c and c[:3] == ["nft", "add", "rule"]]
    indice_dns = next(i for i, c in enumerate(nat) if "53" in c and ":58053" in c)
    indice_loopback = next(i for i, c in enumerate(nat) if "127.0.0.0/8" in c)
    indice_dono = next(i for i, c in enumerate(nat) if "skuid" in c)
    # o próprio Veredo consulta o DNS real sem desvio; o resto (até o 127.0.0.53) vem para cá
    assert indice_dono < indice_dns < indice_loopback


def test_prevencao_de_vazamento_rejeita_todo_udp():
    comandos = ModoTransparenteLinux(58095, porta_dns=58053, bloquear_todo_udp=True).comandos("1000")
    rejeitar = [c for c in comandos if "reject" in c]
    assert rejeitar == [["nft", "add", "rule", "inet", mod.NOME_TABELA, "block_unsupported",
                         "meta", "l4proto", "udp", "reject"]]
