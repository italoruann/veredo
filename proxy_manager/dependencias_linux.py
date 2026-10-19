"""Instala, no Linux, as bibliotecas de sistema que o plugin xcb do Qt precisa.

As wheels do PySide6 não trazem algumas dependências do xcb (principalmente a libxcb-cursor).
Sem elas o Qt aborta com "Could not load the Qt platform plugin xcb". O que faltar é
instalado pelo gerenciador de pacotes da distribuição. Desative com VEREDO_SEM_DEPENDENCIAS=1.
"""
from __future__ import annotations

import ctypes.util
import os
import shutil
import subprocess
import sys

# Nome da lib (como o ctypes.util.find_library espera) -> pacote em cada gerenciador.
_BIBLIOTECAS_NECESSARIAS: dict[str, dict[str, str]] = {
    "xcb-cursor": {"pacman": "xcb-util-cursor", "apt": "libxcb-cursor0", "dnf": "xcb-util-cursor", "zypper": "libxcb-cursor0"},
    "xcb-icccm": {"pacman": "xcb-util-wm", "apt": "libxcb-icccm4", "dnf": "xcb-util-wm", "zypper": "libxcb-icccm4"},
    "xcb-keysyms": {"pacman": "xcb-util-keysyms", "apt": "libxcb-keysyms1", "dnf": "xcb-util-keysyms", "zypper": "libxcb-keysyms1"},
    "xcb-image": {"pacman": "xcb-util-image", "apt": "libxcb-image0", "dnf": "xcb-util-image", "zypper": "libxcb-image0"},
    "xcb-render-util": {"pacman": "xcb-util-renderutil", "apt": "libxcb-render-util0", "dnf": "xcb-util-renderutil", "zypper": "libxcb-render-util0"},
    "xkbcommon-x11": {"pacman": "libxkbcommon-x11", "apt": "libxkbcommon-x11-0", "dnf": "libxkbcommon-x11", "zypper": "libxkbcommon-x11-0"},
}

_COMANDOS_INSTALACAO: dict[str, list[str]] = {
    "pacman": ["pacman", "-S", "--needed", "--noconfirm"],
    "apt": ["apt-get", "install", "-y"],
    "dnf": ["dnf", "install", "-y"],
    "zypper": ["zypper", "--non-interactive", "install"],
}


def bibliotecas_ausentes() -> list[str]:
    return [biblioteca for biblioteca in _BIBLIOTECAS_NECESSARIAS if ctypes.util.find_library(biblioteca) is None]


def detectar_gerenciador_pacotes() -> str | None:
    for gerenciador, binario in (("pacman", "pacman"), ("apt", "apt-get"), ("dnf", "dnf"), ("zypper", "zypper")):
        if shutil.which(binario):
            return gerenciador
    return None


def comando_instalacao(gerenciador: str, bibliotecas: list[str], *, eh_root: bool, interativo: bool) -> list[str] | None:
    """Monta o comando de instalação, elevando com sudo (terminal) ou pkexec (sem terminal)."""
    pacotes = sorted({_BIBLIOTECAS_NECESSARIAS[biblioteca][gerenciador] for biblioteca in bibliotecas})
    cmd = _COMANDOS_INSTALACAO[gerenciador] + pacotes
    if eh_root:
        return cmd
    if interativo and shutil.which("sudo"):
        return ["sudo", *cmd]
    if shutil.which("pkexec"):
        return ["pkexec", *cmd]
    return None


def _usa_xcb() -> bool:
    platform = os.environ.get("QT_QPA_PLATFORM", "")
    # Vazio = o Qt escolhe sozinho e pode cair no xcb; "wayland;xcb" também pode chegar nele.
    return not platform or "xcb" in platform


def garantir_bibliotecas_qt() -> None:
    if not sys.platform.startswith("linux") or os.environ.get("VEREDO_SEM_DEPENDENCIAS") == "1":
        return
    if not _usa_xcb():
        return
    ausentes = bibliotecas_ausentes()
    if not ausentes:
        return

    gerenciador = detectar_gerenciador_pacotes()
    cmd = None
    if gerenciador:
        cmd = comando_instalacao(gerenciador, ausentes, eh_root=os.geteuid() == 0, interativo=sys.stdin.isatty())

    if cmd:
        print(f"[veredo] Faltam bibliotecas do Qt ({', '.join(ausentes)}). Instalando:", file=sys.stderr)
        print("  " + " ".join(cmd), file=sys.stderr)
        try:
            subprocess.run(cmd, check=False)
        except OSError as exc:
            print(f"[veredo] Falha ao rodar o instalador: {exc}", file=sys.stderr)
        ausentes = bibliotecas_ausentes()
        if not ausentes:
            return

    print(
        f"[veredo] Bibliotecas do Qt ainda ausentes: {', '.join(ausentes)}. "
        "Instale-as manualmente pelo gerenciador de pacotes da sua distro.",
        file=sys.stderr,
    )
    # Numa sessão Wayland dá para seguir sem o xcb, desde que ninguém tenha forçado a plataforma.
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
        print("[veredo] Usando o backend Wayland do Qt no lugar do xcb.", file=sys.stderr)
        os.environ["QT_QPA_PLATFORM"] = "wayland"
