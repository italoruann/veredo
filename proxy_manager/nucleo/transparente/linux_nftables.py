"""Modo transparente no Linux.

Uma tabela própria do nftables redireciona o TCP/IPv4 de saída para a porta transparente; o
destino original é recuperado com SO_ORIGINAL_DST. QUIC e TCP sobre IPv6 são rejeitados para
que os aplicativos voltem ao TCP/IPv4. Desligar é só apagar a tabela.

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
_SO_ORIGINAL_DST = 80  # não existe em socket.*


def suportado() -> bool:
    return platform.system() == "Linux" and which("nft") is not None


def destino_original(escritor: asyncio.StreamWriter) -> Optional[tuple[str, int]]:
    """Destino original de uma conexão redirecionada, lido do conntrack pelo socket aceito."""
    sock = escritor.get_extra_info("socket")
    if sock is None:
        return None
    try:
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
    def __init__(self, porta_transparente: int):
        self.porta_transparente = porta_transparente
        self._ativo = False

    def resolver_destino(self, escritor: asyncio.StreamWriter, porta_origem: int) -> Optional[tuple[str, int]]:
        # A porta de origem só é necessária no Windows.
        return destino_original(escritor)

    def iniciar(self) -> tuple[bool, str]:
        if os.geteuid() != 0:
            return False, ("O modo transparente no Linux exige root (para configurar o "
                           "nftables). Rode o Veredo com sudo/pkexec.")
        if which("nft") is None:
            return False, ("Comando 'nft' não encontrado. Instale o nftables (Ubuntu/Debian: "
                           "sudo apt install nftables; Fedora: sudo dnf install nftables).")

        # Remove a tabela que possa ter sobrado de uma execução interrompida.
        _apagar_tabela()
        uid = str(os.geteuid())
        try:
            _executar(["nft", "add", "table", "inet", NOME_TABELA])
            _executar(["nft", "add", "chain", "inet", NOME_TABELA, "output",
                       "{", "type", "nat", "hook", "output", "priority", "-100", ";", "}"])
            # O tráfego do próprio app não é redirecionado, senão entraria em loop.
            _executar(["nft", "add", "rule", "inet", NOME_TABELA, "output",
                       "meta", "skuid", uid, "return"])
            _executar(["nft", "add", "rule", "inet", NOME_TABELA, "output",
                       "ip", "daddr", "127.0.0.0/8", "return"])
            _executar(["nft", "add", "rule", "inet", NOME_TABELA, "output",
                       "meta", "nfproto", "ipv4", "meta", "l4proto", "tcp",
                       "redirect", "to", f":{self.porta_transparente}"])

            # Só TCP/IPv4 é interceptado. QUIC e IPv6 são rejeitados (não descartados) para que
            # o navegador volte na hora para TCP/IPv4 em vez de escapar do proxy.
            _executar(["nft", "add", "chain", "inet", NOME_TABELA, "block_unsupported",
                       "{", "type", "filter", "hook", "output", "priority", "0", ";", "}"])
            _executar(["nft", "add", "rule", "inet", NOME_TABELA, "block_unsupported",
                       "meta", "skuid", uid, "return"])
            _executar(["nft", "add", "rule", "inet", NOME_TABELA, "block_unsupported",
                       "oifname", "lo", "return"])
            _executar(["nft", "add", "rule", "inet", NOME_TABELA, "block_unsupported",
                       "udp", "dport", "443", "reject"])
            _executar(["nft", "add", "rule", "inet", NOME_TABELA, "block_unsupported",
                       "meta", "nfproto", "ipv6", "meta", "l4proto", "tcp", "reject", "with", "tcp", "reset"])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            _apagar_tabela()
            return False, f"Falha ao aplicar regras de nftables: {_erro_comando(exc)}"

        self._ativo = True
        return True, f"Modo transparente ativo, redirecionando TCP para 127.0.0.1:{self.porta_transparente}."

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
