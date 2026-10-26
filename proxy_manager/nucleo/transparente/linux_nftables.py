"""Modo transparente no Linux.

Uma tabela própria do nftables redireciona o TCP de saída (IPv4 e IPv6) para a porta
transparente; o destino original é recuperado com SO_ORIGINAL_DST. Com o DNS pelo proxy, o UDP
à porta 53 vai para o servidor DNS local, inclusive o que iria ao systemd-resolved. QUIC é
rejeitado para que os aplicativos usem TCP; na prevenção de vazamento, todo UDP de saída.

Com "UDP pelas regras", o UDP de saída é marcado e devolvido pelo loopback a um socket TPROXY,
que recebe cada datagrama com o destino original (IP_RECVORIGDSTADDR). As respostas saem de
sockets IP_TRANSPARENT vinculados a esse destino, para o programa recebê-las do endereço certo.
Desligar é apagar a tabela e a regra de roteamento da marca.

Exige root. iniciar()/parar() não levantam exceção: devolvem (ok, mensagem)."""
from __future__ import annotations

import asyncio
import os
import platform
import socket
import struct
import subprocess
import threading
from collections import OrderedDict
from shutil import which
from typing import Callable, Optional

from .. import dns

NOME_TABELA = "proxymgr"
_SOL_IP = socket.SOL_IP if hasattr(socket, "SOL_IP") else 0
_SOL_IPV6 = getattr(socket, "SOL_IPV6", 41)
_SO_ORIGINAL_DST = 80  # SO_ORIGINAL_DST e IP6T_SO_ORIGINAL_DST; não existem em socket.*
_IP_TRANSPARENT = 19
_IP_RECVORIGDSTADDR = 20
_IPV6_RECVORIGDSTADDR = 74
_IPV6_TRANSPARENT = 75

# Marca dos pacotes UDP desviados e tabela de roteamento que os entrega ao loopback.
MARCA_UDP = "0x5644"
TABELA_ROTAS = "5644"
_MAX_SOCKETS_RESPOSTA = 256


def suportado() -> bool:
    return platform.system() == "Linux" and which("nft") is not None


def destino_original(escritor: asyncio.StreamWriter) -> Optional[tuple[str, int]]:
    """Destino original de uma conexão redirecionada, lido do conntrack pelo socket aceito."""
    sock = escritor.get_extra_info("socket")
    if sock is None:
        return None
    try:
        if getattr(sock, "family", socket.AF_INET) == socket.AF_INET6:
            bruto = sock.getsockopt(_SOL_IPV6, _SO_ORIGINAL_DST, 28)  # sockaddr_in6
            porta = struct.unpack("!H", bruto[2:4])[0]
            return socket.inet_ntop(socket.AF_INET6, bruto[8:24]), porta
        bruto = sock.getsockopt(_SOL_IP, _SO_ORIGINAL_DST, 16)
    except OSError:
        return None
    port = struct.unpack("!H", bruto[2:4])[0]
    host = socket.inet_ntoa(bruto[4:8])
    return host, port


def _executar(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, timeout=5)


def _erro_comando(exc: Exception) -> str:
    stderr = getattr(exc, "stderr", None)
    if stderr:
        texto = stderr.decode(errors="replace").strip() if isinstance(stderr, bytes) else str(stderr)
        if texto:
            return texto
    return str(exc)


def _apagar_tabela() -> None:
    for familia in ("inet", "ip"):
        subprocess.run(["nft", "delete", "table", familia, NOME_TABELA], capture_output=True, timeout=5)


def _comandos_rotas(acao: str) -> list[list[str]]:
    comandos = []
    for familia, destino in (("-4", "0.0.0.0/0"), ("-6", "::/0")):
        comandos.append(["ip", familia, "rule", acao, "fwmark", MARCA_UDP, "lookup", TABELA_ROTAS])
        comandos.append(["ip", familia, "route", acao, "local", destino, "dev", "lo", "table", TABELA_ROTAS])
    return comandos


def _apagar_rotas() -> None:
    for comando in _comandos_rotas("del"):
        subprocess.run(comando, capture_output=True, timeout=5)


def limpar_regras() -> None:
    """Apaga a tabela e as rotas do modo transparente que tenham sobrado de um processo que caiu."""
    try:
        _apagar_tabela()
        _apagar_rotas()
    except (OSError, subprocess.TimeoutExpired):
        pass  # sem nft/ip instalados não há o que apagar


def _ler_destino_original(ancilares) -> Optional[tuple[str, int]]:
    for nivel, tipo, dados in ancilares:
        if nivel == _SOL_IP and tipo == _IP_RECVORIGDSTADDR:
            return socket.inet_ntoa(dados[4:8]), struct.unpack("!H", dados[2:4])[0]
        if nivel == _SOL_IPV6 and tipo == _IPV6_RECVORIGDSTADDR:
            return socket.inet_ntop(socket.AF_INET6, dados[8:24]), struct.unpack("!H", dados[2:4])[0]
    return None


# (origem, destino, dados, responder): chamado na thread de recepção.
AoReceberUdp = Callable[[tuple[str, int], tuple[str, int], bytes, Callable[[bytes], None]], None]


class ModoTransparenteLinux:
    def __init__(self, porta_transparente: int, porta_dns: Optional[int] = None,
                 bloquear_todo_udp: bool = False, capturar_udp: bool = False):
        self.porta_transparente = porta_transparente
        self.porta_dns = porta_dns
        self.bloquear_todo_udp = bloquear_todo_udp
        self.capturar_udp = capturar_udp
        self.ao_receber_udp: Optional[AoReceberUdp] = None
        self._ativo = False
        self._receptores: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._parando = threading.Event()
        self._trava = threading.Lock()
        # destino original -> socket que responde em nome dele
        self._sockets_resposta: OrderedDict[tuple[str, int], socket.socket] = OrderedDict()

    def resolver_destino(self, escritor: asyncio.StreamWriter, porta_origem: int) -> Optional[tuple[str, int]]:
        # A porta de origem só é necessária no Windows.
        return destino_original(escritor)

    def comandos(self, uid: str) -> list[list[str]]:
        regra = ["nft", "add", "rule", "inet", NOME_TABELA]
        comandos = [
            ["nft", "add", "table", "inet", NOME_TABELA],
            ["nft", "add", "chain", "inet", NOME_TABELA, "output",
             "{", "type", "nat", "hook", "output", "priority", "-100", ";", "}"],
        ]
        if self.porta_dns:
            # O systemd-resolved não é do nosso uid: as consultas dele vêm para o DNS local e os IPs
            # falsos ficam no cache que atende todo o sistema, inclusive o nosso uid (no serviço, o
            # root: NetworkManager, dnf...). Sem este desvio, essas conexões iriam direto a um
            # endereço que não existe. O motor nunca conecta em IP falso, então não há loop.
            comandos.append(regra + ["output", "ip", "daddr", str(dns.REDE_FALSA), "meta", "l4proto", "tcp",
                                     "redirect", "to", f":{self.porta_transparente}"])
        # O tráfego do próprio app não é redirecionado, senão entraria em loop.
        comandos.append(regra + ["output", "meta", "skuid", uid, "return"])
        if self.porta_dns:
            # Antes da exceção de loopback: pega também as consultas ao systemd-resolved (127.0.0.53).
            comandos.append(regra + ["output", "udp", "dport", "53", "redirect", "to", f":{self.porta_dns}"])
        comandos += [
            regra + ["output", "ip", "daddr", "127.0.0.0/8", "return"],
            regra + ["output", "ip6", "daddr", "::1", "return"],
            regra + ["output", "meta", "l4proto", "tcp", "redirect", "to", f":{self.porta_transparente}"],

            # QUIC (ou todo UDP) é rejeitado, não descartado, para que o navegador volte na hora
            # para TCP em vez de escapar do proxy.
            ["nft", "add", "chain", "inet", NOME_TABELA, "block_unsupported",
             "{", "type", "filter", "hook", "output", "priority", "0", ";", "}"],
        ]
        if self.porta_dns and not self.capturar_udp:
            # Sem "UDP pelas regras" ninguém atende UDP a um IP falso: o pacote sairia para a rede
            # e o programa (NTP, jogos, VPN) esperaria o timeout. Recusado, ele falha na hora.
            comandos.append(regra + ["block_unsupported", "ip", "daddr", str(dns.REDE_FALSA),
                                     "meta", "l4proto", "udp", "reject"])
        comandos += [
            regra + ["block_unsupported", "meta", "skuid", uid, "return"],
            regra + ["block_unsupported", "oifname", "lo", "return"],
        ]
        if self.capturar_udp:
            comandos += self._comandos_tproxy(uid)
        elif self.bloquear_todo_udp:
            comandos.append(regra + ["block_unsupported", "meta", "l4proto", "udp", "reject"])
        else:
            comandos.append(regra + ["block_unsupported", "udp", "dport", "443", "reject"])
        return comandos

    def _comandos_tproxy(self, uid: str) -> list[list[str]]:
        regra = ["nft", "add", "rule", "inet", NOME_TABELA]
        porta = str(self.porta_transparente)
        comandos = [
            # Marca o UDP de saída; a cadeia "route" refaz o roteamento e a marca leva ao loopback.
            ["nft", "add", "chain", "inet", NOME_TABELA, "udp_marca",
             "{", "type", "route", "hook", "output", "priority", "mangle", ";", "}"],
        ]
        if self.porta_dns:
            # Como no TCP: UDP a um IP falso é desviado mesmo vindo do nosso uid.
            comandos.append(regra + ["udp_marca", "ip", "daddr", str(dns.REDE_FALSA), "meta", "l4proto", "udp",
                                     "meta", "mark", "set", MARCA_UDP])
        return comandos + [
            regra + ["udp_marca", "meta", "skuid", uid, "return"],
            regra + ["udp_marca", "udp", "dport", "53", "return"],
            regra + ["udp_marca", "ip", "daddr", "{", "127.0.0.0/8,", "224.0.0.0/4,", "255.255.255.255", "}", "return"],
            regra + ["udp_marca", "ip6", "daddr", "{", "::1,", "ff00::/8", "}", "return"],
            regra + ["udp_marca", "meta", "l4proto", "udp", "meta", "mark", "set", MARCA_UDP],
            ["nft", "add", "chain", "inet", NOME_TABELA, "udp_tproxy",
             "{", "type", "filter", "hook", "prerouting", "priority", "mangle", ";", "}"],
            regra + ["udp_tproxy", "meta", "mark", MARCA_UDP, "meta", "nfproto", "ipv4", "meta", "l4proto", "udp",
                     "tproxy", "ip", "to", f"127.0.0.1:{porta}"],
            regra + ["udp_tproxy", "meta", "mark", MARCA_UDP, "meta", "nfproto", "ipv6", "meta", "l4proto", "udp",
                     "tproxy", "ip6", "to", f"[::1]:{porta}"],
        ] + _comandos_rotas("add")

    def iniciar(self) -> tuple[bool, str]:
        if os.geteuid() != 0:
            return False, ("O modo transparente no Linux exige root (para configurar o "
                           "nftables). Rode o Veredo com sudo/pkexec.")
        if which("nft") is None:
            return False, ("Comando 'nft' não encontrado. Instale o nftables (Ubuntu/Debian: "
                           "sudo apt install nftables; Fedora: sudo dnf install nftables).")

        # Remove o que possa ter sobrado de uma execução interrompida.
        _apagar_tabela()
        _apagar_rotas()
        try:
            if self.capturar_udp:
                # Os receptores existem antes das regras: nenhum datagrama desviado se perde.
                self._abrir_receptores()
            for comando in self.comandos(str(os.geteuid())):
                _executar(comando)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            _apagar_tabela()
            _apagar_rotas()
            self._fechar_receptores()
            return False, f"Falha ao aplicar regras de nftables: {_erro_comando(exc)}"

        if self.porta_dns:
            # Respostas reais de antes ficariam no cache e escapariam das regras por domínio.
            # (Ao parar, quem limpa é o motor, em qualquer modo.)
            dns.limpar_cache_sistema()
        self._ativo = True
        return True, f"Modo transparente ativo, redirecionando TCP para a porta {self.porta_transparente}."

    def parar(self) -> tuple[bool, str]:
        if not self._ativo:
            return True, "Modo transparente já estava desligado."
        self._ativo = False
        try:
            _executar(["nft", "delete", "table", "inet", NOME_TABELA])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            return False, f"Modo transparente desligado com avisos (revise o nftables manualmente): {_erro_comando(exc)}"
        finally:
            if self.capturar_udp:
                _apagar_rotas()
                self._fechar_receptores()
        return True, "Modo transparente desligado; tabela nftables removida."

    # -- UDP (TPROXY) ------------------------------------------------------------------------

    def _abrir_receptores(self) -> None:
        self._parando.clear()
        for familia, endereco, nivel, transparente, origdst in (
                (socket.AF_INET, "127.0.0.1", _SOL_IP, _IP_TRANSPARENT, _IP_RECVORIGDSTADDR),
                (socket.AF_INET6, "::1", _SOL_IPV6, _IPV6_TRANSPARENT, _IPV6_RECVORIGDSTADDR)):
            sock = socket.socket(familia, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(nivel, transparente, 1)
                sock.setsockopt(nivel, origdst, 1)
                sock.bind((endereco, self.porta_transparente))
            except OSError:
                sock.close()
                if familia == socket.AF_INET:
                    raise
                continue  # sem IPv6
            sock.settimeout(0.5)
            self._receptores.append(sock)
            thread = threading.Thread(target=self._receber, args=(sock,), daemon=True, name="tproxy-udp")
            thread.start()
            self._threads.append(thread)

    def _fechar_receptores(self) -> None:
        self._parando.set()
        for thread in self._threads:
            thread.join(timeout=2)
        for sock in self._receptores:
            sock.close()
        self._threads.clear()
        self._receptores.clear()
        with self._trava:
            for sock in self._sockets_resposta.values():
                sock.close()
            self._sockets_resposta.clear()

    def _receber(self, sock: socket.socket) -> None:
        while not self._parando.is_set():
            try:
                dados, ancilares, _flags, origem = sock.recvmsg(65535, socket.CMSG_SPACE(28))
            except socket.timeout:
                continue
            except OSError:
                break
            destino = _ler_destino_original(ancilares)
            if destino is None or self.ao_receber_udp is None:
                continue
            origem = (origem[0], origem[1])
            self.ao_receber_udp(origem, destino, dados,
                                lambda resposta, o=origem, d=destino: self.responder_udp(d, o, resposta))

    def responder_udp(self, origem: tuple[str, int], destino: tuple[str, int], dados: bytes) -> None:
        """Envia `dados` ao programa em `destino` como se viessem de `origem` (o destino original)."""
        with self._trava:
            sock = self._sockets_resposta.get(origem)
            if sock is not None:
                self._sockets_resposta.move_to_end(origem)
            else:
                familia = socket.AF_INET6 if ":" in origem[0] else socket.AF_INET
                nivel, opcao = (_SOL_IPV6, _IPV6_TRANSPARENT) if familia == socket.AF_INET6 else (_SOL_IP, _IP_TRANSPARENT)
                sock = socket.socket(familia, socket.SOCK_DGRAM)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.setsockopt(nivel, opcao, 1)
                    sock.bind(origem)
                except OSError:
                    sock.close()
                    return
                self._sockets_resposta[origem] = sock
                if len(self._sockets_resposta) > _MAX_SOCKETS_RESPOSTA:
                    _antigo, velho = self._sockets_resposta.popitem(last=False)
                    velho.close()
        try:
            sock.sendto(dados, destino)
        except OSError:
            pass
