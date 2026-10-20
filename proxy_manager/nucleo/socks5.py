"""SOCKS5 (RFC 1928/1929), lado servidor e lado cliente. O servidor também aceita SOCKS4/4A."""
from __future__ import annotations

import asyncio
import ipaddress
import struct
from dataclasses import dataclass

from . import rede

SOCKS_VERSION = 0x05

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

CMD_CONNECT = 0x01
CMD_UDP_ASSOCIATE = 0x03

REP_SUCCESS = 0x00
REP_GENERAL_FAILURE = 0x01
REP_NOT_ALLOWED = 0x02
REP_NETWORK_UNREACHABLE = 0x03
REP_HOST_UNREACHABLE = 0x04
REP_CONN_REFUSED = 0x05
REP_COMMAND_NOT_SUPPORTED = 0x07
REP_ADDR_TYPE_NOT_SUPPORTED = 0x08


class ErroSocks5(Exception):
    pass


@dataclass
class PedidoSocks5:
    cmd: int
    host: str
    port: int
    atyp: int
    versao: int = SOCKS_VERSION  # 4 quando o programa falou SOCKS4/4A


async def negociar_servidor(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter,
                            comandos: tuple[int, ...] = (CMD_CONNECT,)) -> PedidoSocks5:
    """Lê a saudação e o pedido. Sem autenticação: a porta só escuta em 127.0.0.1.

    Também aceita programas que só falam SOCKS4/4A (apenas CONNECT)."""
    versao = (await leitor.readexactly(1))[0]
    if versao == 0x04:
        return await _negociar_servidor_socks4(leitor, escritor)
    if versao != SOCKS_VERSION:
        raise ErroSocks5(f"versão SOCKS não suportada: {versao}")
    qtd_metodos = (await leitor.readexactly(1))[0]
    await leitor.readexactly(qtd_metodos)
    escritor.write(bytes([SOCKS_VERSION, 0x00]))  # 0x00 = sem autenticação
    await escritor.drain()

    cabecalho_pedido = await leitor.readexactly(4)
    versao, cmd, _rsv, atyp = cabecalho_pedido
    if versao != SOCKS_VERSION:
        raise ErroSocks5("versão inválida no pedido")

    if atyp == ATYP_IPV4:
        host = str(ipaddress.IPv4Address(await leitor.readexactly(4)))
    elif atyp == ATYP_DOMAIN:
        tamanho = (await leitor.readexactly(1))[0]
        bruto = await leitor.readexactly(tamanho)
        host = bruto.decode("utf-8", errors="replace")
    elif atyp == ATYP_IPV6:
        host = str(ipaddress.IPv6Address(await leitor.readexactly(16)))
    else:
        await responder_cliente(escritor, REP_ADDR_TYPE_NOT_SUPPORTED)
        raise ErroSocks5("tipo de endereço não suportado")

    bytes_porta = await leitor.readexactly(2)
    port = struct.unpack("!H", bytes_porta)[0]

    if cmd not in comandos:
        await responder_cliente(escritor, REP_COMMAND_NOT_SUPPORTED)
        raise ErroSocks5(f"comando SOCKS não suportado: {cmd}")

    return PedidoSocks5(cmd=cmd, host=host, port=port, atyp=atyp)


async def _negociar_servidor_socks4(leitor: asyncio.StreamReader,
                                    escritor: asyncio.StreamWriter) -> PedidoSocks5:
    cmd = (await leitor.readexactly(1))[0]
    port = struct.unpack("!H", await leitor.readexactly(2))[0]
    ip = await leitor.readexactly(4)
    await leitor.readuntil(b"\x00")  # identificação do usuário, ignorada
    if ip[:3] == b"\x00\x00\x00" and ip[3] != 0:
        # SOCKS4A: o nome vem depois, para o proxy resolver.
        host = (await leitor.readuntil(b"\x00"))[:-1].decode("utf-8", errors="replace")
        atyp = ATYP_DOMAIN
    else:
        host = str(ipaddress.IPv4Address(ip))
        atyp = ATYP_IPV4
    if cmd != CMD_CONNECT:
        await responder_cliente(escritor, REP_COMMAND_NOT_SUPPORTED, versao=4)
        raise ErroSocks5("SOCKS4: apenas CONNECT é suportado")
    return PedidoSocks5(cmd=cmd, host=host, port=port, atyp=atyp, versao=4)


async def responder_cliente(escritor: asyncio.StreamWriter, rep: int, host_vinculo: str = "0.0.0.0",
                            porta_vinculo: int = 0, versao: int = SOCKS_VERSION) -> None:
    if versao == 4:
        codigo = 0x5A if rep == REP_SUCCESS else 0x5B
        pacote = bytes([0x00, codigo]) + struct.pack("!H", porta_vinculo) + bytes(4)
    else:
        pacote = bytes([SOCKS_VERSION, rep, 0x00]) + endereco_socks5(host_vinculo) + \
            struct.pack("!H", porta_vinculo)
    escritor.write(pacote)
    await escritor.drain()


def endereco_socks5(host: str) -> bytes:
    """ATYP + endereço: IPv4 e IPv6 literais vão como endereço, o resto como nome (DNS remoto)."""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        bruto = host.encode("utf-8")
        if len(bruto) > 255:
            raise ErroSocks5("hostname de destino muito longo para SOCKS5")
        return bytes([ATYP_DOMAIN, len(bruto)]) + bruto
    if ip.version == 4:
        return bytes([ATYP_IPV4]) + ip.packed
    return bytes([ATYP_IPV6]) + ip.packed


async def negociar_cliente(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter,
                           host_destino: str, porta_destino: int, usuario: str = "", senha: str = "",
                           timeout: float = 15.0, comando: int = CMD_CONNECT) -> tuple[str, int]:
    """Negocia um pedido SOCKS5 sobre uma conexão já aberta com o proxy.

    Devolve o endereço vinculado informado pelo proxy (usado pelo UDP ASSOCIATE)."""
    metodos = bytes([0x00, 0x02]) if usuario else bytes([0x00])
    escritor.write(bytes([SOCKS_VERSION, len(metodos)]) + metodos)
    await escritor.drain()
    escolhido = await asyncio.wait_for(leitor.readexactly(2), timeout=timeout)
    if escolhido[0] != SOCKS_VERSION:
        raise ErroSocks5("o proxy respondeu com uma versão inválida")
    metodo = escolhido[1]

    if metodo == 0x02:
        nome_usuario = usuario.encode("utf-8")[:255]
        senha_bytes = senha.encode("utf-8")[:255]
        escritor.write(bytes([0x01, len(nome_usuario)]) + nome_usuario + bytes([len(senha_bytes)]) + senha_bytes)
        await escritor.drain()
        resposta_auth = await asyncio.wait_for(leitor.readexactly(2), timeout=timeout)
        if resposta_auth[1] != 0x00:
            raise ErroSocks5("usuário ou senha recusados pelo proxy")
    elif metodo == 0xFF:
        raise ErroSocks5("o proxy não aceitou nenhum método de autenticação")
    elif metodo != 0x00:
        raise ErroSocks5(f"o proxy escolheu um método de autenticação desconhecido ({metodo})")

    escritor.write(bytes([SOCKS_VERSION, comando, 0x00]) + endereco_socks5(host_destino) +
                   struct.pack("!H", porta_destino))
    await escritor.drain()

    cabecalho_resposta = await asyncio.wait_for(leitor.readexactly(4), timeout=timeout)
    rep = cabecalho_resposta[1]
    atyp = cabecalho_resposta[3]
    if atyp == ATYP_IPV4:
        host_vinculo = str(ipaddress.IPv4Address(await leitor.readexactly(4)))
    elif atyp == ATYP_DOMAIN:
        tamanho = (await leitor.readexactly(1))[0]
        host_vinculo = (await leitor.readexactly(tamanho)).decode("utf-8", errors="replace")
    elif atyp == ATYP_IPV6:
        host_vinculo = str(ipaddress.IPv6Address(await leitor.readexactly(16)))
    else:
        raise ErroSocks5(f"o proxy respondeu com um tipo de endereço inválido ({atyp})")
    porta_vinculo = struct.unpack("!H", await leitor.readexactly(2))[0]

    if rep != REP_SUCCESS:
        raise ErroSocks5(f"o proxy recusou a conexão (código {rep})")
    return host_vinculo, porta_vinculo


async def conectar_via_socks5(host: str, port: int, host_destino: str, porta_destino: int,
                              usuario: str = "", senha: str = "",
                              timeout: float = 15.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Conecta ao proxy SOCKS5 e abre um túnel até o destino."""
    leitor, escritor = await rede.abrir_conexao(host, port, timeout=timeout)
    try:
        await negociar_cliente(leitor, escritor, host_destino, porta_destino, usuario, senha, timeout)
        return leitor, escritor
    except BaseException:
        escritor.close()
        raise
