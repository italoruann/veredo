"""Página de Cadeias: perfis de reserva, usados na ordem quando o anterior não responde."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..contexto import Contexto
from ...nucleo.cliente_proxy import Rota
from ...nucleo.configuracao import CadeiaProxy
from ..teste_proxy import TesteProxy

def renomear_cadeia_nas_regras(texto: str, antigo: str, novo: str) -> str:
    return re.sub(rf"\+chain:{re.escape(antigo)}(?=\s|$)", f"+chain:{novo}", texto, flags=re.MULTILINE)


class PaginaCadeias(QWidget):
    def __init__(self, ctx: Contexto, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._atual: CadeiaProxy | None = None
        self._membros: list[str] = []
        self._teste: TesteProxy | None = None

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(28, 24, 28, 24)
        raiz.setSpacing(14)

        titulo = QLabel("Cadeias de proxy")
        titulo.setObjectName("TituloSecao")
        subtitulo = QLabel("Junte perfis de proxy como reserva um do outro: cada conexão usa o primeiro "
                           "da lista e, se ele não responder dentro do tempo limite, tenta o seguinte. "
                           "Nas regras, use +chain:Nome.")
        subtitulo.setObjectName("SubtituloPagina")
        subtitulo.setWordWrap(True)
        raiz.addWidget(titulo)
        raiz.addWidget(subtitulo)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        raiz.addWidget(splitter, 1)

        esquerda = QWidget()
        layout_esquerda = QVBoxLayout(esquerda)
        layout_esquerda.setContentsMargins(0, 0, 0, 0)
        botao_nova = QPushButton("+ Nova cadeia")
        botao_nova.setObjectName("BotaoPrincipal")
        botao_nova.clicked.connect(self._ao_criar)
        layout_esquerda.addWidget(botao_nova)
        self.lista = QListWidget()
        self.lista.currentItemChanged.connect(self._ao_selecionar)
        layout_esquerda.addWidget(self.lista, 1)
        splitter.addWidget(esquerda)

        direita = QFrame()
        direita.setObjectName("Cartao")
        layout_form = QVBoxLayout(direita)
        layout_form.setContentsMargins(20, 18, 20, 18)
        layout_form.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        self.campo_nome = QLineEdit()
        self.campo_nome.setPlaceholderText("sem espaços (vai nas regras)")
        self.campo_timeout = QSpinBox()
        self.campo_timeout.setRange(1, 120)
        self.campo_timeout.setSuffix(" s")
        self.campo_timeout.setToolTip("Tempo para desistir de um proxy e tentar o próximo")
        self.caixa_ativa = QCheckBox("Habilitada")
        form.addRow("Nome", self.campo_nome)
        form.addRow("Tempo limite por proxy", self.campo_timeout)
        form.addRow("", self.caixa_ativa)
        layout_form.addLayout(form)

        layout_form.addWidget(QLabel("Proxies da cadeia, em ordem:"))
        self.lista_membros = QListWidget()
        self.lista_membros.setMaximumHeight(140)
        botao_subir = QPushButton("▲")
        botao_subir.setToolTip("Mover para cima")
        botao_subir.clicked.connect(lambda: self._ao_mover_membro(-1))
        botao_descer = QPushButton("▼")
        botao_descer.setToolTip("Mover para baixo")
        botao_descer.clicked.connect(lambda: self._ao_mover_membro(1))
        botao_tirar = QPushButton("Tirar")
        botao_tirar.clicked.connect(self._ao_tirar_membro)
        coluna_ordem = QVBoxLayout()
        coluna_ordem.addWidget(botao_subir)
        coluna_ordem.addWidget(botao_descer)
        coluna_ordem.addWidget(botao_tirar)
        coluna_ordem.addStretch(1)
        linha_lista = QHBoxLayout()
        linha_lista.addWidget(self.lista_membros, 1)
        linha_lista.addLayout(coluna_ordem)
        layout_form.addLayout(linha_lista)

        linha_incluir = QHBoxLayout()
        self.combo_perfis = QComboBox()
        botao_incluir = QPushButton("Incluir na cadeia")
        botao_incluir.clicked.connect(self._ao_incluir_membro)
        linha_incluir.addWidget(self.combo_perfis, 1)
        linha_incluir.addWidget(botao_incluir)
        layout_form.addLayout(linha_incluir)

        self.rotulo_resultado = QLabel("")
        self.rotulo_resultado.setWordWrap(True)
        layout_form.addWidget(self.rotulo_resultado)

        linha_botoes = QHBoxLayout()
        self.botao_salvar = QPushButton("Salvar")
        self.botao_salvar.setObjectName("BotaoPrincipal")
        self.botao_salvar.clicked.connect(self._ao_salvar)
        self.botao_testar = QPushButton("Testar cadeia")
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

        self._controles = [self.campo_nome, self.campo_timeout, self.caixa_ativa,
                           self.lista_membros, self.combo_perfis, botao_incluir, botao_subir,
                           botao_descer, botao_tirar, self.botao_salvar, self.botao_testar, self.botao_remover]

        splitter.addWidget(direita)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        ctx.configuracao_mudou.connect(self._recarregar_perfis)
        self._recarregar_perfis()
        self._recarregar_lista()

    # -- auxiliares -----------------------------------------------------------

    def _perfis_por_id(self) -> dict:
        return {p.id: p for p in self.ctx.configuracao.proxies}

    def _recarregar_perfis(self) -> None:
        self.combo_perfis.clear()
        for perfil in self.ctx.configuracao.proxies:
            self.combo_perfis.addItem(perfil.nome, perfil.id)
        self._mostrar_membros()

    def _recarregar_lista(self, selecionar_id: str | None = None) -> None:
        self.lista.blockSignals(True)
        self.lista.clear()
        item_alvo: QListWidgetItem | None = None
        for cadeia in self.ctx.configuracao.cadeias:
            rotulo = cadeia.nome if cadeia.ativo else f"{cadeia.nome}  (desabilitada)"
            item = QListWidgetItem(rotulo)
            item.setData(Qt.ItemDataRole.UserRole, cadeia.id)
            self.lista.addItem(item)
            if cadeia.id == selecionar_id:
                item_alvo = item
        self.lista.blockSignals(False)

        if item_alvo is not None:
            self.lista.setCurrentItem(item_alvo)
        elif self.lista.count() > 0:
            self.lista.setCurrentRow(0)
        else:
            self._atual = None
            self._membros = []
            self._mostrar_membros()
            self.campo_nome.clear()
            self.rotulo_resultado.setText("")
            self._habilitar_form(False)

    def _habilitar_form(self, ativo: bool) -> None:
        for widget in self._controles:
            widget.setEnabled(ativo)

    def _mostrar_membros(self) -> None:
        perfis = self._perfis_por_id()
        self.lista_membros.clear()
        for posicao, id_perfil in enumerate(self._membros, start=1):
            perfil = perfis.get(id_perfil)
            if perfil is None:
                texto = f"{posicao}. (perfil removido)"
            else:
                texto = f"{posicao}. {perfil.nome}  —  {perfil.type.upper()} {perfil.host}:{perfil.port}"
                if not perfil.ativo:
                    texto += "  (desabilitado, será ignorado)"
            self.lista_membros.addItem(texto)

    # -- eventos -----------------------------------------------------------

    def _ao_selecionar(self, atual: QListWidgetItem | None, _anterior) -> None:
        cadeia = None
        if atual is not None:
            id_cadeia = atual.data(Qt.ItemDataRole.UserRole)
            cadeia = next((c for c in self.ctx.configuracao.cadeias if c.id == id_cadeia), None)
        self._atual = cadeia
        if cadeia is None:
            self._habilitar_form(False)
            return
        self._habilitar_form(True)
        self.campo_nome.setText(cadeia.nome)
        self.campo_timeout.setValue(cadeia.timeout)
        self.caixa_ativa.setChecked(cadeia.ativo)
        self._membros = list(cadeia.proxies)
        self._mostrar_membros()
        self.rotulo_resultado.setText("")

    def _ao_criar(self) -> None:
        nomes = {c.nome for c in self.ctx.configuracao.cadeias}
        numero = len(nomes) + 1
        while f"Cadeia{numero}" in nomes:
            numero += 1
        cadeia = CadeiaProxy(nome=f"Cadeia{numero}")
        self.ctx.configuracao.cadeias.append(cadeia)
        self.ctx.aplicar_mudancas()
        self._recarregar_lista(selecionar_id=cadeia.id)

    def _ao_incluir_membro(self) -> None:
        id_perfil = self.combo_perfis.currentData()
        if id_perfil:
            self._membros.append(id_perfil)
            self._mostrar_membros()
            self.lista_membros.setCurrentRow(len(self._membros) - 1)

    def _ao_tirar_membro(self) -> None:
        linha = self.lista_membros.currentRow()
        if linha >= 0:
            del self._membros[linha]
            self._mostrar_membros()

    def _ao_mover_membro(self, direcao: int) -> None:
        linha = self.lista_membros.currentRow()
        alvo = linha + direcao
        if linha < 0 or not (0 <= alvo < len(self._membros)):
            return
        self._membros[linha], self._membros[alvo] = self._membros[alvo], self._membros[linha]
        self._mostrar_membros()
        self.lista_membros.setCurrentRow(alvo)

    def _ao_salvar(self) -> None:
        cadeia = self._atual
        if cadeia is None:
            return
        nome = self.campo_nome.text().strip().replace(" ", "_")
        if not nome:
            QMessageBox.warning(self, "Cadeias", "O nome da cadeia não pode ficar vazio.")
            return
        if any(c.nome == nome and c.id != cadeia.id for c in self.ctx.configuracao.cadeias):
            QMessageBox.warning(self, "Cadeias", f"Já existe uma cadeia chamada '{nome}'.")
            return
        if nome != cadeia.nome:
            cfg = self.ctx.configuracao
            cfg.texto_regras = renomear_cadeia_nas_regras(cfg.texto_regras, cadeia.nome, nome)
        cadeia.nome = nome
        cadeia.timeout = self.campo_timeout.value()
        cadeia.ativo = self.caixa_ativa.isChecked()
        cadeia.proxies = [i for i in self._membros if i in self._perfis_por_id()]
        self.ctx.aplicar_mudancas()
        self._recarregar_lista(selecionar_id=cadeia.id)
        self.rotulo_resultado.setText("Cadeia salva.")

    def _ao_remover(self) -> None:
        cadeia = self._atual
        if cadeia is None:
            return
        resposta = QMessageBox.question(self, "Remover cadeia",
                                        f"Remover a cadeia '{cadeia.nome}'?\n"
                                        "Regras que apontam para ela passarão a falhar até serem editadas.")
        if resposta != QMessageBox.StandardButton.Yes:
            return
        self.ctx.configuracao.cadeias = [c for c in self.ctx.configuracao.cadeias if c.id != cadeia.id]
        self.ctx.aplicar_mudancas()
        self._recarregar_lista()

    def _ao_testar(self) -> None:
        if self._atual is None:
            return
        perfis = self._perfis_por_id()
        membros = [perfis[i] for i in self._membros if i in perfis and perfis[i].ativo]
        rota = Rota(id=self._atual.id, nome=self.campo_nome.text(), perfis=membros,
                    timeout=float(self.campo_timeout.value()), eh_cadeia=True)
        self.rotulo_resultado.setText("Testando a cadeia…")
        self.botao_testar.setEnabled(False)
        self._teste = TesteProxy(rota)
        self._teste.teste_concluido.connect(self._ao_concluir_teste)
        self._teste.start()

    def _ao_concluir_teste(self, ok: bool, mensagem: str) -> None:
        self.botao_testar.setEnabled(True)
        self.rotulo_resultado.setText(("✔ " if ok else "✘ ") + mensagem)
