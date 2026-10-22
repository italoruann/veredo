"""Modelo da tabela do inspetor de tráfego: um fluxo interceptado por linha, do mais antigo para
o mais novo. O filtro de texto exige que todas as palavras apareçam no fluxo (URL, método,
status, tipo ou aplicativo); `status:4` e `metodo:post` restringem a um campo."""
from __future__ import annotations

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..nucleo.interceptacao.fluxos import Fluxo
from .modelo_conexoes import formatar_bytes
from .tema import PALETA

COLUNAS = ["#", "Hora", "Método", "Status", "Host", "Caminho", "Tipo", "Tamanho", "Tempo", "Aplicativo"]
(COL_NUMERO, COL_HORA, COL_METODO, COL_STATUS, COL_HOST, COL_CAMINHO, COL_TIPO, COL_TAMANHO, COL_TEMPO,
 COL_APP) = range(len(COLUNAS))
MAX_LINHAS = 5000
LIMITE_HEX = 64 * 1024


def formatar_hex(dados: bytes, limite: int = LIMITE_HEX) -> str:
    """Despejo hexadecimal com deslocamento, 16 bytes por linha e a coluna ASCII."""
    linhas = []
    for deslocamento in range(0, min(len(dados), limite), 16):
        bloco = dados[deslocamento:deslocamento + 16]
        hexa = " ".join(f"{b:02x}" for b in bloco)
        texto = "".join(chr(b) if 32 <= b < 127 else "." for b in bloco)
        linhas.append(f"{deslocamento:08x}  {hexa:<47}  {texto}")
    if len(dados) > limite:
        linhas.append(f"... mais {formatar_bytes(len(dados) - limite)}")
    return "\n".join(linhas)


def texto_status(fluxo: Fluxo) -> str:
    if fluxo.estado == "erro" and not fluxo.status:
        return "Erro"
    if fluxo.status:
        return "101 WebSocket" if fluxo.websocket else str(fluxo.status)
    return "..."


def _casa_filtro(fluxo: Fluxo, termos: list[str]) -> bool:
    texto = None
    for termo in termos:
        campo, separador, valor = termo.partition(":")
        if separador and campo in ("status", "metodo", "host", "app", "tipo"):
            alvo = {"status": str(fluxo.status), "metodo": fluxo.metodo, "host": fluxo.host,
                    "app": fluxo.processo, "tipo": fluxo.tipo_conteudo}[campo].lower()
            if not (alvo.startswith(valor) if campo == "status" else valor in alvo):
                return False
            continue
        if texto is None:
            texto = " ".join((fluxo.url, fluxo.metodo, texto_status(fluxo), fluxo.tipo_conteudo, fluxo.processo,
                              fluxo.erro)).lower()
        if termo not in texto:
            return False
    return True


class ModeloFluxos(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fluxos: list[Fluxo] = []  # todos, na ordem de chegada
        self._numeros: dict[str, int] = {}  # id -> número exibido na coluna "#"
        self._linhas: list[Fluxo] = []  # os que passam no filtro
        self._linha_por_id: dict[str, int] = {}
        self._termos: list[str] = []
        self._proximo_numero = 1

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._linhas)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUNAS)

    def headerData(self, section, orientation, papel=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if papel == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUNAS[section]
        return None

    def data(self, indice, papel=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not indice.isValid() or not (0 <= indice.row() < len(self._linhas)):
            return None
        fluxo = self._linhas[indice.row()]
        coluna = indice.column()
        if papel == Qt.ItemDataRole.DisplayRole:
            return self._texto_celula(fluxo, coluna)
        if papel == Qt.ItemDataRole.ForegroundRole:
            if fluxo.estado == "erro" or fluxo.status >= 500:
                return QColor(PALETA["danger"])
            if fluxo.status >= 400:
                return QColor(PALETA["warning"])
            if fluxo.estado == "pendente":
                return QColor(PALETA["text_faint"])
        if papel == Qt.ItemDataRole.ToolTipRole:
            if fluxo.erro:
                return fluxo.erro
            if coluna in (COL_HOST, COL_CAMINHO):
                return fluxo.url
        if papel == Qt.ItemDataRole.TextAlignmentRole and coluna in (COL_NUMERO, COL_TAMANHO, COL_TEMPO):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _texto_celula(self, fluxo: Fluxo, coluna: int) -> str:
        if coluna == COL_NUMERO:
            return str(self._numeros.get(fluxo.id, ""))
        if coluna == COL_HORA:
            return time.strftime("%H:%M:%S", time.localtime(fluxo.momento))
        if coluna == COL_METODO:
            return fluxo.metodo or ("TLS" if fluxo.esquema == "https" else "?")
        if coluna == COL_STATUS:
            return texto_status(fluxo)
        if coluna == COL_HOST:
            padrao = 443 if fluxo.esquema == "https" else 80
            return fluxo.host if fluxo.porta in (0, padrao) else f"{fluxo.host}:{fluxo.porta}"
        if coluna == COL_CAMINHO:
            return fluxo.caminho
        if coluna == COL_TIPO:
            return fluxo.tipo_conteudo
        if coluna == COL_TAMANHO:
            return formatar_bytes(fluxo.tamanho_resposta) if fluxo.status else ""
        if coluna == COL_TEMPO:
            return f"{fluxo.duracao_ms} ms" if fluxo.metodo and fluxo.estado != "pendente" else ""
        if coluna == COL_APP:
            return fluxo.processo
        return ""

    def fluxo_em(self, linha: int) -> Fluxo | None:
        return self._linhas[linha] if 0 <= linha < len(self._linhas) else None

    def linha_de(self, id_fluxo: str) -> int:
        return self._linha_por_id.get(id_fluxo, -1)

    def visiveis(self) -> list[Fluxo]:
        return list(self._linhas)

    def receber(self, fluxos: list[Fluxo]) -> None:
        """Fluxos novos ou atualizados (o mesmo id pode vir várias vezes)."""
        novos: list[Fluxo] = []
        for fluxo in fluxos:
            if fluxo.id in self._numeros:
                self._substituir(fluxo)
                continue
            self._numeros[fluxo.id] = self._proximo_numero
            self._proximo_numero += 1
            self._fluxos.append(fluxo)
            novos.append(fluxo)
        visiveis = [f for f in novos if _casa_filtro(f, self._termos)]
        if visiveis:
            primeira = len(self._linhas)
            self.beginInsertRows(QModelIndex(), primeira, primeira + len(visiveis) - 1)
            for deslocamento, fluxo in enumerate(visiveis):
                self._linha_por_id[fluxo.id] = primeira + deslocamento
            self._linhas.extend(visiveis)
            self.endInsertRows()
        if len(self._fluxos) > MAX_LINHAS:
            self._descartar(len(self._fluxos) - MAX_LINHAS)

    def _substituir(self, fluxo: Fluxo) -> None:
        for posicao in range(len(self._fluxos) - 1, -1, -1):
            if self._fluxos[posicao].id == fluxo.id:
                self._fluxos[posicao] = fluxo
                break
        linha = self._linha_por_id.get(fluxo.id)
        if linha is not None:
            self._linhas[linha] = fluxo
            self.dataChanged.emit(self.index(linha, 0), self.index(linha, len(COLUNAS) - 1))

    def _descartar(self, quantidade: int) -> None:
        removidos = {f.id for f in self._fluxos[:quantidade]}
        del self._fluxos[:quantidade]
        for id_fluxo in removidos:
            self._numeros.pop(id_fluxo, None)
        self._refiltrar()

    def definir_filtro(self, texto: str) -> None:
        self._termos = texto.lower().split()
        self._refiltrar()

    def _refiltrar(self) -> None:
        self.beginResetModel()
        self._linhas = [f for f in self._fluxos if _casa_filtro(f, self._termos)]
        self._linha_por_id = {f.id: linha for linha, f in enumerate(self._linhas)}
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._fluxos, self._linhas = [], []
        self._numeros, self._linha_por_id = {}, {}
        self.endResetModel()
