"""Ícones de traço fino a partir de SVG embutido (grade 24×24)."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..tema import PALETA

# Só o miolo de cada SVG; cor e espessura do traço são aplicadas em icone_linha().
_FORMAS = {
    "server": '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>'
              '<path d="M7 7.5h.01M7 16.5h.01"/>',
    "rules": '<path d="M11 6h10M11 12h10M11 18h10"/><path d="M3 6l1.5 1.5L7.5 4.5M3 12l1.5 1.5L7.5 10.5'
             'M3 18l1.5 1.5L7.5 16.5"/>',
    "settings": '<path d="M3 6h10M17 6h4M3 12h4M11 12h10M3 18h12M19 18h2"/>'
                '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "play": '<path d="M7 4.5v15l12-7.5z" fill="{color}"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="{color}"/>',
    "route": '<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/>'
             '<path d="M8.5 19H16a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h7.5"/>',
    "chain": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/>'
             '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "import": '<path d="M12 3v12M7 10l5 5 5-5M4 19h16"/>',
    "export": '<path d="M12 15V3M7 8l5-5 5 5M4 19h16"/>',
    "end_process":'<circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/>',
    "filter": '<path d="M3 4h18l-7 8.5V19l-4 2v-8.5z"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
            '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "speed": '<path d="M12 21a9 9 0 1 1 9-9"/><path d="M12 12l4-4"/><path d="M21 16v5h-5"/>',
    "updown": '<path d="M7 20V4M3 8l4-4 4 4M17 4v16M13 16l4 4 4-4"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>',
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16V5a2 2 0 0 1 2-2h11"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    "connections": '<path d="M4 8h15l-3-3M20 16H5l3 3"/>',
    "activity": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "chart": '<path d="M3 21h18M6 17v-5M12 17V5M18 17v-8"/>',
    "pin": '<path d="M9 3h6l-1 7 3 3H7l3-3zM12 13v8"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "save": '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/>',
}

TAMANHOS_PIXEL = (16, 20, 24, 32, 48)


def _svg(nome: str, cor: str, traco: float) -> bytes:
    interno = _FORMAS[nome].replace("{color}", cor)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{cor}" '
            f'stroke-width="{traco}" stroke-linecap="round" stroke-linejoin="round">{interno}</svg>').encode()


def icone_linha(nome: str, cor: str | None = None, traco: float = 1.8) -> QIcon:
    renderizador = QSvgRenderer(QByteArray(_svg(nome, cor or PALETA["icon"], traco)))
    icone = QIcon()
    for px in TAMANHOS_PIXEL:
        pixmap = QPixmap(px, px)
        pixmap.fill(Qt.GlobalColor.transparent)
        pintor = QPainter(pixmap)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderizador.render(pintor, QRectF(0, 0, px, px))
        pintor.end()
        icone.addPixmap(pixmap)
    return icone
