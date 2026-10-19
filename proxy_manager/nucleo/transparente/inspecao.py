"""Descobre o domínio de uma conexão interceptada lendo o início do tráfego.

Sem CONNECT, só se conhece o IP de destino. Para regras por domínio, lê o SNI do ClientHello
TLS (porta 443) ou o cabeçalho Host (porta 80). Os bytes lidos são repassados intactos."""
from __future__ import annotations

import struct
from typing import Optional

TLS_REGISTRO_HANDSHAKE = 0x16
TLS_CLIENT_HELLO = 0x01
TLS_EXTENSAO_SERVER_NAME = 0x0000
SNI_TIPO_HOST = 0x00


def extrair_sni(dados: bytes) -> Optional[str]:
    """Devolve o SNI ou None (não é ClientHello, não tem SNI ou os dados estão incompletos)."""
    try:
        return _extrair_sni(dados)
    except (struct.error, IndexError):
        return None


def _extrair_sni(dados: bytes) -> Optional[str]:
    if len(dados) < 5 or dados[0] != TLS_REGISTRO_HANDSHAKE:
        return None
    pos = 5  # pula o record header: tipo (1) + versão (2) + tamanho (2)
    if pos >= len(dados) or dados[pos] != TLS_CLIENT_HELLO:
        return None
    pos += 4  # handshake header: tipo (1) + tamanho (3)
    pos += 2 + 32  # client_version (2) + random (32)

    tam_session_id = dados[pos]
    pos += 1 + tam_session_id

    tam_cifras = struct.unpack("!H", dados[pos:pos + 2])[0]
    pos += 2 + tam_cifras

    tam_compressao = dados[pos]
    pos += 1 + tam_compressao

    if pos + 2 > len(dados):
        return None
    tam_extensoes = struct.unpack("!H", dados[pos:pos + 2])[0]
    pos += 2
    fim_extensoes = min(pos + tam_extensoes, len(dados))

    while pos + 4 <= fim_extensoes:
        tipo_ext, tam_ext = struct.unpack("!HH", dados[pos:pos + 4])
        pos += 4
        if pos + tam_ext > len(dados):
            return None
        if tipo_ext == TLS_EXTENSAO_SERVER_NAME:
            return _ler_extensao_server_name(dados[pos:pos + tam_ext])
        pos += tam_ext
    return None


def _ler_extensao_server_name(carga: bytes) -> Optional[str]:
    if len(carga) < 2:
        return None
    tam_lista = struct.unpack("!H", carga[0:2])[0]
    pos = 2
    fim = min(2 + tam_lista, len(carga))
    while pos + 3 <= fim:
        tipo_nome = carga[pos]
        tam_nome = struct.unpack("!H", carga[pos + 1:pos + 3])[0]
        pos += 3
        if pos + tam_nome > len(carga):
            return None
        if tipo_nome == SNI_TIPO_HOST:
            # O SNI já vem em ASCII/punycode (RFC 6066).
            return carga[pos:pos + tam_nome].decode("ascii", errors="replace")
        pos += tam_nome
    return None


def extrair_host_http(dados: bytes) -> Optional[str]:
    """Devolve o cabeçalho Host, se os cabeçalhos já chegaram completos."""
    if b"\r\n\r\n" not in dados:
        return None
    try:
        cabecalho = dados.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    except UnicodeDecodeError:
        return None
    for linha_texto in cabecalho.split("\r\n")[1:]:
        if linha_texto.lower().startswith("host:"):
            host = linha_texto.split(":", 1)[1].strip()
            if host.count(":") == 1:  # "host:porta" (host IPv6 vem entre colchetes, sem contar)
                host = host.rsplit(":", 1)[0]
            return host or None
    return None
