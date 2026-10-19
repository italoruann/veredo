"""Testes da checagem de libs do Qt no Linux: mocka find_library/subprocess (nunca instala nada)."""
from proxy_manager import dependencias_linux as deps


def test_comando_instalacao_usa_pacotes_de_cada_gerenciador(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda nome: f"/usr/bin/{nome}")
    cmd = deps.comando_instalacao("pacman", ["xcb-cursor", "xcb-icccm"], eh_root=False, interativo=True)
    assert cmd == ["sudo", "pacman", "-S", "--needed", "--noconfirm", "xcb-util-cursor", "xcb-util-wm"]

    cmd = deps.comando_instalacao("apt", ["xcb-cursor"], eh_root=True, interativo=False)
    assert cmd == ["apt-get", "install", "-y", "libxcb-cursor0"]


def test_comando_instalacao_usa_pkexec_sem_terminal(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda nome: f"/usr/bin/{nome}")
    cmd = deps.comando_instalacao("dnf", ["xcb-cursor"], eh_root=False, interativo=False)
    assert cmd[0] == "pkexec"


def test_instala_bibliotecas_ausentes(monkeypatch):
    instalados = set()
    chamadas = []
    monkeypatch.setattr(deps.sys, "platform", "linux")
    monkeypatch.delenv("VEREDO_SEM_DEPENDENCIAS", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(deps.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(deps.shutil, "which", lambda nome: "/usr/bin/pacman" if nome == "pacman" else None)
    monkeypatch.setattr(
        deps.ctypes.util, "find_library", lambda biblioteca: f"lib{biblioteca}.so" if biblioteca != "xcb-cursor" or instalados else None
    )

    def run_falso(cmd, check=False):
        chamadas.append(cmd)
        instalados.add("xcb-cursor")

    monkeypatch.setattr(deps.subprocess, "run", run_falso)
    deps.garantir_bibliotecas_qt()
    assert chamadas == [["pacman", "-S", "--needed", "--noconfirm", "xcb-util-cursor"]]


def test_ignora_quando_plataforma_nao_e_xcb(monkeypatch):
    monkeypatch.setattr(deps.sys, "platform", "linux")
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
    monkeypatch.setattr(deps, "bibliotecas_ausentes", lambda: (_ for _ in ()).throw(AssertionError("não deveria checar")))
    deps.garantir_bibliotecas_qt()
