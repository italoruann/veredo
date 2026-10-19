"""Componentes dos painéis da janela principal: barra de título, barra de ferramentas vertical,
gráfico de tráfego e lista de estatísticas."""
from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDockWidget, QGridLayout, QHBoxLayout, QLabel, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from ...nucleo.registro import EntradaLog
from ..modelo_conexoes import ConjuntoIdsLimitado, formatar_bytes
from ..tema import PALETA
from .icones import icone_linha

LINHAS_GRADE = 4
ESPACO_PONTOS = 2  # px entre amostras consecutivas no gráfico


def formatar_taxa(bytes_por_seg: float) -> str:
    return f"{formatar_bytes(int(bytes_por_seg))}/s"


class TituloPainel(QWidget):
    """Barra de título de um QDockWidget: nome do painel à esquerda; à direita, "fixar"
    (alterna entre acoplado e flutuante) e "fechar" (reabre pelo menu Exibir > Janelas)."""

    def __init__(self, painel: QDockWidget):
        super().__init__(painel)
        self.setObjectName("TituloPainel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 4, 0)
        layout.setSpacing(2)
        self.titulo = QLabel(painel.windowTitle())
        layout.addWidget(self.titulo, 1)
        for icone, dica, slot in (
            ("pin", "Fixar / soltar o painel", lambda: painel.setFloating(not painel.isFloating())),
            ("close", "Fechar o painel", painel.close),
        ):
            botao = QToolButton()
            botao.setIcon(icone_linha(icone, PALETA["text_faint"]))
            botao.setIconSize(QSize(14, 14))
            botao.setToolTip(dica)
            botao.setAutoRaise(True)
            botao.clicked.connect(slot)
            layout.addWidget(botao)
        painel.windowTitleChanged.connect(self.titulo.setText)


def barra_painel() -> QToolBar:
    """Barra de ferramentas vertical, só com ícones, encostada à esquerda do conteúdo."""
    barra = QToolBar()
    barra.setObjectName("BarraPainel")
    barra.setOrientation(Qt.Orientation.Vertical)
    barra.setIconSize(QSize(18, 18))
    barra.setMovable(False)
    return barra


def corpo_painel(barra: QToolBar, conteudo: QWidget) -> QWidget:
    corpo = QWidget()
    layout = QHBoxLayout(corpo)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(barra)
    layout.addWidget(conteudo, 1)
    return corpo


class GraficoTrafego(QWidget):
    """Velocidade de download/upload ao longo do tempo, mais recente à direita."""

    def __init__(self, max_pontos: int = 1200, parent: QWidget | None = None):
        super().__init__(parent)
        self._recebido: deque[float] = deque(maxlen=max_pontos)
        self._enviado: deque[float] = deque(maxlen=max_pontos)
        self.modo = "both"  # "both" | "in" | "out"
        self.setMinimumHeight(80)

    def adicionar_amostra(self, recebidos: float, enviados: float) -> None:
        self._recebido.append(recebidos)
        self._enviado.append(enviados)
        self.update()

    def ultimos(self) -> tuple[float, float]:
        return (self._recebido[-1], self._enviado[-1]) if self._recebido else (0.0, 0.0)

    def definir_modo(self, modo: str) -> None:
        self.modo = modo
        self.update()

    def clear(self) -> None:
        self._recebido.clear()
        self._enviado.clear()
        self.update()

    def _series(self) -> list[tuple[deque[float], str]]:
        series = []
        if self.modo in ("both", "in"):
            series.append((self._recebido, "graph_recv"))
        if self.modo in ("both", "out"):
            series.append((self._enviado, "graph_sent"))
        return series

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        pintor = QPainter(self)
        total = QRectF(self.rect())
        pintor.fillRect(total, QColor(PALETA["graph_bg"]))
        metricas = pintor.fontMetrics()
        # área do gráfico: margem em cima para a legenda e embaixo para respirar
        ret = total.adjusted(0, metricas.height() + 14, 0, -6)

        series = self._series()
        visiveis = max(int(ret.width() // ESPACO_PONTOS), 1)
        pico = max((max(list(valores)[-visiveis:], default=0) for valores, _chave in series), default=0)

        # Grade clara; o valor de cada linha fica em cinza, colado à esquerda, logo acima dela.
        for i in range(LINHAS_GRADE + 1):
            y = ret.top() + ret.height() * i / LINHAS_GRADE
            pintor.setPen(QPen(QColor(PALETA["graph_grid"]), 1))
            pintor.drawLine(QPointF(ret.left(), y), QPointF(ret.right(), y))
            if i < LINHAS_GRADE:
                pintor.setPen(QColor(PALETA["text_faint"]))
                pintor.drawText(QPointF(ret.left() + 8, y - 3), formatar_taxa(pico * (LINHAS_GRADE - i) / LINHAS_GRADE))

        # Legenda com a velocidade atual de cada série, no canto superior direito.
        x = total.right() - 10
        ultimos = dict(zip(("graph_recv", "graph_sent"), self.ultimos()))
        for valores, chave in reversed(series):
            texto = f"{'Recebido' if chave == 'graph_recv' else 'Enviado'}  {formatar_taxa(ultimos[chave])}"
            largura = metricas.horizontalAdvance(texto)
            x -= largura
            pintor.setPen(QColor(PALETA["text_muted"]))
            pintor.drawText(QPointF(x, total.top() + 6 + metricas.ascent()), texto)
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(QColor(PALETA[chave]))
            pintor.drawEllipse(QPointF(x - 9, total.top() + 6 + metricas.height() / 2), 4, 4)
            x -= 28

        if pico <= 0:
            pintor.end()
            return
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        for valores, chave in series:
            pontos = list(valores)[-visiveis:]
            caminho = QPainterPath()
            for i, valor in enumerate(pontos):
                px = ret.right() - (len(pontos) - 1 - i) * ESPACO_PONTOS
                py = ret.bottom() - ret.height() * valor / pico
                if i == 0:
                    caminho.moveTo(px, py)
                else:
                    caminho.lineTo(px, py)
            cor = QColor(PALETA[chave])
            area = QPainterPath(caminho)
            area.lineTo(ret.right(), ret.bottom())
            area.lineTo(ret.right() - (len(pontos) - 1) * ESPACO_PONTOS, ret.bottom())
            area.closeSubpath()
            area.setFillRule(Qt.FillRule.WindingFill)
            preenchimento = QLinearGradient(ret.topLeft(), ret.bottomLeft())
            topo, base = QColor(cor), QColor(cor)
            topo.setAlpha(70)
            base.setAlpha(0)
            preenchimento.setColorAt(0, topo)
            preenchimento.setColorAt(1, base)
            pintor.fillPath(area, preenchimento)
            pintor.setPen(QPen(cor, 2))
            pintor.setBrush(Qt.BrushStyle.NoBrush)  # só o traço (a legenda deixou um pincel ativo)
            pintor.drawPath(caminho)
        pintor.end()


class VisaoEstatisticas(QWidget):
    """Lista "rótulo : valor" sobre fundo branco, agrupada por seção."""

    SECOES = [
        ("", [("uptime", "Tempo de execução")]),
        ("Conexões:", [("total", "Total"), ("active", "Ativas"), ("proxy", "Via proxy"),
                       ("direct", "Diretas"), ("blocked", "Bloqueadas"), ("failed", "Falhas")]),
        ("Tráfego:", [("recv", "Recebido"), ("sent", "Enviado")]),
    ]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Estatisticas")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        externo = QVBoxLayout(self)
        externo.setContentsMargins(4, 4, 4, 4)
        grade = QGridLayout()
        grade.setHorizontalSpacing(12)
        grade.setVerticalSpacing(0)
        externo.addLayout(grade)
        externo.addStretch(1)
        self.valores: dict[str, QLabel] = {}
        linha = 0
        for secao, itens in self.SECOES:
            if secao:
                if linha:
                    grade.setRowMinimumHeight(linha, 10)
                    linha += 1
                rotulo_secao = QLabel(secao)
                rotulo_secao.setObjectName("TituloEstatistica")
                grade.addWidget(rotulo_secao, linha, 0, 1, 2)
                linha += 1
            for chave, rotulo in itens:
                grade.addWidget(QLabel(("      " if secao else "") + rotulo), linha, 0)
                valor = QLabel(": 0")
                valor.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grade.addWidget(valor, linha, 1)
                self.valores[chave] = valor
                linha += 1
        grade.setColumnStretch(2, 1)

    def definir_valor(self, chave: str, texto: str) -> None:
        self.valores[chave].setText(f": {texto}")

    def como_texto(self) -> str:
        linhas = []
        for secao, itens in self.SECOES:
            if secao:
                linhas.append(secao)
            for chave, rotulo in itens:
                prefixo = "    " if secao else ""
                linhas.append(f"{prefixo}{rotulo} {self.valores[chave].text()}")
        return "\n".join(linhas)


class ContadoresSessao:
    """Contagens de conexões desde a abertura (ou o último "Zerar estatísticas")."""

    def __init__(self) -> None:
        self.zerar()

    def zerar(self) -> None:
        self.inicio = time.monotonic()
        self.total = self.proxy = self.diretas = self.bloqueadas = self.falhas = 0
        self._ids_falhas = ConjuntoIdsLimitado()

    def contar_novas(self, entradas: list[EntradaLog]) -> None:
        for entrada in entradas:
            self.total += 1
            if entrada.acao == "proxy":
                self.proxy += 1
            elif entrada.acao == "block":
                self.bloqueadas += 1
            else:
                self.diretas += 1
        self.contar_atualizadas(entradas)

    def contar_atualizadas(self, entradas: list[EntradaLog]) -> None:
        for entrada in entradas:
            if entrada.estado == "erro" and entrada.id not in self._ids_falhas:
                self._ids_falhas.adicionar(entrada.id)
                self.falhas += 1
