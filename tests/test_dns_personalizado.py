"""DNS personalizado: consultas aos servidores escolhidos, pelo caminho que as regras mandarem."""
import asyncio
import struct

from proxy_manager.nucleo import cliente_proxy, dns, rede
from proxy_manager.nucleo import motor as modulo_motor
from proxy_manager.nucleo.configuracao import Configuracao, PerfilProxy, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from tests.test_cadeias import _proxy_socks5
from tests.test_dns import _consultar, _iniciar_upstream, _resposta_upstream
from tests.test_motor import _iniciar_servidor_destino
from tests.test_motor_transparente import _BackendTransparenteFalso


async def _upstream_tcp():
    """Servidor DNS por TCP de teste: responde 127.0.0.2 para qualquer nome."""
    nomes = []

    async def atender(leitor, escritor):
        try:
            while True:
                tamanho = struct.unpack("!H", await leitor.readexactly(2))[0]
                consulta = await leitor.readexactly(tamanho)
                nomes.append(dns.ler_consulta(consulta).nome)
                resposta = _resposta_upstream(consulta, "127.0.0.2")
                escritor.write(struct.pack("!H", len(resposta)) + resposta)
                await escritor.drain()
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            escritor.close()

    servidor = await asyncio.start_server(atender, "127.0.0.1", 0)
    return servidor, servidor.sockets[0].getsockname()[1], nomes


def test_modo_dns_da_configuracao_antiga_e_da_prevencao():
    assert Preferencias.de_dict({"dns_pelo_proxy": True}).modo_dns == "proxy"
    assert Preferencias.de_dict({"dns_pelo_proxy": False}).modo_dns == "sistema"
    assert Preferencias.de_dict({"modo_dns": "personalizado", "dns_pelo_proxy": True}).modo_dns == "personalizado"
    # a prevenção de vazamento sempre usa o DNS do proxy
    assert Preferencias(modo_dns="personalizado", prevenir_vazamento=True).modo_dns_efetivo == "proxy"
    assert Preferencias(modo_dns="xyz").modo_dns_efetivo == "sistema"
    # o desvio das consultas só existe no modo transparente
    assert not Preferencias(modo_dns="personalizado").dns_ativo
    assert Preferencias(modo_dns="personalizado", modo_transparente_ativo=True).dns_ativo
    assert not Preferencias(modo_dns="sistema", modo_transparente_ativo=True).dns_ativo


def test_encaminhador_direto_por_udp_com_cache():
    async def cenario():
        transporte, upstream, porta = await _iniciar_upstream()
        try:
            caminhos = []
            resolvidos = {}

            def caminho(host, p):
                caminhos.append((host, p))
                return "direct"

            encaminhador = dns.EncaminhadorDns([f"127.0.0.1:{porta}"], caminho,
                                               ao_resolver=lambda nome, ips: resolvidos.update({nome: ips}))
            primeira = await encaminhador.consultar(dns.montar_consulta("x.com", dns.TIPO_A, 1))
            segunda = await encaminhador.consultar(dns.montar_consulta("x.com", dns.TIPO_A, 2))
            assert dns.ler_enderecos(primeira) == dns.ler_enderecos(segunda) == ["127.0.0.1"]
            assert segunda[:2] == struct.pack("!H", 2)  # resposta do cache com o id da consulta nova
            assert upstream.nomes == ["x.com"] and caminhos == [("127.0.0.1", porta)]
            assert resolvidos == {"x.com": ["127.0.0.1"]}
            assert not rede._portas_proprias
        finally:
            transporte.close()

    asyncio.run(cenario())


def test_encaminhador_junta_consultas_iguais_simultaneas():
    async def cenario():
        transporte, upstream, porta = await _iniciar_upstream()
        try:
            encaminhador = dns.EncaminhadorDns([f"127.0.0.1:{porta}"], lambda h, p: "direct")
            respostas = await asyncio.gather(*(
                encaminhador.consultar(dns.montar_consulta("y.com", dns.TIPO_A, 100 + i)) for i in range(5)))
            assert [r[:2] for r in respostas] == [struct.pack("!H", 100 + i) for i in range(5)]
            assert upstream.nomes == ["y.com"]
        finally:
            transporte.close()

    asyncio.run(cenario())


def test_encaminhador_pelo_proxy_usa_tcp_e_pula_servidor_bloqueado():
    async def cenario():
        servidor_dns, porta_dns, nomes = await _upstream_tcp()
        saltos = []
        proxy, porta_proxy = await _proxy_socks5(saltos)
        rota = cliente_proxy.rota_do_perfil(PerfilProxy(nome="p", host="127.0.0.1", port=porta_proxy))

        def caminho(host, porta):
            if host == "10.255.255.1":
                return "block"

            async def abrir():
                leitor, escritor, _ = await cliente_proxy.conectar_rota(rota, host, porta)
                return leitor, escritor
            return abrir

        try:
            encaminhador = dns.EncaminhadorDns(["10.255.255.1", f"127.0.0.1:{porta_dns}"], caminho)
            resposta = await encaminhador.consultar(dns.montar_consulta("z.com", dns.TIPO_A, 7))
            assert dns.ler_enderecos(resposta) == ["127.0.0.2"]
            assert nomes == ["z.com"]
            assert saltos == [("socks5", "127.0.0.1", porta_dns)]  # foi pelo proxy, não direto
        finally:
            proxy.close()
            servidor_dns.close()

    asyncio.run(cenario())


def test_encaminhador_sem_resposta_devolve_none():
    async def cenario():
        encaminhador = dns.EncaminhadorDns(["127.0.0.1:9"], lambda h, p: "direct", timeout=0.2)
        assert await encaminhador.consultar(dns.montar_consulta("w.com", dns.TIPO_A, 3)) is None

    asyncio.run(cenario())


def test_servidor_dns_personalizado_nao_usa_ip_falso_e_mantem_excecoes():
    async def cenario():
        transporte, upstream, porta = await _iniciar_upstream()
        encaminhadas = []

        async def encaminhar(dados):
            encaminhadas.append(dns.ler_consulta(dados).nome)
            return _resposta_upstream(dados, "203.0.113.9")

        try:
            servidor = dns.ServidorDns(dns.TabelaIpsFalsos(), dns.ResolvedorReal([f"127.0.0.1:{porta}"]),
                                       ["*.lan"], encaminhar=encaminhar)
            resposta = await servidor.responder(dns.montar_consulta("site.com", dns.TIPO_A, 1))
            assert dns.ler_enderecos(resposta) == ["203.0.113.9"]
            # AAAA também vai ao servidor escolhido (no DNS do proxy ela voltaria vazia)
            await servidor.responder(dns.montar_consulta("site.com", dns.TIPO_AAAA, 2))
            assert encaminhadas == ["site.com", "site.com"]
            # nome local vai ao DNS da rede
            local = await servidor.responder(dns.montar_consulta("nas.lan", dns.TIPO_A, 3))
            assert dns.ler_enderecos(local) == ["127.0.0.1"] and upstream.nomes == ["nas.lan"]
        finally:
            transporte.close()

    asyncio.run(cenario())


def test_caminho_dns_segue_as_regras():
    prefs = Preferencias(acao_padrao="direct")
    proxy = PerfilProxy(nome="p", host="10.0.0.1", port=1080)
    cfg = Configuracao(texto_regras="1.1.1.1 +direct\n9.9.9.9 +block\n8.8.8.8 +proxy\n",
                       preferencias=prefs, proxies=[proxy])
    motor = MotorProxy(cfg, Registro(dias_retencao=1))
    assert motor._caminho_dns("1.1.1.1", 53) == "direct"
    assert motor._caminho_dns("9.9.9.9", 53) == "block"
    assert callable(motor._caminho_dns("8.8.8.8", 53))
    assert motor._caminho_dns("4.4.4.4", 53) == "direct"  # sem regra: a ação padrão
    # a regra pede proxy, mas não há nenhum: bloquear, para não vazar saindo direto
    motor.atualizar_configuracao(Configuracao(texto_regras="* +proxy\n", preferencias=prefs))
    assert motor._caminho_dns("8.8.8.8", 53) == "block"


def test_motor_com_dns_personalizado_aplica_regra_por_dominio(monkeypatch):
    """O programa consulta o DNS local, recebe o IP real e conecta nele; o motor lembra o nome que
    resolveu esse IP e aplica a regra do domínio, mesmo sem SNI/Host."""
    async def cenario():
        transporte, upstream, porta_upstream = await _iniciar_upstream()
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"pelo dominio")
        backend = _BackendTransparenteFalso(destino_fixo=("127.0.0.1", porta_destino))
        monkeypatch.setattr(modulo_motor, "_criar_backend_transparente", lambda prefs: backend)
        prefs = Preferencias(porta_socks=58780, porta_http=58781, porta_pac=58790,
                             modo_transparente_ativo=True, porta_transparente=58795,
                             modo_dns="personalizado", porta_dns=58753,
                             dns_servidores=f"127.0.0.1:{porta_upstream}", acao_padrao="block")
        regras = f"meusite.teste +direct\n127.0.0.1:{porta_upstream} +direct\n"
        registro = Registro(dias_retencao=1)
        motor = MotorProxy(Configuracao(texto_regras=regras, preferencias=prefs), registro)
        motor.iniciar()
        try:
            assert motor.em_execucao()
            resposta = await _consultar(58753, "meusite.teste")
            assert dns.ler_enderecos(resposta) == ["127.0.0.1"]  # IP real, não falso
            assert upstream.nomes == ["meusite.teste"]

            leitor, escritor = await asyncio.open_connection("127.0.0.1", 58795)
            escritor.write(b"GET / HTTP/1.0\r\n\r\n")  # porta qualquer: sem SNI/Host para inspecionar
            await escritor.drain()
            assert b"pelo dominio" in await asyncio.wait_for(leitor.read(-1), timeout=5)
            await asyncio.sleep(0.2)
            entrada = registro.recentes()[-1]
            assert (entrada.host_destino, entrada.acao) == ("meusite.teste", "direct")
        finally:
            motor.parar()
            transporte.close()
            servidor_destino.close()

    asyncio.run(cenario())


def test_dns_personalizado_sem_servidores_nao_liga(monkeypatch):
    backend = _BackendTransparenteFalso(destino_fixo=None)
    monkeypatch.setattr(modulo_motor, "_criar_backend_transparente", lambda prefs: backend)
    prefs = Preferencias(porta_socks=58780, porta_http=58781, porta_pac=58790,
                         modo_transparente_ativo=True, porta_transparente=58795,
                         modo_dns="personalizado", porta_dns=58753)
    estados = []
    motor = MotorProxy(Configuracao(preferencias=prefs), Registro(dias_retencao=1))
    motor.ao_mudar_estado = lambda ativo, mensagem: estados.append(mensagem)
    motor.iniciar()
    motor.parar()
    assert any("DNS personalizado" in m for m in estados)
