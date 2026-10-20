"""UDP pelas regras: UDP ASSOCIATE na porta SOCKS local, encadeado a um proxy SOCKS5 e no modo
transparente (com um backend falso)."""
import asyncio

from proxy_manager.nucleo import socks5, udp
from proxy_manager.nucleo import motor as modulo_motor
from proxy_manager.nucleo.configuracao import Configuracao, PerfilProxy, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from tests.test_motor_transparente import _BackendTransparenteFalso


class _Eco(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transporte = transport

    def datagram_received(self, dados, endereco):
        self.transporte.sendto(b"eco:" + dados, endereco)


class _Coletor(asyncio.DatagramProtocol):
    def __init__(self):
        self.fila = asyncio.Queue()

    def datagram_received(self, dados, endereco):
        self.fila.put_nowait(dados)


async def _eco_udp():
    transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(_Eco, local_addr=("127.0.0.1", 0))
    return transporte, transporte.get_extra_info("sockname")[1]


def _motor(porta_base: int, texto_regras: str, proxies=None, **prefs) -> tuple[MotorProxy, Registro]:
    preferencias = Preferencias(porta_socks=porta_base, porta_http=porta_base + 1, porta_pac=porta_base + 2,
                                acao_padrao="direct", **prefs)
    registro = Registro(dias_retencao=1)
    return MotorProxy(Configuracao(proxies=proxies or [], texto_regras=texto_regras, preferencias=preferencias),
                      registro), registro


async def _associar(porta_socks: int):
    """Abre um UDP ASSOCIATE no motor; devolve (escritor TCP, transporte UDP, coletor, relay)."""
    leitor, escritor = await asyncio.open_connection("127.0.0.1", porta_socks)
    host_relay, porta_relay = await socks5.negociar_cliente(leitor, escritor, "0.0.0.0", 0,
                                                            comando=socks5.CMD_UDP_ASSOCIATE)
    coletor = _Coletor()
    transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: coletor, local_addr=("127.0.0.1", 0))
    return escritor, transporte, coletor, (host_relay, porta_relay)


async def _trocar(transporte, coletor, relay, porta_destino: int, dados: bytes) -> bytes:
    transporte.sendto(udp.montar_cabecalho_socks("127.0.0.1", porta_destino) + dados, relay)
    resposta = await asyncio.wait_for(coletor.fila.get(), timeout=5)
    host, porta, carga = udp.ler_cabecalho_socks(resposta)
    assert (host, porta) == ("127.0.0.1", porta_destino)  # a resposta diz de onde veio
    return carga


def test_cabecalho_socks_ida_e_volta():
    for host in ("10.1.2.3", "2001:db8::1", "exemplo.com"):
        assert udp.ler_cabecalho_socks(udp.montar_cabecalho_socks(host, 53) + b"x") == (host, 53, b"x")
    fragmentado = b"\x00\x00\x01" + udp.montar_cabecalho_socks("10.0.0.1", 1)[3:]
    assert udp.ler_cabecalho_socks(fragmentado) is None
    assert udp.ler_cabecalho_socks(b"\x00") is None


def test_udp_associate_direto_registra_o_fluxo():
    async def cenario():
        eco, porta_eco = await _eco_udp()
        motor, registro = _motor(58880, "")
        motor.iniciar()
        try:
            escritor, transporte, coletor, relay = await _associar(58880)
            assert await _trocar(transporte, coletor, relay, porta_eco, b"um") == b"eco:um"
            assert await _trocar(transporte, coletor, relay, porta_eco, b"dois") == b"eco:dois"
            escritor.close()  # fim da associação fecha o fluxo
            await asyncio.sleep(0.3)
            [entrada] = [e for e in registro.recentes() if e.protocolo == "socks5-udp"]
            assert (entrada.acao, entrada.estado, entrada.porta_destino) == ("direct", "concluida", porta_eco)
            assert entrada.bytes_enviados == 6 and entrada.bytes_recebidos == 14
            transporte.close()
        finally:
            motor.parar()
            eco.close()

    asyncio.run(cenario())


def test_udp_bloqueado_e_proxy_que_nao_transporta_udp():
    async def cenario():
        eco, porta_eco = await _eco_udp()
        http = PerfilProxy(nome="web", type="http", host="127.0.0.1", port=9)
        motor, registro = _motor(58880, f"127.0.0.1:{porta_eco} +block\n* +proxy:web\n", proxies=[http])
        motor.iniciar()
        try:
            escritor, transporte, coletor, relay = await _associar(58880)
            transporte.sendto(udp.montar_cabecalho_socks("127.0.0.1", porta_eco) + b"x", relay)
            transporte.sendto(udp.montar_cabecalho_socks("127.0.0.1", porta_eco + 1) + b"y", relay)
            await asyncio.sleep(0.5)
            assert coletor.fila.empty()
            estados = {e.porta_destino: (e.estado, e.erro) for e in registro.recentes()}
            assert estados[porta_eco][0] == "bloqueada"
            assert estados[porta_eco + 1][0] == "erro" and "não transporta UDP" in estados[porta_eco + 1][1]
            escritor.close()
            transporte.close()
        finally:
            motor.parar()
            eco.close()

    asyncio.run(cenario())


def test_udp_passa_por_proxy_socks5():
    """Um segundo motor faz o papel do proxy SOCKS5 de saída (ele também aceita UDP ASSOCIATE)."""
    async def cenario():
        eco, porta_eco = await _eco_udp()
        saida, registro_saida = _motor(58980, "")
        perfil = PerfilProxy(nome="saida", type="socks5", host="127.0.0.1", port=58980, padrao=True)
        motor, registro = _motor(58880, "*\n", proxies=[perfil])
        saida.iniciar()
        motor.iniciar()
        try:
            escritor, transporte, coletor, relay = await _associar(58880)
            assert await _trocar(transporte, coletor, relay, porta_eco, b"pelo proxy") == b"eco:pelo proxy"
            [entrada] = [e for e in registro.recentes() if e.protocolo == "socks5-udp"]
            assert (entrada.acao, entrada.proxy_usado) == ("proxy", "saida")
            # o proxy de saída viu o mesmo destino
            assert [e.porta_destino for e in registro_saida.recentes() if e.protocolo == "socks5-udp"] == [porta_eco]
            escritor.close()
            transporte.close()
        finally:
            motor.parar()
            saida.parar()
            eco.close()

    asyncio.run(cenario())


def test_udp_capturado_no_modo_transparente(monkeypatch):
    async def cenario():
        eco, porta_eco = await _eco_udp()
        backend = _BackendTransparenteFalso(destino_fixo=None)
        monkeypatch.setattr(modulo_motor, "_criar_backend_transparente", lambda prefs: backend)
        motor, registro = _motor(58880, f"127.0.0.1:{porta_eco + 1} +block\n",
                                 modo_transparente_ativo=True, porta_transparente=58895, udp_pelas_regras=True)
        motor.iniciar()
        try:
            assert backend.ao_receber_udp is not None
            respostas = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def responder(dados):  # chamado no loop do motor, como faria o backend
                loop.call_soon_threadsafe(respostas.put_nowait, dados)

            backend.ao_receber_udp(("192.168.1.50", 50000), ("127.0.0.1", porta_eco), b"jogo", responder)
            assert await asyncio.wait_for(respostas.get(), timeout=5) == b"eco:jogo"
            backend.ao_receber_udp(("192.168.1.50", 50000), ("127.0.0.1", porta_eco + 1), b"x", responder)
            await asyncio.sleep(0.3)
            assert respostas.empty()
            acoes = {e.porta_destino: (e.protocolo, e.acao) for e in registro.recentes()}
            assert acoes[porta_eco] == ("udp", "direct")
            assert acoes[porta_eco + 1] == ("udp", "block")
        finally:
            motor.parar()
            eco.close()

    asyncio.run(cenario())


def test_prevencao_de_vazamento_bloqueia_udp_direto(monkeypatch):
    async def cenario():
        eco, porta_eco = await _eco_udp()
        backend = _BackendTransparenteFalso(destino_fixo=None)
        monkeypatch.setattr(modulo_motor, "_criar_backend_transparente", lambda prefs: backend)
        motor, registro = _motor(58880, "", modo_transparente_ativo=True, porta_transparente=58895,
                                 udp_pelas_regras=True, prevenir_vazamento=True, porta_dns=58853,
                                 dns_servidores="127.0.0.1:9")
        motor.iniciar()
        try:
            backend.ao_receber_udp(("192.168.1.50", 50000), ("127.0.0.1", porta_eco), b"x", lambda d: None)
            await asyncio.sleep(0.3)
            [entrada] = [e for e in registro.recentes() if e.protocolo == "udp"]
            assert (entrada.acao, entrada.regra_aplicada) == ("block", "(prevenção de vazamento)")
        finally:
            motor.parar()
            eco.close()

    asyncio.run(cenario())
