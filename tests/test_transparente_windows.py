"""Tradução de pacotes do modo transparente no Windows, com pacotes falsos (sem pydivert)."""
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
