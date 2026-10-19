"""Componentes que se adaptam à largura disponível."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget,
)

# Um item de linha: (widget, stretch). widget None vira um espaço elástico (addStretch).
ItemLinha = tuple[QWidget | None, int]
Arranjo = tuple[int, list[list[ItemLinha]]]


class LinhasResponsivas(QWidget):
    """Distribui os mesmos widgets em linhas diferentes conforme a largura do próprio container.

    `arrangements` é uma lista de (largura_mínima, linhas); vale o primeiro arranjo (do mais largo
    para o mais estreito) cuja largura mínima cabe. Todo arranjo deve conter todos os widgets.
    Além do ponto de corte, o arranjo só é usado se os widgets de cada linha couberem de fato."""

    def __init__(self, arranjos: list[Arranjo], espacamento: int = 12,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._arranjos = sorted(arranjos, key=lambda item: item[0], reverse=True)
        self._espacamento = espacamento
        self._atual = -1
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(espacamento)
        # Sem isso a largura mínima ficaria presa à do arranjo atual e nunca trocaria de arranjo.
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._aplicar(0)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(0, self._layout.minimumSize().height())

    def _escolher(self, largura: int) -> int:
        for indice, (largura_min, linhas) in enumerate(self._arranjos):
            if largura >= largura_min and self._cabe(linhas, largura):
                return indice
        return len(self._arranjos) - 1

    def _cabe(self, linhas: list[list[ItemLinha]], largura: int) -> bool:
        """O ponto de corte não basta com fontes maiores ou escala de tela alta."""
        for itens in linhas:
            widgets = [widget for widget, _stretch in itens if widget is not None]
            necessario = sum(max(w.minimumSizeHint().width(), w.minimumWidth()) for w in widgets)
            if necessario + self._espacamento * (len(itens) - 1) > largura:
                return False
        return True

    def resizeEvent(self, evento) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(evento)
        indice = self._escolher(evento.size().width())
        if indice != self._atual:
            self._aplicar(indice)

    def _aplicar(self, indice: int) -> None:
        self._atual = indice
        while self._layout.count():
            linha = self._layout.takeAt(0).layout()
            if linha is not None:
                while linha.count():
                    linha.takeAt(0)  # os widgets continuam filhos deste container
                linha.deleteLater()
        for itens in self._arranjos[indice][1]:
            linha = QHBoxLayout()
            linha.setSpacing(self._espacamento)
            for widget, stretch in itens:
                if widget is None:
                    linha.addStretch(stretch or 1)
                else:
                    linha.addWidget(widget, stretch)
            self._layout.addLayout(linha, 1)


class _RotuloCaixa(QLabel):
    """Texto de um checkbox que quebra linha: clicar nele marca/desmarca o checkbox e ele
    acompanha o estado habilitado/desabilitado do checkbox."""

    def __init__(self, texto: str, caixa: QCheckBox):
        super().__init__(texto)
        self._caixa = caixa
        self.setWordWrap(True)
        self.setEnabled(caixa.isEnabled())
        caixa.installEventFilter(self)

    def mousePressEvent(self, evento) -> None:  # noqa: N802 (Qt override)
        if evento.button() == Qt.MouseButton.LeftButton and self._caixa.isEnabled():
            self._caixa.click()

    def eventFilter(self, watched: QObject, evento: QEvent) -> bool:  # noqa: N802 (Qt override)
        if evento.type() == QEvent.Type.EnabledChange:
            self.setEnabled(self._caixa.isEnabled())
        return False


def caixa_com_quebra(caixa: QCheckBox) -> QWidget:
    """QCheckBox não quebra linha; o texto vai para um rótulo ao lado que quebra."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    rotulo = _RotuloCaixa(caixa.text(), caixa)
    caixa.setText("")
    layout.addWidget(caixa, 0, Qt.AlignmentFlag.AlignTop)
    layout.addWidget(rotulo, 1)
    return container
