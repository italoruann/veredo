"""DNS pelo proxy: formato das mensagens, IPs falsos, servidor local e integração com o motor."""
import asyncio
import ipaddress
import socket

from proxy_manager.nucleo import dns, rede
from proxy_manager.nucleo import motor as modulo_motor
from proxy_manager.nucleo.configuracao import Configuracao, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from tests.test_motor import _iniciar_servidor_destino
from tests.test_motor_transparente import _BackendTransparenteFalso


def _resposta_upstream(consulta: bytes, ip: str) -> bytes:
    lida = dns.ler_consulta(consulta)
    if lida.tipo != dns.TIPO_A:
        return dns.montar_resposta(lida)
    return dns.montar_resposta(lida, respostas=[(dns.TIPO_A, ipaddress.IPv4Address(ip).packed)])


class _UpstreamFalso(asyncio.DatagramProtocol):
    """Servidor DNS "real" de teste: responde 127.0.0.1 para qualquer nome."""
    def __init__(self):
        self.nomes = []

    def connection_made(self, transport):
        self.transporte = transport

    def datagram_received(self, dados, endereco):
        self.nomes.append(dns.ler_consulta(dados).nome)
        self.transporte.sendto(_resposta_upstream(dados, "127.0.0.1"), endereco)


async def _iniciar_upstream():
    upstream = _UpstreamFalso()
    transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: upstream, local_addr=("127.0.0.1", 0))
    return transporte, upstream, transporte.get_extra_info("sockname")[1]


async def _consultar(porta: int, nome: str, tipo: int = dns.TIPO_A) -> bytes:
    real = dns.ResolvedorReal([f"127.0.0.1:{porta}"])
    return await real.consultar(dns.montar_consulta(nome, tipo, 4321))


def test_consulta_e_resposta_ida_e_volta():
    consulta_bruta = dns.montar_consulta("Www.Exemplo.com.", dns.TIPO_A, 99)
    consulta = dns.ler_consulta(consulta_bruta)
    assert (consulta.id, consulta.nome, consulta.tipo) == (99, "www.exemplo.com", dns.TIPO_A)
    resposta = dns.montar_resposta(consulta, respostas=[(dns.TIPO_A, bytes([198, 18, 0, 7]))])
    assert dns.ler_enderecos(resposta) == ["198.18.0.7"]
    assert resposta[:2] == consulta_bruta[:2]
    assert dns.ler_consulta(resposta) is None  # resposta não é consulta


def test_tabela_reaproveita_o_nome_mais_antigo_quando_enche():
    tabela = dns.TabelaIpsFalsos(ipaddress.ip_network("198.18.0.0/30"))  # 2 endereços úteis
    ip_a = tabela.ip_para("a.com")
    ip_b = tabela.ip_para("b.com")
    assert ip_a != ip_b and dns.eh_ip_falso(ip_a)
    assert tabela.ip_para("A.com.") == ip_a  # mesmo nome, mesmo IP (e vira o mais recente)
    ip_c = tabela.ip_para("c.com")
    assert ip_c == ip_b  # b.com era o mais antigo
    assert tabela.nome_de(ip_b) == "c.com"
    assert tabela.nome_de(ip_a) == "a.com"


def test_servidor_dns_responde_ips_falsos_e_encaminha_excecoes():
    async def cenario():
        transporte, upstream, porta_upstream = await _iniciar_upstream()
        try:
            real = dns.ResolvedorReal([f"127.0.0.1:{porta_upstream}"])
            servidor = dns.ServidorDns(dns.TabelaIpsFalsos(), real, ["*.local"], bloquear_outros=False)

            resposta = await servidor.responder(dns.montar_consulta("site.com", dns.TIPO_A, 1))
            [ip] = dns.ler_enderecos(resposta)
            assert dns.eh_ip_falso(ip) and servidor.tabela.nome_de(ip) == "site.com"

            # IPv6 vazio: o programa fica com o IPv4 falso
            assert dns.ler_enderecos(await servidor.responder(dns.montar_consulta("site.com", dns.TIPO_AAAA, 2))) == []

            # exceção vai ao DNS real
            assert dns.ler_enderecos(await servidor.responder(dns.montar_consulta("nas.local", dns.TIPO_A, 3))) == ["127.0.0.1"]
            # MX sem prevenção de vazamento também vai ao DNS real
            await servidor.responder(dns.montar_consulta("site.com", 15, 4))
            assert upstream.nomes == ["nas.local", "site.com"]

            servidor.bloquear_outros = True
            await servidor.responder(dns.montar_consulta("outro.com", 15, 5))
            assert upstream.nomes == ["nas.local", "site.com"]  # nada novo saiu
        finally:
            transporte.close()

    asyncio.run(cenario())


def test_resolvedor_real_usa_porta_reservada_e_cache():
    async def cenario():
        transporte, upstream, porta = await _iniciar_upstream()
        try:
            real = dns.ResolvedorReal([f"127.0.0.1:{porta}"])
            assert await real.resolver("x.com") == ["127.0.0.1"]
            assert await real.resolver("x.com") == ["127.0.0.1"]
            assert upstream.nomes == ["x.com"]
            assert await real.resolver("10.1.2.3") == ["10.1.2.3"]
            assert not rede._portas_proprias  # portas liberadas depois de cada consulta
        finally:
            transporte.close()

    asyncio.run(cenario())


def test_motor_traduz_ip_falso_para_o_nome(monkeypatch):
    """Fluxo completo: o programa consulta o DNS local, conecta no IP falso e o motor encaminha
    pelo nome, resolvendo com o DNS real (o upstream falso responde 127.0.0.1)."""
    async def cenario():
        transporte, upstream, porta_upstream = await _iniciar_upstream()
        servidor_destino, porta_destino = await _iniciar_servidor_destino(b"pelo nome")
        backend = _BackendTransparenteFalso(destino_fixo=None)
        monkeypatch.setattr(modulo_motor, "_criar_backend_transparente", lambda prefs: backend)
        prefs = Preferencias(porta_socks=58680, porta_http=58681, porta_pac=58690,
                             modo_transparente_ativo=True, porta_transparente=58695,
                             dns_pelo_proxy=True, porta_dns=58653,
                             dns_servidores=f"127.0.0.1:{porta_upstream}", acao_padrao="block")
        cfg = Configuracao(texto_regras="meusite.teste +direct\n", preferencias=prefs)
        registro = Registro(dias_retencao=1)
        motor = MotorProxy(cfg, registro)
        motor.iniciar()
        try:
            resposta = await _consultar(58653, "meusite.teste")
            [ip_falso] = dns.ler_enderecos(resposta)
            assert dns.eh_ip_falso(ip_falso)
            backend.fixed_destination = (ip_falso, porta_destino)

            leitor, escritor = await asyncio.open_connection("127.0.0.1", 58695)
            escritor.write(b"GET / HTTP/1.0\r\n\r\n")
            await escritor.drain()
            assert b"pelo nome" in await asyncio.wait_for(leitor.read(-1), timeout=5)

            await asyncio.sleep(0.2)
            entrada = registro.recentes()[-1]
            assert (entrada.host_destino, entrada.acao, entrada.estado) == ("meusite.teste", "direct", "concluida")
            assert "meusite.teste" in upstream.nomes  # resolvido pelo DNS real, não pelo sistema
        finally:
            motor.parar()
            transporte.close()
            servidor_destino.close()
        assert rede.resolvedor is None  # motor parado devolve o resolvedor do sistema

    asyncio.run(cenario())


def test_ip_falso_desconhecido_e_recusado(monkeypatch):
    async def cenario():
        backend = _BackendTransparenteFalso(destino_fixo=("198.18.200.200", 80))
        monkeypatch.setattr(modulo_motor, "_criar_backend_transparente", lambda prefs: backend)
        prefs = Preferencias(porta_socks=58680, porta_http=58681, porta_pac=58690,
                             modo_transparente_ativo=True, porta_transparente=58695,
                             dns_pelo_proxy=True, porta_dns=58653, dns_servidores="127.0.0.1:9",
                             acao_padrao="direct")
        motor = MotorProxy(Configuracao(texto_regras="", preferencias=prefs), Registro(dias_retencao=1))
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", 58695)
            assert await asyncio.wait_for(leitor.read(-1), timeout=5) == b""
        finally:
            motor.parar()

    asyncio.run(cenario())


def test_servidores_do_resolv_conf_ignoram_stub_local(tmp_path, monkeypatch):
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 127.0.0.53\noptions edns0\n", encoding="utf-8")
    real = tmp_path / "real.conf"
    real.write_text("nameserver 192.168.1.1\nnameserver fe80::1%eth0\n", encoding="utf-8")
    caminhos = {"/etc/resolv.conf": resolv, "/run/systemd/resolve/resolv.conf": real}
    original = dns._ler_resolv_conf
    monkeypatch.setattr(dns, "_ler_resolv_conf", lambda caminho: original(str(caminhos[caminho])))
    assert dns._servidores_linux() == ["192.168.1.1", "fe80::1"]


def test_porta_reservada_conta_usos():
    rede.reservar_porta(5000)
    rede.reservar_porta(5000)
    rede.liberar_porta(5000)
    assert rede.porta_propria(5000)
    rede.liberar_porta(5000)
    assert not rede.porta_propria(5000)


def test_sockets_udp_do_windows_suportam_sendto_assincrono():
    """sock_sendto/sock_recvfrom precisam existir no loop padrão do Windows (Proactor)."""
    async def cenario():
        loop = asyncio.get_running_loop()
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for s in (a, b):
            s.setblocking(False)
            s.bind(("127.0.0.1", 0))
        try:
            await loop.sock_sendto(a, b"oi", b.getsockname())
            dados, _ = await asyncio.wait_for(loop.sock_recvfrom(b, 10), timeout=2)
            assert dados == b"oi"
        finally:
            a.close()
            b.close()

    asyncio.run(cenario())


def test_hosts_do_teste_de_conectividade_do_networkmanager(tmp_path):
    pasta = tmp_path / "conf.d"
    pasta.mkdir()
    (pasta / "20-connectivity-fedora.conf").write_text(
        "[connectivity]\nuri=http://fedoraproject.org/static/hotspot.txt\ninterval=300\n", encoding="utf-8")
    principal = tmp_path / "NetworkManager.conf"
    # "uri=" fora da seção [connectivity] não conta
    principal.write_text("[main]\nuri=http://outro.exemplo/\n"
                         "[connectivity]\nuri=http://nmcheck.gnome.org/check_network_status.txt\n", encoding="utf-8")

    hosts = dns.hosts_teste_conectividade((str(principal), str(pasta), str(tmp_path / "nao-existe")))

    assert hosts == ["nmcheck.gnome.org", "fedoraproject.org"]


class _UpstreamSoIpv6(_UpstreamFalso):
    """Responde só as consultas AAAA; as consultas A ficam sem resposta, ou recebem uma resposta
    vazia com `a_vazio`."""
    def __init__(self, a_vazio: bool):
        super().__init__()
        self.a_vazio = a_vazio

    def datagram_received(self, dados, endereco):
        lida = dns.ler_consulta(dados)
        self.nomes.append(lida.nome)
        if lida.tipo == dns.TIPO_AAAA:
            self.transporte.sendto(dns.montar_resposta(
                lida, respostas=[(dns.TIPO_AAAA, ipaddress.IPv6Address("2001:db8::1").packed)]), endereco)
        elif self.a_vazio:
            self.transporte.sendto(dns.montar_resposta(lida), endereco)


def test_resolvedor_real_so_usa_ipv6_quando_o_nome_nao_tem_ipv4():
    async def cenario(a_vazio: bool) -> list[str]:
        upstream = _UpstreamSoIpv6(a_vazio)
        transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: upstream, local_addr=("127.0.0.1", 0))
        try:
            real = dns.ResolvedorReal([f"127.0.0.1:{transporte.get_extra_info('sockname')[1]}"], timeout=0.2)
            return await real.resolver("so-ipv6.exemplo")
        finally:
            transporte.close()

    # o servidor disse que não há IPv4: vale o IPv6
    assert asyncio.run(cenario(a_vazio=True)) == ["2001:db8::1"]
    # a consulta A ficou sem resposta: não se sabe se há IPv4, então nada de IPv6
    assert asyncio.run(cenario(a_vazio=False)) == []


def test_resolvedor_real_rele_os_servidores_do_sistema(monkeypatch):
    monkeypatch.setattr(dns, "INTERVALO_RELEITURA_SERVIDORES", 0.0)

    async def cenario():
        transporte, upstream, porta = await _iniciar_upstream()
        try:
            # o servidor da rede anterior não responde mais; o sistema já lista o da rede nova
            real = dns.ResolvedorReal(["127.0.0.1:9"], timeout=0.2,
                                      fornecedor_servidores=lambda: [f"127.0.0.1:{porta}"])
            assert await real.resolver("x.com") == ["127.0.0.1"]
            assert real.servidores == [f"127.0.0.1:{porta}"]
        finally:
            transporte.close()

    asyncio.run(cenario())


def test_limpar_cache_sistema_usa_o_comando_de_cada_sistema(monkeypatch):
    from tests.conftest import LIMPAR_CACHE_SISTEMA as real
    comandos = []
    monkeypatch.setattr(dns.subprocess, "run", lambda comando, **_: comandos.append(comando))

    monkeypatch.setattr(dns.platform, "system", lambda: "Windows")
    real()
    monkeypatch.setattr(dns.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dns, "which", lambda nome: f"/usr/bin/{nome}")
    real()
    monkeypatch.setattr(dns, "which", lambda nome: None)
    real()

    assert comandos == [["ipconfig", "/flushdns"], ["resolvectl", "flush-caches"]]
