"""Paleta e folha de estilo.

Usa o estilo Fusion com tema claro em todas as plataformas, mesmo com o sistema em modo escuro,
para a aparência ser igual no Windows e no Linux."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

PALETA = {
    "bg": "#f5f6f8",
    "surface": "#ffffff",
    "surface_alt": "#f9fafb",
    "surface_hover": "#eef6f4",
    "border": "#d0d5dd",
    "border_soft": "#e4e7ec",
    "text": "#1d2939",
    "text_muted": "#475467",
    "text_faint": "#98a2b3",
    "icon": "#475467",
    "accent": "#0f8a78",
    "accent_hover": "#0c7364",
    "accent_pressed": "#095c50",
    "accent_soft": "#d5efea",
    "success": "#12a150",
    "success_soft": "#e3f6ea",
    "danger": "#d92d20",
    "danger_soft": "#fdecea",
    "warning": "#b54708",
    "warning_soft": "#fef4e6",
    # Gráfico de tráfego: fundo branco, grade clara, recebido em verde-petróleo e enviado em âmbar.
    "graph_bg": "#ffffff",
    "graph_grid": "#eaecf0",
    "graph_recv": "#0f8a78",
    "graph_sent": "#f59e0b",
}

FONTES_MONO = '"Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace'


def aplicar_tema(app: QApplication) -> None:
    dicas = app.styleHints()
    if hasattr(dicas, "setColorScheme"):  # Qt >= 6.8
        dicas.setColorScheme(Qt.ColorScheme.Light)
    app.setStyle(QStyleFactory.create("Fusion"))
    p = PALETA
    paleta = QPalette()
    paleta.setColor(QPalette.ColorRole.Window, QColor(p["bg"]))
    paleta.setColor(QPalette.ColorRole.Base, QColor(p["surface"]))
    paleta.setColor(QPalette.ColorRole.AlternateBase, QColor(p["surface_alt"]))
    paleta.setColor(QPalette.ColorRole.Button, QColor(p["surface"]))
    paleta.setColor(QPalette.ColorRole.ToolTipBase, QColor(p["text"]))
    paleta.setColor(QPalette.ColorRole.ToolTipText, QColor("#ffffff"))
    for papel in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        paleta.setColor(papel, QColor(p["text"]))
        paleta.setColor(QPalette.ColorGroup.Disabled, papel, QColor(p["text_faint"]))
    paleta.setColor(QPalette.ColorRole.PlaceholderText, QColor(p["text_faint"]))
    paleta.setColor(QPalette.ColorRole.Highlight, QColor(p["accent"]))
    paleta.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    paleta.setColor(QPalette.ColorRole.Link, QColor(p["accent"]))
    for papel, cor in ((QPalette.ColorRole.Light, "#ffffff"), (QPalette.ColorRole.Midlight, p["border_soft"]),
                        (QPalette.ColorRole.Mid, p["border"]), (QPalette.ColorRole.Dark, p["border"]),
                        (QPalette.ColorRole.Shadow, "#98a2b3")):
        paleta.setColor(papel, QColor(cor))
    app.setPalette(paleta)
    app.setFont(QFont("Segoe UI" if sys.platform == "win32" else app.font().family(), 9))
    app.setStyleSheet(gerar_folha_estilo())


def gerar_folha_estilo() -> str:
    p = PALETA
    return f"""
QToolTip {{
    background-color: {p['text']};
    color: #ffffff;
    border: none;
    padding: 4px 6px;
}}

/* ---------- Menu + barra de ferramentas ---------- */
QToolBar#BarraPrincipal {{
    background-color: {p['surface']};
    border: none;
    border-bottom: 1px solid {p['border_soft']};
    padding: 2px 6px;
    spacing: 2px;
}}
QMenuBar {{ background-color: transparent; }}
QMenuBar::item {{ padding: 4px 9px; border-radius: 4px; }}
QMenuBar::item:selected {{ background-color: {p['bg']}; }}
QMenu {{
    background-color: {p['surface']};
    border: 1px solid {p['border_soft']};
    padding: 4px;
}}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {p['accent_soft']}; color: {p['text']}; }}
QMenu::separator {{ height: 1px; background: {p['border_soft']}; margin: 4px 6px; }}
QToolBar QToolButton {{
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px;
}}
QToolBar QToolButton:hover {{ background-color: {p['bg']}; border-color: {p['border_soft']}; }}
QToolBar QToolButton:checked {{ background-color: {p['accent_soft']}; }}
QToolBar::separator {{ width: 1px; background: {p['border_soft']}; margin: 4px 6px; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ---------- Painéis acopláveis ---------- */
QWidget#TituloPainel {{
    background-color: {p['surface']};
    border-bottom: 1px solid {p['border_soft']};
}}
QWidget#TituloPainel QLabel {{
    color: {p['text']};
    font-weight: 600;
    padding: 5px 6px;
}}
QWidget#TituloPainel QToolButton {{
    border: none;
    border-radius: 4px;
    padding: 2px;
}}
QWidget#TituloPainel QToolButton:hover {{ background-color: {p['bg']}; }}
QToolBar#BarraPainel {{
    background-color: {p['surface_alt']};
    border: none;
    border-right: 1px solid {p['border_soft']};
    spacing: 2px;
    padding: 4px 3px;
}}
QMainWindow::separator {{ background-color: {p['border_soft']}; width: 1px; height: 1px; }}
QMainWindow > QTabBar {{
    background-color: {p['surface']};
    border-top: 1px solid {p['border_soft']};
}}
QMainWindow > QTabBar::tab {{
    background-color: transparent;
    color: {p['text_muted']};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 14px;
    margin: 0 2px;
}}
QMainWindow > QTabBar::tab:selected {{ color: {p['text']}; border-bottom-color: {p['accent']}; }}
QMainWindow > QTabBar::tab:hover:!selected {{ color: {p['text']}; }}

/* ---------- Tabelas e log ---------- */
QTableView, QTableWidget, QListWidget, QTreeView {{
    background-color: {p['surface']};
    border: none;
    selection-background-color: {p['accent_soft']};
    selection-color: {p['text']};
    gridline-color: {p['border_soft']};
}}
QHeaderView::section {{
    background-color: {p['surface']};
    color: {p['text_muted']};
    border: none;
    border-bottom: 1px solid {p['border_soft']};
    border-right: 1px solid {p['border_soft']};
    padding: 5px 8px;
}}
QPlainTextEdit#VisualizadorLog {{
    background-color: {p['surface']};
    border: none;
    border-top: 1px solid {p['border_soft']};
    padding: 4px 6px;
    selection-background-color: {p['accent_soft']};
    selection-color: {p['text']};
}}

/* ---------- Barra de status ---------- */
QStatusBar {{
    background-color: {p['surface']};
    border-top: 1px solid {p['border_soft']};
    color: {p['text_muted']};
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {p['text_muted']}; }}
QLabel#SeloMotorLigado {{ color: {p['success']}; font-weight: 600; }}
QLabel#SeloMotorDesligado {{ color: {p['text_faint']}; font-weight: 600; }}

/* ---------- Estatísticas ---------- */
QWidget#Estatisticas {{ background-color: {p['surface']}; }}
QWidget#Estatisticas QLabel#TituloEstatistica {{ color: {p['text_muted']}; font-weight: 600; }}

/* ---------- Controles dos diálogos ---------- */
QPushButton {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 5px;
    padding: 5px 14px;
}}
QPushButton:hover {{ background-color: {p['bg']}; }}
QPushButton:pressed {{ background-color: {p['border_soft']}; }}
QPushButton:disabled {{ color: {p['text_faint']}; border-color: {p['border_soft']}; }}
QPushButton#BotaoPrincipal {{
    background-color: {p['accent']};
    border-color: {p['accent']};
    color: #ffffff;
}}
QPushButton#BotaoPrincipal:hover {{ background-color: {p['accent_hover']}; }}
QPushButton#BotaoPrincipal:pressed {{ background-color: {p['accent_pressed']}; }}
QPushButton#BotaoPerigo {{ color: {p['danger']}; }}
QPushButton#BotaoPerigo:hover {{ background-color: {p['danger_soft']}; }}
QLineEdit, QComboBox, QSpinBox {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 5px;
    padding: 4px 6px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p['accent']}; }}
QFrame#Cartao {{
    background-color: {p['surface']};
    border: 1px solid {p['border_soft']};
    border-radius: 8px;
}}
QLabel#TituloCartao {{ font-weight: 600; }}
QLabel#DicaCartao, QLabel#SubtituloPagina {{ color: {p['text_muted']}; }}
QLabel#TituloSecao {{ font-size: 11pt; font-weight: 600; }}
QPlainTextEdit#EditorRegras {{
    font-family: {FONTES_MONO};
    border: 1px solid {p['border']};
    border-radius: 5px;
}}
QTabWidget::pane {{ border: 1px solid {p['border_soft']}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{
    padding: 6px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    color: {p['text_muted']};
}}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom-color: {p['accent']}; }}
"""
