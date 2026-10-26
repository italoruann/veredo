"""Configurações: portas locais, proxy do sistema, ação padrão, logs, preferências gerais,
modo transparente e serviço do sistema."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..contexto import Contexto
from ...nucleo import inicio_automatico, integracao_sistema, servico
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
    # (instalando, ok, mensagem) vindo da thread que espera o UAC/pkexec
    _servico_concluido = Signal(bool, bool, str)

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
        raiz.addWidget(self._criar_cartao_servico())
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
            "ou root (Linux). No Linux usa o nftables; no Windows, o driver WinDivert. TCP "
            "(IPv4 e IPv6) é interceptado; QUIC é bloqueado para os navegadores usarem TCP. "
            "Liga e desliga junto com o motor; com o motor ligado, a mudança vale ao salvar."
        )
        dica.setWordWrap(True)
        dica.setObjectName("DicaCartao")
        layout.addWidget(dica)

        form_dns = QFormLayout()
        self.combo_dns = QComboBox()
        self.combo_dns.addItem("DNS do sistema (sem desvio)", "sistema")
        self.combo_dns.addItem("DNS do proxy", "proxy")
        self.combo_dns.addItem("DNS personalizado", "personalizado")
        self.campo_dns_servidores = QLineEdit(preferencias.dns_servidores)
        self.campo_dns_excecoes = QLineEdit(preferencias.dns_excecoes)
        self.campo_dns_excecoes.setToolTip(
            "Nomes resolvidos pelo DNS da sua rede (nomes locais), separados por ponto e vírgula")
        self.campo_porta_dns = QSpinBox()
        self.campo_porta_dns.setRange(1024, 65535)
        self.campo_porta_dns.setValue(preferencias.porta_dns)
        self.rotulo_dns_servidores = QLabel()
        form_dns.addRow("DNS", self.combo_dns)
        form_dns.addRow(self.rotulo_dns_servidores, self.campo_dns_servidores)
        form_dns.addRow("Resolver pela rede local", self.campo_dns_excecoes)
        form_dns.addRow("Porta do DNS local", self.campo_porta_dns)
        layout.addLayout(form_dns)

        self.dica_dns = QLabel()
        self.dica_dns.setWordWrap(True)
        self.dica_dns.setObjectName("DicaCartao")
        layout.addWidget(self.dica_dns)
        self._selecionar_modo_dns(preferencias.modo_dns)
        self.combo_dns.currentIndexChanged.connect(self._atualizar_widgets_dns)

        self.caixa_vazamento = QCheckBox("Prevenção de vazamento de DNS e IP")
        self.caixa_vazamento.setChecked(preferencias.prevenir_vazamento)
        layout.addWidget(caixa_com_quebra(self.caixa_vazamento))
        dica_vazamento = QLabel(
            "Liga o DNS pelo proxy, responde vazio às consultas DNS que não são de endereço "
            "(MX, TXT, SRV...) e bloqueia todo o UDP de saída, o que impede o WebRTC de revelar "
            "o seu IP. Jogos, chamadas de voz e VPNs que usam UDP deixam de funcionar."
        )
        dica_vazamento.setWordWrap(True)
        dica_vazamento.setObjectName("DicaCartao")
        layout.addWidget(dica_vazamento)

        self.caixa_udp = QCheckBox("Encaminhar UDP pelas regras (jogos, chamadas, QUIC)")
        self.caixa_udp.setChecked(preferencias.udp_pelas_regras)
        layout.addWidget(caixa_com_quebra(self.caixa_udp))
        dica_udp = QLabel(
            "Cada fluxo UDP segue as regras como uma conexão: direto, bloqueado ou por um proxy "
            "SOCKS5 que aceite UDP (HTTP, HTTPS e SOCKS4 não transportam UDP). Com a prevenção "
            "de vazamento, o UDP que iria direto é bloqueado. Desligado, só o QUIC é bloqueado."
        )
        dica_udp.setWordWrap(True)
        dica_udp.setObjectName("DicaCartao")
        layout.addWidget(dica_udp)

        self._widgets_dns = [self.combo_dns, self.campo_dns_excecoes, self.campo_dns_servidores,
                             self.campo_porta_dns, self.caixa_vazamento, self.caixa_udp]
        self.caixa_transparente.toggled.connect(self._atualizar_widgets_dns)
        self.caixa_vazamento.toggled.connect(self._atualizar_widgets_dns)
        self._atualizar_widgets_dns()

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

    def _selecionar_modo_dns(self, modo: str) -> None:
        indice = self.combo_dns.findData(modo)
        self.combo_dns.setCurrentIndex(indice if indice >= 0 else 0)

    def _atualizar_widgets_dns(self) -> None:
        transparente = self.caixa_transparente.isChecked()
        for widget in self._widgets_dns:
            widget.setEnabled(transparente)
        if self.caixa_vazamento.isChecked():
            # A prevenção de vazamento sempre usa o DNS do proxy.
            self._selecionar_modo_dns("proxy")
            self.combo_dns.setEnabled(False)
        modo = self.combo_dns.currentData()
        desviado = modo != "sistema"
        for widget in (self.campo_dns_servidores, self.campo_dns_excecoes, self.campo_porta_dns):
            widget.setEnabled(transparente and desviado)
        if modo == "personalizado":
            self.rotulo_dns_servidores.setText("Servidores DNS")
            self.campo_dns_servidores.setPlaceholderText("ex.: 1.1.1.1; 8.8.8.8")
            self.campo_dns_servidores.setToolTip(
                "Servidores que respondem às consultas, na ordem, separados por ponto e vírgula. "
                "Porta opcional: 9.9.9.9:53. IPv6 entre colchetes: [2606:4700::1111]")
            self.dica_dns.setText(
                "As consultas dos programas vão aos servidores escolhidos, pelo caminho que as regras "
                "mandam para o IP de cada um: com a regra pelo proxy, por TCP através do proxy (o "
                "provedor não vê); com a regra direto, por UDP. A consulta não tem dono, então vale "
                "como um programa desconhecido: para garantir o caminho, crie uma regra com o IP, "
                "como 1.1.1.1:53 +proxy:Nome. Proxies HTTP costumam recusar a porta 53.")
        else:
            self.rotulo_dns_servidores.setText("Servidores DNS reais")
            self.campo_dns_servidores.setPlaceholderText("automático (os do sistema)")
            self.campo_dns_servidores.setToolTip(
                "Servidores DNS reais usados pelo próprio Veredo e para os nomes locais, separados "
                "por ponto e vírgula (ex.: 192.168.1.1; 1.1.1.1). Porta opcional: 192.168.1.1:5353")
            self.dica_dns.setText(
                "Os programas recebem endereços falsos (198.18.x.x) e o nome do site vai para o proxy, "
                "que faz a consulta DNS do lado dele. Assim as regras por domínio valem para qualquer "
                "programa e o seu provedor não vê quais sites você acessa pelo proxy."
                if modo == "proxy" else
                "As consultas DNS não são desviadas: os programas usam o DNS do sistema, que o seu "
                "provedor vê. As regras por domínio valem pelo nome que o navegador envia (SNI/Host).")

    def _carregar_transparente(self) -> None:
        preferencias = self.ctx.configuracao.preferencias
        self.caixa_transparente.setChecked(preferencias.modo_transparente_ativo)
        self.campo_porta_transparente.setValue(preferencias.porta_transparente)
        self.caixa_vazamento.setChecked(preferencias.prevenir_vazamento)
        self._selecionar_modo_dns(preferencias.modo_dns)
        self.campo_dns_excecoes.setText(preferencias.dns_excecoes)
        self.campo_dns_servidores.setText(preferencias.dns_servidores)
        self.campo_porta_dns.setValue(preferencias.porta_dns)
        self.caixa_udp.setChecked(preferencias.udp_pelas_regras)
        self._atualizar_widgets_dns()

    def _ao_salvar_transparente(self) -> None:
        if (self.combo_dns.currentData() == "personalizado" and not self.caixa_vazamento.isChecked()
                and not self.campo_dns_servidores.text().strip()):
            self.rotulo_transparente.setText("Informe ao menos um servidor para o DNS personalizado.")
            self.campo_dns_servidores.setFocus()
            return
        preferencias = self.ctx.configuracao.preferencias
        preferencias.modo_transparente_ativo = self.caixa_transparente.isChecked()
        preferencias.porta_transparente = self.campo_porta_transparente.value()
        preferencias.prevenir_vazamento = self.caixa_vazamento.isChecked()
        preferencias.modo_dns = "proxy" if preferencias.prevenir_vazamento else self.combo_dns.currentData()
        preferencias.dns_excecoes = self.campo_dns_excecoes.text().strip()
        preferencias.dns_servidores = self.campo_dns_servidores.text().strip()
        preferencias.porta_dns = self.campo_porta_dns.value()
        preferencias.udp_pelas_regras = self.caixa_udp.isChecked()

        if self.ctx.motor.em_execucao():
            ok, mensagem = self.ctx.aplicar_ao_vivo()
            # Em caso de falha os valores anteriores foram restaurados.
            self._carregar_transparente()
            self.rotulo_transparente.setText(mensagem)
        else:
            self.ctx.aplicar_mudancas()
            self.rotulo_transparente.setText(
                "Configurações salvas. Serão aplicadas quando você iniciar o motor no Dashboard.")

    # -- serviço do sistema -------------------------------------------------------

    def _criar_cartao_servico(self) -> QFrame:
        quadro, layout = _cartao("Serviço do sistema")
        dica = QLabel(
            f"Roda o motor como serviço do administrador, para o modo transparente funcionar sem "
            f"pedir senha ao abrir. A janela passa a controlar o serviço e envia a ele a "
            f"configuração (inclusive as senhas, guardadas num arquivo que só o administrador "
            f"lê). O motor só fica ligado com o {NOME_EXIBICAO} aberto: fechá-lo desliga o motor, "
            f"e o serviço fica parado até a janela abrir de novo. Pede permissão de administrador."
        )
        dica.setWordWrap(True)
        dica.setObjectName("DicaCartao")
        layout.addWidget(dica)

        self.rotulo_servico = QLabel()
        self.rotulo_servico.setWordWrap(True)
        layout.addWidget(self.rotulo_servico)

        self.botao_instalar_servico = QPushButton("Instalar serviço")
        self.botao_instalar_servico.setObjectName("BotaoPrincipal")
        self.botao_instalar_servico.clicked.connect(lambda: self._executar_servico(True))
        self.botao_remover_servico = QPushButton("Remover serviço")
        self.botao_remover_servico.clicked.connect(lambda: self._executar_servico(False))
        linha = QHBoxLayout()
        linha.addStretch(1)
        linha.addWidget(self.botao_remover_servico)
        linha.addWidget(self.botao_instalar_servico)
        layout.addLayout(linha)

        self._servico_concluido.connect(self._ao_concluir_servico)
        self._atualizar_servico()
        return quadro

    def _atualizar_servico(self, mensagem: str = "") -> None:
        usando = self.ctx.usando_servico
        self.botao_instalar_servico.setEnabled(not usando)
        self.botao_remover_servico.setEnabled(usando)
        estado = ("Esta janela está controlando o serviço do sistema." if usando
                  else "O motor roda dentro desta janela.")
        self.rotulo_servico.setText(f"{mensagem}\n{estado}" if mensagem else estado)

    def _executar_servico(self, instalar: bool) -> None:
        self.botao_instalar_servico.setEnabled(False)
        self.botao_remover_servico.setEnabled(False)
        self.rotulo_servico.setText("Aguardando a permissão de administrador...")

        def trabalho() -> None:
            ok, mensagem = servico.executar_como_administrador(
                "--instalar-servico" if instalar else "--remover-servico")
            if ok and instalar and not servico.esperar_servico():
                ok, mensagem = False, "O serviço foi instalado mas não respondeu ao canal de controle."
            self._servico_concluido.emit(instalar, ok, mensagem)

        threading.Thread(target=trabalho, daemon=True).start()

    def _ao_concluir_servico(self, instalar: bool, ok: bool, mensagem: str) -> None:
        if ok:
            if instalar:
                self.ctx.conectar_servico()
                mensagem = mensagem or "Serviço instalado e em execução."
            else:
                self.ctx.desconectar_servico()
                mensagem = mensagem or "Serviço removido."
        else:
            mensagem = f"Falhou: {mensagem}"
        self._atualizar_servico(mensagem)
