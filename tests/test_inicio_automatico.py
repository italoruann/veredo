"""Início automático no Windows (subprocess simulado; schtasks nunca é executado)."""

import pytest

from proxy_manager.nucleo import inicio_automatico


class _ProcessoConcluidoFalso:
    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = stderr


def test_esta_ativo_consulta_tarefa_agendada(monkeypatch):
    chamadas = []

    def run_falso(cmd, capture_output=False, timeout=None):
        chamadas.append(cmd)
        return _ProcessoConcluidoFalso(0)

    monkeypatch.setattr(inicio_automatico.subprocess, "run", run_falso)
    assert inicio_automatico._ativo_windows() is True
    assert chamadas[0][:3] == ["schtasks", "/Query", "/TN"]


def test_esta_ativo_falso_sem_tarefa(monkeypatch):
    monkeypatch.setattr(inicio_automatico.subprocess, "run", lambda *a, **k: _ProcessoConcluidoFalso(1))
    assert inicio_automatico._ativo_windows() is False


def test_esta_ativo_falso_sem_schtasks(monkeypatch):
    def levantar_oserror(*a, **k):
        raise OSError("schtasks not found")

    monkeypatch.setattr(inicio_automatico.subprocess, "run", levantar_oserror)
    assert inicio_automatico._ativo_windows() is False


@pytest.mark.parametrize("admin,nivel", [(True, "HIGHEST"), (False, "LIMITED")])
def test_ativar_cria_tarefa_com_nivel_conforme_elevacao(monkeypatch, admin, nivel):
    """Só um admin pode criar tarefa /RL HIGHEST; sem elevação o schtasks negaria o acesso."""
    chamadas = []

    def run_falso(cmd, capture_output=False, timeout=None):
        chamadas.append(cmd)
        return _ProcessoConcluidoFalso(0)

    monkeypatch.setattr(inicio_automatico.subprocess, "run", run_falso)
    monkeypatch.setattr(inicio_automatico, "_windows_admin", lambda: admin)
    ok, mensagem = inicio_automatico._definir_windows(True)

    assert ok is True
    cmd = chamadas[0]
    assert cmd[:3] == ["schtasks", "/Create", "/TN"]
    assert "/RL" in cmd and cmd[cmd.index("/RL") + 1] == nivel
    assert "/SC" in cmd and cmd[cmd.index("/SC") + 1] == "ONLOGON"


def test_desativar_apaga_tarefa(monkeypatch):
    chamadas = []

    def run_falso(cmd, capture_output=False, timeout=None):
        chamadas.append(cmd)
        return _ProcessoConcluidoFalso(0)

    monkeypatch.setattr(inicio_automatico.subprocess, "run", run_falso)
    ok, mensagem = inicio_automatico._definir_windows(False)

    assert ok is True
    assert chamadas[0][:3] == ["schtasks", "/Delete", "/TN"]


def test_desativar_sem_tarefa_nao_e_erro(monkeypatch):
    monkeypatch.setattr(inicio_automatico.subprocess, "run", lambda *a, **k: _ProcessoConcluidoFalso(1))
    ok, mensagem = inicio_automatico._definir_windows(False)
    assert ok is True


def test_ativar_informa_falha(monkeypatch):
    monkeypatch.setattr(
        inicio_automatico.subprocess, "run",
        lambda *a, **k: _ProcessoConcluidoFalso(1, stderr=b"ERRO: acesso negado."),
    )
    ok, mensagem = inicio_automatico._definir_windows(True)
    assert ok is False
    assert "acesso negado" in mensagem.lower()


def test_ativar_sem_schtasks(monkeypatch):
    def levantar_oserror(*a, **k):
        raise OSError("schtasks not found")

    monkeypatch.setattr(inicio_automatico.subprocess, "run", levantar_oserror)
    ok, mensagem = inicio_automatico._definir_windows(True)
    assert ok is False
