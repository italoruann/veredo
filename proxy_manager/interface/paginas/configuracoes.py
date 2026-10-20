"""Configurações: portas locais, proxy do sistema, ação padrão, logs e preferências gerais."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..contexto import Contexto
from ...nucleo import inicio_automatico, integracao_sistema
from ...nucleo.configuracao import NOME_EXIBICAO, pasta_dados
from ..componentes.responsivo import caixa_com_quebra


def _cartao(texto_titulo: str) -> tuple[QFrame, QVBoxLayout]:
    quadro = QFrame()
    quadro.setObjectName("Cartao")
    layout = QVBoxLayout(quadro)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    titulo = QLabel(texto_titulo)
    titulo.setObjectName("TituloCartao")
    layout.addWidget(titulo)
    return quadro, layout


class PaginaConfiguracoes(QWidget):
    def __init__(self, ctx: Contexto, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx

        externo = QVBoxLayout(self)
        externo.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QScrollArea
        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setFrameShape(QFrame.Shape.NoFrame)
        # Só rola na vertical: o conteúdo acompanha a largura do painel de gestão.
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        externo.addWidget(rolagem)

        conteudo = QWidget()
        rolagem.setWidget(conteudo)
        raiz = QVBoxLayout(conteudo)
        raiz.setContentsMargins(16, 14, 16, 14)
        raiz.setSpacing(16)

        titulo = QLabel("Configurações")
        titulo.setObjectName("TituloSecao")
        raiz.addWidget(titulo)

        raiz.addWidget(self._criar_cartao_portas())
        raiz.addWidget(self._criar_cartao_integracao())
        raiz.addWidget(self._criar_cartao_acao_padrao())
        raiz.addWidget(self._criar_cartao_logs())
        raiz.addWidget(self._criar_cartao_geral())
        raiz.addWidget(self._criar_cartao_transparente())
        raiz.addStretch(1)

    # -- portas -----------------------------------------------------------------

    def _criar_cartao_portas(self) -> QFrame:
        quadro, layout = _cartao("Portas locais")
        form = QFormLayout()
        preferencias = self.ctx.configuracao.preferencias

        self.campo_porta_socks = QSpinBox()
        self.campo_porta_socks.setRange(1024, 65535)
        self.campo_porta_socks.setValue(preferencias.porta_socks)
        self.campo_porta_http = QSpinBox()
        self.campo_porta_http.setRange(1024, 65535)
        self.campo_porta_http.setValue(preferencias.porta_http)
        self.campo_porta_pac = QSpinBox()
        self.campo_porta_pac.setRange(1024, 65535)
        self.campo_porta_pac.setValue(preferencias.porta_pac)

        form.addRow("Porta SOCKS5", self.campo_porta_socks)
        form.addRow("Porta HTTP/HTTPS", self.campo_porta_http)
        form.addRow("Porta do arquivo PAC", self.campo_porta_pac)
        layout.addLayout(form)

        dica = QLabel("Sempre em 127.0.0.1 (apenas local). Se o motor estiver ativo, "
                      "ele será reiniciado automaticamente ao salvar.")
        dica.setObjectName("DicaCartao")
        dica.setWordWrap(True)
        layout.addWidget(dica)

        botao_salvar = QPushButton("Salvar portas")
        botao_salvar.setObjectName("BotaoPrincipal")
        botao_salvar.clicked.connect(self._ao_salvar_portas)
        linha = QHBoxLayout()
        linha.addStretch(1)
        linha.addWidget(botao_salvar)
        layout.addLayout(linha)
        return quadro

    def _ao_salvar_portas(self) -> None:
        preferencias = self.ctx.configuracao.preferencias
        preferencias.porta_socks = self.campo_porta_socks.value()
        preferencias.porta_http = self.campo_porta_http.value()
        preferencias.porta_pac = self.campo_porta_pac.value()

        if self.ctx.motor.em_execucao():
            ok, mensagem = self.ctx.aplicar_ao_vivo()
            # Portas que não puderam ser trocadas voltaram ao valor anterior.
            self.campo_porta_socks.setValue(preferencias.porta_socks)
            self.campo_porta_http.setValue(preferencias.porta_http)
            self.campo_porta_pac.setValue(preferencias.porta_pac)
            if ok:
                QMessageBox.information(self, "Portas", mensagem)
            else:
                QMessageBox.warning(self, "Portas", mensagem)
        else:
            self.ctx.aplicar_mudancas()
            QMessageBox.information(self, "Portas", "Portas atualizadas.")

    # -- integração com o sistema -----------------------------------------------

    def _criar_cartao_integracao(self) -> QFrame:
        quadro, layout = _cartao("Integração com o sistema")
        preferencias = self.ctx.configuracao.preferencias

        self.caixa_integracao = QCheckBox("Configurar automaticamente o proxy do sistema (via PAC)")
        self.caixa_integracao.setChecked(preferencias.integracao_sistema_ativa)
        layout.addWidget(caixa_com_quebra(self.caixa_integracao))

        linha_pac = QHBoxLayout()
        self.campo_url_pac = QLineEdit(integracao_sistema.url_pac(preferencias.porta_pac))
        self.campo_url_pac.setReadOnly(True)
        linha_pac.addWidget(QLabel("URL do PAC:"))
        linha_pac.addWidget(self.campo_url_pac, 1)
        layout.addLayout(linha_pac)

        dica = QLabel(
            f"Configura o sistema com um script PAC que envia o tráfego para o {NOME_EXIBICAO}, "
            "onde as suas regras decidem o caminho. A configuração é aplicada quando o motor "
            "inicia e removida quando ele para. Os botões abaixo servem para forçar manualmente. "
            "Se algum navegador não pegar a configuração, cole essa URL na opção de "
            "configuração automática de proxy dele."
        )
        dica.setWordWrap(True)
        dica.setObjectName("DicaCartao")
        layout.addWidget(dica)

        botoes = QHBoxLayout()
        botao_aplicar = QPushButton("Aplicar agora")
        botao_aplicar.setObjectName("BotaoPrincipal")
        botao_aplicar.clicked.connect(self._ao_aplicar_integracao)
        botao_remover = QPushButton("Remover")
        botao_remover.clicked.connect(self._ao_remover_integracao)
        botoes.addWidget(botao_aplicar)
        botoes.addWidget(botao_remover)
        botoes.addStretch(1)
        layout.addLayout(botoes)

        self.rotulo_integracao = QLabel("")
        self.rotulo_integracao.setWordWrap(True)
        layout.addWidget(self.rotulo_integracao)
        return quadro

    def _ao_aplicar_integracao(self) -> None:
        preferencias = self.ctx.configuracao.preferencias
        preferencias.integracao_sistema_ativa = True
        self.caixa_integracao.setChecked(True)
        self.ctx.aplicar_mudancas()
        if self.ctx.motor.em_execucao():
            ok, mensagem = integracao_sistema.aplicar_proxy_sistema(
                integracao_sistema.url_pac(preferencias.porta_pac), preferencias.porta_http)
            self.rotulo_integracao.setText(("✔ " if ok else "✘ ") + mensagem)
        else:
            # Com o motor parado o PAC não responde; a configuração é aplicada ao iniciar.
            self.rotulo_integracao.setText(
                "Preferência salva. Será aplicada automaticamente assim que você iniciar o "
                "motor no Dashboard, e removida sozinha quando ele parar.")

    def _ao_remover_integracao(self) -> None:
        preferencias = self.ctx.configuracao.preferencias
        preferencias.integracao_sistema_ativa = False
        self.caixa_integracao.setChecked(False)
        self.ctx.aplicar_mudancas()
        ok, mensagem = integracao_sistema.remover_proxy_sistema()
        self.rotulo_integracao.setText(("✔ " if ok else "✘ ") + mensagem)

    # -- ação padrão --------------------------------------------------------------

    def _criar_cartao_acao_padrao(self) -> QFrame:
        quadro, layout = _cartao("Comportamento padrão")
        form = QFormLayout()
        self.combo_acao_padrao = QComboBox()
        self.combo_acao_padrao.addItem("Ir direto (sem proxy)", "direct")
        self.combo_acao_padrao.addItem("Bloquear", "block")
        indice = self.combo_acao_padrao.findData(self.ctx.configuracao.preferencias.acao_padrao)
        self.combo_acao_padrao.setCurrentIndex(max(indice, 0))
        self.combo_acao_padrao.currentIndexChanged.connect(self._ao_mudar_acao_padrao)
        form.addRow("Quando nenhuma regra casar", self.combo_acao_padrao)
        layout.addLayout(form)
        return quadro

    def _ao_mudar_acao_padrao(self, _indice: int) -> None:
        self.ctx.configuracao.preferencias.acao_padrao = self.combo_acao_padrao.currentData()
        self.ctx.aplicar_mudancas()

    # -- logs -----------------------------------------------------------------------

    def _criar_cartao_logs(self) -> QFrame:
        quadro, layout = _cartao("Logs")
        form = QFormLayout()
        self.campo_retencao = QSpinBox()
        self.campo_retencao.setRange(1, 365)
        self.campo_retencao.setSuffix(" dias")
        self.campo_retencao.setValue(self.ctx.configuracao.preferencias.dias_retencao_log)
        self.campo_retencao.valueChanged.connect(self._ao_mudar_retencao)
        form.addRow("Manter histórico salvo por", self.campo_retencao)
        layout.addLayout(form)

        botoes = QHBoxLayout()
        botao_abrir_pasta = QPushButton("Abrir pasta de dados")
        botao_abrir_pasta.clicked.connect(self._abrir_pasta_dados)
        botao_limpar = QPushButton("Limpar histórico salvo")
        botao_limpar.setObjectName("BotaoPerigo")
        botao_limpar.clicked.connect(self._ao_limpar_historico)
        botoes.addWidget(botao_abrir_pasta)
        botoes.addWidget(botao_limpar)
        botoes.addStretch(1)
        layout.addLayout(botoes)
        return quadro

    def _ao_mudar_retencao(self, valor: int) -> None:
        self.ctx.configuracao.preferencias.dias_retencao_log = valor
        self.ctx.aplicar_mudancas()

    def _abrir_pasta_dados(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(pasta_dados())))

    def _ao_limpar_historico(self) -> None:
        confirmacao = QMessageBox.question(self, "Limpar histórico",
                                           "Apagar todo o histórico de conexões salvo em disco? "
                                           "Isso não pode ser desfeito.")
        if confirmacao == QMessageBox.StandardButton.Yes:
            self.ctx.registro.limpar_historico()
            QMessageBox.information(self, "Histórico", "Histórico salvo apagado.")

    # -- geral -----------------------------------------------------------------------

    def _criar_cartao_geral(self) -> QFrame:
        quadro, layout = _cartao("Geral")
        preferencias = self.ctx.configuracao.preferencias
        self.caixa_minimizar_bandeja = QCheckBox(
            "Ao fechar a janela, continuar rodando na bandeja do sistema")
        self.caixa_minimizar_bandeja.setChecked(preferencias.minimizar_para_bandeja)
        self.caixa_minimizar_bandeja.toggled.connect(self._ao_alternar_bandeja)
        layout.addWidget(caixa_com_quebra(self.caixa_minimizar_bandeja))

        self.caixa_iniciar_minimizado = QCheckBox("Iniciar minimizado na bandeja do sistema")
        self.caixa_iniciar_minimizado.setChecked(preferencias.iniciar_minimizado)
        self.caixa_iniciar_minimizado.setEnabled(preferencias.minimizar_para_bandeja)
        self.caixa_iniciar_minimizado.toggled.connect(self._ao_alternar_iniciar_minimizado)
        layout.addWidget(caixa_com_quebra(self.caixa_iniciar_minimizado))

        self.caixa_inicio_automatico = QCheckBox("Iniciar automaticamente com o sistema")
        self.caixa_inicio_automatico.setChecked(inicio_automatico.esta_ativo())
        self.caixa_inicio_automatico.toggled.connect(self._ao_alternar_inicio_automatico)
        layout.addWidget(caixa_com_quebra(self.caixa_inicio_automatico))

        self.caixa_aviso_voz = QCheckBox("Avisar por voz quando o IP de saída do proxy mudar")
        self.caixa_aviso_voz.setChecked(preferencias.aviso_voz_ip_ativo)
        self.caixa_aviso_voz.toggled.connect(self._ao_alternar_aviso_voz)
        botao_testar_voz = QPushButton("Testar voz")
        botao_testar_voz.clicked.connect(self._ao_testar_voz)
        linha_voz = QHBoxLayout()
        linha_voz.addWidget(caixa_com_quebra(self.caixa_aviso_voz), 1)
        linha_voz.addWidget(botao_testar_voz)
        layout.addLayout(linha_voz)
        return quadro

    def _ao_alternar_aviso_voz(self, marcado: bool) -> None:
        self.ctx.configuracao.preferencias.aviso_voz_ip_ativo = marcado
        self.ctx.aplicar_mudancas()

    def _ao_testar_voz(self) -> None:
        from ..aviso_voz import FRASE_TROCA_IP
        if not self.ctx.aviso_voz.falar(FRASE_TROCA_IP):
            QMessageBox.warning(self, "Aviso por voz",
                                "Nenhuma voz de síntese disponível no sistema. No Linux, instale o "
                                "speech-dispatcher.")

    def _ao_alternar_bandeja(self, marcado: bool) -> None:
        self.ctx.configuracao.preferencias.minimizar_para_bandeja = marcado
        # Iniciar minimizado depende da bandeja: sem ela, o app abriria sem janela nenhuma.
        self.caixa_iniciar_minimizado.setEnabled(marcado)
        self.ctx.aplicar_mudancas()

    def _ao_alternar_iniciar_minimizado(self, marcado: bool) -> None:
        self.ctx.configuracao.preferencias.iniciar_minimizado = marcado
        self.ctx.aplicar_mudancas()

    def _ao_alternar_inicio_automatico(self, marcado: bool) -> None:
        ok, mensagem = inicio_automatico.definir_ativo(marcado)
        if not ok:
            QMessageBox.warning(self, "Início automático", mensagem)
            self.caixa_inicio_automatico.blockSignals(True)
            self.caixa_inicio_automatico.setChecked(not marcado)
            self.caixa_inicio_automatico.blockSignals(False)

    # -- modo transparente -----------------------------------------------

    def _criar_cartao_transparente(self) -> QFrame:
        quadro, layout = _cartao("Modo transparente")
        preferencias = self.ctx.configuracao.preferencias

        self.caixa_transparente = QCheckBox(
            "Capturar qualquer aplicativo, mesmo sem configurar proxy nele (nftables no Linux / "
            "WinDivert no Windows)")
        self.caixa_transparente.setChecked(preferencias.modo_transparente_ativo)
        layout.addWidget(caixa_com_quebra(self.caixa_transparente))

        form = QFormLayout()
        self.campo_porta_transparente = QSpinBox()
        self.campo_porta_transparente.setRange(1024, 65535)
        self.campo_porta_transparente.setValue(preferencias.porta_transparente)
        form.addRow("Porta do listener transparente", self.campo_porta_transparente)
        layout.addLayout(form)

        dica = QLabel(
            "Intercepta as conexões no próprio sistema, inclusive de programas que ignoram a "
            f"configuração de proxy. Exige abrir o {NOME_EXIBICAO} como administrador (Windows) "
            "ou root (Linux). No Linux usa o nftables; no Windows, o driver WinDivert. Só TCP "
            "sobre IPv4 é interceptado. "
            "Liga e desliga junto com o motor; com o motor ligado, a mudança vale ao salvar."
        )
        dica.setWordWrap(True)
        dica.setObjectName("DicaCartao")
        layout.addWidget(dica)

        botao_salvar = QPushButton("Salvar")
        botao_salvar.setObjectName("BotaoPrincipal")
        botao_salvar.clicked.connect(self._ao_salvar_transparente)
        linha = QHBoxLayout()
        linha.addStretch(1)
        linha.addWidget(botao_salvar)
        layout.addLayout(linha)

        self.rotulo_transparente = QLabel("")
        self.rotulo_transparente.setWordWrap(True)
        layout.addWidget(self.rotulo_transparente)
        return quadro

    def _ao_salvar_transparente(self) -> None:
        preferencias = self.ctx.configuracao.preferencias
        preferencias.modo_transparente_ativo = self.caixa_transparente.isChecked()
        preferencias.porta_transparente = self.campo_porta_transparente.value()

        if self.ctx.motor.em_execucao():
            ok, mensagem = self.ctx.aplicar_ao_vivo()
            # Em caso de falha os valores anteriores foram restaurados.
            self.caixa_transparente.setChecked(preferencias.modo_transparente_ativo)
            self.campo_porta_transparente.setValue(preferencias.porta_transparente)
            self.rotulo_transparente.setText(mensagem)
        else:
            self.ctx.aplicar_mudancas()
            self.rotulo_transparente.setText(
                "Configurações salvas. Serão aplicadas quando você iniciar o motor no Dashboard.")
