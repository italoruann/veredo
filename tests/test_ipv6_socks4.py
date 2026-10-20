"""IPv6 de ponta a ponta e programas que falam SOCKS4/4A com a porta SOCKS local."""
import asyncio
import socket

import pytest

from proxy_manager.nucleo import cliente_proxy
from proxy_manager.nucleo.configuracao import Configuracao, PerfilProxy, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from tests.test_cadeias import _conversar, _eco, _proxy_socks5, _repassar

PORTA_SOCKS = 58580
PORTA_HTTP = 58581
PORTA_PAC = 58590


def _tem_ipv6() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.bind(("::1", 0))
        return True
    except OSError:
        return False


precisa_ipv6 = pytest.mark.skipif(not _tem_ipv6(), reason="máquina sem IPv6 no loopback")


def _motor(texto_regras: str, proxies=None) -> tuple[MotorProxy, Registro]:
    prefs = Preferencias(porta_socks=PORTA_SOCKS, porta_http=PORTA_HTTP, porta_pac=PORTA_PAC, acao_padrao="direct")
    registro = Registro(dias_retencao=1)
    return MotorProxy(Configuracao(proxies=proxies or [], texto_regras=texto_regras, preferencias=prefs),
                      registro), registro


async def _eco_ipv6():
    servidor = await asyncio.start_server(_repassar, "::1", 0)
    return servidor, servidor.sockets[0].getsockname()[1]


def test_programa_socks4a_usa_a_porta_socks_local():
    async def cenario():
        eco, porta_eco = await _eco()
        motor, registro = _motor("localhost +direct\n")
        motor.iniciar()
        try:
            perfil = PerfilProxy(type="socks4", host="127.0.0.1", port=PORTA_SOCKS)
            leitor, escritor = await cliente_proxy.abrir_tunel([perfil], "localhost", porta_eco)
            assert await _conversar(leitor, escritor) == b"ping"
            await asyncio.sleep(0.2)
            entrada = registro.recentes()[-1]
            assert (entrada.protocolo, entrada.host_destino, entrada.acao) == ("socks4", "localhost", "direct")
        finally:
            motor.parar()
            eco.close()

    asyncio.run(cenario())


def test_socks4_bloqueado_recebe_recusa():
    async def cenario():
        motor, _ = _motor("* +block\n")
        motor.iniciar()
        try:
            perfil = PerfilProxy(type="socks4", host="127.0.0.1", port=PORTA_SOCKS)
            with pytest.raises(cliente_proxy.ErroSocks4):
                await cliente_proxy.abrir_tunel([perfil], "10.0.0.1", 80)
        finally:
            motor.parar()

    asyncio.run(cenario())


@precisa_ipv6
def test_socks5_com_destino_ipv6_e_regra_ipv6():
    async def cenario():
        eco, porta_eco = await _eco_ipv6()
        motor, registro = _motor(f"[::1]:{porta_eco} +direct\n* +block\n")
        motor.iniciar()
        try:
            perfil = PerfilProxy(type="socks5", host="127.0.0.1", port=PORTA_SOCKS)
            leitor, escritor = await cliente_proxy.abrir_tunel([perfil], "::1", porta_eco)
            assert await _conversar(leitor, escritor) == b"ping"
            await asyncio.sleep(0.2)
            entrada = registro.recentes()[-1]
            assert (entrada.host_destino, entrada.acao) == ("::1", "direct")
        finally:
            motor.parar()
            eco.close()

    asyncio.run(cenario())


@precisa_ipv6
def test_connect_http_com_ipv6_entre_colchetes_passando_por_proxy_socks5():
    async def cenario():
        saltos = []
        proxy, porta_proxy = await _proxy_socks5(saltos)
        eco, porta_eco = await _eco_ipv6()
        perfil = PerfilProxy(nome="p", type="socks5", host="127.0.0.1", port=porta_proxy, padrao=True)
        motor, _ = _motor("*\n", proxies=[perfil])
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            escritor.write(f"CONNECT [::1]:{porta_eco} HTTP/1.1\r\nHost: [::1]:{porta_eco}\r\n\r\n".encode())
            await escritor.drain()
            assert b" 200 " in await asyncio.wait_for(leitor.readuntil(b"\r\n\r\n"), timeout=5)
            assert await _conversar(leitor, escritor) == b"ping"
            # o proxy recebeu o endereço IPv6 como endereço (ATYP 4), não como nome
            # (a verificação do IP de saída também passa pelo proxy, rumo ao ip-api.com)
            assert ("socks5", "::1", porta_eco) in saltos
        finally:
            motor.parar()
            proxy.close()
            eco.close()

    asyncio.run(cenario())
