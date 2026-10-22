"""Fluxos interceptados: uma requisição com a sua resposta. Ficam só em memória (podem conter
senhas e cookies) e saem da memória pelo limite de quantidade e de bytes.

Os observadores são chamados na thread do motor; a interface faz a ponte para o Qt."""
from __future__ import annotations

import base64
import datetime
import gzip
import shlex
import threading
import time
import uuid
import zlib
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from typing import Callable, Optional

try:
    from compression import zstd  # Python 3.14+
except ImportError:  # pragma: no cover
    zstd = None  # type: ignore[assignment]

# (evento, fluxo); evento é "novo" ou "atualizado"
CallbackFluxo = Callable[[str, "Fluxo"], None]

_CAMPOS_BYTES = ("corpo_requisicao", "corpo_resposta")


@dataclass
class Fluxo:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    momento: float = field(default_factory=time.time)
    id_conexao: str = ""  # entrada do log de conexões
    processo: str = ""
    esquema: str = "http"  # "http" ou "https"
    metodo: str = ""
    host: str = ""
    porta: int = 0
    caminho: str = ""
    versao_http: str = "HTTP/1.1"
    cabecalhos_requisicao: list[tuple[str, str]] = field(default_factory=list)
    corpo_requisicao: bytes = b""
    tamanho_requisicao: int = 0
    status: int = 0
    motivo: str = ""
    cabecalhos_resposta: list[tuple[str, str]] = field(default_factory=list)
    corpo_resposta: bytes = b""
    tamanho_resposta: int = 0
    duracao_ms: int = 0
    estado: str = "pendente"  # "pendente", "concluido" ou "erro"
    erro: str = ""
    websocket: bool = False

    @property
    def url(self) -> str:
        padrao = 443 if self.esquema == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        porta = "" if self.porta in (0, padrao) else f":{self.porta}"
        return f"{self.esquema}://{host}{porta}{self.caminho}"

    @property
    def requisicao_truncada(self) -> bool:
        return self.tamanho_requisicao > len(self.corpo_requisicao)

    @property
    def resposta_truncada(self) -> bool:
        return self.tamanho_resposta > len(self.corpo_resposta)

    def cabecalho_requisicao(self, nome: str) -> Optional[str]:
        return _valor(self.cabecalhos_requisicao, nome)

    def cabecalho_resposta(self, nome: str) -> Optional[str]:
        return _valor(self.cabecalhos_resposta, nome)

    @property
    def tipo_conteudo(self) -> str:
        """"json", "html", "image/png"... a partir do Content-Type da resposta."""
        tipo = (self.cabecalho_resposta("Content-Type") or "").split(";", 1)[0].strip().lower()
        for curto in ("json", "html", "javascript", "css", "xml"):
            if curto in tipo:
                return curto
        return tipo

    def bytes_em_memoria(self) -> int:
        return len(self.corpo_requisicao) + len(self.corpo_resposta) + 512

    def para_dict(self) -> dict:
        dados = asdict(self)
        for campo in _CAMPOS_BYTES:
            dados[campo] = base64.b64encode(dados[campo]).decode("ascii")
        return dados

    @staticmethod
    def de_dict(dados: dict) -> Fluxo:
        dados = {k: v for k, v in dados.items() if k in Fluxo.__dataclass_fields__}
        for campo in _CAMPOS_BYTES:
            if isinstance(dados.get(campo), str):
                dados[campo] = base64.b64decode(dados[campo])
        for campo in ("cabecalhos_requisicao", "cabecalhos_resposta"):
            dados[campo] = [tuple(par) for par in dados.get(campo, [])]
        return Fluxo(**dados)


def _valor(cabecalhos: list[tuple[str, str]], nome: str) -> Optional[str]:
    nome = nome.lower()
    return next((v for n, v in cabecalhos if n.lower() == nome), None)


class ArmazemFluxos:
    def __init__(self, max_fluxos: int = 2000, max_bytes: int = 256 * 1024 * 1024):
        self._trava = threading.Lock()
        self._fluxos: OrderedDict[str, Fluxo] = OrderedDict()
        self._bytes = 0
        self._callbacks: list[CallbackFluxo] = []
        self.max_fluxos = max_fluxos
        self.max_bytes = max_bytes

    def inscrever(self, callback: CallbackFluxo) -> None:
        with self._trava:
            self._callbacks.append(callback)

    def _notificar(self, evento: str, fluxo: Fluxo) -> None:
        for callback in list(self._callbacks):
            try:
                callback(evento, fluxo)
            except Exception:
                pass

    def adicionar(self, fluxo: Fluxo) -> Fluxo:
        with self._trava:
            self._fluxos[fluxo.id] = fluxo
            self._bytes += fluxo.bytes_em_memoria()
            self._descartar_excesso()
        self._notificar("novo", fluxo)
        return fluxo

    def atualizar(self, fluxo: Fluxo, **mudancas) -> Fluxo:
        with self._trava:
            antes = fluxo.bytes_em_memoria()
            for campo, valor in mudancas.items():
                setattr(fluxo, campo, valor)
            if fluxo.id in self._fluxos:
                self._bytes += fluxo.bytes_em_memoria() - antes
                self._descartar_excesso()
        self._notificar("atualizado", fluxo)
        return fluxo

    def receber(self, fluxo: Fluxo) -> None:
        """Fluxo vindo de outro processo (serviço): substitui o de mesmo id ou entra como novo."""
        with self._trava:
            existente = self._fluxos.get(fluxo.id)
        if existente is None:
            self.adicionar(fluxo)
        else:
            self.atualizar(existente, **{f.name: getattr(fluxo, f.name) for f in fields(Fluxo)})

    def _descartar_excesso(self) -> None:
        while self._fluxos and (len(self._fluxos) > self.max_fluxos or self._bytes > self.max_bytes):
            _id, antigo = self._fluxos.popitem(last=False)
            self._bytes -= antigo.bytes_em_memoria()

    def obter(self, id_fluxo: str) -> Optional[Fluxo]:
        with self._trava:
            return self._fluxos.get(id_fluxo)

    def todos(self) -> list[Fluxo]:
        with self._trava:
            return list(self._fluxos.values())

    def limpar(self) -> None:
        with self._trava:
            self._fluxos.clear()
            self._bytes = 0


# -- utilidades para quem mostra os fluxos -----------------------------------------------------

def decodificar_corpo(corpo: bytes, content_encoding: Optional[str]) -> tuple[bytes, str]:
    """Corpo sem a compressão de transporte, para exibir. O segundo valor explica o que foi feito
    ("" se nada, ou o motivo de não ter decodificado)."""
    codificacoes = [c.strip().lower() for c in (content_encoding or "").split(",") if c.strip()]
    if not codificacoes or not corpo:
        return corpo, ""
    dados = corpo
    try:
        for codificacao in reversed(codificacoes):
            if codificacao in ("gzip", "x-gzip"):
                dados = gzip.decompress(dados)
            elif codificacao == "deflate":
                try:
                    dados = zlib.decompress(dados)
                except zlib.error:
                    dados = zlib.decompress(dados, -zlib.MAX_WBITS)  # deflate sem cabeçalho zlib
            elif codificacao == "zstd" and zstd is not None:
                dados = zstd.decompress(dados)
            elif codificacao == "identity":
                continue
            else:
                return corpo, f"compressão {codificacao} não suportada; mostrando os bytes originais"
    except Exception as exc:  # cada biblioteca de compressão tem a sua exceção
        return corpo, f"não foi possível descomprimir ({codificacao}): {exc}"
    return dados, f"descomprimido de {', '.join(codificacoes)}"


def comando_curl(fluxo: Fluxo) -> str:
    partes = ["curl"]
    if fluxo.metodo != "GET":
        partes += ["-X", fluxo.metodo]
    partes.append(fluxo.url)
    for nome, valor in fluxo.cabecalhos_requisicao:
        if nome.lower() in ("host", "content-length", "connection", "proxy-connection"):
            continue
        partes += ["-H", f"{nome}: {valor}"]
    if fluxo.corpo_requisicao:
        try:
            partes += ["--data-raw", fluxo.corpo_requisicao.decode("utf-8")]
        except UnicodeDecodeError:
            partes += ["--data-binary", "@corpo.bin"]
    if fluxo.cabecalho_requisicao("Accept-Encoding"):
        partes.append("--compressed")
    return " ".join(shlex.quote(p) for p in partes)


def _har_texto(corpo: bytes) -> dict:
    try:
        return {"text": corpo.decode("utf-8")}
    except UnicodeDecodeError:
        return {"text": base64.b64encode(corpo).decode("ascii"), "encoding": "base64"}


def exportar_har(fluxos: list[Fluxo]) -> dict:
    """Arquivo HAR 1.2, aberto pelo DevTools dos navegadores, pelo Insomnia, pelo Charles etc."""
    entradas = []
    for f in fluxos:
        if not f.metodo:
            continue
        tipo_req = f.cabecalho_requisicao("Content-Type") or ""
        corpo_resp, _nota = decodificar_corpo(f.corpo_resposta, f.cabecalho_resposta("Content-Encoding"))
        _caminho, _, consulta = f.caminho.partition("?")
        entradas.append({
            "startedDateTime": datetime.datetime.fromtimestamp(f.momento, datetime.timezone.utc).isoformat(),
            "time": f.duracao_ms,
            "request": {
                "method": f.metodo, "url": f.url, "httpVersion": f.versao_http,
                "headers": [{"name": n, "value": v} for n, v in f.cabecalhos_requisicao],
                "queryString": [{"name": n, "value": v} for n, _, v in
                                (p.partition("=") for p in consulta.split("&") if p)],
                "cookies": [], "headersSize": -1, "bodySize": f.tamanho_requisicao,
                **({"postData": {"mimeType": tipo_req, **_har_texto(f.corpo_requisicao)}}
                   if f.corpo_requisicao else {}),
            },
            "response": {
                "status": f.status, "statusText": f.motivo, "httpVersion": f.versao_http,
                "headers": [{"name": n, "value": v} for n, v in f.cabecalhos_resposta],
                "cookies": [], "redirectURL": f.cabecalho_resposta("Location") or "",
                "headersSize": -1, "bodySize": f.tamanho_resposta,
                "content": {"size": len(corpo_resp), "mimeType": f.cabecalho_resposta("Content-Type") or "",
                            **_har_texto(corpo_resp)},
            },
            "cache": {}, "timings": {"send": 0, "wait": f.duracao_ms, "receive": 0},
            **({"comment": f.erro} if f.erro else {}),
        })
    return {"log": {"version": "1.2", "creator": {"name": "Veredo", "version": "0.1.0"}, "entries": entradas}}
