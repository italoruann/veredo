"""Teste de conectividade de um perfil, executado fora da thread da interface."""
from __future__ import annotations

import asyncio

from PySide6.QtCore import QThread, Signal

from ..nucleo import proxy_http, socks5
from ..nucleo.configuracao import PerfilProxy

HOST_TESTE = "1.1.1.1"
PORTA_TESTE = 443


class TesteProxy(QThread):
    teste_concluido = Signal(bool, str)

    def __init__(self, perfil: PerfilProxy, parent=None):
        super().__init__(parent)
        self.perfil = perfil

    def run(self) -> None:
        try:
            ok, mensagem = asyncio.run(self._testar())
        except Exception as exc:  # pragma: no cover - rede é imprevisível
            ok, mensagem = False, f"Falha inesperada: {exc}"
        self.teste_concluido.emit(ok, mensagem)

    async def _testar(self) -> tuple[bool, str]:
        perfil = self.perfil
        if not perfil.host or not perfil.port:
            return False, "Preencha host e porta antes de testar."
        try:
            if perfil.type == "socks5":
                leitor, escritor = await socks5.conectar_via_socks5(
                    perfil.host, perfil.port, HOST_TESTE, PORTA_TESTE,
                    perfil.usuario, perfil.senha, timeout=8.0)
            else:
                leitor, escritor = await proxy_http.conectar_via_http(
                    perfil.host, perfil.port, HOST_TESTE, PORTA_TESTE,
                    perfil.usuario, perfil.senha, timeout=8.0)
            escritor.close()
            return True, "Conexão e autenticação OK."
        except (socks5.ErroSocks5, proxy_http.ErroProxyHttp) as exc:
            return False, f"O proxy recusou a conexão: {exc}"
        except (OSError, asyncio.TimeoutError) as exc:
            return False, f"Não foi possível conectar ao proxy: {exc}"
