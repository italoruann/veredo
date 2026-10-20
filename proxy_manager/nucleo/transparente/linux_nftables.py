"""Modo transparente no Linux.

Uma tabela própria do nftables redireciona o TCP de saída (IPv4 e IPv6) para a porta
transparente; o destino original é recuperado com SO_ORIGINAL_DST. Com o DNS pelo proxy, o UDP
à porta 53 vai para o servidor DNS local, inclusive o que iria ao systemd-resolved. QUIC é
rejeitado para que os aplicativos usem TCP; na prevenção de vazamento, todo UDP de saída.
Desligar é só apagar a tabela.

Exige root. iniciar()/parar() não levantam exceção: devolvem (ok, mensagem)."""
from __future__ import annotations

import asyncio
import os
import platform
import socket
import struct
import subprocess
from shutil import which
from typing import Optional

NOME_TABELA = "proxymgr"
_SOL_IP = socket.SOL_IP if hasattr(socket, "SOL_IP") else 0
_SOL_IPV6 = getattr(socket, "SOL_IPV6", 41)
_SO_ORIGINAL_DST = 80  # SO_ORIGINAL_DST e IP6T_SO_ORIGINAL_DST; não existem em socket.*


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


class ModoTransparenteLinux:
    def __init__(self, porta_transparente: int, porta_dns: Optional[int] = None,
                 bloquear_todo_udp: bool = False):
        self.porta_transparente = porta_transparente
        self.porta_dns = porta_dns
        self.bloquear_todo_udp = bloquear_todo_udp
        self._ativo = False

    def resolver_destino(self, escritor: asyncio.StreamWriter, porta_origem: int) -> Optional[tuple[str, int]]:
        # A porta de origem só é necessária no Windows.
        return destino_original(escritor)

    def comandos(self, uid: str) -> list[list[str]]:
        regra = ["nft", "add", "rule", "inet", NOME_TABELA]
        comandos = [
            ["nft", "add", "table", "inet", NOME_TABELA],
            ["nft", "add", "chain", "inet", NOME_TABELA, "output",
             "{", "type", "nat", "hook", "output", "priority", "-100", ";", "}"],
            # O tráfego do próprio app não é redirecionado, senão entraria em loop.
            regra + ["output", "meta", "skuid", uid, "return"],
        ]
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
            regra + ["block_unsupported", "meta", "skuid", uid, "return"],
            regra + ["block_unsupported", "oifname", "lo", "return"],
        ]
        if self.bloquear_todo_udp:
            comandos.append(regra + ["block_unsupported", "meta", "l4proto", "udp", "reject"])
        else:
            comandos.append(regra + ["block_unsupported", "udp", "dport", "443", "reject"])
        return comandos

    def iniciar(self) -> tuple[bool, str]:
        if os.geteuid() != 0:
            return False, ("O modo transparente no Linux exige root (para configurar o "
                           "nftables). Rode o Veredo com sudo/pkexec.")
        if which("nft") is None:
            return False, ("Comando 'nft' não encontrado. Instale o nftables (Ubuntu/Debian: "
                           "sudo apt install nftables; Fedora: sudo dnf install nftables).")

        # Remove a tabela que possa ter sobrado de uma execução interrompida.
        _apagar_tabela()
        try:
            for comando in self.comandos(str(os.geteuid())):
                _executar(comando)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            _apagar_tabela()
            return False, f"Falha ao aplicar regras de nftables: {_erro_comando(exc)}"

        self._ativo = True
        return True, f"Modo transparente ativo, redirecionando TCP para a porta {self.porta_transparente}."

    def parar(self) -> tuple[bool, str]:
        if not self._ativo:
            return True, "Modo transparente já estava desligado."
        try:
            _executar(["nft", "delete", "table", "inet", NOME_TABELA])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            self._ativo = False
            return False, f"Modo transparente desligado com avisos (revise o nftables manualmente): {_erro_comando(exc)}"
        self._ativo = False
        return True, "Modo transparente desligado; tabela nftables removida."
