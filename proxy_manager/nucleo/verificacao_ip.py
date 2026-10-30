"""Descobre o IP de saída de um proxy consultando o ip-api.com pelo próprio túnel.

O endereço do perfil nem sempre é o IP que os sites veem (proxies rotativos, por exemplo).
Também consulta o IP real da máquina, por conexão direta, para o teste de vazamento."""
from __future__ import annotations

import asyncio
import ipaddress

from . import cliente_proxy, rede
from .configuracao import PerfilProxy

_HOST_VERIFICACAO = "ip-api.com"
_PORTA_VERIFICACAO = 80
_CAMINHO_VERIFICACAO = "/line/?fields=query"
# Só responde por IPv6; sem conectividade IPv6 a consulta falha.
_HOST_VERIFICACAO_IPV6 = "api6.ipify.org"


async def consultar_ip_saida(perfil: PerfilProxy, timeout: float = 8.0) -> str | None:
    """Devolve o IP de saída ou None se a consulta falhar. Nunca levanta exceção."""
    try:
        leitor, escritor = await cliente_proxy.abrir_tunel(perfil, _HOST_VERIFICACAO, _PORTA_VERIFICACAO,
                                                           timeout=timeout)
    except Exception:
        return None
    return await _pedir_ip(leitor, escritor, _HOST_VERIFICACAO, _CAMINHO_VERIFICACAO, timeout)


async def consultar_ip_real(ipv6: bool = False, timeout: float = 5.0) -> str | None:
    """IP público da máquina, por conexão direta (sem proxy). None se a consulta falhar."""
    host, caminho = (_HOST_VERIFICACAO_IPV6, "/") if ipv6 else (_HOST_VERIFICACAO, _CAMINHO_VERIFICACAO)
    try:
        leitor, escritor = await rede.abrir_conexao(host, _PORTA_VERIFICACAO, timeout=timeout)
    except Exception:
        return None
    return await _pedir_ip(leitor, escritor, host, caminho, timeout)


async def _pedir_ip(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter, host: str, caminho: str,
                    timeout: float) -> str | None:
    try:
        requisicao = (f"GET {caminho} HTTP/1.1\r\nHost: {host}\r\n"
                      f"Connection: close\r\n\r\n").encode("ascii")
        escritor.write(requisicao)
        await escritor.drain()
        bruto = await asyncio.wait_for(leitor.read(-1), timeout=timeout)
    except Exception:
        return None
    finally:
        escritor.close()

    # A primeira linha do corpo que for um IP: ignora o tamanho dos blocos se vier "chunked".
    corpo = bruto.split(b"\r\n\r\n", 1)[-1].decode("utf-8", errors="replace")
    for linha in corpo.splitlines():
        try:
            return str(ipaddress.ip_address(linha.strip()))
        except ValueError:
            continue
    return None
