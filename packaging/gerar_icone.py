"""Gera icon.ico (Windows) e icon.png (Linux) com o mesmo desenho do ícone da bandeja."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from proxy_manager.interface.icone import desenhar_icone

PASTA_SAIDA = os.path.dirname(__file__)


def main() -> None:
    app = QApplication.instance() or QApplication([])
    icone = desenhar_icone(ativo=False, tamanho=256)

    caminho_ico = os.path.join(PASTA_SAIDA, "icon.ico")
    caminho_png = os.path.join(PASTA_SAIDA, "icon.png")

    pixmap_256 = icone.pixmap(256, 256)
    pixmap_256.save(caminho_ico, "ICO")
    pixmap_256.save(caminho_png, "PNG")

    print(f"Ícone gerado em:\n  {caminho_ico}\n  {caminho_png}")
    del app


if __name__ == "__main__":
    main()
