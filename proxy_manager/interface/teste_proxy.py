"""Teste de conectividade de um perfil ou cadeia, executado fora da thread da interface."""
from __future__ import annotations

import asyncio

from PySide6.QtCore import QThread, Signal

from ..nucleo import cliente_proxy, proxy_http, socks5
from ..nucleo.cliente_proxy import ErroRota, ErroSocks4, Rota
from ..nucleo.configuracao import PerfilProxy

HOST_TESTE = "1.1.1.1"
PORTA_TESTE = 443


class TesteProxy(QThread):
    teste_concluido = Signal(bool, str)

    def __init__(self, alvo: PerfilProxy | Rota, parent=None):
        super().__init__(parent)
        self.rota = cliente_proxy.rota_do_perfil(alvo) if isinstance(alvo, PerfilProxy) else alvo

    def run(self) -> None:
        try:
            ok, mensagem = asyncio.run(self._testar())
        except Exception as exc:  # pragma: no cover - rede é imprevisível
            ok, mensagem = False, f"Falha inesperada: {exc}"
        self.teste_concluido.emit(ok, mensagem)

    async def _testar(self) -> tuple[bool, str]:
        rota = self.rota
        if not rota.perfis:
            return False, "A cadeia não tem nenhum proxy habilitado."
        if any(not perfil.host or not perfil.port for perfil in rota.perfis):
            return False, "Preencha host e porta antes de testar."
        rota.timeout = min(rota.timeout, 8.0)
        try:
            _leitor, escritor, usado = await cliente_proxy.conectar_rota(rota, HOST_TESTE, PORTA_TESTE)
            escritor.close()
        except (socks5.ErroSocks5, proxy_http.ErroProxyHttp, ErroSocks4, ErroRota) as exc:
            return False, f"O proxy recusou a conexão: {exc}"
        except (OSError, asyncio.TimeoutError) as exc:
            return False, f"Não foi possível conectar ao proxy: {exc or 'tempo esgotado'}"
        if rota.eh_cadeia:
            return True, f"Conexão OK (atendida por {usado.nome})."
        return True, "Conexão e autenticação OK."
