"""Leitura de mensagens HTTP/1.x para a interceptação.

Os corpos são repassados exatamente como chegam (inclusive o enquadramento chunked); só a cópia
guardada no fluxo é decodificada, até o limite configurado."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from .canal import Canal, ErroCanal, TAMANHO_BLOCO

LIMITE_CABECALHO = 256 * 1024
METODOS = (b"GET", b"POST", b"PUT", b"DELETE", b"HEAD", b"OPTIONS", b"PATCH", b"CONNECT", b"TRACE",
           b"PROPFIND", b"PROPPATCH", b"MKCOL", b"COPY", b"MOVE", b"LOCK", b"UNLOCK", b"REPORT", b"PRI")


class ErroHttp(Exception):
    pass


def parece_http(dados: bytes) -> bool:
    """Os primeiros bytes de uma conexão são o começo de uma requisição HTTP/1.x?"""
    metodo, espaco, _ = dados[:16].partition(b" ")
    return bool(espaco) and metodo in METODOS


@dataclass
class Cabecalho:
    primeira_linha: str
    campos: list[tuple[str, str]] = field(default_factory=list)

    def valor(self, nome: str) -> Optional[str]:
        nome = nome.lower()
        return next((v for n, v in self.campos if n.lower() == nome), None)

    def tem_token(self, nome: str, token: str) -> bool:
        """`Connection: keep-alive, Upgrade` tem o token "upgrade"?"""
        token = token.lower()
        return any(n.lower() == nome.lower() and token in (p.strip().lower() for p in v.split(","))
                   for n, v in self.campos)

    def remover(self, nome: str) -> None:
        nome = nome.lower()
        self.campos = [(n, v) for n, v in self.campos if n.lower() != nome]

    def definir(self, nome: str, valor: str) -> None:
        for i, (n, _v) in enumerate(self.campos):
            if n.lower() == nome.lower():
                self.campos[i] = (n, valor)
                return
        self.campos.append((nome, valor))

    def bruto(self) -> bytes:
        linhas = [self.primeira_linha] + [f"{n}: {v}" for n, v in self.campos]
        return ("\r\n".join(linhas) + "\r\n\r\n").encode("iso-8859-1")


@dataclass
class Requisicao:
    cabecalho: Cabecalho
    metodo: str
    alvo: str
    versao: str

    def caminho_e_host(self) -> tuple[str, Optional[str], Optional[int]]:
        """(caminho na forma de origem, host e porta da forma absoluta, se for o caso)."""
        if self.alvo.startswith(("http://", "https://")):
            url = urlsplit(self.alvo)
            caminho = (url.path or "/") + (f"?{url.query}" if url.query else "")
            return caminho, url.hostname, url.port
        return self.alvo, None, None

    def atualizar_primeira_linha(self, alvo: str) -> None:
        self.alvo = alvo
        self.cabecalho.primeira_linha = f"{self.metodo} {alvo} {self.versao}"


@dataclass
class Resposta:
    cabecalho: Cabecalho
    versao: str
    status: int
    motivo: str


class Captura:
    """Cópia de um corpo, limitada a `limite` bytes; `tamanho` conta o corpo inteiro."""

    def __init__(self, limite: int):
        self.limite = limite
        self.dados = bytearray()
        self.tamanho = 0

    @property
    def truncada(self) -> bool:
        return self.tamanho > len(self.dados)

    def adicionar(self, bloco: bytes) -> None:
        self.tamanho += len(bloco)
        espaco = self.limite - len(self.dados)
        if espaco > 0:
            self.dados.extend(bloco[:espaco])


async def _ler_cabecalho(canal: Canal) -> Optional[Cabecalho]:
    # Linhas em branco antes da mensagem são permitidas (RFC 9112, 2.2).
    while True:
        primeiro = await canal.ler(2)
        if not primeiro:
            return None
        if primeiro != b"\r\n":
            canal.devolver(primeiro)
            break
    bruto = await canal.ler_ate(b"\r\n\r\n", LIMITE_CABECALHO)
    linhas = bruto.decode("iso-8859-1").split("\r\n")
    campos: list[tuple[str, str]] = []
    for linha in linhas[1:]:
        if not linha:
            continue
        if linha[0] in " \t" and campos:  # continuação obsoleta da linha anterior
            nome, valor = campos[-1]
            campos[-1] = (nome, f"{valor} {linha.strip()}")
            continue
        nome, separador, valor = linha.partition(":")
        if not separador:
            raise ErroHttp(f"cabeçalho inválido: {linha!r}")
        campos.append((nome.strip(), valor.strip()))
    return Cabecalho(primeira_linha=linhas[0], campos=campos)


async def ler_requisicao(canal: Canal) -> Optional[Requisicao]:
    """None se o aplicativo fechou a conexão entre requisições."""
    cabecalho = await _ler_cabecalho(canal)
    if cabecalho is None:
        return None
    partes = cabecalho.primeira_linha.split(" ")
    if len(partes) != 3 or not partes[2].startswith("HTTP/"):
        raise ErroHttp(f"linha de requisição inválida: {cabecalho.primeira_linha!r}")
    return Requisicao(cabecalho=cabecalho, metodo=partes[0].upper(), alvo=partes[1], versao=partes[2])


async def ler_resposta(canal: Canal) -> Resposta:
    cabecalho = await _ler_cabecalho(canal)
    if cabecalho is None:
        raise ErroHttp("o servidor fechou a conexão sem responder")
    partes = cabecalho.primeira_linha.split(" ", 2)
    if len(partes) < 2 or not partes[0].startswith("HTTP/") or not partes[1].isdigit():
        raise ErroHttp(f"linha de status inválida: {cabecalho.primeira_linha!r}")
    return Resposta(cabecalho=cabecalho, versao=partes[0], status=int(partes[1]),
                    motivo=partes[2] if len(partes) > 2 else "")


# -- corpos ---------------------------------------------------------------------------------

def enquadramento_requisicao(req: Requisicao) -> tuple[str, int]:
    """("chunked", 0), ("tamanho", n) ou ("nenhum", 0)."""
    if req.cabecalho.tem_token("Transfer-Encoding", "chunked"):
        return "chunked", 0
    tamanho = _content_length(req.cabecalho)
    return ("tamanho", tamanho) if tamanho else ("nenhum", 0)


def enquadramento_resposta(resp: Resposta, metodo: str) -> tuple[str, int]:
    """Como `enquadramento_requisicao`, mais ("ate_fechar", 0) para corpos sem tamanho."""
    if metodo == "HEAD" or resp.status < 200 or resp.status in (204, 304):
        return "nenhum", 0
    if metodo == "CONNECT" and 200 <= resp.status < 300:
        return "nenhum", 0
    if resp.cabecalho.tem_token("Transfer-Encoding", "chunked"):
        return "chunked", 0
    tamanho = _content_length(resp.cabecalho)
    if tamanho is not None:
        return ("tamanho", tamanho) if tamanho else ("nenhum", 0)
    return "ate_fechar", 0


def _content_length(cabecalho: Cabecalho) -> Optional[int]:
    valor = cabecalho.valor("Content-Length")
    if valor is None:
        return None
    try:
        # Valores repetidos ("10, 10") são aceitos se forem iguais.
        valores = {int(v.strip()) for v in valor.split(",")}
    except ValueError:
        raise ErroHttp(f"Content-Length inválido: {valor!r}") from None
    if len(valores) != 1 or min(valores) < 0:
        raise ErroHttp(f"Content-Length inválido: {valor!r}")
    return valores.pop()


async def repassar_corpo(origem: Canal, destino: Canal, modo: str, tamanho: int, captura: Captura) -> None:
    if modo == "nenhum":
        return
    if modo == "tamanho":
        await _repassar_n(origem, destino, tamanho, captura)
    elif modo == "chunked":
        await _repassar_chunked(origem, destino, captura)
    else:  # ate_fechar
        while bloco := await origem.ler():
            captura.adicionar(bloco)
            await destino.escrever(bloco)


async def _repassar_n(origem: Canal, destino: Canal, restante: int, captura: Optional[Captura]) -> None:
    while restante > 0:
        bloco = await origem.ler(min(restante, TAMANHO_BLOCO))
        if not bloco:
            raise ErroCanal("a conexão terminou no meio do corpo")
        restante -= len(bloco)
        if captura is not None:
            captura.adicionar(bloco)
        await destino.escrever(bloco)


async def _repassar_chunked(origem: Canal, destino: Canal, captura: Captura) -> None:
    while True:
        linha = await origem.ler_ate(b"\r\n", 4096)
        await destino.escrever(linha)
        try:
            tamanho = int(linha.split(b";", 1)[0].strip(), 16)
        except ValueError:
            raise ErroHttp(f"tamanho de bloco chunked inválido: {linha!r}") from None
        if tamanho == 0:
            # Trailers opcionais e a linha vazia final.
            while (trailer := await origem.ler_ate(b"\r\n", LIMITE_CABECALHO)) != b"\r\n":
                await destino.escrever(trailer)
            await destino.escrever(b"\r\n")
            return
        await _repassar_n(origem, destino, tamanho, captura)
        await destino.escrever(await origem.ler_exatamente(2))


def resposta_erro(status: int, motivo: str, mensagem: str) -> bytes:
    corpo = f"Veredo: {mensagem}\n".encode("utf-8")
    return (f"HTTP/1.1 {status} {motivo}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(corpo)}\r\nConnection: close\r\n\r\n").encode("ascii") + corpo
