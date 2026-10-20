"""Conexões de saída do próprio Veredo.

No modo transparente do Windows o WinDivert vê todos os pacotes, inclusive os do motor: sem uma
marca, a conexão do motor até o proxy seria capturada de novo e entraria em loop. Cada socket é
vinculado a uma porta local antes do connect() e essa porta fica anunciada aqui até a conexão
se estabelecer, para que o backend deixe o SYN passar."""
from __future__ import annotations

import asyncio
import socket
import threading

_trava = threading.Lock()
_portas_proprias: set[int] = set()


def porta_propria(porta: int) -> bool:
    """True se `porta` é a porta local de uma conexão de saída que o motor está abrindo."""
    with _trava:
        return porta in _portas_proprias


async def abrir_conexao(host: str, porta: int, timeout: float | None = 15.0,
                        **kwargs) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Como `asyncio.open_connection`, com a porta local anunciada durante o connect().

    `kwargs` vai para `open_connection` (ssl, server_hostname...)."""
    return await asyncio.wait_for(_abrir(host, porta, **kwargs), timeout=timeout)


async def _abrir(host: str, porta: int, **kwargs) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, porta, type=socket.SOCK_STREAM)
    ultimo_erro: OSError | None = None
    for familia, tipo, protocolo, _, endereco in infos:
        sock = socket.socket(familia, tipo, protocolo)
        porta_local = 0
        try:
            sock.setblocking(False)
            sock.bind(("::" if familia == socket.AF_INET6 else "0.0.0.0", 0))
            porta_local = sock.getsockname()[1]
            with _trava:
                _portas_proprias.add(porta_local)
            await loop.sock_connect(sock, endereco)
        except OSError as exc:
            sock.close()
            ultimo_erro = exc
            continue
        except BaseException:
            sock.close()
            raise
        finally:
            with _trava:
                _portas_proprias.discard(porta_local)
        return await asyncio.open_connection(sock=sock, **kwargs)
    raise ultimo_erro or OSError(f"não foi possível resolver {host}")
