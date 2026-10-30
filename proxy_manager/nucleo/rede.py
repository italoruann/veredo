"""Conexões de saída do próprio Veredo.

No modo transparente do Windows o WinDivert vê todos os pacotes, inclusive os do motor: sem uma
marca, a conexão do motor até o proxy seria capturada de novo e entraria em loop. Cada socket é
vinculado a uma porta local antes do connect() e essa porta fica anunciada aqui até a conexão
se estabelecer, para que o backend deixe o SYN passar. Sockets UDP do motor (consultas DNS)
ficam anunciados enquanto existirem.

Com o DNS pelo proxy ativo, o resolvedor do sistema devolveria IPs falsos; o motor então troca
`resolvedor` por um que consulta os servidores DNS reais."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
from typing import Awaitable, Callable, Optional

_trava = threading.Lock()
_portas_proprias: dict[int, int] = {}

# Um endereço que não responde (o SYN se perde) passa a vez ao próximo depois deste tempo; sem isso
# ele consumiria todo o prazo da conexão e os outros endereços do nome nunca seriam tentados.
TEMPO_POR_ENDERECO = 3.0

# nome -> endereços IP; None usa o resolvedor do sistema.
resolvedor: Optional[Callable[[str], Awaitable[list[str]]]] = None


def porta_propria(porta: int) -> bool:
    """True se `porta` é a porta local de um socket de saída do próprio motor."""
    with _trava:
        return porta in _portas_proprias


def reservar_porta(porta: int) -> None:
    with _trava:
        _portas_proprias[porta] = _portas_proprias.get(porta, 0) + 1


def liberar_porta(porta: int) -> None:
    with _trava:
        restantes = _portas_proprias.get(porta, 0) - 1
        if restantes > 0:
            _portas_proprias[porta] = restantes
        else:
            _portas_proprias.pop(porta, None)


def endereco_socket(ip: str, porta: int) -> tuple:
    # O loop Proactor do Windows recusa (EINVAL) o par (ip, porta) em sockets IPv6.
    return (ip, porta, 0, 0) if ":" in ip else (ip, porta)


async def resolver(host: str) -> list[str]:
    """Endereços IP de `host`, pelo resolvedor ativo."""
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    if resolvedor is not None:
        return await resolvedor(host)
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def abrir_conexao(host: str, porta: int, timeout: float | None = 15.0
                        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Como `asyncio.open_connection`, com a porta local anunciada durante o connect()."""
    return await asyncio.wait_for(_abrir(host, porta), timeout=timeout)


async def _abrir(host: str, porta: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    enderecos = await resolver(host)
    ultimo_erro: OSError | None = None
    for indice, endereco in enumerate(enderecos):
        ultimo = indice == len(enderecos) - 1
        familia = socket.AF_INET6 if ":" in endereco else socket.AF_INET
        sock = socket.socket(familia, socket.SOCK_STREAM)
        porta_local = 0
        try:
            sock.setblocking(False)
            sock.bind(("::" if familia == socket.AF_INET6 else "0.0.0.0", 0))
            porta_local = sock.getsockname()[1]
            reservar_porta(porta_local)
            await asyncio.wait_for(loop.sock_connect(sock, endereco_socket(endereco, porta)),
                                   timeout=None if ultimo else TEMPO_POR_ENDERECO)
        except OSError as exc:  # inclui TimeoutError
            sock.close()
            ultimo_erro = exc
            continue
        except BaseException:
            sock.close()
            raise
        finally:
            if porta_local:
                liberar_porta(porta_local)
        return await asyncio.open_connection(sock=sock)
    raise ultimo_erro or OSError(f"não foi possível resolver {host}")
