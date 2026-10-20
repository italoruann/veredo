"""Página de Proxies: cadastro dos perfis (host, porta, usuário, senha) usados pelas regras."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..contexto import Contexto
from ...nucleo.configuracao import PerfilProxy
from ..componentes.campos import CampoSenha
from ..componentes.responsivo import caixa_com_quebra
from ..teste_proxy import TesteProxy


class PaginaProxies(QWidget):
    def __init__(self, ctx: Contexto, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._atual: PerfilProxy | None = None
        self._teste: TesteProxy | None = None

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(28, 24, 28, 24)
        raiz.setSpacing(14)

        titulo = QLabel("Perfis de proxy")
        titulo.setObjectName("TituloSecao")
        subtitulo = QLabel("Cadastre um ou mais proxies (SOCKS5, SOCKS4, HTTP ou HTTPS) para usar nas regras "
                           "de roteamento. O nome não pode ter espaços, porque vai no texto das regras.")
        subtitulo.setObjectName("SubtituloPagina")
        subtitulo.setWordWrap(True)
        raiz.addWidget(titulo)
        raiz.addWidget(subtitulo)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        raiz.addWidget(splitter, 1)

        # -- lista à esquerda -------------------------------------------------
        esquerda = QWidget()
        layout_esquerda = QVBoxLayout(esquerda)
        layout_esquerda.setContentsMargins(0, 0, 0, 0)
        botoes_lista = QHBoxLayout()
        botao_novo = QPushButton("+ Novo perfil")
        botao_novo.setObjectName("BotaoPrincipal")
        botao_novo.clicked.connect(self._ao_criar)
        botoes_lista.addWidget(botao_novo)
        layout_esquerda.addLayout(botoes_lista)

        self.lista = QListWidget()
        self.lista.currentItemChanged.connect(self._ao_selecionar)
        layout_esquerda.addWidget(self.lista, 1)
        splitter.addWidget(esquerda)

        # -- formulário à direita ----------------------------------------------
        direita = QFrame()
        direita.setObjectName("Cartao")
        layout_form = QVBoxLayout(direita)
        layout_form.setContentsMargins(20, 18, 20, 18)
        layout_form.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        self.campo_nome = QLineEdit()
        self.combo_tipo = QComboBox()
        self.combo_tipo.addItem("SOCKS5", "socks5")
        self.combo_tipo.addItem("SOCKS4 / 4A", "socks4")
        self.combo_tipo.addItem("HTTP (CONNECT)", "http")
        self.combo_tipo.addItem("HTTPS (conexão criptografada até o proxy)", "https")
        self.campo_host = QLineEdit()
        self.campo_host.setPlaceholderText("ex.: proxy.minhaempresa.com")
        self.campo_porta = QSpinBox()
        self.campo_porta.setRange(1, 65535)
        self.campo_porta.setValue(1080)
        self.campo_usuario = QLineEdit()
        self.campo_usuario.setPlaceholderText("(opcional)")
        self.campo_senha = CampoSenha()
        self.caixa_ativo = QCheckBox("Habilitado")
        self.caixa_ativo.setChecked(True)
        self.caixa_padrao = QCheckBox("Usar como proxy padrão (quando a regra não especificar um nome)")

        form.addRow("Nome", self.campo_nome)
        form.addRow("Tipo", self.combo_tipo)
        form.addRow("Host", self.campo_host)
        form.addRow("Porta", self.campo_porta)
        form.addRow("Usuário", self.campo_usuario)
        form.addRow("Senha", self.campo_senha)
        form.addRow("", self.caixa_ativo)
        form.addRow("", caixa_com_quebra(self.caixa_padrao))
        layout_form.addLayout(form)

        self.rotulo_resultado = QLabel("")
        self.rotulo_resultado.setWordWrap(True)
        layout_form.addWidget(self.rotulo_resultado)

        linha_botoes = QHBoxLayout()
        self.botao_salvar = QPushButton("Salvar")
        self.botao_salvar.setObjectName("BotaoPrincipal")
        self.botao_salvar.clicked.connect(self._ao_salvar)
        self.botao_testar = QPushButton("Testar conexão")
        self.botao_testar.clicked.connect(self._ao_testar)
        self.botao_remover = QPushButton("Remover")
        self.botao_remover.setObjectName("BotaoPerigo")
        self.botao_remover.clicked.connect(self._ao_remover)
        linha_botoes.addWidget(self.botao_salvar)
        linha_botoes.addWidget(self.botao_testar)
        linha_botoes.addStretch(1)
        linha_botoes.addWidget(self.botao_remover)
        layout_form.addLayout(linha_botoes)
        layout_form.addStretch(1)

        splitter.addWidget(direita)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self._recarregar_lista()

    # -- auxiliares -----------------------------------------------------------

    def _recarregar_lista(self, selecionar_id: str | None = None) -> None:
        # A seleção final é feita com os sinais ligados para atualizar o formulário.
        self.lista.blockSignals(True)
        self.lista.clear()
        item_alvo: QListWidgetItem | None = None
        for perfil in self.ctx.configuracao.proxies:
            rotulo = f"{perfil.nome}"
            if perfil.padrao:
                rotulo += "  ★ padrão"
            if not perfil.ativo:
                rotulo += "  (desabilitado)"
            item = QListWidgetItem(rotulo)
            item.setData(Qt.ItemDataRole.UserRole, perfil.id)
            self.lista.addItem(item)
            if selecionar_id and perfil.id == selecionar_id:
                item_alvo = item
        self.lista.blockSignals(False)

        if item_alvo is not None:
            self.lista.setCurrentItem(item_alvo)
        elif self.lista.count() > 0:
            self.lista.setCurrentRow(0)
        else:
            self._atual = None
            self._limpar_form()
            self._habilitar_form(False)

    def _habilitar_form(self, ativo: bool) -> None:
        for widget in (self.campo_nome, self.combo_tipo, self.campo_host, self.campo_porta,
                       self.campo_usuario, self.campo_senha, self.caixa_ativo, self.caixa_padrao,
                       self.botao_salvar, self.botao_testar, self.botao_remover):
            widget.setEnabled(ativo)

    def _limpar_form(self) -> None:
        self.campo_nome.clear()
        self.campo_host.clear()
        self.campo_porta.setValue(1080)
        self.campo_usuario.clear()
        self.campo_senha.setText("")
        self.caixa_ativo.setChecked(True)
        self.caixa_padrao.setChecked(False)
        self.rotulo_resultado.setText("")

    def _carregar_perfil(self, perfil: PerfilProxy) -> None:
        self.campo_nome.setText(perfil.nome)
        indice = self.combo_tipo.findData(perfil.type)
        self.combo_tipo.setCurrentIndex(max(indice, 0))
        self.campo_host.setText(perfil.host)
        self.campo_porta.setValue(perfil.port)
        self.campo_usuario.setText(perfil.usuario)
        self.campo_senha.setText(perfil.senha)
        self.caixa_ativo.setChecked(perfil.ativo)
        self.caixa_padrao.setChecked(perfil.padrao)
        self.rotulo_resultado.setText("")

    # -- eventos -----------------------------------------------------------

    def _ao_selecionar(self, atual: QListWidgetItem | None, _anterior) -> None:
        if atual is None:
            self._atual = None
            self._limpar_form()
            self._habilitar_form(False)
            return
        id_perfil = atual.data(Qt.ItemDataRole.UserRole)
        perfil = next((p for p in self.ctx.configuracao.proxies if p.id == id_perfil), None)
        if perfil is None:
            return
        self._atual = perfil
        self._carregar_perfil(perfil)
        self._habilitar_form(True)

    def _ao_criar(self) -> None:
        perfil = PerfilProxy(nome=f"Novo proxy {len(self.ctx.configuracao.proxies) + 1}")
        if not self.ctx.configuracao.proxies:
            perfil.padrao = True
        self.ctx.configuracao.proxies.append(perfil)
        self.ctx.aplicar_mudancas()
        self._recarregar_lista(selecionar_id=perfil.id)

    def _ao_salvar(self) -> None:
        if self._atual is None:
            return
        perfil = self._atual
        perfil.nome = self.campo_nome.text().strip().replace(" ", "_") or perfil.nome
        perfil.type = self.combo_tipo.currentData()
        perfil.host = self.campo_host.text().strip()
        perfil.port = self.campo_porta.value()
        perfil.usuario = self.campo_usuario.text()
        perfil.senha = self.campo_senha.text()
        perfil.ativo = self.caixa_ativo.isChecked()
        perfil.padrao = self.caixa_padrao.isChecked()

        if perfil.padrao:
            for outro in self.ctx.configuracao.proxies:
                if outro.id != perfil.id:
                    outro.padrao = False
        self.ctx.configuracao.garantir_padrao_unico()

        self.ctx.aplicar_mudancas()
        self._recarregar_lista(selecionar_id=perfil.id)
        self.rotulo_resultado.setText("Perfil salvo.")

    def _ao_remover(self) -> None:
        if self._atual is None:
            return
        confirmacao = QMessageBox.question(self, "Remover proxy",
                                           f"Remover o perfil '{self._atual.nome}'?\n"
                                           "Regras que apontam para ele passarão a usar o proxy padrão.")
        if confirmacao != QMessageBox.StandardButton.Yes:
            return
        self.ctx.configuracao.proxies = [p for p in self.ctx.configuracao.proxies if p.id != self._atual.id]
        self.ctx.configuracao.garantir_padrao_unico()
        self.ctx.aplicar_mudancas()
        self._recarregar_lista()

    def _ao_testar(self) -> None:
        if self._atual is None:
            return
        candidato = PerfilProxy(
            nome=self.campo_nome.text(), type=self.combo_tipo.currentData(),
            host=self.campo_host.text().strip(), port=self.campo_porta.value(),
            usuario=self.campo_usuario.text(), senha=self.campo_senha.text(),
        )
        self.rotulo_resultado.setText("Testando conexão…")
        self.botao_testar.setEnabled(False)
        self._teste = TesteProxy(candidato)
        self._teste.teste_concluido.connect(self._ao_concluir_teste)
        self._teste.start()

    def _ao_concluir_teste(self, ok: bool, mensagem: str) -> None:
        self.botao_testar.setEnabled(True)
        prefixo = "✔ " if ok else "✘ "
        self.rotulo_resultado.setText(prefixo + mensagem)
