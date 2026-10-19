"""Ícone do aplicativo, desenhado em código.

Um caminho que se divide em dois: um ramo apagado (conexão direta) e outro que termina num
ponto âmbar (o proxy). Com o motor ligado, ganha um selo verde."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from .tema import PALETA

TAMANHOS_ICONE = (16, 24, 32, 48, 64, 128, 256)


def desenhar_pixmap(ativo: bool = False, tamanho: int = 256) -> QPixmap:
    pixmap = QPixmap(tamanho, tamanho)
    pixmap.fill(Qt.GlobalColor.transparent)
    s = float(tamanho)

    pintor = QPainter(pixmap)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)

    ret = QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92)
    corpo = QPainterPath()
    corpo.addRoundedRect(ret, s * 0.24, s * 0.24)
    gradient = QLinearGradient(ret.topLeft(), ret.bottomRight())
    gradient.setColorAt(0, QColor("#14a38b"))
    gradient.setColorAt(1, QColor("#0b4f4a"))
    pintor.fillPath(corpo, gradient)

    bifurcacao = QPointF(s * 0.50, s * 0.56)
    fim_esquerda = QPointF(s * 0.27, s * 0.26)
    fim_direita = QPointF(s * 0.73, s * 0.26)
    largura = max(s * 0.075, 1.5)

    # ramo da conexão direta: apagado
    pintor.setPen(QPen(QColor(255, 255, 255, 85), largura, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    pintor.setBrush(Qt.BrushStyle.NoBrush)
    esquerda = QPainterPath(bifurcacao)
    esquerda.cubicTo(QPointF(bifurcacao.x(), s * 0.40), QPointF(fim_esquerda.x(), s * 0.42), fim_esquerda)
    pintor.drawPath(esquerda)

    # tronco + ramo pelo proxy: sólidos
    pintor.setPen(QPen(QColor("#ffffff"), largura, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    rota = QPainterPath(QPointF(s * 0.50, s * 0.84))
    rota.lineTo(bifurcacao)
    rota.cubicTo(QPointF(bifurcacao.x(), s * 0.40), QPointF(fim_direita.x(), s * 0.42), fim_direita)
    pintor.drawPath(rota)

    pintor.setPen(Qt.PenStyle.NoPen)
    pintor.setBrush(QColor("#ffffff"))
    pintor.drawEllipse(bifurcacao, s * 0.065, s * 0.065)
    pintor.drawEllipse(fim_direita, s * 0.115, s * 0.115)
    pintor.setBrush(QColor("#f5a623"))
    pintor.drawEllipse(fim_direita, s * 0.075, s * 0.075)

    if ativo:
        raio = s * 0.13
        centro = QPointF(s - raio - s * 0.03, s - raio - s * 0.03)
        pintor.setBrush(QColor("#ffffff"))
        pintor.drawEllipse(centro, raio + s * 0.03, raio + s * 0.03)
        pintor.setBrush(QColor(PALETA["success"]))
        pintor.drawEllipse(centro, raio, raio)

    pintor.end()
    return pixmap


def desenhar_icone(ativo: bool = False, tamanho: int = 256) -> QIcon:
    """Cada resolução é desenhada à parte para ficar nítida em tamanhos pequenos."""
    icone = QIcon()
    for px in sorted({*TAMANHOS_ICONE, tamanho}):
        if px <= tamanho:
            icone.addPixmap(desenhar_pixmap(ativo, px))
    return icone
