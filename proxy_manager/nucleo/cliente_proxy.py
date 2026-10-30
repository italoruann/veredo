"""Lado cliente dos proxies: abre túneis por um perfil ou por uma cadeia de perfis de reserva,
que usa um perfil por vez e passa ao seguinte quando um falha."""
from __future__ import annotations

import asyncio
import ipaddress
import struct
from dataclasses import dataclass, field

from . import proxy_http, rede, socks5
from .configuracao import PerfilProxy


class ErroRota(Exception):
    pass


class ErroSocks4(Exception):
    pass


@dataclass
class Rota:
    """Caminho até o destino: um perfil sozinho ou os perfis de uma cadeia."""
    id: str
    nome: str
    perfis: list[PerfilProxy] = field(default_factory=list)
    timeout: float = 15.0
    eh_cadeia: bool = False


def rota_do_perfil(perfil: PerfilProxy) -> Rota:
    return Rota(id=perfil.id, nome=perfil.nome, perfis=[perfil])


async def negociar_socks4(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter,
                          host_destino: str, porta_destino: int, usuario: str = "",
                          timeout: float = 15.0) -> None:
    """SOCKS4 com IPv4 literal; SOCKS4A (nome resolvido pelo proxy) nos demais casos."""
    try:
        ip = ipaddress.ip_address(host_destino)
    except ValueError:
        ip = None
    if ip is not None and ip.version == 6:
        raise ErroSocks4("SOCKS4 não suporta endereços IPv6")
    pedido = bytes([0x04, 0x01]) + struct.pack("!H", porta_destino)
    if ip is not None:
        pedido += ip.packed + usuario.encode("utf-8") + b"\x00"
    else:
        pedido += bytes([0, 0, 0, 1]) + usuario.encode("utf-8") + b"\x00" + host_destino.encode("utf-8") + b"\x00"
    escritor.write(pedido)
    await escritor.drain()
    resposta = await asyncio.wait_for(leitor.readexactly(8), timeout=timeout)
    if resposta[1] != 0x5A:
        raise ErroSocks4(f"o proxy recusou a conexão (código {resposta[1]})")


async def _negociar(perfil: PerfilProxy, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter,
                    host: str, porta: int, timeout: float) -> None:
    if perfil.type == "socks5":
        await socks5.negociar_cliente(leitor, escritor, host, porta, perfil.usuario, perfil.senha, timeout)
    elif perfil.type == "socks4":
        await negociar_socks4(leitor, escritor, host, porta, perfil.usuario, timeout)
    else:
        await proxy_http.negociar_connect(leitor, escritor, host, porta, perfil.usuario, perfil.senha, timeout)


async def abrir_tunel(perfil: PerfilProxy, host: str, porta: int, timeout: float = 15.0
                      ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Túnel até `host:porta` pelo `perfil`."""
    leitor, escritor = await rede.abrir_conexao(perfil.host, perfil.port, timeout=timeout)
    try:
        await _negociar(perfil, leitor, escritor, host, porta, timeout)
    except BaseException:
        escritor.close()
        raise
    return leitor, escritor


async def conectar_rota(rota: Rota, host: str, porta: int
                        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, PerfilProxy]:
    """Abre o túnel pela rota. O terceiro valor é o perfil que conectou."""
    if not rota.perfis:
        raise ErroRota(f"'{rota.nome}' não tem nenhum proxy habilitado")
    if not rota.eh_cadeia:
        perfil = rota.perfis[0]
        leitor, escritor = await abrir_tunel(perfil, host, porta, rota.timeout)
        return leitor, escritor, perfil

    erros: list[str] = []
    for perfil in rota.perfis:
        try:
            leitor, escritor = await asyncio.wait_for(
                abrir_tunel(perfil, host, porta, rota.timeout), timeout=rota.timeout)
            return leitor, escritor, perfil
        except Exception as exc:
            erros.append(f"{perfil.nome}: {exc or type(exc).__name__}")
    raise ErroRota("nenhum proxy da cadeia conectou (" + "; ".join(erros) + ")")
