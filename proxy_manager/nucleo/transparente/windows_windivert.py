"""Modo transparente no Windows.

O WinDivert (via pydivert) captura os pacotes TCP de saída e os reescreve para a porta
transparente local, guardando o destino original pela porta de origem. Com o DNS pelo proxy,
as consultas UDP à porta 53 são reescritas da mesma forma para o servidor DNS local. QUIC (UDP
443) é descartado para que os navegadores usem TCP; na prevenção de vazamento, todo UDP de saída.
Com "UDP pelas regras", cada datagrama de saída é entregue ao motor (`ao_receber_udp`) e as
respostas voltam injetadas como pacotes de entrada.

Exige administrador e o pacote pydivert; nada disso é verificado antes de iniciar()."""
from __future__ import annotations

import ipaddress
import platform
import socket
import struct
import threading
from typing import Callable, Optional

from .. import rede


def suportado() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import pydivert  # noqa: F401
    except ImportError:
        return False
    return True


def _loopback(endereco: str) -> str:
    return "::1" if ":" in endereco else "127.0.0.1"


def _roteavel(endereco: str) -> bool:
    """Multicast e broadcast (mDNS, SSDP, DHCP) ficam na rede local, fora das regras."""
    try:
        ip = ipaddress.ip_address(endereco)
    except ValueError:
        return False
    return not (ip.is_multicast or endereco == "255.255.255.255")


def montar_pacote_udp(origem: tuple[str, int], destino: tuple[str, int], dados: bytes) -> bytes:
    """Pacote IP+UDP cru; os checksums são calculados pelo WinDivert ao enviar."""
    udp = struct.pack("!HHHH", origem[1], destino[1], 8 + len(dados), 0) + dados
    if ":" in origem[0]:
        return (struct.pack("!IHBB", 6 << 28, len(udp), 17, 64) +
                socket.inet_pton(socket.AF_INET6, origem[0]) + socket.inet_pton(socket.AF_INET6, destino[0]) + udp)
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                       socket.inet_aton(origem[0]), socket.inet_aton(destino[0])) + udp


# (origem, destino, dados, responder): chamado na thread de captura.
AoReceberUdp = Callable[[tuple[str, int], tuple[str, int], bytes, Callable[[bytes], None]], None]


class ModoTransparenteWindows:
    def __init__(self, porta_transparente: int, porta_dns: Optional[int] = None,
                 bloquear_todo_udp: bool = False, capturar_udp: bool = False):
        self.porta_transparente = porta_transparente
        self.porta_dns = porta_dns
        self.bloquear_todo_udp = bloquear_todo_udp
        self.capturar_udp = capturar_udp
        self.ao_receber_udp: Optional[AoReceberUdp] = None
        self._thread: Optional[threading.Thread] = None
        self._evento_parar = threading.Event()
        self._handle = None
        self._trava = threading.Lock()
        # porta de origem -> destino original
        self._tabela_nat: dict[int, tuple[str, int]] = {}
        self._tabela_nat_udp: dict[int, tuple[str, int]] = {}

    def filtro(self) -> str:
        filtro_tcp = (
            f"(tcp and ("
            f"(outbound and !loopback and tcp.DstPort != {self.porta_transparente}) or "
            f"(loopback and tcp.SrcPort == {self.porta_transparente})"
            f"))"
        )
        if self.bloquear_todo_udp or self.capturar_udp:
            udp_saida = "outbound and !loopback"
        else:
            portas = ["udp.DstPort == 443"] + (["udp.DstPort == 53"] if self.porta_dns else [])
            udp_saida = f"outbound and !loopback and ({' or '.join(portas)})"
        partes_udp = [f"({udp_saida})"]
        if self.porta_dns:
            partes_udp.append(f"(loopback and udp.SrcPort == {self.porta_dns})")
        return f"{filtro_tcp} or (udp and ({' or '.join(partes_udp)}))"

    def iniciar(self) -> tuple[bool, str]:
        try:
            import pydivert
        except ImportError:
            return False, "Pacote 'pydivert' não instalado. Rode: uv sync"

        try:
            handle = pydivert.WinDivert(self.filtro())
            handle.open()
        except OSError as exc:
            return False, (f"Falha ao abrir o driver WinDivert: {exc}. Confirme que está rodando "
                           "como Administrador e que o pacote pydivert está instalado corretamente.")

        self._handle = handle
        self._evento_parar.clear()
        with self._trava:
            self._tabela_nat.clear()
            self._tabela_nat_udp.clear()
        self._thread = threading.Thread(target=self._loop_captura, daemon=True,
                                        name="windivert-transparent")
        self._thread.start()
        return True, f"Modo transparente ativo, redirecionando TCP para a porta {self.porta_transparente}."

    def parar(self) -> tuple[bool, str]:
        self._evento_parar.set()
        if self._handle is not None:
            try:
                self._handle.close()  # desbloqueia o recv() pendente na thread de captura
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._handle = None
        with self._trava:
            self._tabela_nat.clear()
            self._tabela_nat_udp.clear()
        return True, "Modo transparente desligado."

    def resolver_destino(self, escritor, porta_origem: int) -> Optional[tuple[str, int]]:
        with self._trava:
            return self._tabela_nat.get(porta_origem)

    def _loop_captura(self) -> None:
        while not self._evento_parar.is_set():
            try:
                pacote = self._handle.recv()
            except OSError:
                break  # handle fechado por stop()

            if getattr(pacote, "udp", None) is not None:
                decisao = self._traduzir_udp(pacote)
                if decisao is None:
                    continue  # descartado
                alterado = decisao
            else:
                alterado = self._traduzir(pacote)
            if alterado:
                # Depois de reescrever endereço/porta, os checksums precisam ser recalculados.
                pacote.recalculate_checksums()

            try:
                self._handle.send(pacote)
            except OSError:
                break

    def _traduzir(self, pacote) -> bool:
        """Reescreve endereço/porta quando necessário; devolve True se alterou o pacote."""
        tcp = pacote.tcp
        if tcp is None:
            return False

        if pacote.is_outbound:
            with self._trava:
                rastreado = self._tabela_nat.get(tcp.src_port)
            eh_syn_novo = tcp.syn and not tcp.ack
            if rastreado is None and eh_syn_novo and rede.porta_propria(tcp.src_port):
                return False  # conexão do próprio motor: capturá-la de novo criaria um loop
            if rastreado is None and eh_syn_novo:
                rastreado = (pacote.dst_addr, pacote.dst_port)
                with self._trava:
                    self._tabela_nat[tcp.src_port] = rastreado
            if rastreado is not None:
                pacote.dst_addr = _loopback(rastreado[0])
                pacote.dst_port = self.porta_transparente
                if tcp.fin or tcp.rst:
                    with self._trava:
                        self._tabela_nat.pop(tcp.src_port, None)
                return True
        else:
            # Resposta da porta transparente local, voltando para o processo de origem.
            if pacote.src_addr in ("127.0.0.1", "::1") and tcp.src_port == self.porta_transparente:
                with self._trava:
                    original = self._tabela_nat.get(tcp.dst_port)
                if original is not None:
                    pacote.src_addr, tcp.src_port = original
                    return True
        return False

    def _traduzir_udp(self, pacote) -> Optional[bool]:
        """True se alterou, False se o pacote segue intacto, None para descartá-lo."""
        udp = pacote.udp
        if self.porta_dns and udp.src_port == self.porta_dns and pacote.src_addr in ("127.0.0.1", "::1"):
            # Resposta do servidor DNS local: volta a parecer que veio do servidor original.
            with self._trava:
                original = self._tabela_nat_udp.get(udp.dst_port)
            if original is None:
                return False
            pacote.src_addr, udp.src_port = original
            return True
        if not pacote.is_outbound or rede.porta_propria(udp.src_port):
            return False  # consultas do próprio motor aos servidores DNS reais
        if self.porta_dns and udp.dst_port == 53:
            with self._trava:
                self._tabela_nat_udp[udp.src_port] = (pacote.dst_addr, 53)
            pacote.dst_addr = _loopback(pacote.dst_addr)
            udp.dst_port = self.porta_dns
            return True
        if self.capturar_udp:
            if not _roteavel(pacote.dst_addr):
                return False
            if self.ao_receber_udp is not None:
                origem = (pacote.src_addr, udp.src_port)
                destino = (pacote.dst_addr, udp.dst_port)
                interface = getattr(pacote, "interface", None)
                self.ao_receber_udp(origem, destino, bytes(pacote.payload or b""),
                                    lambda dados: self.injetar_udp(destino, origem, dados, interface))
        return None

    def injetar_udp(self, origem: tuple[str, int], destino: tuple[str, int], dados: bytes, interface) -> None:
        """Entrega ao programa uma resposta como se tivesse vindo de `origem` pela rede."""
        handle = self._handle
        if handle is None:
            return
        import pydivert

        pacote = pydivert.Packet(montar_pacote_udp(origem, destino, dados), interface, pydivert.Direction.INBOUND)
        try:
            handle.send(pacote, recalculate_checksum=True)
        except OSError:
            pass
