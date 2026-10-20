"""Tradução de pacotes do modo transparente no Windows, com pacotes falsos (sem pydivert)."""
import pytest

from proxy_manager.nucleo.transparente.windows_windivert import ModoTransparenteWindows


class _TcpFalso:
    def __init__(self, src_port, syn=False, ack=False, fin=False, rst=False):
        self.src_port = src_port
        self.syn = syn
        self.ack = ack
        self.fin = fin
        self.rst = rst


class _PacoteFalso:
    def __init__(self, is_outbound, src_addr, dst_addr, dst_port, tcp):
        self.is_outbound = is_outbound
        self.src_addr = src_addr
        self.dst_addr = dst_addr
        self.dst_port = dst_port
        self.tcp = tcp
        self.checksums_recalculated = False

    def recalculate_checksums(self):
        self.checksums_recalculated = True


def test_syn_de_saida_e_redirecionado_e_registrado():
    backend = ModoTransparenteWindows(porta_transparente=58095)
    pacote = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                          dst_port=443, tcp=_TcpFalso(src_port=53211, syn=True, ack=False))

    mudou = backend._traduzir(pacote)

    assert mudou is True
    assert pacote.dst_addr == "127.0.0.1"
    assert pacote.dst_port == 58095
    assert backend.resolver_destino(escritor=None, porta_origem=53211) == ("203.0.113.10", 443)


def test_pacotes_seguintes_do_fluxo_sao_redirecionados():
    backend = ModoTransparenteWindows(porta_transparente=58095)
    syn = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                       dst_port=443, tcp=_TcpFalso(src_port=53211, syn=True))
    backend._traduzir(syn)

    data_packet = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                               dst_port=443, tcp=_TcpFalso(src_port=53211))
    backend._traduzir(data_packet)

    assert data_packet.dst_addr == "127.0.0.1"
    assert data_packet.dst_port == 58095


def test_fluxo_desconhecido_nao_e_alterado():
    """Pacotes de conexões cujo SYN não foi visto não são redirecionados."""
    backend = ModoTransparenteWindows(porta_transparente=58095)
    pacote = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                          dst_port=443, tcp=_TcpFalso(src_port=53211, syn=False, ack=True))

    mudou = backend._traduzir(pacote)

    assert mudou is False
    assert pacote.dst_addr == "203.0.113.10"
    assert pacote.dst_port == 443


def test_resposta_local_tem_origem_restaurada():
    backend = ModoTransparenteWindows(porta_transparente=58095)
    syn = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                       dst_port=443, tcp=_TcpFalso(src_port=53211, syn=True))
    backend._traduzir(syn)

    reply = _PacoteFalso(is_outbound=False, src_addr="127.0.0.1", dst_addr="192.168.1.50",
                         dst_port=53211, tcp=_TcpFalso(src_port=58095))
    reply.tcp.dst_port = 53211
    mudou = backend._traduzir(reply)

    assert mudou is True
    assert reply.src_addr == "203.0.113.10"
    assert reply.tcp.src_port == 443


def test_fluxo_e_esquecido_apos_fin():
    backend = ModoTransparenteWindows(porta_transparente=58095)
    syn = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                       dst_port=443, tcp=_TcpFalso(src_port=53211, syn=True))
    backend._traduzir(syn)
    assert backend.resolver_destino(escritor=None, porta_origem=53211) is not None

    fin = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                       dst_port=443, tcp=_TcpFalso(src_port=53211, fin=True))
    backend._traduzir(fin)

    assert backend.resolver_destino(escritor=None, porta_origem=53211) is None


def test_checksum_recalculado_so_se_pacote_mudou():
    """Reescrever IP/porta invalida os checksums originais — sem recalcular, o pacote pode ser
    descartado silenciosamente por quem o recebe. Só precisa disso quando algo foi alterado."""
    backend = ModoTransparenteWindows(porta_transparente=58095)
    pacote_alterado = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                                   dst_port=443, tcp=_TcpFalso(src_port=53211, syn=True))
    pacote_inalterado = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                                     dst_port=443, tcp=_TcpFalso(src_port=9999, syn=False, ack=True))

    enviados = []

    class _HandleFalso:
        def __init__(self):
            self._queue = [pacote_alterado, pacote_inalterado]

        def recv(self):
            if not self._queue:
                raise OSError("closed")
            return self._queue.pop(0)

        def send(self, pacote):
            enviados.append(pacote)

    backend._handle = _HandleFalso()
    backend._loop_captura()

    assert enviados == [pacote_alterado, pacote_inalterado]
    assert pacote_alterado.checksums_recalculated is True
    assert pacote_inalterado.checksums_recalculated is False


def test_resolver_destino_none_para_porta_desconhecida():
    backend = ModoTransparenteWindows(porta_transparente=58095)
    assert backend.resolver_destino(escritor=None, porta_origem=9999) is None


def test_iniciar_informa_falta_do_pydivert(monkeypatch):
    import builtins

    import_real = builtins.__import__

    def import_falso(nome, *args, **kwargs):
        if nome == "pydivert":
            raise ImportError("no module named pydivert")
        return import_real(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_falso)
    backend = ModoTransparenteWindows(porta_transparente=58095)

    ok, mensagem = backend.iniciar()

    assert ok is False
    assert "pydivert" in mensagem.lower()


class _UdpFalso:
    def __init__(self, src_port, dst_port):
        self.src_port = src_port
        self.dst_port = dst_port


class _PacoteUdpFalso:
    def __init__(self, is_outbound, src_addr, dst_addr, udp):
        self.is_outbound = is_outbound
        self.src_addr = src_addr
        self.dst_addr = dst_addr
        self.udp = udp


def test_consulta_dns_vai_ao_servidor_local_e_resposta_volta_com_endereco_original():
    backend = ModoTransparenteWindows(58095, porta_dns=58053)
    consulta = _PacoteUdpFalso(True, "192.168.1.50", "192.168.1.1", _UdpFalso(61000, 53))
    assert backend._traduzir_udp(consulta) is True
    assert (consulta.dst_addr, consulta.udp.dst_port) == ("127.0.0.1", 58053)

    resposta = _PacoteUdpFalso(True, "127.0.0.1", "192.168.1.50", _UdpFalso(58053, 61000))
    assert backend._traduzir_udp(resposta) is True
    assert (resposta.src_addr, resposta.udp.src_port) == ("192.168.1.1", 53)


def test_dns_ipv6_vai_para_o_loopback_ipv6():
    backend = ModoTransparenteWindows(58095, porta_dns=58053)
    consulta = _PacoteUdpFalso(True, "2001:db8::50", "2001:db8::1", _UdpFalso(61001, 53))
    backend._traduzir_udp(consulta)
    assert consulta.dst_addr == "::1"


def test_quic_e_descartado_e_dns_do_proprio_motor_passa():
    from proxy_manager.nucleo import rede

    backend = ModoTransparenteWindows(58095, porta_dns=58053)
    assert backend._traduzir_udp(_PacoteUdpFalso(True, "192.168.1.50", "142.250.0.1", _UdpFalso(62000, 443))) is None
    rede.reservar_porta(62001)
    try:
        proprio = _PacoteUdpFalso(True, "192.168.1.50", "192.168.1.1", _UdpFalso(62001, 53))
        assert backend._traduzir_udp(proprio) is False
        assert proprio.dst_addr == "192.168.1.1"
    finally:
        rede.liberar_porta(62001)


def test_filtro_captura_so_o_necessario():
    simples = ModoTransparenteWindows(58095).filtro()
    assert "udp.DstPort == 443" in simples and "udp.DstPort == 53" not in simples
    com_dns = ModoTransparenteWindows(58095, porta_dns=58053).filtro()
    assert "udp.DstPort == 53" in com_dns and "udp.SrcPort == 58053" in com_dns
    tudo = ModoTransparenteWindows(58095, porta_dns=58053, bloquear_todo_udp=True).filtro()
    assert "udp.DstPort == 443" not in tudo


def test_syn_do_proprio_motor_nao_e_capturado():
    from proxy_manager.nucleo import rede

    backend = ModoTransparenteWindows(porta_transparente=58095)
    rede.reservar_porta(53300)
    try:
        pacote = _PacoteFalso(is_outbound=True, src_addr="192.168.1.50", dst_addr="203.0.113.10",
                              dst_port=1080, tcp=_TcpFalso(src_port=53300, syn=True))
        assert backend._traduzir(pacote) is False
        assert pacote.dst_addr == "203.0.113.10"
    finally:
        rede.liberar_porta(53300)


def test_udp_pelas_regras_entrega_o_datagrama_ao_motor_e_descarta_o_original():
    recebidos = []
    backend = ModoTransparenteWindows(58095, porta_dns=58053, capturar_udp=True)
    backend.ao_receber_udp = lambda origem, destino, dados, responder: recebidos.append((origem, destino, dados))
    assert "outbound and !loopback)" in backend.filtro()

    pacote = _PacoteUdpFalso(True, "192.168.1.50", "203.0.113.7", _UdpFalso(50000, 3478))
    pacote.payload = b"stun"
    assert backend._traduzir_udp(pacote) is None
    assert recebidos == [(("192.168.1.50", 50000), ("203.0.113.7", 3478), b"stun")]

    # multicast/broadcast da rede local passam sem ir às regras
    mdns = _PacoteUdpFalso(True, "192.168.1.50", "224.0.0.251", _UdpFalso(5353, 5353))
    assert backend._traduzir_udp(mdns) is False
    # DNS continua indo para o servidor local
    consulta = _PacoteUdpFalso(True, "192.168.1.50", "192.168.1.1", _UdpFalso(61000, 53))
    assert backend._traduzir_udp(consulta) is True and consulta.udp.dst_port == 58053


def test_pacote_de_resposta_montado_e_valido():
    pydivert = pytest.importorskip("pydivert")
    from proxy_manager.nucleo.transparente.windows_windivert import montar_pacote_udp

    for origem, destino in ((("203.0.113.7", 3478), ("192.168.1.50", 50000)),
                            (("2001:db8::7", 3478), ("2001:db8::50", 50000))):
        pacote = pydivert.Packet(montar_pacote_udp(origem, destino, b"resposta"), None, pydivert.Direction.INBOUND)
        assert (pacote.src_addr, pacote.src_port) == origem
        assert (pacote.dst_addr, pacote.dst_port) == destino
        assert bytes(pacote.payload) == b"resposta"
