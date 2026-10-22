"""Modo transparente com um backend falso: ciclo de vida, leitura de SNI/Host e encaminhamento."""
import asyncio

from proxy_manager.nucleo import motor as engine_module
from proxy_manager.nucleo.configuracao import Configuracao, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from tests.test_motor import _iniciar_servidor_destino
from tests.test_transparente_inspecao import _montar_client_hello

PORTA_TRANSPARENTE = 58795


class _BackendTransparenteFalso:
    def __init__(self, destino_fixo):
        self.fixed_destination = destino_fixo
        self.inicio = False
        self.stopped = False

    def iniciar(self):
        self.inicio = True
        return True, "ok (falso)"

    def parar(self):
        self.stopped = True
        return True, "ok (falso)"

    def resolver_destino(self, escritor, porta_origem):
        return self.fixed_destination


def _criar_motor_transparente(texto_regras: str, backend_falso, acao_padrao="block"):
    preferencias = Preferencias(porta_socks=58780, porta_http=58781, porta_pac=58790,
                                modo_transparente_ativo=True, porta_transparente=PORTA_TRANSPARENTE,
                                acao_padrao=acao_padrao)
    cfg = Configuracao(proxies=[], texto_regras=texto_regras, preferencias=preferencias)
    registro = Registro(dias_retencao=1)
    return MotorProxy(cfg, registro), registro


def test_backend_transparente_acompanha_motor(monkeypatch):
    """O backend liga antes do motor ficar pronto e desliga junto com ele."""
    async def cenario():
        backend_falso = _BackendTransparenteFalso(destino_fixo=("127.0.0.1", 1))
        monkeypatch.setattr(engine_module, "_criar_backend_transparente", lambda port: backend_falso)

        motor, _registro = _criar_motor_transparente("* +direct\n", backend_falso)
        motor.iniciar()
        assert backend_falso.inicio
        assert motor.backend_transparente is backend_falso

        motor.parar()
        assert backend_falso.stopped
        assert motor.backend_transparente is None

    asyncio.run(cenario())


def test_falha_do_transparente_libera_as_portas(monkeypatch):
    """Se o backend não liga, as portas SOCKS/HTTP/PAC já abertas são fechadas e o motor
    consegue ser iniciado de novo, sem "address already in use"."""
    class _BackendQueFalha(_BackendTransparenteFalso):
        def iniciar(self):
            return False, "nft indisponível (falso)"

    backend = _BackendQueFalha(destino_fixo=("127.0.0.1", 1))
    monkeypatch.setattr(engine_module, "_criar_backend_transparente", lambda port: backend)
    motor, _registro = _criar_motor_transparente("* +direct\n", backend)
    estados = []
    motor.ao_mudar_estado = lambda ativo, mensagem: estados.append((ativo, mensagem))

    motor.iniciar()
    motor._thread.join(timeout=5)
    assert not motor.em_execucao()
    # No Windows o loop fecha os sockets de escuta junto; no Linux, não: o motor tem que fechá-los.
    assert motor._servidor_socks is None and motor._servidor_http is None and motor._servidor_pac is None

    backend_bom = _BackendTransparenteFalso(destino_fixo=("127.0.0.1", 1))
    monkeypatch.setattr(engine_module, "_criar_backend_transparente", lambda port: backend_bom)
    motor.iniciar()
    try:
        assert motor.em_execucao()
        assert estados[-1] == (True, "Motor iniciado")
    finally:
        motor.parar()


def test_transparente_direto_usa_destino_original(monkeypatch):
    async def cenario():
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"via transparente")
        backend_falso = _BackendTransparenteFalso(destino_fixo=("127.0.0.1", porta_destino))
        monkeypatch.setattr(engine_module, "_criar_backend_transparente", lambda port: backend_falso)

        motor, registro = _criar_motor_transparente("127.0.0.1 +direct\n", backend_falso)
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_TRANSPARENTE)
            escritor.write(b"GET / HTTP/1.0\r\n\r\n")  # porta do alvo nao eh 80/443: sem sniff
            await escritor.drain()
            dados = await leitor.read(-1)
            assert b"via transparente" in dados

            await asyncio.sleep(0.2)
            entradas = [e for e in registro.recentes() if e.protocolo == "transparente"]
            assert any(e.acao == "direct" and e.estado == "concluida" for e in entradas)
        finally:
            motor.parar()
            servidor_destino.close()

    asyncio.run(cenario())


def test_transparente_sni_permite_regra_por_dominio(monkeypatch):
    """O SNI permite casar regras por domínio. Usa +block para não precisar de servidor na 443."""
    async def cenario():
        backend_falso = _BackendTransparenteFalso(destino_fixo=("203.0.113.10", 443))
        monkeypatch.setattr(engine_module, "_criar_backend_transparente", lambda port: backend_falso)

        motor, registro = _criar_motor_transparente("*.paypal.com +block\n", backend_falso,
                                                 acao_padrao="direct")
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_TRANSPARENTE)
            escritor.write(_montar_client_hello("checkout.paypal.com"))
            await escritor.drain()
            await asyncio.sleep(0.3)

            entradas = [e for e in registro.recentes() if e.host_destino == "checkout.paypal.com"]
            assert entradas, "deveria ter descoberto e logado o host via SNI"
            assert entradas[-1].acao == "block"
            assert entradas[-1].regra_aplicada == "*.paypal.com +block"
        finally:
            motor.parar()

    asyncio.run(cenario())


def test_transparente_host_http_permite_regra_por_dominio(monkeypatch):
    """Mesma ideia da regra por SNI, mas pra HTTP em texto puro (porta 80): o hostname vem do
    cabeçalho Host, não de um handshake TLS."""
    async def cenario():
        backend_falso = _BackendTransparenteFalso(destino_fixo=("203.0.113.10", 80))
        monkeypatch.setattr(engine_module, "_criar_backend_transparente", lambda port: backend_falso)

        motor, registro = _criar_motor_transparente("*.exemplo.com +block\n", backend_falso,
                                                 acao_padrao="direct")
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_TRANSPARENTE)
            escritor.write(b"GET /caminho HTTP/1.1\r\nHost: www.exemplo.com\r\nConnection: close\r\n\r\n")
            await escritor.drain()
            await asyncio.sleep(0.3)

            entradas = [e for e in registro.recentes() if e.host_destino == "www.exemplo.com"]
            assert entradas, "deveria ter descoberto e logado o host via cabeçalho Host"
            assert entradas[-1].acao == "block"
            assert entradas[-1].regra_aplicada == "*.exemplo.com +block"
        finally:
            motor.parar()

    asyncio.run(cenario())
