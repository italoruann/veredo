"""Testes do motor com servidores e clientes reais em 127.0.0.1."""
import asyncio
import time

from proxy_manager.nucleo.configuracao import Configuracao, PerfilProxy, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from proxy_manager.nucleo.socks5 import (
    REP_HOST_UNREACHABLE,
    REP_SUCCESS,
    conectar_via_socks5,
    responder_cliente,
    negociar_servidor,
)

PORTA_SOCKS = 58380
PORTA_HTTP = 58381
PORTA_PAC = 58390


async def _iniciar_servidor_destino(corpo: bytes):
    async def handle(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(leitor.readuntil(b"\r\n\r\n"), timeout=5)
        except Exception:
            escritor.close()
            return
        resp = (b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(corpo)).encode() +
                b"\r\nConnection: close\r\n\r\n" + corpo)
        escritor.write(resp)
        await escritor.drain()
        escritor.close()

    servidor = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = servidor.sockets[0].getsockname()[1]
    return servidor, port


def _criar_motor(texto_regras: str, acao_padrao: str = "block",
                 proxies: list[PerfilProxy] | None = None) -> tuple[MotorProxy, Registro]:
    preferencias = Preferencias(porta_socks=PORTA_SOCKS, porta_http=PORTA_HTTP, porta_pac=PORTA_PAC,
                                acao_padrao=acao_padrao)
    cfg = Configuracao(proxies=proxies or [], texto_regras=texto_regras, preferencias=preferencias)
    registro = Registro(dias_retencao=1)
    return MotorProxy(cfg, registro), registro


async def _iniciar_proxy_socks5_falso():
    """Proxy SOCKS5 mínimo para testar o caminho +proxy."""
    async def repassar(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            while True:
                bloco = await leitor.read(4096)
                if not bloco:
                    break
                escritor.write(bloco)
                await escritor.drain()
        except Exception:
            pass
        finally:
            escritor.close()

    async def handle(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            pedido = await negociar_servidor(leitor, escritor)
        except Exception:
            escritor.close()
            return
        try:
            leitor_destino, escritor_destino = await asyncio.open_connection(pedido.host, pedido.port)
        except OSError:
            await responder_cliente(escritor, REP_HOST_UNREACHABLE)
            escritor.close()
            return
        await responder_cliente(escritor, REP_SUCCESS)
        await asyncio.gather(repassar(leitor, escritor_destino), repassar(leitor_destino, escritor))

    servidor = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = servidor.sockets[0].getsockname()[1]
    return servidor, port


def test_http_direto_e_registro():
    async def cenario():
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"ola mundo")
        motor, registro = _criar_motor("127.0.0.1 +direct\n")
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            pedido = (f"GET http://127.0.0.1:{porta_destino}/ HTTP/1.1\r\n"
                      f"Host: 127.0.0.1:{porta_destino}\r\nConnection: close\r\n\r\n").encode()
            escritor.write(pedido)
            await escritor.drain()
            dados = await leitor.read(-1)
            assert b"ola mundo" in dados

            await asyncio.sleep(0.2)
            entradas = registro.recentes()
            assert any(e.acao == "direct" and e.estado == "concluida" for e in entradas)
        finally:
            motor.parar()
            servidor_destino.close()

    asyncio.run(cenario())


def test_regra_block_responde_403():
    async def cenario():
        motor, registro = _criar_motor("127.0.0.1 +block\n")
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            pedido = b"GET http://127.0.0.1:9/ HTTP/1.1\r\nHost: 127.0.0.1:9\r\nConnection: close\r\n\r\n"
            escritor.write(pedido)
            await escritor.drain()
            dados = await leitor.read(-1)
            assert b"403" in dados

            await asyncio.sleep(0.2)
            entradas = registro.recentes()
            assert any(e.acao == "block" and e.estado == "bloqueada" for e in entradas)
        finally:
            motor.parar()

    asyncio.run(cenario())


def test_socks5_direto():
    async def cenario():
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"via socks5")
        motor, _registro = _criar_motor("127.0.0.1 +direct\n")
        motor.iniciar()
        try:
            leitor, escritor = await conectar_via_socks5("127.0.0.1", PORTA_SOCKS, "127.0.0.1", porta_destino)
            pedido = (f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{porta_destino}\r\nConnection: close\r\n\r\n").encode()
            escritor.write(pedido)
            await escritor.drain()
            dados = await leitor.read(-1)
            assert b"via socks5" in dados
        finally:
            motor.parar()
            servidor_destino.close()

    asyncio.run(cenario())


def test_via_proxy_e_contadores_de_bytes():
    """Cobre o caminho '+proxy' (client -> engine -> proxy upstream -> destino) e confirma que
    os contadores de bytes por categoria (via proxy x direto) batem com a realidade."""
    async def cenario():
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"resposta via proxy upstream")
        servidor_proxy, proxy_port = await _iniciar_proxy_socks5_falso()
        perfil = PerfilProxy(nome="upstream-teste", type="socks5", host="127.0.0.1",
                             port=proxy_port, padrao=True)
        motor, registro = _criar_motor("127.0.0.1\n", proxies=[perfil])  # sem sufixo => via proxy
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            pedido = (f"GET http://127.0.0.1:{porta_destino}/ HTTP/1.1\r\n"
                      f"Host: 127.0.0.1:{porta_destino}\r\nConnection: close\r\n\r\n").encode()
            escritor.write(pedido)
            await escritor.drain()
            dados = await leitor.read(-1)
            assert b"resposta via proxy upstream" in dados

            await asyncio.sleep(0.2)
            entradas = registro.recentes()
            assert any(e.acao == "proxy" and e.proxy_usado == "upstream-teste" and
                       e.estado == "concluida" for e in entradas)

            # O GET é enviado antes do túnel; a resposta é que passa pela contagem.
            assert motor.proxy_recebido > 0
            assert motor.direto_enviado == 0
            assert motor.direto_recebido == 0
        finally:
            motor.parar()
            servidor_destino.close()
            servidor_proxy.close()

    asyncio.run(cenario())


def test_parar_e_rapido_apos_conexao_encerrada():
    """parar() não pode esperar o timeout depois de atender uma conexão."""
    async def cenario():
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"ola mundo")
        motor, _registro = _criar_motor("127.0.0.1 +direct\n")
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            pedido = (f"GET http://127.0.0.1:{porta_destino}/ HTTP/1.1\r\n"
                      f"Host: 127.0.0.1:{porta_destino}\r\nConnection: close\r\n\r\n").encode()
            escritor.write(pedido)
            await escritor.drain()
            await leitor.read(-1)
            await asyncio.sleep(0.2)
        finally:
            t0 = time.monotonic()
            motor.parar()
            decorrido = time.monotonic() - t0
            servidor_destino.close()
        assert decorrido < 2.0, f"stop() levou {decorrido:.2f}s (deveria ser quase instantâneo)"
        assert not motor.em_execucao()

    asyncio.run(cenario())


def test_trocar_porta_do_perfil_com_motor_ligado(monkeypatch):
    """Editar o endereço do perfil encerra os túneis antigos e consulta o novo IP de saída na hora."""
    from proxy_manager.nucleo import verificacao_ip

    async def ip_falso(perfil, timeout=8.0):
        return f"ip-{perfil.port}"
    monkeypatch.setattr(verificacao_ip, "consultar_ip_saida", ip_falso)

    async def cenario():
        servidor_proxy_1, porta_1 = await _iniciar_proxy_socks5_falso()
        servidor_proxy_2, porta_2 = await _iniciar_proxy_socks5_falso()
        servidor_eco, porta_eco = await _iniciar_proxy_socks5_falso()  # destino que mantém a conexão aberta
        perfil = PerfilProxy(nome="rotativo", type="socks5", host="127.0.0.1", port=porta_1, padrao=True)
        motor, _registro = _criar_motor("127.0.0.1\n", proxies=[perfil])
        mudancas = []
        motor.ao_mudar_ip_saida = lambda *args: mudancas.append(args)
        motor.iniciar()
        try:
            # Túnel longo pela porta antiga (como um HTTPS mantido aberto pelo navegador).
            leitor, escritor = await conectar_via_socks5("127.0.0.1", PORTA_SOCKS, "127.0.0.1", porta_eco)
            await asyncio.sleep(0.3)
            assert mudancas == [(perfil.id, "", f"ip-{porta_1}")]

            perfil.port = porta_2
            motor.atualizar_configuracao(motor.configuracao)
            assert await asyncio.wait_for(leitor.read(-1), timeout=3) == b""  # túnel antigo encerrado
            await asyncio.sleep(0.3)
            # Sem nenhuma conexão nova, o IP da porta nova já foi verificado.
            assert mudancas[-1] == (perfil.id, f"ip-{porta_1}", f"ip-{porta_2}")
            escritor.close()
        finally:
            motor.parar()
            for servidor in (servidor_proxy_1, servidor_proxy_2, servidor_eco):
                servidor.close()

    asyncio.run(cenario())


def test_conexao_para_porta_do_proprio_motor_e_bloqueada():
    async def cenario():
        motor, registro = _criar_motor("* +direct\n", acao_padrao="direct")
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            pedido = (f"GET http://127.0.0.1:{PORTA_SOCKS}/ HTTP/1.1\r\n"
                      f"Host: 127.0.0.1:{PORTA_SOCKS}\r\nConnection: close\r\n\r\n").encode()
            escritor.write(pedido)
            await escritor.drain()
            dados = await leitor.read(-1)
            assert b"403" in dados

            await asyncio.sleep(0.2)
            entrada = registro.recentes()[-1]
            assert entrada.estado == "bloqueada"
            assert "loop" in entrada.erro
        finally:
            motor.parar()

    asyncio.run(cenario())


def test_conexao_ao_proprio_servidor_proxy_vai_direto():
    async def cenario():
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"ok")
        proxy = PerfilProxy(nome="p", type="socks5", host="127.0.0.1", port=porta_destino, padrao=True)
        motor, registro = _criar_motor("*\n", proxies=[proxy])
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            pedido = (f"GET http://127.0.0.1:{porta_destino}/ HTTP/1.1\r\n"
                      f"Host: 127.0.0.1:{porta_destino}\r\nConnection: close\r\n\r\n").encode()
            escritor.write(pedido)
            await escritor.drain()
            assert b"ok" in await leitor.read(-1)
            await asyncio.sleep(0.2)
            assert registro.recentes()[-1].acao == "direct"
        finally:
            motor.parar()
            servidor_destino.close()

    asyncio.run(cenario())
