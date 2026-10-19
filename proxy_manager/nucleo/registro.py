"""Log de conexões: mantém as mais recentes em memória e grava as encerradas em SQLite.

Os observadores são chamados na thread do motor; a interface faz a ponte para o Qt."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import astuple, dataclass, field, fields
from typing import Callable, Optional

from .configuracao import pasta_dados

# (evento, entrada); evento é "nova" ou "atualizada"
CallbackEvento = Callable[[str, "EntradaLog"], None]


@dataclass
class EntradaLog:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    momento: float = field(default_factory=time.time)
    pid: int = -1
    nome_processo: str = ""
    caminho_processo: str = ""
    protocolo: str = ""  # "socks5", "http" ou "transparente"
    host_destino: str = ""
    ip_destino: str = ""
    porta_destino: int = 0
    regra_aplicada: str = ""
    acao: str = ""  # "direct", "block" ou "proxy"
    proxy_usado: str = ""
    ip_proxy: str = ""
    bytes_enviados: int = 0
    bytes_recebidos: int = 0
    duracao_ms: int = 0
    estado: str = "ativa"  # "ativa", "concluida", "erro" ou "bloqueada"
    erro: str = ""


_COLUNAS = [f.name for f in fields(EntradaLog)]
_TIPOS_SQL = {int: "INTEGER", float: "REAL", str: "TEXT", "int": "INTEGER", "float": "REAL", "str": "TEXT"}


class Registro:
    def __init__(self, dias_retencao: int = 14, max_em_memoria: int = 5000):
        self._trava = threading.Lock()
        self._entradas: dict[str, EntradaLog] = {}
        self._ordem: deque[str] = deque(maxlen=max_em_memoria)
        self._callbacks: list[CallbackEvento] = []
        self.dias_retencao = dias_retencao
        self._caminho_banco = pasta_dados() / "conexoes.db"
        self._criar_banco()

    def _conectar(self) -> sqlite3.Connection:
        return sqlite3.connect(self._caminho_banco, timeout=5)

    def _criar_banco(self) -> None:
        definicoes = ", ".join(
            f"{f.name} {_TIPOS_SQL.get(f.type, 'TEXT')}" + (" PRIMARY KEY" if f.name == "id" else "")
            for f in fields(EntradaLog)
        )
        with self._conectar() as con:
            con.execute(f"CREATE TABLE IF NOT EXISTS conexoes ({definicoes})")
            con.execute("CREATE INDEX IF NOT EXISTS idx_conexoes_momento ON conexoes(momento)")
        self._apagar_antigas()

    def _apagar_antigas(self) -> None:
        limite = time.time() - self.dias_retencao * 86400
        try:
            with self._conectar() as con:
                con.execute("DELETE FROM conexoes WHERE momento < ?", (limite,))
        except sqlite3.Error:
            pass

    def inscrever(self, callback: CallbackEvento) -> None:
        with self._trava:
            self._callbacks.append(callback)

    def _notificar(self, evento: str, entrada: EntradaLog) -> None:
        for callback in list(self._callbacks):
            try:
                callback(evento, entrada)
            except Exception:
                pass

    def adicionar(self, entrada: EntradaLog) -> EntradaLog:
        with self._trava:
            descartada = self._ordem[0] if len(self._ordem) == self._ordem.maxlen else None
            self._entradas[entrada.id] = entrada
            self._ordem.append(entrada.id)
            if descartada is not None:
                self._entradas.pop(descartada, None)
        self._notificar("nova", entrada)
        return entrada

    def atualizar(self, id_entrada: str, **mudancas) -> Optional[EntradaLog]:
        with self._trava:
            entrada = self._entradas.get(id_entrada)
            if entrada is None:
                return None
            for campo, valor in mudancas.items():
                setattr(entrada, campo, valor)
        self._notificar("atualizada", entrada)
        if entrada.estado in ("concluida", "erro", "bloqueada"):
            self._gravar(entrada)
        return entrada

    def _gravar(self, entrada: EntradaLog) -> None:
        marcadores = ",".join("?" * len(_COLUNAS))
        try:
            with self._conectar() as con:
                con.execute(f"INSERT OR REPLACE INTO conexoes ({','.join(_COLUNAS)}) VALUES ({marcadores})",
                            astuple(entrada))
        except sqlite3.Error:
            pass

    def recentes(self, limite: int = 500) -> list[EntradaLog]:
        with self._trava:
            ids = list(self._ordem)[-limite:]
            return [self._entradas[i] for i in ids if i in self._entradas]

    def historico(self, limite: int = 1000) -> list[EntradaLog]:
        try:
            with self._conectar() as con:
                con.row_factory = sqlite3.Row
                linhas = con.execute("SELECT * FROM conexoes ORDER BY momento DESC LIMIT ?", (limite,)).fetchall()
            return [EntradaLog(**dict(linha)) for linha in linhas]
        except sqlite3.Error:
            return []

    def limpar_historico(self) -> None:
        try:
            with self._conectar() as con:
                con.execute("DELETE FROM conexoes")
        except sqlite3.Error:
            pass
