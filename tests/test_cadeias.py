"""Cadeias de proxy e protocolos de cliente, com proxies falsos em 127.0.0.1."""
import asyncio
import socket
import struct

from proxy_manager.nucleo import cliente_proxy
from proxy_manager.nucleo.cliente_proxy import Rota
from proxy_manager.nucleo.configuracao import CadeiaProxy, Configuracao, PerfilProxy, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.proxy_http import ler_cabecalho_requisicao, extrair_destino
from proxy_manager.nucleo.registro import Registro
from proxy_manager.nucleo.socks5 import REP_SUCCESS, negociar_servidor, responder_cliente

PORTA_SOCKS = 58480
PORTA_HTTP = 58481
PORTA_PAC = 58490


async def _repassar(leitor, escritor):
    try:
        while bloco := await leitor.read(4096):
            escritor.write(bloco)
            await escritor.drain()
    except Exception:
        pass
    finally:
        escritor.close()


async def _servidor(handle):
    servidor = await asyncio.start_server(handle, "127.0.0.1", 0)
    return servidor, servidor.sockets[0].getsockname()[1]


async def _proxy_socks5(saltos: list):
    """Proxy SOCKS5 falso; anota em `saltos` cada destino pedido."""
    async def handle(leitor, escritor):
        try:
            pedido = await negociar_servidor(leitor, escritor)
            saltos.append(("socks5", pedido.host, pedido.port))
            leitor_d, escritor_d = await asyncio.open_connection(pedido.host, pedido.port)
        except Exception:
            escritor.close()
            return
        await responder_cliente(escritor, REP_SUCCESS)
        await asyncio.gather(_repassar(leitor, escritor_d), _repassar(leitor_d, escritor))
    return await _servidor(handle)


async def _proxy_http(saltos: list):
    async def handle(leitor, escritor):
        try:
            cabecalho = await ler_cabecalho_requisicao(leitor)
            host, porta = extrair_destino(cabecalho)
            saltos.append(("http", host, porta))
            leitor_d, escritor_d = await asyncio.open_connection(host, porta)
        except Exception:
            escritor.close()
            return
        escritor.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await escritor.drain()
        await asyncio.gather(_repassar(leitor, escritor_d), _repassar(leitor_d, escritor))
    return await _servidor(handle)


async def _proxy_socks4(saltos: list):
    async def handle(leitor, escritor):
        try:
            cabecalho = await leitor.readexactly(8)
            porta = struct.unpack("!H", cabecalho[2:4])[0]
            await leitor.readuntil(b"\x00")  # usuário
            if cabecalho[4:7] == b"\x00\x00\x00" and cabecalho[7] != 0:
                host = (await leitor.readuntil(b"\x00"))[:-1].decode()
            else:
                host = socket.inet_ntoa(cabecalho[4:8])
            saltos.append(("socks4", host, porta))
            leitor_d, escritor_d = await asyncio.open_connection(host, porta)
        except Exception:
            escritor.close()
            return
        escritor.write(b"\x00\x5a" + b"\x00" * 6)
        await escritor.drain()
        await asyncio.gather(_repassar(leitor, escritor_d), _repassar(leitor_d, escritor))
    return await _servidor(handle)


async def _eco():
    return await _servidor(_repassar_eco)


async def _repassar_eco(leitor, escritor):
    await _repassar(leitor, escritor)


def _porta_fechada() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    porta = sock.getsockname()[1]
    sock.close()
    return porta


async def _conversar(leitor, escritor, dados=b"ping") -> bytes:
    escritor.write(dados)
    await escritor.drain()
    resposta = await asyncio.wait_for(leitor.readexactly(len(dados)), timeout=5)
    escritor.close()
    return resposta


def test_cadeia_simples_passa_por_todos_os_proxies_em_ordem():
    async def cenario():
        saltos = []
        s1, p1 = await _proxy_socks5(saltos)
        s2, p2 = await _proxy_http(saltos)
        eco, porta_eco = await _eco()
        try:
            perfis = [PerfilProxy(nome="a", type="socks5", host="127.0.0.1", port=p1),
                      PerfilProxy(nome="b", type="http", host="127.0.0.1", port=p2)]
            leitor, escritor = await cliente_proxy.abrir_tunel(perfis, "127.0.0.1", porta_eco)
            assert await _conversar(leitor, escritor) == b"ping"
            assert saltos == [("socks5", "127.0.0.1", p2), ("http", "127.0.0.1", porta_eco)]
        finally:
            for s in (s1, s2, eco):
                s.close()

    asyncio.run(cenario())


def test_redundancia_pula_proxy_que_falha():
    async def cenario():
        saltos = []
        s1, p1 = await _proxy_socks5(saltos)
        eco, porta_eco = await _eco()
        try:
            morto = PerfilProxy(nome="morto", type="socks5", host="127.0.0.1", port=_porta_fechada())
            vivo = PerfilProxy(nome="vivo", type="socks5", host="127.0.0.1", port=p1)
            rota = Rota(id="r", nome="r", perfis=[morto, vivo], tipo="redundancia", timeout=3, eh_cadeia=True)
            leitor, escritor, usado = await cliente_proxy.conectar_rota(rota, "127.0.0.1", porta_eco)
            assert usado is vivo
            assert await _conversar(leitor, escritor) == b"ping"
        finally:
            s1.close()
            eco.close()

    asyncio.run(cenario())


def test_balanceamento_reveza_o_perfil_inicial():
    async def cenario():
        saltos_a, saltos_b = [], []
        sa, pa = await _proxy_socks5(saltos_a)
        sb, pb = await _proxy_socks5(saltos_b)
        eco, porta_eco = await _eco()
        try:
            a = PerfilProxy(nome="a", type="socks5", host="127.0.0.1", port=pa)
            b = PerfilProxy(nome="b", type="socks5", host="127.0.0.1", port=pb)
            rota = Rota(id="balanceada", nome="x", perfis=[a, b], tipo="balanceamento", eh_cadeia=True)
            usados = []
            for _ in range(4):
                leitor, escritor, usado = await cliente_proxy.conectar_rota(rota, "127.0.0.1", porta_eco)
                await _conversar(leitor, escritor)
                usados.append(usado.nome)
            assert sorted(usados) == ["a", "a", "b", "b"]
        finally:
            for s in (sa, sb, eco):
                s.close()

    asyncio.run(cenario())


def test_todos_os_proxies_falhando_gera_erro_de_rota():
    async def cenario():
        morto = PerfilProxy(nome="morto", type="socks5", host="127.0.0.1", port=_porta_fechada())
        rota = Rota(id="r2", nome="r2", perfis=[morto], tipo="redundancia", timeout=2, eh_cadeia=True)
        try:
            await cliente_proxy.conectar_rota(rota, "127.0.0.1", 9)
        except cliente_proxy.ErroRota as exc:
            assert "morto" in str(exc)
        else:
            raise AssertionError("deveria falhar")

    asyncio.run(cenario())


def test_socks4a_envia_o_nome_ao_proxy():
    async def cenario():
        saltos = []
        s, p = await _proxy_socks4(saltos)
        eco, porta_eco = await _eco()
        try:
            perfil = PerfilProxy(nome="s4", type="socks4", host="127.0.0.1", port=p)
            leitor, escritor = await cliente_proxy.abrir_tunel([perfil], "localhost", porta_eco)
            assert await _conversar(leitor, escritor) == b"ping"
            assert saltos == [("socks4", "localhost", porta_eco)]
        finally:
            s.close()
            eco.close()

    asyncio.run(cenario())


def test_motor_usa_cadeia_da_regra():
    async def cenario():
        saltos = []
        s1, p1 = await _proxy_socks5(saltos)
        s2, p2 = await _proxy_socks5(saltos)
        eco, porta_eco = await _eco()
        a = PerfilProxy(nome="a", type="socks5", host="127.0.0.1", port=p1)
        b = PerfilProxy(nome="b", type="socks5", host="127.0.0.1", port=p2)
        cadeia = CadeiaProxy(nome="Dupla", tipo="simples", proxies=[a.id, b.id])
        cfg = Configuracao(proxies=[a, b], cadeias=[cadeia], texto_regras="* +chain:Dupla\n",
                           preferencias=Preferencias(porta_socks=PORTA_SOCKS, porta_http=PORTA_HTTP,
                                                     porta_pac=PORTA_PAC))
        registro = Registro(dias_retencao=1)
        motor = MotorProxy(cfg, registro)
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            escritor.write(f"CONNECT 127.0.0.1:{porta_eco} HTTP/1.1\r\n\r\n".encode())
            await escritor.drain()
            assert b"200" in await asyncio.wait_for(leitor.readuntil(b"\r\n\r\n"), timeout=5)
            assert await _conversar(leitor, escritor) == b"ping"
            assert [s[2] for s in saltos] == [p2, porta_eco]
            await asyncio.sleep(0.2)
            assert registro.recentes()[-1].proxy_usado == "Dupla"
        finally:
            motor.parar()
            for s in (s1, s2, eco):
                s.close()

    asyncio.run(cenario())
