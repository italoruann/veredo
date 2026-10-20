"""Descobre o IP de saída de um proxy consultando o ip-api.com pelo próprio túnel.

O endereço do perfil nem sempre é o IP que os sites veem (proxies rotativos, por exemplo)."""
from __future__ import annotations

import asyncio

from . import cliente_proxy
from .configuracao import PerfilProxy

_HOST_VERIFICACAO = "ip-api.com"
_PORTA_VERIFICACAO = 80
_CAMINHO_VERIFICACAO = "/line/?fields=query"


async def consultar_ip_saida(perfil: PerfilProxy, timeout: float = 8.0) -> str | None:
    """Devolve o IP de saída ou None se a consulta falhar. Nunca levanta exceção."""
    try:
        leitor, escritor = await cliente_proxy.abrir_tunel([perfil], _HOST_VERIFICACAO, _PORTA_VERIFICACAO,
                                                           timeout=timeout)
    except Exception:
        return None

    try:
        requisicao = (f"GET {_CAMINHO_VERIFICACAO} HTTP/1.1\r\nHost: {_HOST_VERIFICACAO}\r\n"
                      f"Connection: close\r\n\r\n").encode("ascii")
        escritor.write(requisicao)
        await escritor.drain()
        bruto = await asyncio.wait_for(leitor.read(-1), timeout=timeout)
    except Exception:
        return None
    finally:
        escritor.close()

    corpo = bruto.split(b"\r\n\r\n", 1)[-1].decode("utf-8", errors="replace").strip()
    if not corpo:
        return None
    return corpo.splitlines()[-1].strip() or None
