from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from ..tema import PALETA

_COMENTARIO_RE = re.compile(r"#.*$")
_APPS_RE = re.compile(r"^\s*apps\s*:", re.IGNORECASE)
_ACAO_RE = re.compile(r"\+\w+(:\S+)?")
_DESATIVADA_RE = re.compile(r"^\s*!")


class RealceRegras(QSyntaxHighlighter):
    def __init__(self, documento):
        super().__init__(documento)

        self._fmt_comentario = QTextCharFormat()
        self._fmt_comentario.setForeground(QColor(PALETA["text_faint"]))
        self._fmt_comentario.setFontItalic(True)

        self._fmt_apps = QTextCharFormat()
        self._fmt_apps.setForeground(QColor(PALETA["accent_hover"]))
        self._fmt_apps.setFontWeight(QFont.Weight.DemiBold)

        self._fmt_acao = QTextCharFormat()
        self._fmt_acao.setForeground(QColor(PALETA["warning"]))

        self._fmt_desativada = QTextCharFormat()
        self._fmt_desativada.setForeground(QColor(PALETA["danger"]))

    def highlightBlock(self, texto: str) -> None:  # noqa: N802 (Qt override)
        if _APPS_RE.match(texto):
            self.setFormat(0, len(texto), self._fmt_apps)
            return

        for resultado in _ACAO_RE.finditer(texto):
            self.setFormat(resultado.start(), resultado.end() - resultado.start(), self._fmt_acao)

        desativada = _DESATIVADA_RE.match(texto)
        if desativada:
            self.setFormat(desativada.start(), desativada.end() - desativada.start(), self._fmt_desativada)

        comentario = _COMENTARIO_RE.search(texto)
        if comentario:
            self.setFormat(comentario.start(), len(texto) - comentario.start(), self._fmt_comentario)
