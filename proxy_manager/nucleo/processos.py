"""Descobre qual processo abriu uma conexão local, consultando a tabela de conexões do sistema."""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

import psutil


@dataclass
class InfoProcesso:
    pid: int = -1
    nome: str = "desconhecido"
    caminho: str = ""


DESCONHECIDO = InfoProcesso()

# Cache LRU por PID. O create_time() guardado junto detecta PIDs reaproveitados pelo sistema.
_MAX_CACHE = 512
_cache_processos: OrderedDict[int, tuple[float, InfoProcesso]] = OrderedDict()

# Ler a tabela de conexões do sistema custa dezenas de milissegundos com muitos sockets abertos, e
# cada conexão nova esperava a sua leitura antes de seguir. Uma leitura que começou depois do
# pedido já contém a conexão dele, então serve a todos os pedidos feitos até ali: numa rajada (o
# navegador abre dezenas de conexões de uma vez) a tabela é lida duas ou três vezes, não uma por
# conexão.
_trava_tabelas = threading.Lock()
_tabelas: dict[str, tuple[float, list]] = {}  # tipo -> (início da leitura, conexões)


def _conexoes(tipo: str, pedido_em: float) -> list:
    with _trava_tabelas:
        lida = _tabelas.get(tipo)
        if lida is not None and lida[0] > pedido_em:
            return lida[1]
        inicio = time.perf_counter()
        conexoes = psutil.net_connections(kind=tipo)
        _tabelas[tipo] = (inicio, conexoes)
        return conexoes


def processo_da_conexao(ip_origem: str, porta_origem: int, porta_escuta: int,
                        destino_original: Optional[tuple[str, int]] = None) -> InfoProcesso:
    """`ip_origem`/`porta_origem` são o lado do cliente; `porta_escuta` é a nossa porta local.

    No modo transparente o socket do aplicativo continua apontando para o destino original
    (o redirecionamento é invisível para ele), por isso `destino_original` também é aceito."""
    try:
        conexoes = _conexoes("tcp", time.perf_counter())
    except (psutil.AccessDenied, PermissionError):
        return DESCONHECIDO

    pid_proprio = os.getpid()
    for c in conexoes:
        if not c.laddr or not c.raddr or not c.pid or c.pid == pid_proprio:
            continue
        if c.laddr.port != porta_origem:
            continue
        if c.raddr.port == porta_escuta:
            return _resolver_processo(c.pid)
        if (destino_original is not None and c.raddr.port == destino_original[1]
            and _mesmo_ip(c.raddr.ip, destino_original[0])):
            return _resolver_processo(c.pid)
    return DESCONHECIDO


def processo_udp(porta_origem: int) -> InfoProcesso:
    """Processo dono do socket UDP local `porta_origem` (UDP não tem conexão: só a porta)."""
    try:
        conexoes = _conexoes("udp", time.perf_counter())
    except (psutil.AccessDenied, PermissionError):
        return DESCONHECIDO
    pid_proprio = os.getpid()
    for c in conexoes:
        if c.laddr and c.laddr.port == porta_origem and c.pid and c.pid != pid_proprio:
            return _resolver_processo(c.pid)
    return DESCONHECIDO


def _mesmo_ip(a: str, b: str) -> bool:
    # Sockets IPv6 dual-stack mostram destinos IPv4 como "::ffff:1.2.3.4".
    return a.removeprefix("::ffff:") == b.removeprefix("::ffff:")


def _resolver_processo(pid: int) -> InfoProcesso:
    em_cache = _cache_processos.get(pid)
    if em_cache is not None:
        criado_em, info = em_cache
        try:
            mesmo_processo = psutil.Process(pid).create_time() == criado_em
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            mesmo_processo = False
        if mesmo_processo:
            _cache_processos.move_to_end(pid)
            return info
        _cache_processos.pop(pid, None)

    try:
        proc = psutil.Process(pid)
        criado_em = proc.create_time()
        info = InfoProcesso(pid=pid, nome=proc.name(), caminho=_executavel(proc))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return InfoProcesso(pid=pid, nome=f"pid {pid}", caminho="")

    _cache_processos[pid] = (criado_em, info)
    if len(_cache_processos) > _MAX_CACHE:
        _cache_processos.popitem(last=False)
    return info


def _executavel(proc: psutil.Process) -> str:
    try:
        return proc.exe()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return ""


def limpar_cache() -> None:
    _cache_processos.clear()
    with _trava_tabelas:
        _tabelas.clear()
