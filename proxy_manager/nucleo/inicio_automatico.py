"""Início automático junto com o login do usuário."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from .configuracao import NOME_EXIBICAO

ID_TAREFA = "Veredo"


def _comando_inicio() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimizado'
    return f'"{sys.executable}" -m proxy_manager --minimizado'


def esta_ativo() -> bool:
    sistema = platform.system()
    if sistema == "Windows":
        return _ativo_windows()
    if sistema == "Linux":
        return _arquivo_autostart().exists()
    return False


def definir_ativo(ativo: bool) -> tuple[bool, str]:
    sistema = platform.system()
    if sistema == "Windows":
        return _definir_windows(ativo)
    if sistema == "Linux":
        return _definir_linux(ativo)
    return False, f"Início automático não suportado em {sistema}."


def _ativo_windows() -> bool:
    try:
        retorno = subprocess.run(["schtasks", "/Query", "/TN", ID_TAREFA], capture_output=True, timeout=5)
        return retorno.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _definir_windows(ativo: bool) -> tuple[bool, str]:
    # Agendador de Tarefas em vez da chave Run: o executável de administrador pede UAC e não
    # abriria pela chave Run. Só um administrador pode criar a tarefa com /RL HIGHEST.
    nivel = "HIGHEST" if _windows_admin() else "LIMITED"
    try:
        if ativo:
            retorno = subprocess.run(
                ["schtasks", "/Create", "/TN", ID_TAREFA, "/TR", _comando_inicio(),
                 "/SC", "ONLOGON", "/RL", nivel, "/F"],
                capture_output=True, timeout=5,
            )
        else:
            retorno = subprocess.run(["schtasks", "/Delete", "/TN", ID_TAREFA, "/F"],
                                     capture_output=True, timeout=5)
            if retorno.returncode != 0:
                return True, "Início automático já estava desabilitado."

        if retorno.returncode != 0:
            stderr = retorno.stderr.decode(errors="replace").strip()
            return False, f"Falha ao atualizar a tarefa agendada: {stderr}"
        return True, "Início automático atualizado (tarefa agendada no login)."
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Falha ao atualizar a tarefa agendada: {exc}"


def _windows_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _arquivo_autostart() -> Path:
    return Path.home() / ".config" / "autostart" / "veredo.desktop"


def _definir_linux(ativo: bool) -> tuple[bool, str]:
    caminho = _arquivo_autostart()
    if ativo:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        conteudo = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={NOME_EXIBICAO}\n"
            f"Exec={_comando_inicio()}\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        caminho.write_text(conteudo, encoding="utf-8")
        return True, f"Início automático habilitado ({caminho})."
    if caminho.exists():
        caminho.unlink()
    return True, "Início automático desabilitado."
