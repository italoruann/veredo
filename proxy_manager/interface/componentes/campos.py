from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget


class CampoSenha(QWidget):
    """QLineEdit em modo senha com um botão para mostrar/ocultar o texto."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.campo = QLineEdit()
        self.campo.setEchoMode(QLineEdit.EchoMode.Password)

        self._botao_mostrar = QPushButton("Mostrar")
        self._botao_mostrar.setCheckable(True)
        self._botao_mostrar.setFixedWidth(76)
        self._botao_mostrar.toggled.connect(self._ao_alternar)

        layout.addWidget(self.campo, 1)
        layout.addWidget(self._botao_mostrar)

    def _ao_alternar(self, marcado: bool) -> None:
        self.campo.setEchoMode(QLineEdit.EchoMode.Normal if marcado else QLineEdit.EchoMode.Password)
        self._botao_mostrar.setText("Ocultar" if marcado else "Mostrar")

    def text(self) -> str:
        return self.campo.text()

    def setText(self, valor: str) -> None:
        self.campo.setText(valor)
