"""SOCKS5 (RFC 1928/1929), lado servidor e lado cliente. Apenas o comando CONNECT."""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

from . import rede

SOCKS_VERSION = 0x05

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

CMD_CONNECT = 0x01

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


async def negociar_servidor(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> PedidoSocks5:
    """Lê a saudação e o pedido CONNECT. Sem autenticação: a porta só escuta em 127.0.0.1."""
    cabecalho = await leitor.readexactly(2)
    versao, qtd_metodos = cabecalho[0], cabecalho[1]
    if versao != SOCKS_VERSION:
        raise ErroSocks5(f"versão SOCKS não suportada: {versao}")
    await leitor.readexactly(qtd_metodos)
    escritor.write(bytes([SOCKS_VERSION, 0x00]))  # 0x00 = sem autenticação
    await escritor.drain()

    cabecalho_pedido = await leitor.readexactly(4)
    versao, cmd, _rsv, atyp = cabecalho_pedido
    if versao != SOCKS_VERSION:
        raise ErroSocks5("versão inválida no pedido")

    if atyp == ATYP_IPV4:
        bruto = await leitor.readexactly(4)
        host = ".".join(str(b) for b in bruto)
    elif atyp == ATYP_DOMAIN:
        tamanho = (await leitor.readexactly(1))[0]
        bruto = await leitor.readexactly(tamanho)
        host = bruto.decode("utf-8", errors="replace")
    elif atyp == ATYP_IPV6:
        bruto = await leitor.readexactly(16)
        host = ":".join(f"{bruto[i]:02x}{bruto[i+1]:02x}" for i in range(0, 16, 2))
    else:
        await responder_cliente(escritor, REP_ADDR_TYPE_NOT_SUPPORTED)
        raise ErroSocks5("tipo de endereço não suportado")

    bytes_porta = await leitor.readexactly(2)
    port = struct.unpack("!H", bytes_porta)[0]

    if cmd != CMD_CONNECT:
        await responder_cliente(escritor, REP_COMMAND_NOT_SUPPORTED)
        raise ErroSocks5("apenas CONNECT é suportado")

    return PedidoSocks5(cmd=cmd, host=host, port=port, atyp=atyp)


async def responder_cliente(escritor: asyncio.StreamWriter, rep: int,
                            host_vinculo: str = "0.0.0.0", porta_vinculo: int = 0) -> None:
    bytes_endereco = bytes(int(x) for x in host_vinculo.split(".")) if "." in host_vinculo else b"\x00\x00\x00\x00"
    pacote = bytes([SOCKS_VERSION, rep, 0x00, ATYP_IPV4]) + bytes_endereco + struct.pack("!H", porta_vinculo)
    escritor.write(pacote)
    await escritor.drain()


async def conectar_via_socks5(host: str, port: int, host_destino: str, porta_destino: int,
                              usuario: str = "", senha: str = "",
                              timeout: float = 15.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Conecta ao proxy SOCKS5 e abre um túnel até o destino."""
    leitor, escritor = await rede.abrir_conexao(host, port, timeout=timeout)
    try:
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

        bytes_destino = host_destino.encode("utf-8")
        if len(bytes_destino) > 255:
            raise ErroSocks5("hostname de destino muito longo para SOCKS5")
        requisicao = bytes([SOCKS_VERSION, CMD_CONNECT, 0x00, ATYP_DOMAIN, len(bytes_destino)]) + \
            bytes_destino + struct.pack("!H", porta_destino)
        escritor.write(requisicao)
        await escritor.drain()

        cabecalho_resposta = await asyncio.wait_for(leitor.readexactly(4), timeout=timeout)
        rep = cabecalho_resposta[1]
        atyp = cabecalho_resposta[3]
        if atyp == ATYP_IPV4:
            await leitor.readexactly(4)
        elif atyp == ATYP_DOMAIN:
            tamanho = (await leitor.readexactly(1))[0]
            await leitor.readexactly(tamanho)
        elif atyp == ATYP_IPV6:
            await leitor.readexactly(16)
        await leitor.readexactly(2)

        if rep != REP_SUCCESS:
            raise ErroSocks5(f"o proxy recusou a conexão (código {rep})")

        return leitor, escritor
    except Exception:
        escritor.close()
        raise
