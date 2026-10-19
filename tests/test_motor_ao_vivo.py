"""Mudanças de porta e do modo transparente com o motor ligado, sem derrubar conexões abertas."""
import asyncio
import socket
from dataclasses import replace

from proxy_manager.nucleo.configuracao import Configuracao, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from proxy_manager.nucleo.socks5 import conectar_via_socks5

PORTA_SOCKS = 58900
PORTA_HTTP = 58901
PORTA_PAC = 58902


def _criar_motor(texto_regras: str = "127.0.0.1 +direct\n") -> tuple[MotorProxy, Registro]:
    preferencias = Preferencias(porta_socks=PORTA_SOCKS, porta_http=PORTA_HTTP, porta_pac=PORTA_PAC,
                                acao_padrao="block")
    cfg = Configuracao(proxies=[], texto_regras=texto_regras, preferencias=preferencias)
    registro = Registro(dias_retencao=1)
    return MotorProxy(cfg, registro), registro


async def _iniciar_servidor_eco():
    async def handle(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
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

    servidor = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = servidor.sockets[0].getsockname()[1]
    return servidor, port


async def _baixar_pac(port: int) -> bytes:
    leitor, escritor = await asyncio.open_connection("127.0.0.1", port)
    escritor.write(b"GET /proxy.pac HTTP/1.1\r\n\r\n")
    await escritor.drain()
    dados = await leitor.read(-1)
    escritor.close()
    return dados


def test_trocar_porta_nao_derruba_conexao_aberta():
    async def cenario():
        servidor_destino, porta_destino = await _iniciar_servidor_eco()
        motor, _registro = _criar_motor()
        motor.iniciar()
        try:
            leitor, escritor = await conectar_via_socks5("127.0.0.1", PORTA_SOCKS, "127.0.0.1", porta_destino)
            escritor.write(b"ping1")
            await escritor.drain()
            assert await leitor.read(5) == b"ping1"

            # troca a porta do PAC (não tem nada a ver com essa conexão SOCKS5 já aberta)
            prefs_novas = replace(motor.configuracao.preferencias, porta_pac=PORTA_PAC + 1)
            nova_configuracao = Configuracao(proxies=[], texto_regras=motor.configuracao.texto_regras, preferencias=prefs_novas)
            ok, mensagem = motor.aplicar_ao_vivo(nova_configuracao)
            assert ok is True, mensagem

            # a conexão que já estava aberta ANTES da troca continua funcionando normalmente
            escritor.write(b"ping2")
            await escritor.drain()
            assert await leitor.read(5) == b"ping2"
        finally:
            motor.parar()
            servidor_destino.close()

    asyncio.run(cenario())


def test_trocar_porta_pac_move_o_servidor():
    async def cenario():
        motor, _registro = _criar_motor()
        motor.iniciar()
        try:
            assert b"200" in await _baixar_pac(PORTA_PAC)

            prefs_novas = replace(motor.configuracao.preferencias, porta_pac=PORTA_PAC + 2)
            nova_configuracao = Configuracao(proxies=[], texto_regras=motor.configuracao.texto_regras, preferencias=prefs_novas)
            ok, mensagem = motor.aplicar_ao_vivo(nova_configuracao)
            assert ok is True, mensagem
            assert motor.configuracao.preferencias.porta_pac == PORTA_PAC + 2

            assert b"200" in await _baixar_pac(PORTA_PAC + 2)
            try:
                await asyncio.wait_for(asyncio.open_connection("127.0.0.1", PORTA_PAC), timeout=1)
                assert False, "a porta antiga do PAC deveria ter parado de aceitar conexões"
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
                pass
        finally:
            motor.parar()

    asyncio.run(cenario())


def test_aplicar_ao_vivo_com_motor_parado_da_erro():
    motor, _registro = _criar_motor()
    nova_configuracao = Configuracao(proxies=[], texto_regras=motor.configuracao.texto_regras, preferencias=motor.configuracao.preferencias)

    ok, mensagem = motor.aplicar_ao_vivo(nova_configuracao)

    assert ok is False
    assert "não está em execução" in mensagem


def test_aplicar_ao_vivo_reverte_porta_ocupada():
    async def cenario():
        motor, _registro = _criar_motor()
        motor.iniciar()
        porta_ocupada = 58909
        ocupante = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ocupante.bind(("127.0.0.1", porta_ocupada))
        ocupante.listen(1)
        try:
            prefs_novas = replace(motor.configuracao.preferencias, porta_socks=porta_ocupada)
            nova_configuracao = Configuracao(proxies=[], texto_regras=motor.configuracao.texto_regras, preferencias=prefs_novas)

            ok, mensagem = motor.aplicar_ao_vivo(nova_configuracao)

            assert ok is False
            assert str(porta_ocupada) in mensagem
            # a config reflete a porta REAL que continua escutando, não a que falhou
            assert motor.configuracao.preferencias.porta_socks == PORTA_SOCKS

            # e a porta antiga continua aceitando conexões normalmente (só confirma que o
            # listener segue de pé; não precisa completar um CONNECT de verdade pra isso)
            leitor, escritor = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", PORTA_SOCKS), timeout=2)
            escritor.close()
        finally:
            ocupante.close()
            motor.parar()

    asyncio.run(cenario())


def test_aplicar_ao_vivo_liga_e_desliga_transparente(monkeypatch):
    from proxy_manager.nucleo import motor as engine_module

    class _BackendFalso:
        def __init__(self):
            self.inicio = False
            self.stopped = False

        def iniciar(self):
            self.inicio = True
            return True, "ok (falso)"

        def parar(self):
            self.stopped = True
            return True, "ok (falso)"

        def resolver_destino(self, escritor, porta_origem):
            return None

    async def cenario():
        backend_falso = _BackendFalso()
        monkeypatch.setattr(engine_module, "_criar_backend_transparente", lambda port: backend_falso)

        motor, _registro = _criar_motor()
        motor.iniciar()
        try:
            assert motor.backend_transparente is None

            prefs_novas = replace(motor.configuracao.preferencias, modo_transparente_ativo=True,
                                  porta_transparente=58908)
            nova_configuracao = Configuracao(proxies=[], texto_regras=motor.configuracao.texto_regras, preferencias=prefs_novas)
            ok, mensagem = motor.aplicar_ao_vivo(nova_configuracao)

            assert ok is True, mensagem
            assert backend_falso.inicio is True
            assert motor.backend_transparente is backend_falso

            novas_prefs2 = replace(motor.configuracao.preferencias, modo_transparente_ativo=False)
            nova_config2 = Configuracao(proxies=[], texto_regras=motor.configuracao.texto_regras, preferencias=novas_prefs2)
            ok2, mensagem2 = motor.aplicar_ao_vivo(nova_config2)

            assert ok2 is True, mensagem2
            assert backend_falso.stopped is True
            assert motor.backend_transparente is None
        finally:
            motor.parar()

    asyncio.run(cenario())
