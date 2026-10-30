"""UDP pelas regras: cada par (programa, destino) vira um fluxo que vai direto, é bloqueado ou
passa por um proxy SOCKS5 (UDP ASSOCIATE). HTTP, HTTPS e SOCKS4 não transportam UDP.

Os datagramas chegam pela porta SOCKS local (UDP ASSOCIATE do próprio programa) ou pelo modo
transparente; quem entrega o datagrama também diz como devolver as respostas (`responder`)."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from . import rede, socks5
from .cliente_proxy import Rota

# Fluxos sem tráfego por esse tempo são fechados.
OCIOSIDADE_FLUXO = 120.0
TAMANHO_MAXIMO = 65535

Responder = Callable[[bytes], None]


class ErroUdp(Exception):
    pass


# -- cabeçalho UDP do SOCKS5 (RFC 1928, seção 7) --------------------------------------------

def montar_cabecalho_socks(host: str, porta: int) -> bytes:
    return b"\x00\x00\x00" + socks5.endereco_socks5(host) + struct.pack("!H", porta)


def ler_cabecalho_socks(dados: bytes) -> Optional[tuple[str, int, bytes]]:
    """(host, porta, carga) de um datagrama SOCKS5; None se fragmentado ou inválido."""
    try:
        if dados[2] != 0:  # fragmentação não é suportada (quase nenhum cliente usa)
            return None
        atyp = dados[3]
        if atyp == socks5.ATYP_IPV4:
            host, pos = str(ipaddress.IPv4Address(dados[4:8])), 8
        elif atyp == socks5.ATYP_IPV6:
            host, pos = str(ipaddress.IPv6Address(dados[4:20])), 20
        elif atyp == socks5.ATYP_DOMAIN:
            tamanho = dados[4]
            host, pos = dados[5:5 + tamanho].decode("utf-8", errors="replace"), 5 + tamanho
        else:
            return None
        porta = struct.unpack("!H", dados[pos:pos + 2])[0]
        return host, porta, dados[pos + 2:]
    except (IndexError, struct.error, ValueError):
        return None


# -- saídas de um fluxo -------------------------------------------------------------------------

class ProtocoloDatagramas(asyncio.DatagramProtocol):
    def __init__(self, ao_receber: Callable[[bytes, tuple], None]):
        self.ao_receber = ao_receber

    def datagram_received(self, dados: bytes, endereco) -> None:
        self.ao_receber(dados, endereco)

    def error_received(self, exc) -> None:
        pass


async def _socket_udp_proprio(ip_destino: str) -> socket.socket:
    """Socket UDP com a porta anunciada como do motor (o WinDivert não o captura de novo)."""
    familia = socket.AF_INET6 if ":" in ip_destino else socket.AF_INET
    sock = socket.socket(familia, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("::" if familia == socket.AF_INET6 else "0.0.0.0", 0))
    rede.reservar_porta(sock.getsockname()[1])
    return sock


class SaidaDireta:
    def __init__(self, transporte: asyncio.DatagramTransport, porta_local: int, endereco: tuple):
        self.transporte = transporte
        self.porta_local = porta_local
        self.endereco = endereco

    @staticmethod
    async def abrir(host: str, porta: int, ao_receber: Callable[[bytes], None]) -> SaidaDireta:
        enderecos = await rede.resolver(host)
        if not enderecos:
            raise ErroUdp(f"não foi possível resolver {host}")
        sock = await _socket_udp_proprio(enderecos[0])
        porta_local = sock.getsockname()[1]
        try:
            transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: ProtocoloDatagramas(lambda dados, _origem: ao_receber(dados)), sock=sock)
        except BaseException:
            rede.liberar_porta(porta_local)
            sock.close()
            raise
        return SaidaDireta(transporte, porta_local, rede.endereco_socket(enderecos[0], porta))

    def enviar(self, dados: bytes) -> None:
        self.transporte.sendto(dados, self.endereco)

    def fechar(self) -> None:
        self.transporte.close()
        rede.liberar_porta(self.porta_local)


class SaidaSocks5:
    """UDP ASSOCIATE num proxy SOCKS5. A associação vive enquanto a conexão TCP de controle
    estiver aberta."""

    def __init__(self, transporte, porta_local: int, relay: tuple, controle: asyncio.StreamWriter,
                 host: str, porta: int):
        self.transporte = transporte
        self.porta_local = porta_local
        self.relay = relay
        self.controle = controle
        self.cabecalho = montar_cabecalho_socks(host, porta)

    @staticmethod
    async def abrir(perfil, host: str, porta: int, ao_receber: Callable[[bytes], None],
                    timeout: float = 15.0) -> SaidaSocks5:
        leitor, escritor = await rede.abrir_conexao(perfil.host, perfil.port, timeout=timeout)
        try:
            host_relay, porta_relay = await socks5.negociar_cliente(
                leitor, escritor, "0.0.0.0", 0, perfil.usuario, perfil.senha, timeout,
                comando=socks5.CMD_UDP_ASSOCIATE)
            if not host_relay or (_eh_ip(host_relay) and ipaddress.ip_address(host_relay).is_unspecified):
                host_relay = perfil.host  # "0.0.0.0" quer dizer: o mesmo endereço do proxy
            enderecos = await rede.resolver(host_relay)
            if not enderecos:
                raise ErroUdp(f"não foi possível resolver o relay UDP {host_relay}")
            ip_relay = enderecos[0]
            sock = await _socket_udp_proprio(ip_relay)
        except BaseException:
            escritor.close()
            raise
        porta_local = sock.getsockname()[1]

        def receber(dados: bytes, _origem) -> None:
            lido = ler_cabecalho_socks(dados)
            if lido is not None:
                ao_receber(lido[2])

        transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: ProtocoloDatagramas(receber), sock=sock)
        saida = SaidaSocks5(transporte, porta_local, rede.endereco_socket(ip_relay, porta_relay),
                            escritor, host, porta)
        # Se o proxy encerrar a associação (fechando o TCP), a saída para de funcionar.
        saida.vigia = asyncio.ensure_future(_esperar_fechamento(leitor, saida))
        return saida

    def enviar(self, dados: bytes) -> None:
        self.transporte.sendto(self.cabecalho + dados, self.relay)

    def fechar(self) -> None:
        self.transporte.close()
        self.controle.close()
        rede.liberar_porta(self.porta_local)
        vigia = getattr(self, "vigia", None)
        if vigia is not None:
            vigia.cancel()


def _eh_ip(texto: str) -> bool:
    try:
        ipaddress.ip_address(texto)
        return True
    except ValueError:
        return False


async def _esperar_fechamento(leitor: asyncio.StreamReader, saida: SaidaSocks5) -> None:
    try:
        await leitor.read()
    except Exception:
        pass
    saida.transporte.close()


# -- fluxos ----------------------------------------------------------------------------------

@dataclass
class Decisao:
    """O que fazer com um fluxo, decidido pelo motor."""
    acao: str  # "direct", "block" ou "proxy"
    rota: Optional[Rota] = None
    id_entrada: str = ""


@dataclass
class Fluxo:
    decisao: Decisao
    responder: Responder
    saida: Optional[SaidaDireta | SaidaSocks5] = None
    pendentes: list[bytes] = field(default_factory=list)
    ultimo_uso: float = field(default_factory=time.monotonic)
    enviados: int = 0
    recebidos: int = 0
    abrindo: bool = False
    erro: str = ""


# (chave do cliente, host, porta) -> Decisao
Decidir = Callable[[object, str, int], Awaitable[Decisao]]
# (id da entrada, mudanças)
Finalizar = Callable[[str, dict], None]
Contar = Callable[[int, bool, bool], None]


class GerenciadorUdp:
    def __init__(self, decidir: Decidir, finalizar: Finalizar, contar: Contar):
        self.decidir = decidir
        self.finalizar = finalizar
        self.contar = contar
        self._fluxos: dict[tuple, Fluxo] = {}
        self._decidindo: dict[tuple, asyncio.Future] = {}
        self._limpeza: Optional[asyncio.Task] = None

    def iniciar(self) -> None:
        if self._limpeza is None:
            self._limpeza = asyncio.ensure_future(self._limpar_periodicamente())

    def parar(self) -> None:
        if self._limpeza is not None:
            self._limpeza.cancel()
            self._limpeza = None
        for chave in list(self._fluxos):
            self._fechar(chave)

    @property
    def fluxos_ativos(self) -> int:
        return len(self._fluxos)

    async def datagrama(self, cliente: object, host: str, porta: int, dados: bytes,
                        responder: Responder) -> None:
        """Encaminha um datagrama do programa `cliente` para `host:porta`."""
        chave = (cliente, host, porta)
        fluxo = self._fluxos.get(chave)
        if fluxo is None:
            futuro = self._decidindo.get(chave)
            if futuro is None:
                futuro = asyncio.get_running_loop().create_future()
                self._decidindo[chave] = futuro
                try:
                    decisao = await self.decidir(cliente, host, porta)
                    fluxo = Fluxo(decisao=decisao, responder=responder)
                    self._fluxos[chave] = fluxo
                    futuro.set_result(fluxo)
                except BaseException as exc:
                    futuro.set_exception(exc)
                    raise
                finally:
                    self._decidindo.pop(chave, None)
            else:
                fluxo = await asyncio.shield(futuro)
        fluxo.ultimo_uso = time.monotonic()

        if fluxo.decisao.acao == "block" or fluxo.erro:
            return
        if fluxo.saida is None:
            fluxo.pendentes.append(dados)
            if not fluxo.abrindo:
                fluxo.abrindo = True
                await self._abrir_saida(chave, fluxo, host, porta)
            return
        self._enviar(fluxo, dados)

    def _enviar(self, fluxo: Fluxo, dados: bytes) -> None:
        try:
            fluxo.saida.enviar(dados)
        except OSError:
            return
        fluxo.enviados += len(dados)
        self.contar(len(dados), True, fluxo.decisao.acao == "proxy")

    async def _abrir_saida(self, chave: tuple, fluxo: Fluxo, host: str, porta: int) -> None:
        def receber(dados: bytes) -> None:
            fluxo.ultimo_uso = time.monotonic()
            fluxo.recebidos += len(dados)
            self.contar(len(dados), False, fluxo.decisao.acao == "proxy")
            try:
                fluxo.responder(dados)
            except OSError:
                pass

        try:
            if fluxo.decisao.acao == "direct":
                fluxo.saida = await SaidaDireta.abrir(host, porta, receber)
            else:
                fluxo.saida = await SaidaSocks5.abrir(self._perfil_udp(fluxo.decisao.rota), host, porta, receber)
        except Exception as exc:
            fluxo.erro = str(exc) or type(exc).__name__
            fluxo.pendentes.clear()
            self.finalizar(fluxo.decisao.id_entrada, {"estado": "erro", "erro": fluxo.erro})
            return
        pendentes, fluxo.pendentes = fluxo.pendentes, []
        for dados in pendentes:
            self._enviar(fluxo, dados)

    @staticmethod
    def _perfil_udp(rota: Optional[Rota]):
        if rota is None or not rota.perfis:
            raise ErroUdp("nenhum perfil de proxy disponível para esta regra")
        for perfil in rota.perfis:
            if perfil.type == "socks5":
                return perfil
        raise ErroUdp(f"'{rota.nome}' não transporta UDP (só proxies SOCKS5 transportam)")

    def _fechar(self, chave: tuple) -> None:
        fluxo = self._fluxos.pop(chave, None)
        if fluxo is None:
            return
        if fluxo.saida is not None:
            fluxo.saida.fechar()
        if fluxo.decisao.id_entrada and not fluxo.erro and fluxo.decisao.acao != "block":
            self.finalizar(fluxo.decisao.id_entrada, {"estado": "concluida", "bytes_enviados": fluxo.enviados,
                                                      "bytes_recebidos": fluxo.recebidos})

    def fechar_cliente(self, cliente: object) -> None:
        """Fecha os fluxos de um cliente (fim de uma associação SOCKS5 do programa)."""
        for chave in [c for c in self._fluxos if c[0] == cliente]:
            self._fechar(chave)

    async def _limpar_periodicamente(self) -> None:
        while True:
            await asyncio.sleep(OCIOSIDADE_FLUXO / 4)
            limite = time.monotonic() - OCIOSIDADE_FLUXO
            for chave in [c for c, f in self._fluxos.items() if f.ultimo_uso < limite]:
                self._fechar(chave)
