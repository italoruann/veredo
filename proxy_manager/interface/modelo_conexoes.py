"""Modelo da tabela do painel Conexões.

Mostra as conexões abertas e mantém as recém-encerradas por alguns segundos, em cinza. O filtro
escolhe quais aparecem (por padrão, só as que passam pelo proxy)."""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QFileInfo, QModelIndex, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QFileIconProvider

from ..nucleo.registro import EntradaLog
from .tema import PALETA

COLUNAS = ["Aplicativo", "Destino", "Tempo/Status", "Regra : Proxy", "Enviado", "Recebido"]
COL_APP, COL_DESTINO, COL_ESTADO, COL_REGRA, COL_ENVIADO, COL_RECEBIDO = range(len(COLUNAS))

# Quanto tempo uma conexão encerrada continua na lista antes de sair.
SEGUNDOS_APOS_FECHAR = 3.0

ROTULOS_ACAO = {"direct": "Direto", "proxy": "Via proxy", "block": "Bloqueado"}
# Filtro de conexões exibidas -> ações incluídas. Vale para o painel Conexões e para o log.
FILTROS_CONEXOES = {
    "todas": frozenset({"proxy", "direct", "block"}),
    "proxy": frozenset({"proxy"}),
    "direct": frozenset({"direct"}),
    "block": frozenset({"block"}),
}
FILTRO_PADRAO = "proxy"
ROTULOS_ESTADO = {"ativa": "Em andamento", "concluida": "Fechada", "erro": "Erro", "bloqueada": "Bloqueada"}


def formatar_bytes(n: int) -> str:
    valor = float(n)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024 or unidade == "GB":
            return f"{valor:.0f} {unidade}" if unidade == "B" else f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} GB"


def formatar_duracao(segundos: float) -> str:
    """00:05, 12:34 ou 1:02:03."""
    segundos = max(int(segundos), 0)
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    return f"{horas}:{minutos:02d}:{segs:02d}" if horas else f"{minutos:02d}:{segs:02d}"


def regra_e_destino(entrada: EntradaLog) -> str:
    """Texto da coluna "Regra : Proxy" (ex.: "padrão : Direto", "chrome : Proxy SP")."""
    regra = entrada.regra_aplicada or "padrão"
    alvo = entrada.proxy_usado if entrada.acao == "proxy" and entrada.proxy_usado else \
        ROTULOS_ACAO.get(entrada.acao, entrada.acao or "?")
    return f"{regra} : {alvo}"


class ConjuntoIdsLimitado:
    """Conjunto de ids com tamanho máximo; descarta os mais antigos."""

    def __init__(self, tamanho_max: int = 20000):
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._tamanho_max = tamanho_max

    def adicionar(self, id_entrada: str) -> None:
        self._ids[id_entrada] = None
        while len(self._ids) > self._tamanho_max:
            self._ids.popitem(last=False)

    def __contains__(self, id_entrada: str) -> bool:
        return id_entrada in self._ids

    def clear(self) -> None:
        self._ids.clear()


class ModeloConexoes(QAbstractTableModel):
    def __init__(self, segundos_apos_fechar: float = SEGUNDOS_APOS_FECHAR,
                 relogio: Callable[[], float] = time.time, parent=None):
        super().__init__(parent)
        # Todas as conexões em andamento ou recém-encerradas; `_linhas` são as que passam no filtro.
        self._entradas: list[EntradaLog] = []
        self._linhas: list[EntradaLog] = []
        self._acoes_visiveis = FILTROS_CONEXOES[FILTRO_PADRAO]
        self._fechadas_em: dict[str, float] = {}
        self._espera = segundos_apos_fechar
        self._relogio = relogio
        self._icones: dict[str, QIcon] = {}
        self._provedor_icones: QFileIconProvider | None = None
        self.detalhado = False

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
        entrada = self._linhas[indice.row()]
        coluna = indice.column()
        if papel == Qt.ItemDataRole.DisplayRole:
            return self._texto_celula(entrada, coluna)
        if papel == Qt.ItemDataRole.DecorationRole and coluna == COL_APP:
            return self._icone_app(entrada.caminho_processo)
        if papel == Qt.ItemDataRole.ForegroundRole:
            if entrada.estado in ("erro", "bloqueada"):
                return QColor(PALETA["danger"])
            if entrada.id in self._fechadas_em:
                return QColor(PALETA["text_faint"])
        if papel == Qt.ItemDataRole.ToolTipRole:
            if coluna == COL_APP:
                return entrada.caminho_processo or entrada.nome_processo
            if coluna == COL_ESTADO and entrada.erro:
                return entrada.erro
        return None

    def _texto_celula(self, entrada: EntradaLog, coluna: int) -> str:
        if coluna == COL_APP:
            nome = entrada.nome_processo or "desconhecido"
            return f"{nome} ({entrada.pid})" if self.detalhado and entrada.pid > 0 else nome
        if coluna == COL_DESTINO:
            if self.detalhado and entrada.ip_destino and entrada.ip_destino != entrada.host_destino:
                return f"{entrada.host_destino} ({entrada.ip_destino}):{entrada.porta_destino}"
            return f"{entrada.host_destino}:{entrada.porta_destino}"
        if coluna == COL_ESTADO:
            if entrada.estado == "ativa":
                return formatar_duracao(self._relogio() - entrada.momento)
            return ROTULOS_ESTADO.get(entrada.estado, entrada.estado)
        if coluna == COL_REGRA:
            return regra_e_destino(entrada)
        if coluna == COL_ENVIADO:
            return formatar_bytes(entrada.bytes_enviados)
        if coluna == COL_RECEBIDO:
            return formatar_bytes(entrada.bytes_recebidos)
        return ""

    def _icone_app(self, caminho: str) -> QIcon | None:
        if not caminho:
            return None
        icone = self._icones.get(caminho)
        if icone is None:
            if self._provedor_icones is None:
                self._provedor_icones = QFileIconProvider()
            icone = self._provedor_icones.icon(QFileInfo(caminho))
            self._icones[caminho] = icone
        return icone

    def entrada_em(self, linha: int) -> EntradaLog | None:
        if 0 <= linha < len(self._linhas):
            return self._linhas[linha]
        return None

    def definir_detalhado(self, detalhado: bool) -> None:
        self.detalhado = detalhado
        self._emitir_tudo_mudou()

    def definir_filtro(self, filtro: str) -> None:
        self._acoes_visiveis = FILTROS_CONEXOES[filtro]
        self.beginResetModel()
        self._linhas = [entrada for entrada in self._entradas if entrada.acao in self._acoes_visiveis]
        self.endResetModel()

    def adicionar_entradas(self, entradas: list[EntradaLog]) -> None:
        if not entradas:
            return
        self._entradas.extend(entradas)
        agora = self._relogio()
        for entrada in entradas:
            if entrada.estado != "ativa":
                self._fechadas_em[entrada.id] = agora
        visiveis = [entrada for entrada in entradas if entrada.acao in self._acoes_visiveis]
        if not visiveis:
            return
        primeira = len(self._linhas)
        self.beginInsertRows(QModelIndex(), primeira, primeira + len(visiveis) - 1)
        self._linhas.extend(visiveis)
        self.endInsertRows()

    def atualizar_entradas(self, entradas: list[EntradaLog]) -> None:
        por_id = {entrada.id: entrada for entrada in entradas}
        agora = self._relogio()
        for posicao, atual in enumerate(self._entradas):
            entrada = por_id.get(atual.id)
            if entrada is not None:
                self._entradas[posicao] = entrada
                if entrada.estado != "ativa":
                    self._fechadas_em.setdefault(entrada.id, agora)
        for linha, atual in enumerate(self._linhas):
            entrada = por_id.get(atual.id)
            if entrada is None:
                continue
            self._linhas[linha] = entrada
            self.dataChanged.emit(self.index(linha, 0), self.index(linha, len(COLUNAS) - 1))

    def atualizar_tempos(self) -> None:
        """Chamado a cada segundo: remove as encerradas há tempo e atualiza as durações."""
        agora = self._relogio()
        expiradas = {id_entrada for id_entrada, fechada_em in self._fechadas_em.items()
                     if agora - fechada_em >= self._espera}
        if expiradas:
            self._entradas = [entrada for entrada in self._entradas if entrada.id not in expiradas]
        for linha in range(len(self._linhas) - 1, -1, -1):
            if self._linhas[linha].id in expiradas:
                self.beginRemoveRows(QModelIndex(), linha, linha)
                del self._linhas[linha]
                self.endRemoveRows()
        for id_entrada in expiradas:
            del self._fechadas_em[id_entrada]
        if self._linhas:
            self.dataChanged.emit(self.index(0, COL_ESTADO), self.index(len(self._linhas) - 1, COL_ESTADO))

    def clear(self) -> None:
        self.beginResetModel()
        self._entradas = []
        self._linhas = []
        self._fechadas_em = {}
        self.endResetModel()

    def _emitir_tudo_mudou(self) -> None:
        if self._linhas:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._linhas) - 1, len(COLUNAS) - 1))
