"""Regras: cadastro de aplicativos e as regras de roteamento, editáveis em tabela ou em texto."""
from __future__ import annotations

import platform

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..contexto import Contexto
from ...nucleo.configuracao import TEXTO_REGRAS_PADRAO, Aplicativo
from ...nucleo.regras import Regra, ConjuntoRegras, dividir_alvo
from ..componentes.responsivo import LinhasResponsivas
from ..componentes.realce_regras import RealceRegras

ROTULOS_COLUNAS = ["Ativo", "Aplicativos", "Alvo", "Ação"]
# Cabeçalhos curtos para caber no painel lateral; a explicação completa fica no tooltip.
DICAS_COLUNAS = ["", "Vazio = qualquer aplicativo",
                 "Domínio, IP, faixa ou CIDR (* = qualquer destino); portas opcionais: *.site.com:443, *:80,8000-8100", ""]
ROTULOS_COLUNAS_APPS = ["Nome", "Executável"]

DIGITAR_MANUALMENTE = "✎ Digitar manualmente…"


def _filtro_executaveis() -> str:
    if platform.system() == "Windows":
        return "Executáveis (*.exe);;Todos os arquivos (*.*)"
    return "Todos os arquivos (*)"


class PaginaRegras(QWidget):
    def __init__(self, ctx: Contexto, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(28, 24, 28, 24)
        raiz.setSpacing(18)

        titulo_cabecalho = QLabel("Regras de roteamento")
        titulo_cabecalho.setObjectName("TituloSecao")
        raiz.addWidget(titulo_cabecalho)

        raiz.addWidget(self._criar_cartao_aplicativos())

        titulo_regras = QLabel("Regras")
        titulo_regras.setObjectName("TituloSecao")
        subtitulo = QLabel("Avaliadas de cima para baixo: a primeira regra que casar com o "
                           "aplicativo e o destino define a ação.")
        subtitulo.setObjectName("SubtituloPagina")
        subtitulo.setWordWrap(True)
        raiz.addWidget(titulo_regras)
        raiz.addWidget(subtitulo)

        self.abas = QTabWidget()
        self.abas.addTab(self._criar_aba_tabela(), "Tabela")
        self.abas.addTab(self._criar_aba_texto(), "Texto avançado")
        raiz.addWidget(self.abas, 1)

        self._carregar_da_configuracao()
        self._recarregar_tabela_apps()

    # -- seção de aplicativos cadastrados --------------------------------------

    def _criar_cartao_aplicativos(self) -> QFrame:
        cartao = QFrame()
        cartao.setObjectName("Cartao")
        layout = QVBoxLayout(cartao)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        titulo = QLabel("Aplicativos cadastrados")
        titulo.setObjectName("TituloCartao")
        dica = QLabel("Cadastre os programas que você vai usar nas regras, para não precisar "
                      "lembrar o nome exato do processo.")
        dica.setObjectName("DicaCartao")
        dica.setWordWrap(True)
        layout.addWidget(titulo)
        layout.addWidget(dica)

        self.campo_nome_app = QLineEdit()
        self.campo_nome_app.setPlaceholderText("Nome (ex.: Chrome)")
        self.campo_caminho_app = QLineEdit()
        self.campo_caminho_app.setPlaceholderText("Caminho do executável (ex.: /usr/bin/google-chrome) ou clique em Procurar…")
        botao_procurar = QPushButton("Procurar…")
        botao_procurar.clicked.connect(self._ao_procurar_executavel)
        self.botao_adicionar_app = QPushButton("+ Adicionar")
        self.botao_adicionar_app.setObjectName("BotaoPrincipal")
        self.botao_adicionar_app.setEnabled(False)
        self.botao_adicionar_app.setToolTip("Escolha um executável em \"Procurar…\" primeiro.")
        self.botao_adicionar_app.clicked.connect(self._ao_adicionar_app)
        # "Procurar…" só preenche os campos; o botão Adicionar é que salva.
        self.campo_caminho_app.textChanged.connect(
            lambda texto: self.botao_adicionar_app.setEnabled(bool(texto.strip())))
        # Duas linhas (campos / botões) para caber no painel lateral estreito.
        campos = QHBoxLayout()
        campos.addWidget(self.campo_nome_app, 1)
        campos.addWidget(self.campo_caminho_app, 2)
        layout.addLayout(campos)
        botoes_form = QHBoxLayout()
        botoes_form.addStretch(1)
        botoes_form.addWidget(botao_procurar)
        botoes_form.addWidget(self.botao_adicionar_app)
        layout.addLayout(botoes_form)

        self.tabela_apps = QTableWidget(0, len(ROTULOS_COLUNAS_APPS))
        self.tabela_apps.setHorizontalHeaderLabels(ROTULOS_COLUNAS_APPS)
        self.tabela_apps.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela_apps.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela_apps.verticalHeader().setVisible(False)
        self.tabela_apps.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabela_apps.setMaximumHeight(120)
        layout.addWidget(self.tabela_apps)

        linha_remover = QHBoxLayout()
        linha_remover.addStretch(1)
        botao_remover = QPushButton("Remover selecionado")
        botao_remover.setObjectName("BotaoPerigo")
        botao_remover.clicked.connect(self._ao_remover_app)
        linha_remover.addWidget(botao_remover)
        layout.addLayout(linha_remover)
        return cartao

    def _ao_procurar_executavel(self) -> None:
        caminho, _ = QFileDialog.getOpenFileName(self, "Selecionar executável", "", _filtro_executaveis())
        if not caminho:
            return
        self.campo_caminho_app.setText(caminho)
        if not self.campo_nome_app.text().strip():
            sugerido = Aplicativo(caminho_executavel=caminho).nome_processo
            self.campo_nome_app.setText(sugerido.rsplit(".", 1)[0] if "." in sugerido else sugerido)

    def _ao_adicionar_app(self) -> None:
        caminho = self.campo_caminho_app.text().strip()
        if not caminho:
            QMessageBox.warning(self, "Aplicativos", "Escolha o executável com \"Procurar…\" antes de adicionar.")
            return
        nome = self.campo_nome_app.text().strip() or Aplicativo(caminho_executavel=caminho).nome_processo
        novo_app = Aplicativo(nome=nome, caminho_executavel=caminho)
        self.ctx.configuracao.aplicativos.append(novo_app)

        regra_criada = self._garantir_regra_geral(novo_app.nome_processo)

        self.ctx.aplicar_mudancas()
        self.campo_nome_app.clear()
        self.campo_caminho_app.clear()
        self._recarregar_tabela_apps()
        self._atualizar_visoes_regras()

        if regra_criada:
            QMessageBox.information(
                self, "Aplicativos",
                f'"{novo_app.nome}" cadastrado. Como ainda não havia regra para ele, foi criada uma '
                f"que manda todo o tráfego de {novo_app.nome_processo} pelo proxy padrão. Para tratar "
                "sites específicos de outro jeito, adicione regras ACIMA dela na lista."
            )

    def _garantir_regra_geral(self, nome_processo: str) -> bool:
        """Cria a regra `*` para o app se ele ainda não tiver nenhuma. True se criou."""
        if not nome_processo:
            return False
        conjunto_regras = ConjuntoRegras.interpretar(self.ctx.configuracao.texto_regras)
        ja_tem_regra = any(nome_processo.lower() in (a.lower() for a in r.aplicativos) for r in conjunto_regras.regras)
        if ja_tem_regra:
            return False
        conjunto_regras.regras.append(Regra(aplicativos=[nome_processo], alvo="*", acao="proxy"))
        self.ctx.configuracao.texto_regras = conjunto_regras.para_texto()
        return True

    def _atualizar_visoes_regras(self) -> None:
        conjunto_regras = ConjuntoRegras.interpretar(self.ctx.configuracao.texto_regras)
        self._preencher_tabela(conjunto_regras)
        self.editor_texto.blockSignals(True)
        self.editor_texto.setPlainText(self.ctx.configuracao.texto_regras)
        self.editor_texto.blockSignals(False)

    def _ao_remover_app(self) -> None:
        linha = self.tabela_apps.currentRow()
        if linha < 0:
            return
        del self.ctx.configuracao.aplicativos[linha]
        self.ctx.aplicar_mudancas()
        self._recarregar_tabela_apps()

    def _recarregar_tabela_apps(self) -> None:
        self.tabela_apps.setRowCount(0)
        for app in self.ctx.configuracao.aplicativos:
            linha = self.tabela_apps.rowCount()
            self.tabela_apps.insertRow(linha)
            self.tabela_apps.setItem(linha, 0, QTableWidgetItem(app.nome))
            self.tabela_apps.setItem(linha, 1, QTableWidgetItem(app.caminho_executavel))

    def _escolher_app_cadastrado(self) -> str | None:
        """Oferece os apps cadastrados. None se o usuário preferir digitar (ou cancelar)."""
        if not self.ctx.configuracao.aplicativos:
            return None
        opcoes = [f"{a.nome} ({a.nome_processo})" for a in self.ctx.configuracao.aplicativos] + [DIGITAR_MANUALMENTE]
        escolha, ok = QInputDialog.getItem(self, "Escolher aplicativo", "Aplicativo cadastrado:",
                                           opcoes, 0, False)
        if not ok or escolha == DIGITAR_MANUALMENTE:
            return None
        indice = opcoes.index(escolha)
        return self.ctx.configuracao.aplicativos[indice].nome_processo

    # -- aba tabela ----------------------------------------------------------

    def _criar_aba_tabela(self) -> QWidget:
        aba = QWidget()
        layout = QVBoxLayout(aba)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.tabela = QTableWidget(0, len(ROTULOS_COLUNAS))
        self.tabela.setHorizontalHeaderLabels(ROTULOS_COLUNAS)
        for coluna, dica_coluna in enumerate(DICAS_COLUNAS):
            if dica_coluna:
                self.tabela.horizontalHeaderItem(coluna).setToolTip(dica_coluna)
        self.tabela.horizontalHeader().setStretchLastSection(False)
        self.tabela.horizontalHeader().setSectionResizeMode(1, self.tabela.horizontalHeader().ResizeMode.Stretch)
        self.tabela.horizontalHeader().setSectionResizeMode(2, self.tabela.horizontalHeader().ResizeMode.Stretch)
        self.tabela.horizontalHeader().setSectionResizeMode(0, self.tabela.horizontalHeader().ResizeMode.ResizeToContents)
        # A coluna Ação guarda um QComboBox (cell widget), que ResizeToContents não enxerga.
        self.tabela.horizontalHeader().setSectionResizeMode(3, self.tabela.horizontalHeader().ResizeMode.Fixed)
        self.tabela.setColumnWidth(3, 150)
        self.tabela.verticalHeader().setDefaultSectionSize(38)  # altura da combo + respiro
        self.tabela.verticalHeader().setVisible(False)
        layout.addWidget(self.tabela, 1)

        botao_adicionar = QPushButton("+ Adicionar regra")
        botao_adicionar.setObjectName("BotaoPrincipal")
        botao_adicionar.clicked.connect(self._ao_adicionar_linha)
        botao_app_geral = QPushButton("+ App com proxy padrão")
        botao_app_geral.setToolTip(
            "Cria uma regra com alvo * para um aplicativo: todo o tráfego dele vai pelo proxy "
            "padrão. Regras colocadas ACIMA dela continuam tendo prioridade."
        )
        botao_app_geral.clicked.connect(self._ao_adicionar_app_geral)
        botao_duplicar = QPushButton("Duplicar")
        botao_duplicar.clicked.connect(self._ao_duplicar_linha)
        botao_remover = QPushButton("Remover")
        botao_remover.setObjectName("BotaoPerigo")
        botao_remover.clicked.connect(self._ao_remover_linha)
        botao_subir = QPushButton("▲")
        botao_subir.setToolTip("Mover para cima (maior prioridade)")
        botao_subir.clicked.connect(lambda: self._ao_mover_linha(-1))
        botao_descer = QPushButton("▼")
        botao_descer.setToolTip("Mover para baixo (menor prioridade)")
        botao_descer.clicked.connect(lambda: self._ao_mover_linha(1))
        botao_salvar = QPushButton("Salvar alterações")
        botao_salvar.setObjectName("BotaoPrincipal")
        botao_salvar.clicked.connect(self._ao_salvar_tabela)

        # Criar em cima; organizar e salvar embaixo, em duas linhas se o painel for estreito.
        criar = [(botao_adicionar, 0), (botao_app_geral, 0), (None, 1)]
        organizar = [(botao_subir, 0), (botao_descer, 0), (botao_duplicar, 0), (botao_remover, 0)]
        layout.addWidget(LinhasResponsivas([
            (440, [criar, organizar + [(None, 1), (botao_salvar, 0)]]),
            (0, [criar, organizar + [(None, 1)], [(None, 1), (botao_salvar, 0)]]),
        ], espacamento=8))
        return aba

    def _combo_acao(self, tipo_acao: str, nome_proxy: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItem("Proxy padrão", ("proxy", None))
        combo.addItem("Direto (sem proxy)", ("direct", None))
        combo.addItem("Bloquear", ("block", None))
        for perfil in self.ctx.configuracao.proxies:
            combo.addItem(f"Proxy: {perfil.nome}", ("proxy", perfil.nome))
        desejado = (tipo_acao, nome_proxy if tipo_acao == "proxy" else None)
        for i in range(combo.count()):
            if combo.itemData(i) == desejado:
                combo.setCurrentIndex(i)
                break
        return combo

    def _inserir_linha(self, indice: int, aplicativos: str = "", alvo: str = "",
                       tipo_acao: str = "proxy", nome_proxy: str | None = None,
                       ativo: bool = True) -> None:
        self.tabela.insertRow(indice)
        item_ativo = QTableWidgetItem()
        item_ativo.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled |
                            Qt.ItemFlag.ItemIsSelectable)
        item_ativo.setCheckState(Qt.CheckState.Checked if ativo else Qt.CheckState.Unchecked)
        self.tabela.setItem(indice, 0, item_ativo)
        self.tabela.setItem(indice, 1, QTableWidgetItem(aplicativos))
        self.tabela.setItem(indice, 2, QTableWidgetItem(alvo))
        self.tabela.setCellWidget(indice, 3, self._combo_acao(tipo_acao, nome_proxy))

    def _dados_linha(self, linha: int) -> dict:
        combo: QComboBox = self.tabela.cellWidget(linha, 3)  # type: ignore[assignment]
        tipo, nome_proxy = combo.currentData()
        return {
            "enabled": self.tabela.item(linha, 0).checkState() == Qt.CheckState.Checked,
            "apps": self.tabela.item(linha, 1).text(),
            "pattern": self.tabela.item(linha, 2).text(),
            "action_kind": tipo,
            "proxy_name": nome_proxy,
        }

    def _definir_dados_linha(self, linha: int, dados: dict) -> None:
        self.tabela.item(linha, 0).setCheckState(
            Qt.CheckState.Checked if dados["enabled"] else Qt.CheckState.Unchecked)
        self.tabela.item(linha, 1).setText(dados["apps"])
        self.tabela.item(linha, 2).setText(dados["pattern"])
        self.tabela.setCellWidget(linha, 3, self._combo_acao(dados["action_kind"], dados["proxy_name"]))

    def _preencher_tabela(self, conjunto_regras: ConjuntoRegras) -> None:
        self.tabela.setRowCount(0)
        for regra in conjunto_regras.regras:
            linha = self.tabela.rowCount()
            self._inserir_linha(linha, aplicativos=", ".join(regra.aplicativos), alvo=regra.alvo_completo,
                                tipo_acao=regra.tipo_acao, nome_proxy=regra.nome_proxy,
                                ativo=regra.ativo)

    def _regras_da_tabela(self) -> ConjuntoRegras:
        regras: list[Regra] = []
        for linha in range(self.tabela.rowCount()):
            dados = self._dados_linha(linha)
            alvo = dados["pattern"].strip()
            if not alvo:
                continue
            aplicativos = [a.strip() for a in dados["apps"].split(",") if a.strip()]
            tipo, nome_proxy = dados["action_kind"], dados["proxy_name"]
            if tipo == "proxy" and nome_proxy:
                acao = f"proxy:{nome_proxy}"
            else:
                acao = tipo
            alvo, portas = dividir_alvo(alvo)
            regras.append(Regra(aplicativos=aplicativos, alvo=alvo, portas=portas, acao=acao,
                                ativo=dados["enabled"]))
        return ConjuntoRegras(regras)

    def _ao_adicionar_linha(self) -> None:
        self._inserir_linha(self.tabela.rowCount())

    def _ao_adicionar_app_geral(self) -> None:
        """Todo o tráfego de um app pelo proxy padrão (`apps: <app>` + `*`)."""
        nome_processo = self._escolher_app_cadastrado()
        linha = self.tabela.rowCount()
        self._inserir_linha(linha, aplicativos=nome_processo or "", alvo="*", tipo_acao="proxy")
        self.tabela.setCurrentCell(linha, 1)
        if not nome_processo:
            self.tabela.editItem(self.tabela.item(linha, 1))

    def _ao_duplicar_linha(self) -> None:
        linha = self.tabela.currentRow()
        if linha < 0:
            return
        dados = self._dados_linha(linha)
        self._inserir_linha(linha + 1, aplicativos=dados["apps"], alvo=dados["pattern"],
                            tipo_acao=dados["action_kind"], nome_proxy=dados["proxy_name"],
                            ativo=dados["enabled"])

    def _ao_remover_linha(self) -> None:
        linha = self.tabela.currentRow()
        if linha >= 0:
            self.tabela.removeRow(linha)

    def _ao_mover_linha(self, direcao: int) -> None:
        linha = self.tabela.currentRow()
        alvo = linha + direcao
        if linha < 0 or not (0 <= alvo < self.tabela.rowCount()):
            return
        dados_linha = self._dados_linha(linha)
        dados_alvo = self._dados_linha(alvo)
        self._definir_dados_linha(linha, dados_alvo)
        self._definir_dados_linha(alvo, dados_linha)
        self.tabela.setCurrentCell(alvo, 0)

    def _ao_salvar_tabela(self) -> None:
        conjunto_regras = self._regras_da_tabela()
        self.ctx.configuracao.texto_regras = conjunto_regras.para_texto()
        self.ctx.aplicar_mudancas()
        self._atualizar_visoes_regras()
        QMessageBox.information(self, "Regras", "Regras salvas e aplicadas ao motor.")

    # -- aba texto -------------------------------------------------------------

    def _criar_aba_texto(self) -> QWidget:
        aba = QWidget()
        layout = QVBoxLayout(aba)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        rotulo_ajuda = QLabel(
            "Sintaxe: <code>apps: proc1.exe, proc2</code> define o escopo das linhas seguintes "
            "(<code>apps: *</code> volta a valer para todos); várias linhas <code>apps:</code> "
            "seguidas somam os apps. Cada regra é "
            "<code>padrao.dominio.com [+direct|+block|+proxy:nome]</code>; sem sufixo, "
            "usa o proxy padrão. Para limitar a portas, use <code>alvo:443</code>, "
            "<code>*:80,8000-8100</code> ou <code>[::1]:443</code> (sem espaços). "
            "Prefixe com <code>!</code> para desabilitar uma regra sem apagá-la."
        )
        rotulo_ajuda.setWordWrap(True)
        rotulo_ajuda.setObjectName("DicaCartao")
        layout.addWidget(rotulo_ajuda)

        self.editor_texto = QPlainTextEdit()
        self.editor_texto.setObjectName("EditorRegras")
        self._realce = RealceRegras(self.editor_texto.document())
        layout.addWidget(self.editor_texto, 1)

        botoes = QHBoxLayout()
        botao_aplicar = QPushButton("Aplicar")
        botao_aplicar.setObjectName("BotaoPrincipal")
        botao_aplicar.clicked.connect(self._ao_aplicar_texto)
        botao_restaurar = QPushButton("Restaurar exemplo")
        botao_restaurar.clicked.connect(self._ao_restaurar_texto)
        botoes.addWidget(botao_aplicar)
        botoes.addWidget(botao_restaurar)
        botoes.addStretch(1)
        layout.addLayout(botoes)
        return aba

    def _ao_aplicar_texto(self) -> None:
        texto = self.editor_texto.toPlainText()
        conjunto_regras = ConjuntoRegras.interpretar(texto)
        self.ctx.configuracao.texto_regras = texto
        self.ctx.aplicar_mudancas()
        self._preencher_tabela(conjunto_regras)
        QMessageBox.information(self, "Regras", f"{len(conjunto_regras.regras)} regra(s) aplicadas ao motor.")

    def _ao_restaurar_texto(self) -> None:
        self.editor_texto.setPlainText(TEXTO_REGRAS_PADRAO)

    # -- sincronização geral ----------------------------------------------------

    def _carregar_da_configuracao(self) -> None:
        conjunto_regras = ConjuntoRegras.interpretar(self.ctx.configuracao.texto_regras)
        self._preencher_tabela(conjunto_regras)
        self.editor_texto.setPlainText(self.ctx.configuracao.texto_regras)

