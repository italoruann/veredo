"""Inspetor de tráfego: lista os fluxos HTTP/HTTPS interceptados e mostra cabeçalhos e corpos da
requisição e da resposta. Também liga/desliga a interceptação, configura os hosts e cuida do
certificado da CA do Veredo."""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional
from urllib.parse import parse_qsl

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTableView,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from ..nucleo.configuracao import NOME_EXIBICAO
from ..nucleo.integracao_sistema import CAMINHO_CERTIFICADO_CA
from ..nucleo.interceptacao import instalacao_ca
from ..nucleo.interceptacao.fluxos import Fluxo, comando_curl, decodificar_corpo, exportar_har
from ..nucleo.interceptacao.interceptador import interpretar_padroes
from .componentes.icones import icone_linha
from .contexto import Contexto
from .modelo_conexoes import formatar_bytes
from .modelo_fluxos import (
    COL_APP, COL_CAMINHO, COL_HORA, COL_HOST, COL_METODO, COL_NUMERO, COL_STATUS, COL_TAMANHO, COL_TEMPO,
    COL_TIPO, formatar_hex, texto_status,
)
from .tema import FONTES_MONO, PALETA

LARGURAS_COLUNAS = {COL_NUMERO: 50, COL_HORA: 70, COL_METODO: 70, COL_STATUS: 70, COL_HOST: 220,
                    COL_CAMINHO: 340, COL_TIPO: 90, COL_TAMANHO: 80, COL_TEMPO: 70, COL_APP: 120}
MODOS_CORPO = ["Automático", "Texto", "JSON", "Hex", "Imagem"]
# Corpos maiores que isto são mostrados só em parte, para a janela não travar.
LIMITE_EXIBICAO = 2 * 1024 * 1024

AJUDA_VAZIA = f"""<h3>Nenhum fluxo interceptado ainda</h3>
<ol>
<li>Ligue a interceptação no botão <b>Interceptar</b> acima.</li>
<li>Instale o certificado da CA do {NOME_EXIBICAO} em <b>Certificado &gt; Instalar no sistema</b>,
para os aplicativos aceitarem o HTTPS decifrado.</li>
<li>O tráfego precisa passar pelo {NOME_EXIBICAO}: proxy do sistema, aplicativo configurado com as
portas locais ou modo transparente, e uma regra que não o bloqueie.</li>
</ol>
<p>Só conexões novas são interceptadas: recarregue a página ou reinicie o aplicativo depois de
ligar. Aplicativos com certificado fixo (pinning) recusam a interceptação; ponha esses hosts nas
exceções. Os fluxos ficam só na memória e somem ao fechar o {NOME_EXIBICAO}.</p>"""


def _fonte_mono() -> QFont:
    fonte = QFont()
    fonte.setFamilies([f.strip().strip('"') for f in FONTES_MONO.split(",")])
    fonte.setStyleHint(QFont.StyleHint.Monospace)
    return fonte


def _charset(tipo_conteudo: str) -> str:
    for parte in tipo_conteudo.split(";")[1:]:
        nome, _, valor = parte.strip().partition("=")
        if nome.lower() == "charset" and valor:
            return valor.strip('"')
    return "utf-8"


class VisualizadorCorpo(QWidget):
    """Corpo de uma mensagem em texto, JSON formatado, hexadecimal ou imagem."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._corpo = b""
        self._tipo = ""
        self._nome_arquivo = "corpo.bin"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        linha = QHBoxLayout()
        self.combo_modo = QComboBox()
        self.combo_modo.addItems(MODOS_CORPO)
        self.combo_modo.currentIndexChanged.connect(self._renderizar)
        self.rotulo_nota = QLabel()
        self.rotulo_nota.setObjectName("DicaCartao")
        botao_copiar = QPushButton("Copiar")
        botao_copiar.clicked.connect(self._copiar)
        botao_salvar = QPushButton("Salvar...")
        botao_salvar.clicked.connect(self._salvar)
        linha.addWidget(QLabel("Corpo:"))
        linha.addWidget(self.combo_modo)
        linha.addWidget(self.rotulo_nota, 1)
        linha.addWidget(botao_copiar)
        linha.addWidget(botao_salvar)
        layout.addLayout(linha)

        self.pilha = QStackedWidget()
        self.texto = QPlainTextEdit()
        self.texto.setReadOnly(True)
        self.texto.setFont(_fonte_mono())
        self.texto.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.imagem = QLabel()
        self.imagem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area_imagem = QScrollArea()
        area_imagem.setWidgetResizable(True)
        area_imagem.setWidget(self.imagem)
        self.pilha.addWidget(self.texto)
        self.pilha.addWidget(area_imagem)
        layout.addWidget(self.pilha, 1)

    def mostrar(self, corpo: bytes, cabecalhos: list[tuple[str, str]], tamanho_total: int,
                nome_arquivo: str) -> None:
        encoding = next((v for n, v in cabecalhos if n.lower() == "content-encoding"), None)
        self._tipo = next((v for n, v in cabecalhos if n.lower() == "content-type"), "")
        self._corpo, nota = decodificar_corpo(corpo, encoding)
        self._nome_arquivo = nome_arquivo
        partes = [formatar_bytes(tamanho_total)] if tamanho_total else ["sem corpo"]
        if self._tipo:
            partes.append(self._tipo.split(";")[0])
        if nota:
            partes.append(nota)
        if tamanho_total > len(corpo):
            partes.append(f"guardados só os primeiros {formatar_bytes(len(corpo))} (limite nas configurações)")
        self.rotulo_nota.setText(" · ".join(partes))
        self._renderizar()

    def _modo_automatico(self) -> str:
        tipo = self._tipo.lower()
        if tipo.startswith("image/"):
            return "Imagem"
        if "json" in tipo:
            return "JSON"
        if any(t in tipo for t in ("text/", "xml", "javascript", "x-www-form-urlencoded", "graphql")):
            return "Texto"
        try:
            self._corpo[:4096].decode("utf-8")
            return "Texto"
        except UnicodeDecodeError:
            return "Hex"

    def _renderizar(self) -> None:
        modo = self.combo_modo.currentText()
        if modo == "Automático":
            modo = self._modo_automatico()
        corpo = self._corpo[:LIMITE_EXIBICAO]
        if modo == "Imagem":
            pixmap = QPixmap()
            if pixmap.loadFromData(self._corpo):
                self.imagem.setPixmap(pixmap)
                self.pilha.setCurrentIndex(1)
                return
            modo = "Hex"
        self.pilha.setCurrentIndex(0)
        if modo == "Hex":
            self.texto.setPlainText(formatar_hex(corpo))
            return
        texto = corpo.decode(_charset(self._tipo), errors="replace")
        if modo == "JSON":
            try:
                texto = json.dumps(json.loads(texto), indent=2, ensure_ascii=False)
            except ValueError:
                pass
        elif "x-www-form-urlencoded" in self._tipo.lower():
            texto = "\n".join(f"{nome} = {valor}" for nome, valor in parse_qsl(texto, keep_blank_values=True))
        if len(self._corpo) > LIMITE_EXIBICAO:
            texto += f"\n\n... (exibidos {formatar_bytes(LIMITE_EXIBICAO)}; use Salvar para ver tudo)"
        self.texto.setPlainText(texto)

    def _copiar(self) -> None:
        if self.pilha.currentIndex() == 1 and self.imagem.pixmap() is not None:
            QApplication.clipboard().setPixmap(self.imagem.pixmap())
        else:
            QApplication.clipboard().setText(self.texto.toPlainText())

    def _salvar(self) -> None:
        caminho, _ = QFileDialog.getSaveFileName(self, "Salvar corpo", self._nome_arquivo)
        if caminho:
            with open(caminho, "wb") as arq:
                arq.write(self._corpo)


class PainelMensagem(QWidget):
    """Primeira linha, cabeçalhos e corpo de uma requisição ou resposta."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.primeira_linha = QLabel()
        self.primeira_linha.setFont(_fonte_mono())
        self.primeira_linha.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.primeira_linha.setWordWrap(True)
        layout.addWidget(self.primeira_linha)

        divisor = QSplitter(Qt.Orientation.Vertical)
        self.tabela = QTableWidget(0, 2)
        self.tabela.setHorizontalHeaderLabels(["Cabeçalho", "Valor"])
        self.tabela.verticalHeader().setVisible(False)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setWordWrap(False)
        self.tabela.horizontalHeader().setStretchLastSection(True)
        self.tabela.setColumnWidth(0, 200)
        self.tabela.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        acao_copiar = QAction("Copiar cabeçalhos", self.tabela)
        acao_copiar.triggered.connect(self._copiar_cabecalhos)
        self.tabela.addAction(acao_copiar)
        self.corpo = VisualizadorCorpo()
        divisor.addWidget(self.tabela)
        divisor.addWidget(self.corpo)
        divisor.setSizes([160, 300])
        layout.addWidget(divisor, 1)

    def mostrar(self, primeira_linha: str, cabecalhos: list[tuple[str, str]], corpo: bytes, tamanho: int,
                nome_arquivo: str) -> None:
        self.primeira_linha.setText(primeira_linha)
        self.tabela.setRowCount(len(cabecalhos))
        for linha, (nome, valor) in enumerate(cabecalhos):
            self.tabela.setItem(linha, 0, QTableWidgetItem(nome))
            item = QTableWidgetItem(valor)
            item.setToolTip(valor)
            self.tabela.setItem(linha, 1, item)
        self.corpo.mostrar(corpo, cabecalhos, tamanho, nome_arquivo)

    def _copiar_cabecalhos(self) -> None:
        linhas = [f"{self.tabela.item(i, 0).text()}: {self.tabela.item(i, 1).text()}"
                  for i in range(self.tabela.rowCount())]
        QApplication.clipboard().setText("\n".join(linhas))


class DialogoConfiguracao(QDialog):
    def __init__(self, ctx: Contexto, parent: QWidget):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Configurar interceptação")
        self.resize(520, 0)
        prefs = ctx.configuracao.preferencias
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.campo_hosts = QLineEdit(prefs.interceptacao_hosts)
        self.campo_hosts.setPlaceholderText("todos (ex.: *.exemplo.com; api.site.com)")
        self.campo_excecoes = QLineEdit(prefs.interceptacao_excecoes)
        self.campo_excecoes.setPlaceholderText("ex.: *.banco.com.br; *.apple.com")
        self.campo_limite = QSpinBox()
        self.campo_limite.setRange(0, 64 * 1024)
        self.campo_limite.setSuffix(" KB")
        self.campo_limite.setValue(prefs.interceptacao_limite_corpo_kb)
        self.caixa_verificar = QCheckBox("Recusar servidores com certificado inválido")
        self.caixa_verificar.setChecked(prefs.interceptacao_verificar_destino)
        form.addRow("Interceptar só estes hosts", self.campo_hosts)
        form.addRow("Nunca interceptar", self.campo_excecoes)
        form.addRow("Guardar de cada corpo até", self.campo_limite)
        form.addRow("", self.caixa_verificar)
        layout.addLayout(form)
        dica = QLabel("Separe os hosts com ponto e vírgula; *.exemplo.com vale também para exemplo.com. "
                      "Hosts fora do filtro passam sem serem decifrados. O corpo é repassado inteiro "
                      "mesmo acima do limite; o limite vale só para o que fica guardado. Sem a "
                      "verificação, certificados inválidos dos servidores são aceitos sem aviso.")
        dica.setObjectName("DicaCartao")
        dica.setWordWrap(True)
        layout.addWidget(dica)
        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        botoes.accepted.connect(self._salvar)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)

    def _salvar(self) -> None:
        prefs = self.ctx.configuracao.preferencias
        prefs.interceptacao_hosts = "; ".join(interpretar_padroes(self.campo_hosts.text()))
        prefs.interceptacao_excecoes = "; ".join(interpretar_padroes(self.campo_excecoes.text()))
        prefs.interceptacao_limite_corpo_kb = self.campo_limite.value()
        prefs.interceptacao_verificar_destino = self.caixa_verificar.isChecked()
        self.ctx.aplicar_mudancas()
        self.accept()


class JanelaInterceptacao(QMainWindow):
    # (função a chamar na thread da interface, resultado) vindo das tarefas em segundo plano
    _tarefa_concluida = Signal(object, object)

    def __init__(self, ctx: Contexto, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.modelo = ctx.modelo_fluxos
        self._fluxo_atual: Optional[Fluxo] = None
        self.setWindowTitle(f"Inspetor de tráfego — {NOME_EXIBICAO}")
        self.resize(1280, 820)
        self._tarefa_concluida.connect(lambda ao_terminar, resultado: ao_terminar(resultado))

        self._criar_barra()
        self._criar_tabela()
        self._criar_detalhes()

        self.pilha_lista = QStackedWidget()
        ajuda = QLabel(AJUDA_VAZIA)
        ajuda.setWordWrap(True)
        ajuda.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        ajuda.setContentsMargins(24, 16, 24, 16)
        self.pilha_lista.addWidget(self.tabela)
        self.pilha_lista.addWidget(ajuda)

        divisor = QSplitter(Qt.Orientation.Vertical)
        divisor.addWidget(self.pilha_lista)
        divisor.addWidget(self.abas)
        divisor.setSizes([380, 420])
        self.setCentralWidget(divisor)

        self.rotulo_contagem = QLabel()
        self.rotulo_estado = QLabel()
        for rotulo in (self.rotulo_contagem, self.rotulo_estado):
            rotulo.setContentsMargins(8, 0, 8, 0)
        self.statusBar().addWidget(self.rotulo_contagem, 1)
        self.statusBar().addPermanentWidget(self.rotulo_estado)

        self.modelo.rowsInserted.connect(self._atualizar_contagem)
        self.modelo.modelReset.connect(self._atualizar_contagem)
        ctx.fluxos_mudaram.connect(self._ao_mudar_fluxos)
        ctx.configuracao_mudou.connect(self._sincronizar_estado)
        ctx.estado_mudou.connect(lambda *_args: self._sincronizar_estado())
        self._sincronizar_estado()
        self._atualizar_contagem()

    # -- construção ----------------------------------------------------------------------------

    def _acao(self, texto: str, slot: Callable, icone: str | None = None, atalho: str | None = None,
              dica: str = "") -> QAction:
        acao = QAction(texto, self)
        if icone:
            acao.setIcon(icone_linha(icone))
        if atalho:
            acao.setShortcut(QKeySequence(atalho))
        acao.setToolTip(dica or texto)
        acao.triggered.connect(slot)
        self.addAction(acao)
        return acao

    def _criar_barra(self) -> None:
        barra = QToolBar("Inspetor")
        barra.setObjectName("BarraInspetor")
        barra.setMovable(False)
        barra.setIconSize(QSize(18, 18))
        barra.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        self.acao_interceptar = QAction("Interceptar", self)
        self.acao_interceptar.setCheckable(True)
        self.acao_interceptar.setToolTip("Liga/desliga a interceptação de HTTP e HTTPS nas conexões novas")
        self.acao_interceptar.toggled.connect(self._alternar_interceptacao)
        barra.addAction(self.acao_interceptar)
        barra.addSeparator()

        self.campo_filtro = QLineEdit()
        self.campo_filtro.setPlaceholderText("Filtrar: texto da URL, método:post, status:4, host:api, app:chrome")
        self.campo_filtro.setClearButtonEnabled(True)
        self.campo_filtro.setMinimumWidth(260)
        self.campo_filtro.textChanged.connect(self.modelo.definir_filtro)
        barra.addWidget(self.campo_filtro)
        self._acao("Filtrar", lambda: self.campo_filtro.setFocus(), atalho="Ctrl+F")
        barra.addSeparator()

        barra.addAction(self._acao("Limpar", self.limpar, "trash", "Ctrl+L", "Apaga os fluxos da lista"))
        barra.addAction(self._acao("Exportar HAR", self.exportar_har, "export", "Ctrl+S",
                                   "Salva os fluxos exibidos num arquivo HAR (DevTools, Insomnia, Charles...)"))

        menu_certificado = QMenu(self)
        menu_certificado.addAction("&Instalar no sistema...", self.instalar_certificado)
        menu_certificado.addAction("&Remover do sistema...", self.remover_certificado)
        menu_certificado.addSeparator()
        menu_certificado.addAction("&Exportar certificado...", self.exportar_certificado)
        menu_certificado.addAction("Ver &impressão digital...", self.mostrar_impressao_digital)
        menu_certificado.addSeparator()
        menu_certificado.addAction("&Gerar nova CA...", self.recriar_certificado)
        botao_certificado = QToolButton()
        botao_certificado.setText("Certificado")
        botao_certificado.setIcon(icone_linha("shield"))
        botao_certificado.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        botao_certificado.setMenu(menu_certificado)
        botao_certificado.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        barra.addWidget(botao_certificado)
        barra.addAction(self._acao("Configurar", lambda: DialogoConfiguracao(self.ctx, self).exec(), "settings",
                                   dica="Hosts interceptados, exceções e limite dos corpos"))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, barra)

    def _criar_tabela(self) -> None:
        self.tabela = QTableView()
        self.tabela.setModel(self.modelo)
        self.tabela.setShowGrid(False)
        self.tabela.setWordWrap(False)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)
        self.tabela.verticalHeader().setDefaultSectionSize(24)
        cabecalho = self.tabela.horizontalHeader()
        cabecalho.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        cabecalho.setStretchLastSection(True)
        cabecalho.setHighlightSections(False)
        for coluna, largura in LARGURAS_COLUNAS.items():
            self.tabela.setColumnWidth(coluna, largura)
        self.tabela.selectionModel().currentRowChanged.connect(
            lambda atual, _anterior: self._mostrar(self.modelo.fluxo_em(atual.row())))

        self.tabela.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        for texto, slot in (("Copiar URL", self.copiar_url), ("Copiar como cURL", self.copiar_curl),
                            ("Salvar corpo da resposta...", self.salvar_corpo_resposta),
                            ("Não interceptar este host", self.excluir_host)):
            acao = QAction(texto, self.tabela)
            acao.triggered.connect(slot)
            self.tabela.addAction(acao)

    def _criar_detalhes(self) -> None:
        self.abas = QTabWidget()
        self.painel_requisicao = PainelMensagem()
        self.painel_resposta = PainelMensagem()
        detalhes = QWidget()
        layout = QVBoxLayout(detalhes)
        layout.setContentsMargins(8, 8, 8, 8)
        self.texto_detalhes = QPlainTextEdit()
        self.texto_detalhes.setReadOnly(True)
        self.texto_detalhes.setFont(_fonte_mono())
        layout.addWidget(self.texto_detalhes, 1)
        linha = QHBoxLayout()
        linha.addStretch(1)
        for texto, slot in (("Copiar URL", self.copiar_url), ("Copiar como cURL", self.copiar_curl)):
            botao = QPushButton(texto)
            botao.clicked.connect(slot)
            linha.addWidget(botao)
        layout.addLayout(linha)
        self.abas.addTab(self.painel_requisicao, "Requisição")
        self.abas.addTab(self.painel_resposta, "Resposta")
        self.abas.addTab(detalhes, "Detalhes")
        self.abas.setEnabled(False)

    # -- exibição -------------------------------------------------------------------------------

    def _mostrar(self, fluxo: Optional[Fluxo]) -> None:
        self._fluxo_atual = fluxo
        self.abas.setEnabled(fluxo is not None)
        if fluxo is None:
            return
        base = (fluxo.caminho.rsplit("/", 1)[-1].split("?", 1)[0] or "corpo")
        if fluxo.metodo:
            self.painel_requisicao.mostrar(f"{fluxo.metodo} {fluxo.url} {fluxo.versao_http}",
                                           fluxo.cabecalhos_requisicao, fluxo.corpo_requisicao,
                                           fluxo.tamanho_requisicao, f"{base}-requisicao.bin")
        else:
            self.painel_requisicao.mostrar(f"(nenhuma requisição) {fluxo.url}", [], b"", 0, "corpo.bin")
        if fluxo.status:
            self.painel_resposta.mostrar(f"{fluxo.versao_http} {fluxo.status} {fluxo.motivo}",
                                         fluxo.cabecalhos_resposta, fluxo.corpo_resposta,
                                         fluxo.tamanho_resposta, base)
        else:
            self.painel_resposta.mostrar(fluxo.erro or "Aguardando a resposta...", [], b"", 0, base)
        self.texto_detalhes.setPlainText(self._texto_detalhes(fluxo))

    @staticmethod
    def _texto_detalhes(fluxo: Fluxo) -> str:
        linhas = [
            ("URL", fluxo.url),
            ("Método", fluxo.metodo or "—"),
            ("Status", f"{texto_status(fluxo)} {fluxo.motivo}".strip()),
            ("Início", time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(fluxo.momento))),
            ("Duração", f"{fluxo.duracao_ms} ms" if fluxo.estado != "pendente" else "em andamento"),
            ("Aplicativo", fluxo.processo or "desconhecido"),
            ("Protocolo", f"{fluxo.versao_http} sobre {'TLS' if fluxo.esquema == 'https' else 'TCP'}"
                          + (" (WebSocket)" if fluxo.websocket else "")),
            ("Enviado", formatar_bytes(fluxo.tamanho_requisicao)),
            ("Recebido", formatar_bytes(fluxo.tamanho_resposta)),
            ("Conexão no log", fluxo.id_conexao or "—"),
        ]
        if fluxo.erro:
            linhas.append(("Erro", fluxo.erro))
        largura = max(len(nome) for nome, _ in linhas)
        return "\n".join(f"{nome.ljust(largura)}  {valor}" for nome, valor in linhas)

    def _ao_mudar_fluxos(self, fluxos: list) -> None:
        atual = self._fluxo_atual
        if atual is not None:
            novo = next((f for f in fluxos if f.id == atual.id), None)
            if novo is not None:
                self._mostrar(novo)
        self._atualizar_contagem()

    def _atualizar_contagem(self, *_args) -> None:
        total = self.modelo.rowCount()
        sem_filtro = not self.campo_filtro.text().strip()
        self.pilha_lista.setCurrentIndex(1 if total == 0 and sem_filtro else 0)
        self.rotulo_contagem.setText(f"{total} fluxo(s)" + ("" if sem_filtro else " no filtro"))

    def _sincronizar_estado(self) -> None:
        prefs = self.ctx.configuracao.preferencias
        ativo = prefs.interceptacao_ativa
        self.acao_interceptar.blockSignals(True)
        self.acao_interceptar.setChecked(ativo)
        self.acao_interceptar.blockSignals(False)
        self.acao_interceptar.setIcon(icone_linha("stop", PALETA["danger"]) if ativo
                                      else icone_linha("play", PALETA["success"]))
        self.acao_interceptar.setText("Interceptando" if ativo else "Interceptar")
        if not ativo:
            estado = "Interceptação desligada"
        elif not self.ctx.motor.em_execucao():
            estado = "Interceptação ligada · motor parado"
        else:
            filtro = prefs.interceptacao_hosts or "todos os hosts"
            estado = f"Interceptando {filtro}"
        self.rotulo_estado.setText(estado)

    # -- comandos -------------------------------------------------------------------------------

    def _em_segundo_plano(self, funcao: Callable[[], object], ao_terminar: Callable[[object], None]) -> None:
        """Roda `funcao` numa thread (certutil/pkexec esperam o usuário) e entrega o resultado,
        ou a exceção, a `ao_terminar` na thread da interface."""
        def executar() -> None:
            try:
                resultado = funcao()
            except Exception as exc:
                resultado = exc
            self._tarefa_concluida.emit(ao_terminar, resultado)
        threading.Thread(target=executar, daemon=True).start()

    def _alternar_interceptacao(self, ligar: bool) -> None:
        self.ctx.configuracao.preferencias.interceptacao_ativa = ligar
        self.ctx.aplicar_mudancas()
        if ligar:
            self._em_segundo_plano(lambda: instalacao_ca.instalada(self.ctx.motor.certificado_ca()),
                                   self._oferecer_instalacao)

    def _oferecer_instalacao(self, instalada) -> None:
        if instalada is True:
            return
        resposta = QMessageBox.question(
            self, "Interceptação",
            f"O certificado da CA do {NOME_EXIBICAO} ainda não está instalado. Sem ele os aplicativos "
            "recusam o HTTPS interceptado (só o HTTP aparece).\n\nInstalar agora?")
        if resposta == QMessageBox.StandardButton.Yes:
            self.instalar_certificado(confirmar=False)

    def _avisar_resultado(self, titulo: str, resultado) -> None:
        if isinstance(resultado, Exception):
            QMessageBox.warning(self, titulo, f"Não foi possível concluir: {resultado}")
            return
        ok, mensagem = resultado
        (QMessageBox.information if ok else QMessageBox.warning)(self, titulo, mensagem)

    def instalar_certificado(self, confirmar: bool = True) -> None:
        if confirmar and QMessageBox.question(
                self, "Instalar certificado",
                f"Instalar a CA do {NOME_EXIBICAO} como confiável? Com ela, o {NOME_EXIBICAO} consegue "
                "decifrar o HTTPS dos aplicativos deste usuário. A chave privada fica só nesta "
                "máquina; remova o certificado quando não precisar mais.") != QMessageBox.StandardButton.Yes:
            return
        self._em_segundo_plano(lambda: instalacao_ca.instalar(self.ctx.motor.certificado_ca()),
                               lambda r: self._avisar_resultado("Instalar certificado", r))

    def remover_certificado(self) -> None:
        self._em_segundo_plano(lambda: instalacao_ca.remover(self.ctx.motor.certificado_ca()),
                               lambda r: self._avisar_resultado("Remover certificado", r))

    def exportar_certificado(self) -> None:
        try:
            pem = self.ctx.motor.certificado_ca()
        except Exception as exc:
            QMessageBox.warning(self, "Exportar certificado", f"Não foi possível obter o certificado: {exc}")
            return
        caminho, _ = QFileDialog.getSaveFileName(self, "Exportar certificado", "veredo-ca.crt",
                                                 "Certificado (*.crt *.pem *.cer)")
        if not caminho:
            return
        with open(caminho, "wb") as arq:
            arq.write(pem)
        porta = self.ctx.configuracao.preferencias.porta_pac
        QMessageBox.information(
            self, "Exportar certificado",
            f"Certificado salvo em {caminho}.\n\nCom o motor ligado ele também pode ser baixado em "
            f"http://127.0.0.1:{porta}{CAMINHO_CERTIFICADO_CA} (útil para o Firefox).")

    def mostrar_impressao_digital(self) -> None:
        def mostrar(resultado) -> None:
            if isinstance(resultado, Exception):
                QMessageBox.warning(self, "Certificado", f"Não foi possível obter o certificado: {resultado}")
            else:
                QMessageBox.information(self, "Certificado", f"SHA-256 da CA do {NOME_EXIBICAO}:\n\n{resultado}")
        self._em_segundo_plano(lambda: instalacao_ca.impressao_sha256(self.ctx.motor.certificado_ca()), mostrar)

    def recriar_certificado(self) -> None:
        if QMessageBox.question(
                self, "Gerar nova CA",
                "Gerar uma CA nova? A atual deixa de valer: remova-a do sistema antes (Certificado > "
                "Remover) e instale a nova depois.") != QMessageBox.StandardButton.Yes:
            return
        self._em_segundo_plano(lambda: self.ctx.motor.recriar_ca(),
                               lambda r: self._avisar_resultado(
                                   "Gerar nova CA", r if isinstance(r, Exception)
                                   else (True, "CA nova gerada. Instale o certificado novo no sistema.")))

    def limpar(self) -> None:
        self.ctx.fluxos.limpar()
        self.modelo.clear()
        self._mostrar(None)

    def exportar_har(self) -> None:
        fluxos = self.modelo.visiveis()
        if not fluxos:
            QMessageBox.information(self, "Exportar HAR", "Não há fluxos para exportar.")
            return
        caminho, _ = QFileDialog.getSaveFileName(self, "Exportar HAR", "veredo.har", "HAR (*.har *.json)")
        if not caminho:
            return
        with open(caminho, "w", encoding="utf-8") as arq:
            json.dump(exportar_har(fluxos), arq, ensure_ascii=False, indent=1)
        self.statusBar().showMessage(f"{len(fluxos)} fluxo(s) exportado(s) para {caminho}", 5000)

    def copiar_url(self) -> None:
        if self._fluxo_atual is not None:
            QApplication.clipboard().setText(self._fluxo_atual.url)

    def copiar_curl(self) -> None:
        if self._fluxo_atual is not None and self._fluxo_atual.metodo:
            QApplication.clipboard().setText(comando_curl(self._fluxo_atual))
            self.statusBar().showMessage("Comando cURL copiado", 3000)

    def salvar_corpo_resposta(self) -> None:
        if self._fluxo_atual is not None:
            self.abas.setCurrentWidget(self.painel_resposta)
            self.painel_resposta.corpo._salvar()

    def excluir_host(self) -> None:
        fluxo = self._fluxo_atual
        if fluxo is None or not fluxo.host:
            return
        prefs = self.ctx.configuracao.preferencias
        excecoes = interpretar_padroes(prefs.interceptacao_excecoes)
        if fluxo.host.lower() not in excecoes:
            prefs.interceptacao_excecoes = "; ".join(excecoes + [fluxo.host.lower()])
            self.ctx.aplicar_mudancas()
        self.statusBar().showMessage(f"{fluxo.host} não será mais interceptado", 5000)
