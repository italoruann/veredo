"""Proxy HTTP: leitura das requisições recebidas (CONNECT e HTTP simples) e cliente CONNECT
para proxies HTTP externos."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import rede


class ErroProxyHttp(Exception):
    pass


@dataclass
class CabecalhoHttp:
    primeira_linha: str
    metodo: str
    alvo: str
    versao_http: str
    cabecalhos: list[str] = field(default_factory=list)

    def valor_cabecalho(self, nome: str) -> str | None:
        nome_min = nome.lower() + ":"
        for h in self.cabecalhos:
            if h.lower().startswith(nome_min):
                return h.split(":", 1)[1].strip()
        return None


async def ler_cabecalho_requisicao(leitor: asyncio.StreamReader, timeout: float = 20.0) -> CabecalhoHttp:
    bruto = await asyncio.wait_for(leitor.readuntil(b"\r\n\r\n"), timeout=timeout)
    texto = bruto.decode("iso-8859-1")
    linhas = [l for l in texto.split("\r\n") if l]
    if not linhas:
        raise ErroProxyHttp("requisição HTTP vazia")
    partes = linhas[0].split(" ", 2)
    if len(partes) != 3:
        raise ErroProxyHttp(f"linha de requisição inválida: {linhas[0]!r}")
    metodo, alvo, versao_http = partes
    return CabecalhoHttp(primeira_linha=linhas[0], metodo=metodo.upper(), alvo=alvo, versao_http=versao_http, cabecalhos=linhas[1:])


def extrair_destino(cabecalho: CabecalhoHttp) -> tuple[str, int]:
    if cabecalho.metodo == "CONNECT":
        host, _, porta_txt = cabecalho.alvo.rpartition(":")
        if not host:
            raise ErroProxyHttp(f"alvo de CONNECT inválido: {cabecalho.alvo!r}")
        return host, int(porta_txt)

    if cabecalho.alvo.startswith("http://") or cabecalho.alvo.startswith("https://"):
        analisado = urlsplit(cabecalho.alvo)
        port = analisado.port or (443 if analisado.scheme == "https" else 80)
        return analisado.hostname or "", port

    cabecalho_host = cabecalho.valor_cabecalho("Host") or ""
    if ":" in cabecalho_host:
        host, _, porta_txt = cabecalho_host.rpartition(":")
        return host, int(porta_txt)
    return cabecalho_host, 80


def requisicao_forma_origem(cabecalho: CabecalhoHttp) -> bytes:
    """Troca a URL completa pelo caminho, para enviar direto ao servidor de destino."""
    caminho = cabecalho.alvo
    if caminho.startswith("http://") or caminho.startswith("https://"):
        analisado = urlsplit(caminho)
        caminho = analisado.path or "/"
        if analisado.query:
            caminho += "?" + analisado.query
    linha_requisicao = f"{cabecalho.metodo} {caminho} {cabecalho.versao_http}\r\n"
    cabecalhos_mantidos = [h for h in cabecalho.cabecalhos if not h.lower().startswith("proxy-connection:")]
    return (linha_requisicao + "\r\n".join(cabecalhos_mantidos) + "\r\n\r\n").encode("iso-8859-1")


def requisicao_forma_absoluta(cabecalho: CabecalhoHttp, auth_proxy: str | None) -> bytes:
    """Mantém a URL completa para repassar a outro proxy HTTP, com autenticação se houver."""
    cabecalhos = list(cabecalho.cabecalhos)
    if auth_proxy:
        cabecalhos = [h for h in cabecalhos if not h.lower().startswith("proxy-authorization:")]
        cabecalhos.append(f"Proxy-Authorization: Basic {auth_proxy}")
    return (cabecalho.primeira_linha + "\r\n" + "\r\n".join(cabecalhos) + "\r\n\r\n").encode("iso-8859-1")


def requisicao_connect(host_destino: str, porta_destino: int, auth_proxy: str | None) -> bytes:
    linhas = [f"CONNECT {host_destino}:{porta_destino} HTTP/1.1", f"Host: {host_destino}:{porta_destino}"]
    if auth_proxy:
        linhas.append(f"Proxy-Authorization: Basic {auth_proxy}")
    linhas.append("Proxy-Connection: Keep-Alive")
    return ("\r\n".join(linhas) + "\r\n\r\n").encode("iso-8859-1")


def auth_basica(usuario: str, senha: str) -> str:
    return base64.b64encode(f"{usuario}:{senha}".encode("utf-8")).decode("ascii")


async def ler_linha_status(leitor: asyncio.StreamReader, timeout: float = 20.0) -> tuple[int, str]:
    bruto = await asyncio.wait_for(leitor.readuntil(b"\r\n\r\n"), timeout=timeout)
    texto = bruto.decode("iso-8859-1")
    primeira_linha = texto.split("\r\n", 1)[0]
    partes = primeira_linha.split(" ", 2)
    if len(partes) < 2 or not partes[0].startswith("HTTP/"):
        raise ErroProxyHttp(f"resposta HTTP inválida: {primeira_linha!r}")
    return int(partes[1]), (partes[2] if len(partes) > 2 else "")


async def conectar_via_http(host: str, port: int, host_destino: str, porta_destino: int,
                            usuario: str = "", senha: str = "",
                            timeout: float = 15.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    leitor, escritor = await rede.abrir_conexao(host, port, timeout=timeout)
    try:
        auth = auth_basica(usuario, senha) if usuario else None
        escritor.write(requisicao_connect(host_destino, porta_destino, auth))
        await escritor.drain()
        estado, motivo = await ler_linha_status(leitor, timeout=timeout)
        if estado != 200:
            raise ErroProxyHttp(f"o proxy recusou o CONNECT:{estado} {motivo}")
        return leitor, escritor
    except Exception:
        escritor.close()
        raise
