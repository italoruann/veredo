"""Log de texto da área central: uma linha por evento, com data e hora."""
from __future__ import annotations

import datetime

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ...nucleo.registro import EntradaLog
from ..modelo_conexoes import ConjuntoIdsLimitado, formatar_bytes, formatar_duracao
from ..tema import PALETA

# Níveis do menu Log > Log na tela.
NIVEL_ERROS, NIVEL_NORMAL, NIVEL_DETALHADO = range(3)
TAMANHOS_FONTE = {"small": 8, "medium": 9, "large": 11}
MAX_LINHAS_LOG = 10000
FAMILIAS_MONO = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Monospace"]


def _carimbo(momento: float | None = None) -> str:
    instante = datetime.datetime.fromtimestamp(momento) if momento else datetime.datetime.now()
    return instante.strftime("[%m.%d %H:%M:%S]")


def _frase_bytes(n: int) -> str:
    return f"{n} bytes" if n < 1024 else f"{n} bytes ({formatar_bytes(n)})"


class VisualizadorLog(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("VisualizadorLog")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(MAX_LINHAS_LOG)
        self.nivel = NIVEL_NORMAL
        self._finalizadas = ConjuntoIdsLimitado()
        self._fmt_normal = QTextCharFormat()
        self._fmt_normal.setForeground(QColor(PALETA["text"]))
        self._fmt_carimbo = QTextCharFormat()
        self._fmt_carimbo.setForeground(QColor(PALETA["text_faint"]))
        self._fmt_erro = QTextCharFormat()
        self._fmt_erro.setForeground(QColor(PALETA["danger"]))
        self.definir_tamanho_fonte("medium")

    # -- configuração -------------------------------------------------------------

    def definir_nivel(self, nivel: int) -> None:
        self.nivel = nivel

    def definir_tamanho_fonte(self, tamanho: str) -> None:
        fonte = QFont()
        fonte.setFamilies(FAMILIAS_MONO)
        fonte.setStyleHint(QFont.StyleHint.Monospace)
        fonte.setPointSize(TAMANHOS_FONTE[tamanho])
        self.setFont(fonte)

    # -- escrita --------------------------------------------------------------------

    def mensagem(self, texto: str, erro: bool = False, momento: float | None = None) -> None:
        """Acrescenta uma linha. Só rola até o fim se o usuário já estava no fim."""
        if not erro and self.nivel == NIVEL_ERROS:
            return
        barra_rolagem = self.verticalScrollBar()
        no_fim = barra_rolagem.value() >= barra_rolagem.maximum() - 2
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(f"{_carimbo(momento)} ", self._fmt_carimbo)
        cursor.insertText(texto, self._fmt_erro if erro else self._fmt_normal)
        if no_fim:
            barra_rolagem.setValue(barra_rolagem.maximum())

    def ao_adicionar_entradas(self, entradas: list[EntradaLog]) -> None:
        for entrada in entradas:
            if entrada.acao != "proxy":
                continue
            self.mensagem(self._texto_abertura(entrada), momento=entrada.momento)
            if entrada.estado != "ativa":
                self._registrar_fim(entrada)

    def ao_atualizar_entradas(self, entradas: list[EntradaLog]) -> None:
        for entrada in entradas:
            if entrada.acao == "proxy" and entrada.estado != "ativa" and entrada.id not in self._finalizadas:
                self._registrar_fim(entrada)

    def limpar_log(self) -> None:
        self.clear()

    # -- formatação -------------------------------------------------------------------

    def _quem(self, entrada: EntradaLog) -> str:
        nome = entrada.nome_processo or "desconhecido"
        if self.nivel == NIVEL_DETALHADO and entrada.pid > 0:
            nome = f"{nome} ({entrada.pid})"
        return f"{nome} - {entrada.host_destino}:{entrada.porta_destino}"

    def _texto_abertura(self, entrada: EntradaLog) -> str:
        if entrada.acao == "proxy":
            texto = f"{self._quem(entrada)} aberta através do proxy {entrada.proxy_usado or '?'}"
        elif entrada.acao == "block":
            texto = f"{self._quem(entrada)} bloqueada pela regra {entrada.regra_aplicada or 'padrão'}"
        else:
            texto = f"{self._quem(entrada)} aberta diretamente"
        if self.nivel == NIVEL_DETALHADO:
            texto += f" [regra: {entrada.regra_aplicada or 'padrão'}, {entrada.protocolo.upper() or '?'}]"
        return texto

    def _registrar_fim(self, entrada: EntradaLog) -> None:
        self._finalizadas.adicionar(entrada.id)
        if entrada.estado == "erro":
            self.mensagem(f"{self._quem(entrada)} erro : {entrada.erro or 'falha desconhecida'}", erro=True)
        elif entrada.estado == "concluida":
            self.mensagem(f"{self._quem(entrada)} fechada, {_frase_bytes(entrada.bytes_enviados)} enviados, "
                          f"{_frase_bytes(entrada.bytes_recebidos)} recebidos, "
                          f"duração {formatar_duracao(entrada.duracao_ms / 1000)}")
