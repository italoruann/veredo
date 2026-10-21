"""Janela principal: menus, painéis Conexões/Tráfego/Estatísticas, log central e barra de status.

Proxies, Regras e Configurações abrem como diálogos pelo menu Perfil."""
from __future__ import annotations

import time
from typing import Callable, Optional

import psutil
from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QDockWidget, QFileDialog, QFrame,
    QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMenuBar, QMessageBox, QScrollArea,
    QSizePolicy, QSystemTrayIcon, QTableView, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from ..nucleo import arquivo_perfil, teste_vazamento
from ..nucleo.configuracao import NOME_EXIBICAO
from .contexto import Contexto
from .icone import desenhar_icone
from .modelo_conexoes import (
    COL_APP, COL_RECEBIDO, COL_REGRA, COL_ENVIADO, COL_ESTADO, COL_DESTINO, FILTRO_PADRAO, formatar_bytes,
)
from .paginas.proxies import PaginaProxies
from .paginas.regras import PaginaRegras
from .paginas.cadeias import PaginaCadeias
from .paginas.configuracoes import PaginaConfiguracoes
from .tema import PALETA
from .componentes.icones import icone_linha
from ..nucleo.regras import ConjuntoRegras
from .componentes.visualizador_log import NIVEL_ERROS, NIVEL_NORMAL, NIVEL_DETALHADO, VisualizadorLog
from .componentes.paineis import (
    TituloPainel, ContadoresSessao, VisaoEstatisticas, GraficoTrafego, formatar_taxa, corpo_painel, barra_painel,
)

# Diálogos do menu Perfil, na ordem dos atalhos Ctrl+1..Ctrl+4.
DIALOGOS_PERFIL = ["Servidores proxy", "Regras de proxificação", "Configurações", "Cadeias de proxy"]
# Intervalo de atualização do gráfico de tráfego (menu do painel Tráfego), em ms.
TAXAS_ATUALIZACAO = {"fast": 500, "normal": 1000, "slow": 2000}
# Larguras iniciais das colunas do painel Conexões.
LARGURAS_COLUNAS = {COL_APP: 200, COL_DESTINO: 260, COL_ESTADO: 110, COL_REGRA: 260, COL_ENVIADO: 100, COL_RECEBIDO: 100}
ALTURAS_LINHA = {"small": 24, "large": 36}


def _hms(segundos: float) -> str:
    segundos = int(segundos)
    return f"{segundos // 3600:02d}:{segundos % 3600 // 60:02d}:{segundos % 60:02d}"


class DialogoGestao(QDialog):
    """Diálogo não modal que hospeda uma página de gestão (as páginas salvam sozinhas)."""

    def __init__(self, titulo: str, pagina: QWidget, tamanho: QSize, parent: QWidget, rolagem: bool = True):
        super().__init__(parent)
        self.pagina = pagina
        self.setWindowTitle(titulo)
        self.resize(tamanho)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        if rolagem:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(pagina)
            layout.addWidget(area, 1)
        else:
            layout.addWidget(pagina, 1)
        botoes = QDialogButtonBox()
        botoes.addButton("Fechar", QDialogButtonBox.ButtonRole.RejectRole)
        botoes.rejected.connect(self.close)
        botoes.setContentsMargins(0, 0, 10, 0)
        layout.addWidget(botoes)


class JanelaPrincipal(QMainWindow):
    def __init__(self, ctx: Contexto):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(NOME_EXIBICAO)
        self.setWindowIcon(desenhar_icone(False))
        self.resize(1440, 760)
        self.setMinimumSize(560, 400)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks)
        # Abas embaixo dos painéis.
        self.setTabPosition(Qt.DockWidgetArea.TopDockWidgetArea, QTabWidget.TabPosition.South)

        self.pagina_proxies = PaginaProxies(ctx)
        self.pagina_regras = PaginaRegras(ctx)
        self.pagina_configuracoes = PaginaConfiguracoes(ctx)
        self.pagina_cadeias = PaginaCadeias(ctx)
        self.dialogos = [
            DialogoGestao(DIALOGOS_PERFIL[0], self.pagina_proxies, QSize(760, 560), self),
            DialogoGestao(DIALOGOS_PERFIL[1], self.pagina_regras, QSize(900, 680), self),
            DialogoGestao(DIALOGOS_PERFIL[2], self.pagina_configuracoes, QSize(640, 700), self, rolagem=False),
            DialogoGestao(DIALOGOS_PERFIL[3], self.pagina_cadeias, QSize(820, 660), self),
        ]

        self.log = VisualizadorLog()
        self.setCentralWidget(self.log)

        self.contadores = ContadoresSessao()
        self._base_estatisticas = self._totais_motor()
        self._ultimos_totais_status = self._totais_motor()
        self._ultimos_totais_trafego = self._totais_motor()

        self._criar_acoes()
        self._criar_painel_conexoes()
        self._criar_painel_trafego()
        self._criar_painel_estatisticas()
        self.tabifyDockWidget(self.painel_conexoes, self.painel_trafego)
        self.tabifyDockWidget(self.painel_conexoes, self.painel_estatisticas)
        self.painel_conexoes.raise_()
        self.resizeDocks([self.painel_conexoes], [320], Qt.Orientation.Vertical)
        self._criar_menu_e_barra()
        self._criar_barra_status()
        self._criar_icone_bandeja()
        self._layout_padrao = self.saveState()

        ctx.log_adicionado.connect(self._ao_adicionar_log)
        ctx.log_atualizado.connect(self._ao_atualizar_log)
        ctx.estado_mudou.connect(self._ao_mudar_estado_motor)
        ctx.configuracao_mudou.connect(self._ao_mudar_configuracao)
        ctx.ip_saida_mudou.connect(lambda *_args: self._atualizar_rotulo_ip())

        self._temporizador_segundo = QTimer(self)
        self._temporizador_segundo.setInterval(1000)
        self._temporizador_segundo.timeout.connect(self._a_cada_segundo)
        self._temporizador_segundo.start()
        self._temporizador_trafego = QTimer(self)
        self._temporizador_trafego.timeout.connect(self._atualizar_trafego)
        self.definir_taxa_trafego("normal")

        self.log.mensagem(f"Bem-vindo ao {NOME_EXIBICAO}")
        self._ao_mudar_configuracao()
        ativo = ctx.motor.em_execucao()
        self._aplicar_estado_motor(ativo)
        self.log.mensagem("Motor parado. Use Arquivo > Iniciar motor (Ctrl+E)." if not ativo
                          else "Motor ativo.")
        self._atualizar_estatisticas()

    # -- ações ----------------------------------------------------------------------

    def _acao(self, texto: str, slot: Callable | None = None, atalho_teclado: str | None = None,
              dica: str = "", icone: QIcon | None = None, marcavel: bool = False) -> QAction:
        acao = QAction(texto, self)
        if icone is not None:
            acao.setIcon(icone)
        if atalho_teclado:
            acao.setShortcut(QKeySequence(atalho_teclado))
        if dica:
            acao.setStatusTip(dica)
            acao.setToolTip(dica)
        acao.setCheckable(marcavel)
        if slot is not None:
            (acao.toggled if marcavel else acao.triggered).connect(slot)
        self.addAction(acao)  # atalhos funcionam mesmo com a barra de menus escondida/flutuante
        return acao

    def _menu_opcoes(self, titulo: str, itens: list[tuple[str, str]], atual: str,
                     slot: Callable[[str], None]) -> QMenu:
        menu = QMenu(titulo, self)
        grupo = QActionGroup(menu)
        for chave, rotulo in itens:
            acao = menu.addAction(rotulo)
            acao.setCheckable(True)
            acao.setChecked(chave == atual)
            acao.triggered.connect(lambda _checked=False, chave=chave: slot(chave))
            grupo.addAction(acao)
        return menu

    def _criar_acoes(self) -> None:
        self.acao_motor = self._acao("&Iniciar motor", self.alternar_motor, "Ctrl+E",
                                     "Liga/desliga os listeners SOCKS5/HTTP do motor",
                                     icone_linha("play", PALETA["success"]))
        self.acoes_perfil = []
        for i, (titulo, dica, nome_icone) in enumerate((
            (DIALOGOS_PERFIL[0], "Configurar os servidores proxy", "server"),
            (DIALOGOS_PERFIL[1], "Editar as regras de proxificação", "rules"),
            (DIALOGOS_PERFIL[2], "Portas, integração com o sistema, logs e modo transparente", "settings"),
            (DIALOGOS_PERFIL[3], "Juntar proxies em sequência, redundância ou balanceamento", "chain"),
        )):
            self.acoes_perfil.append(self._acao(
                f"{titulo}...", lambda _c=False, i=i: self.abrir_dialogo_perfil(i), f"Ctrl+{i + 1}", dica,
                icone_linha(nome_icone)))
        self.acao_teste_vazamento = self._acao("&Testar vazamentos...", self.testar_vazamentos,
                                               dica="Abre no navegador uma página que confere o IP visto "
                                                    "pelos sites, o IPv6, o WebRTC e o DNS")
        self.acao_importar_perfil = self._acao("&Importar perfil...", self.importar_perfil,
                                               dica="Carrega proxies, cadeias e regras de um arquivo "
                                                    "do Veredo (.json) ou do Proxifier (.ppx)",
                                               icone=icone_linha("import"))
        self.acao_exportar_perfil = self._acao("E&xportar perfil...", self.exportar_perfil,
                                               dica="Salva proxies, cadeias e regras num arquivo",
                                               icone=icone_linha("export"))
        self.acao_pasta_dados = self._acao("Abrir &pasta de dados...",
                                           self.pagina_configuracoes._abrir_pasta_dados,
                                           dica="Abre a pasta com config.json e o histórico")
        self.acao_inicio_automatico = self._acao("&Início automático", marcavel=True,
                                                 dica=f"Iniciar o {NOME_EXIBICAO} junto com o sistema")
        self.acao_icone_bandeja = self._acao("Ícone na &bandeja do sistema", marcavel=True,
                                             dica="Ao fechar a janela, continuar rodando na bandeja")
        # Espelham as caixas de seleção das Configurações (que têm a lógica de salvar/validar).
        for acao, caixa in ((self.acao_inicio_automatico, self.pagina_configuracoes.caixa_inicio_automatico),
                              (self.acao_icone_bandeja, self.pagina_configuracoes.caixa_minimizar_bandeja)):
            acao.setChecked(caixa.isChecked())
            acao.toggled.connect(caixa.setChecked)
            caixa.toggled.connect(acao.setChecked)
        self.acao_fechar_janela = self._acao("&Fechar janela", self._fechar_para_bandeja,
                                             dica="Fecha a janela e continua na bandeja do sistema")
        self.acao_sair = self._acao("Sai&r", self.sair, "Ctrl+Q", "Encerra o aplicativo")

        self.acao_localizar = self._acao("&Localizar...", self.localizar, "Ctrl+F", "Localiza um texto no log")
        self.acao_localizar_proxima = self._acao("Localizar &próxima", self.localizar_proxima, "F3",
                                                 "Repete a última busca")
        self.acao_copiar = self._acao("&Copiar", self.log.copy, "Ctrl+C",
                                      "Copia a seleção do log")
        self.acao_selecionar_tudo = self._acao("Selecionar &tudo", self.log.selectAll, "Ctrl+A",
                                               "Seleciona todo o log")
        self.acao_limpar_log = self._acao("&Limpar", self.log.limpar_log, dica="Apaga o log da tela")
        self.acao_salvar_log = self._acao("&Salvar log...", self._salvar_log, "Ctrl+S",
                                          "Salva o log da tela num arquivo de texto")
        self.acao_redefinir_layout = self._acao("&Redefinir layout", self.redefinir_layout,
                                                dica="Volta os painéis à posição padrão")
        self.acao_sobre = self._acao(f"&Sobre o {NOME_EXIBICAO}...", self._mostrar_sobre)
        self._ultima_busca = ""

    def _criar_menu_e_barra(self) -> None:
        # A barra de menus fica dentro da barra de ferramentas, na mesma linha dos botões.
        self.barra_menus = QMenuBar()
        self.barra_menus.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        menu_arquivo = self.barra_menus.addMenu("&Arquivo")
        menu_arquivo.addAction(self.acao_motor)
        menu_arquivo.addAction(self.acao_teste_vazamento)
        menu_arquivo.addSeparator()
        menu_arquivo.addAction(self.acao_importar_perfil)
        menu_arquivo.addAction(self.acao_exportar_perfil)
        menu_arquivo.addSeparator()
        menu_arquivo.addAction(self.acao_pasta_dados)
        menu_arquivo.addSeparator()
        menu_arquivo.addAction(self.acao_inicio_automatico)
        menu_arquivo.addSeparator()
        menu_arquivo.addAction(self.acao_fechar_janela)
        menu_arquivo.addAction(self.acao_sair)

        menu_perfil = self.barra_menus.addMenu("&Perfil")
        for acao in self.acoes_perfil:
            menu_perfil.addAction(acao)
        menu_perfil.addSeparator()
        self.menu_proxy_padrao = menu_perfil.addMenu("Proxy &padrão")

        menu_log = self.barra_menus.addMenu("&Log")
        menu_log.addMenu(self._menu_opcoes("Log na &tela", [
            ("errors", "Somente &erros"), ("normal", "&Normal"), ("verbose", "&Detalhado")],
            "normal", self.definir_nivel_log))
        menu_log.addMenu(self._menu_opcoes("Tamanho da &fonte", [
            ("small", "&Pequena"), ("medium", "&Média"), ("large", "&Grande")],
            "medium", self.log.definir_tamanho_fonte))
        menu_log.addSeparator()
        for acao in (self.acao_copiar, self.acao_selecionar_tudo, self.acao_limpar_log):
            menu_log.addAction(acao)
        menu_log.addSeparator()
        menu_log.addAction(self.acao_localizar)
        menu_log.addAction(self.acao_localizar_proxima)
        menu_log.addSeparator()
        menu_log.addAction(self.acao_salvar_log)

        menu_exibir = self.barra_menus.addMenu("&Exibir")
        menu_exibir.addAction(self.acao_icone_bandeja)
        menu_exibir.addMenu(self.menu_filtro_conexoes)
        menu_exibir.addSeparator()
        menu_janelas = menu_exibir.addMenu("&Janelas")
        for painel in (self.painel_conexoes, self.painel_trafego, self.painel_estatisticas):
            menu_janelas.addAction(painel.toggleViewAction())
        menu_exibir.addAction(self.acao_redefinir_layout)

        menu_ajuda = self.barra_menus.addMenu("Aj&uda")
        menu_ajuda.addAction(self.acao_sobre)

        self.barra_principal = QToolBar("Padrão")
        self.barra_principal.setObjectName("BarraPrincipal")
        self.barra_principal.setMovable(False)
        self.barra_principal.setFloatable(False)
        self.barra_principal.setIconSize(QSize(18, 18))
        self.barra_principal.toggleViewAction().setEnabled(False)
        self.barra_principal.addWidget(self.barra_menus)
        self.barra_principal.addSeparator()
        for acao in self.acoes_perfil:
            self.barra_principal.addAction(acao)
        self.barra_principal.addSeparator()
        self.barra_principal.addAction(self.acao_motor)
        botao_proxy_padrao = QToolButton()
        botao_proxy_padrao.setIcon(icone_linha("route"))
        botao_proxy_padrao.setToolTip("Escolher o proxy padrão")
        botao_proxy_padrao.setMenu(self.menu_proxy_padrao)
        botao_proxy_padrao.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.barra_principal.addWidget(botao_proxy_padrao)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.barra_principal)

    def _criar_painel(self, titulo: str, nome: str, corpo: QWidget, icone: QIcon) -> QDockWidget:
        painel = QDockWidget(titulo, self)
        painel.setObjectName(nome)  # obrigatório para saveState()/restoreState()
        painel.setWindowIcon(icone)
        painel.setTitleBarWidget(TituloPainel(painel))
        painel.setWidget(corpo)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, painel)
        return painel

    # -- painel Conexões --------------------------------------------------------------

    def _criar_painel_conexoes(self) -> None:
        self.tabela_conexoes = QTableView()
        self.tabela_conexoes.setModel(self.ctx.modelo_conexoes)
        self.tabela_conexoes.setShowGrid(False)
        self.tabela_conexoes.setWordWrap(False)
        self.tabela_conexoes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela_conexoes.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabela_conexoes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela_conexoes.verticalHeader().setVisible(False)
        cabecalho = self.tabela_conexoes.horizontalHeader()
        cabecalho.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        cabecalho.setStretchLastSection(True)
        cabecalho.setHighlightSections(False)
        for coluna, largura in LARGURAS_COLUNAS.items():
            self.tabela_conexoes.setColumnWidth(coluna, largura)
        self.tabela_conexoes.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

        self.acao_encerrar_processo = self._acao("&Encerrar processo...", self.encerrar_processo,
                                                 dica="Encerra o processo dono da conexão selecionada",
                                                 icone=icone_linha("end_process", PALETA["danger"]))
        self.acao_detalhado = self._acao("&Detalhado", self.ctx.modelo_conexoes.definir_detalhado,
                                         dica="Mostra PID e IP de destino nas conexões",
                                         icone=icone_linha("eye"), marcavel=True)
        self.menu_filtro_conexoes = self._menu_opcoes("Conexões &exibidas", [
            ("todas", "&Todas"), ("proxy", "Somente via &proxy"), ("direct", "Somente &diretas"),
            ("block", "Somente &bloqueadas")], FILTRO_PADRAO, self.definir_filtro_conexoes)
        menu_tamanho_icones = self._menu_opcoes("Tamanho dos ícones", [
            ("small", "Ícones &pequenos"), ("large", "Ícones &grandes")], "small", self.definir_tamanho_icones)
        self.tabela_conexoes.addAction(self.acao_encerrar_processo)
        self.tabela_conexoes.addAction(self.acao_detalhado)
        self.definir_tamanho_icones("small")

        barra = barra_painel()
        barra.addAction(self.acao_encerrar_processo)
        barra.addAction(self.acao_detalhado)
        barra.addWidget(self._botao_menu(self.menu_filtro_conexoes, "filter",
                                         "Conexões exibidas no painel e no log"))
        barra.addWidget(self._botao_menu(menu_tamanho_icones, "grid", "Tamanho dos ícones"))
        self.painel_conexoes = self._criar_painel(
            "Conexões", "ConnectionsPane", corpo_painel(barra, self.tabela_conexoes),
            icone_linha("connections"))

    def _botao_menu(self, menu: QMenu, nome_icone: str, dica: str) -> QToolButton:
        botao = QToolButton()
        botao.setIcon(icone_linha(nome_icone))
        botao.setToolTip(dica)
        botao.setMenu(menu)
        botao.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        botao.setAutoRaise(True)
        return botao

    def definir_tamanho_icones(self, tamanho: str) -> None:
        altura = ALTURAS_LINHA[tamanho]
        self.tabela_conexoes.setIconSize(QSize(altura - 2, altura - 2))
        self.tabela_conexoes.verticalHeader().setDefaultSectionSize(altura)

    def definir_filtro_conexoes(self, filtro: str) -> None:
        self.ctx.modelo_conexoes.definir_filtro(filtro)
        self.log.definir_filtro(filtro)

    def encerrar_processo(self) -> None:
        indice = self.tabela_conexoes.currentIndex()
        entrada = self.ctx.modelo_conexoes.entrada_em(indice.row()) if indice.isValid() else None
        if entrada is None or entrada.pid <= 0:
            QMessageBox.information(self, "Encerrar processo",
                                    "Selecione uma conexão cujo processo seja conhecido.")
            return
        nome = entrada.nome_processo or "desconhecido"
        resposta = QMessageBox.question(self, "Encerrar processo",
                                        f"Encerrar o processo {nome} (PID {entrada.pid})?")
        if resposta != QMessageBox.StandardButton.Yes:
            return
        try:
            psutil.Process(entrada.pid).terminate()
        except (psutil.Error, OSError) as exc:
            QMessageBox.warning(self, "Encerrar processo", f"Não foi possível encerrar {nome}: {exc}")
            return
        self.log.mensagem(f"Processo {nome} ({entrada.pid}) encerrado pelo usuário")

    # -- painel Tráfego ----------------------------------------------------------------

    def _criar_painel_trafego(self) -> None:
        self.grafico_trafego = GraficoTrafego()
        barra = barra_painel()
        barra.addWidget(self._botao_menu(self._menu_opcoes("Velocidade de atualização", [
            ("fast", "&Rápida"), ("normal", "&Normal"), ("slow", "&Lenta")], "normal",
            self.definir_taxa_trafego), "speed", "Velocidade de atualização do gráfico"))
        barra.addWidget(self._botao_menu(self._menu_opcoes("Tráfego exibido", [
            ("both", "&Ambos"), ("in", "Somente &recebido"), ("out", "Somente &enviado")], "both",
            self.grafico_trafego.definir_modo), "updown", "Tipo de tráfego exibido"))
        barra.addAction(self._acao("&Limpar", self.grafico_trafego.clear, dica="Limpa o gráfico",
                                   icone=icone_linha("trash")))
        self.painel_trafego = self._criar_painel("Tráfego", "TrafficPane", corpo_painel(barra, self.grafico_trafego),
                                                 icone_linha("activity"))

    def definir_taxa_trafego(self, taxa: str) -> None:
        self._temporizador_trafego.start(TAXAS_ATUALIZACAO[taxa])

    def _atualizar_trafego(self) -> None:
        enviados, recebidos = self._totais_motor()[:2]
        ultimo_enviado, ultimo_recebido = self._ultimos_totais_trafego[:2]
        segundos = self._temporizador_trafego.interval() / 1000
        self.grafico_trafego.adicionar_amostra(max(recebidos - ultimo_recebido, 0) / segundos, max(enviados - ultimo_enviado, 0) / segundos)
        self._ultimos_totais_trafego = self._totais_motor()

    # -- painel Estatísticas -------------------------------------------------------------

    def _criar_painel_estatisticas(self) -> None:
        self.estatisticas = VisaoEstatisticas()
        barra = barra_painel()
        barra.addAction(self._acao("&Copiar estatísticas", self._copiar_estatisticas,
                                   dica="Copia as estatísticas para a área de transferência",
                                   icone=icone_linha("copy")))
        barra.addAction(self._acao("&Zerar estatísticas", self.zerar_estatisticas,
                                   dica="Zera todas as estatísticas",
                                   icone=icone_linha("reset")))
        self.painel_estatisticas = self._criar_painel(
            "Estatísticas", "StatisticsPane", corpo_painel(barra, self.estatisticas),
            icone_linha("chart"))

    def _totais_motor(self) -> tuple[int, int, int, int]:
        motor = self.ctx.motor
        return motor.total_enviado, motor.total_recebido, motor.proxy_enviado, motor.proxy_recebido

    def zerar_estatisticas(self) -> None:
        self.contadores.zerar()
        self._base_estatisticas = self._totais_motor()
        self._atualizar_estatisticas()

    def _atualizar_estatisticas(self) -> None:
        contadores, visao = self.contadores, self.estatisticas
        visao.definir_valor("uptime", _hms(time.monotonic() - contadores.inicio))
        visao.definir_valor("total", str(contadores.total))
        visao.definir_valor("active", str(self.ctx.motor.conexoes_ativas))
        visao.definir_valor("proxy", str(contadores.proxy))
        visao.definir_valor("direct", str(contadores.diretas))
        visao.definir_valor("blocked", str(contadores.bloqueadas))
        visao.definir_valor("failed", str(contadores.falhas))
        totais = zip(self._totais_motor(), self._base_estatisticas)
        enviados, recebidos, via_proxy_enviado, via_proxy_recebido = (max(agora - base, 0) for agora, base in totais)
        visao.definir_valor("recv", f"{formatar_bytes(recebidos)}    (via proxy: {formatar_bytes(via_proxy_recebido)})")
        visao.definir_valor("sent", f"{formatar_bytes(enviados)}    (via proxy: {formatar_bytes(via_proxy_enviado)})")

    def _copiar_estatisticas(self) -> None:
        QApplication.clipboard().setText(self.estatisticas.como_texto())

    # -- barra de status -------------------------------------------------------------------

    def _criar_barra_status(self) -> None:
        barra_rolagem = self.statusBar()
        barra_rolagem.addWidget(QLabel("Pronto"), 1)
        self.status_conexoes = QLabel()
        self.status_download = QLabel()
        self.status_upload = QLabel()
        self.status_ip_saida = QLabel()
        self.status_motor = QLabel()
        for rotulo in (self.status_conexoes, self.status_download, self.status_upload, self.status_ip_saida,
                       self.status_motor):
            rotulo.setContentsMargins(8, 0, 8, 0)
            barra_rolagem.addPermanentWidget(rotulo)
        self._atualizar_taxas(0, 0)

    def _atualizar_taxas(self, download: float, upload: float) -> None:
        self.status_conexoes.setText(f"{self.ctx.motor.conexoes_ativas} conexões ativas")
        self.status_download.setText(f"Download {formatar_taxa(download)}")
        self.status_upload.setText(f"Upload {formatar_taxa(upload)}")

    def _atualizar_rotulo_ip(self) -> None:
        padrao = self.ctx.configuracao.proxy_padrao()
        if padrao is None:
            self.status_ip_saida.setText("Sem proxy padrão")
            self.status_ip_saida.setToolTip("")
            return
        ip_anterior, ip_atual = self.ctx.ips_saida.get(padrao.id, ("", ""))
        self.status_ip_saida.setText(f"IP de saída: {ip_atual or '—'}")
        dica = f"IP de saída real verificado através de {padrao.nome}."
        if ip_anterior and ip_anterior != ip_atual:
            dica += f"\nMudou de {ip_anterior} para {ip_atual}."
        self.status_ip_saida.setToolTip(dica)

    # -- eventos ----------------------------------------------------------------------------

    def _ao_adicionar_log(self, entradas: list) -> None:
        self.log.ao_adicionar_entradas(entradas)
        self.contadores.contar_novas(entradas)

    def _ao_atualizar_log(self, entradas: list) -> None:
        self.log.ao_atualizar_entradas(entradas)
        self.contadores.contar_atualizadas(entradas)

    def _a_cada_segundo(self) -> None:
        self.ctx.modelo_conexoes.atualizar_tempos()
        totais = self._totais_motor()
        self._atualizar_taxas(max(totais[1] - self._ultimos_totais_status[1], 0),
                              max(totais[0] - self._ultimos_totais_status[0], 0))
        self._ultimos_totais_status = totais
        self._atualizar_estatisticas()

    def _ao_mudar_estado_motor(self, ativo: bool, mensagem: str) -> None:
        self._aplicar_estado_motor(ativo)
        if mensagem:
            self.log.mensagem(mensagem, erro=not ativo and "erro" in mensagem.lower())

    def _aplicar_estado_motor(self, ativo: bool) -> None:
        self.acao_motor.setText("&Parar motor" if ativo else "&Iniciar motor")
        self.acao_motor.setIcon(icone_linha("stop", PALETA["danger"]) if ativo
                                else icone_linha("play", PALETA["success"]))
        preferencias = self.ctx.configuracao.preferencias
        self.status_motor.setText("● Motor ativo" if ativo else "● Motor parado")
        self.status_motor.setObjectName("SeloMotorLigado" if ativo else "SeloMotorDesligado")
        self.status_motor.style().unpolish(self.status_motor)
        self.status_motor.style().polish(self.status_motor)
        self.status_motor.setToolTip(f"SOCKS5 127.0.0.1:{preferencias.porta_socks} · HTTP 127.0.0.1:"
                                     f"{preferencias.porta_http} · PAC na porta {preferencias.porta_pac}")
        self.setWindowIcon(desenhar_icone(ativo))
        if self.bandeja is not None:
            self.bandeja.setIcon(desenhar_icone(ativo))
            self.acao_bandeja_motor.setText("Parar motor" if ativo else "Iniciar motor")
            self.bandeja.setToolTip(f"{NOME_EXIBICAO} — {'ativo' if ativo else 'parado'}")

    def _ao_mudar_configuracao(self) -> None:
        self._recriar_menu_proxy_padrao()
        self._atualizar_rotulo_ip()

    def _recriar_menu_proxy_padrao(self) -> None:
        menu = self.menu_proxy_padrao
        menu.clear()
        if not self.ctx.configuracao.proxies:
            menu.addAction("Nenhum proxy cadastrado").setEnabled(False)
            return
        grupo = QActionGroup(menu)
        padrao = self.ctx.configuracao.proxy_padrao()
        for perfil in self.ctx.configuracao.proxies:
            acao = menu.addAction(perfil.nome if perfil.ativo else f"{perfil.nome} (desabilitado)")
            acao.setCheckable(True)
            acao.setChecked(padrao is not None and perfil.id == padrao.id)
            acao.triggered.connect(lambda _checked=False, pid=perfil.id: self.definir_proxy_padrao(pid))
            grupo.addAction(acao)

    def definir_proxy_padrao(self, id_perfil: str) -> None:
        for perfil in self.ctx.configuracao.proxies:
            perfil.padrao = perfil.id == id_perfil
        self.ctx.aplicar_mudancas()

    # -- comandos ------------------------------------------------------------------------------

    def alternar_motor(self) -> None:
        if self.ctx.motor.em_execucao():
            self.ctx.motor.parar()
        else:
            self.ctx.motor.iniciar()

    def importar_perfil(self) -> None:
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Importar perfil", "",
            "Perfis (*.json *.ppx);;Perfil do Veredo (*.json);;Perfil do Proxifier (*.ppx)")
        if not caminho:
            return
        try:
            novo = arquivo_perfil.importar(caminho)
        except arquivo_perfil.ErroPerfil as exc:
            QMessageBox.warning(self, "Importar perfil", f"Não foi possível importar: {exc}")
            return
        qtd_regras = len(ConjuntoRegras.interpretar(novo.texto_regras).regras)
        resposta = QMessageBox.question(
            self, "Importar perfil",
            f"O arquivo tem {len(novo.proxies)} proxy(s), {len(novo.cadeias)} cadeia(s) e {qtd_regras} "
            "regra(s). Eles vão substituir os proxies, cadeias, aplicativos e regras atuais "
            "(as configurações não mudam). Exporte o perfil atual antes, se quiser guardá-lo.\n\n"
            "Continuar?")
        if resposta != QMessageBox.StandardButton.Yes:
            return
        cfg = self.ctx.configuracao
        cfg.proxies, cfg.cadeias = novo.proxies, novo.cadeias
        cfg.aplicativos, cfg.texto_regras = novo.aplicativos, novo.texto_regras
        cfg.garantir_padrao_unico()
        self.ctx.aplicar_mudancas()
        self.pagina_proxies._recarregar_lista()
        self.pagina_cadeias._recarregar_lista()
        self.pagina_regras._carregar_da_configuracao()
        self.pagina_regras._recarregar_tabela_apps()
        self.log.mensagem(f"Perfil importado de {caminho}")

    def exportar_perfil(self) -> None:
        caminho, _ = QFileDialog.getSaveFileName(self, "Exportar perfil", "veredo-perfil.json",
                                                 "Perfil do Veredo (*.json)")
        if not caminho:
            return
        incluir_senhas = QMessageBox.question(
            self, "Exportar perfil",
            "Incluir as senhas dos proxies no arquivo?\n\nElas ficam em texto puro: só inclua se o "
            "arquivo for guardado em lugar seguro.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        try:
            arquivo_perfil.exportar(self.ctx.configuracao, caminho, incluir_senhas)
        except OSError as exc:
            QMessageBox.warning(self, "Exportar perfil", f"Não foi possível salvar: {exc}")
            return
        self.log.mensagem(f"Perfil exportado para {caminho}")

    def abrir_dialogo_perfil(self, indice: int) -> None:
        dialogo = self.dialogos[indice]
        dialogo.show()
        dialogo.raise_()
        dialogo.activateWindow()

    def definir_nivel_log(self, nivel: str) -> None:
        self.log.definir_nivel({"errors": NIVEL_ERROS, "normal": NIVEL_NORMAL,
                                "verbose": NIVEL_DETALHADO}[nivel])

    def localizar(self) -> None:
        texto, ok = QInputDialog.getText(self, "Localizar", "Localizar:", text=self._ultima_busca)
        if ok and texto:
            self._ultima_busca = texto
            self.localizar_proxima()

    def localizar_proxima(self) -> None:
        if not self._ultima_busca:
            self.localizar()
            return
        if not self.log.find(self._ultima_busca):
            # Chegou ao fim: recomeça do topo.
            cursor = self.log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.log.setTextCursor(cursor)
            if not self.log.find(self._ultima_busca):
                self.statusBar().showMessage(f"\"{self._ultima_busca}\" não encontrado", 3000)

    def testar_vazamentos(self) -> None:
        if not self.ctx.motor.em_execucao():
            QMessageBox.information(self, "Testar vazamentos", "Inicie o motor antes de testar.")
            return
        url = teste_vazamento.url_teste(self.ctx.configuracao.preferencias.porta_pac)
        QDesktopServices.openUrl(QUrl(url))
        self.log.mensagem(f"Teste de vazamento aberto no navegador padrão. Para testar outro navegador, "
                          f"abra {url} nele.")

    def _salvar_log(self) -> None:
        caminho, _ = QFileDialog.getSaveFileName(self, "Salvar log", "veredo.log", "Log (*.log *.txt)")
        if caminho:
            with open(caminho, "w", encoding="utf-8") as arq:
                arq.write(self.log.toPlainText())

    def redefinir_layout(self) -> None:
        self.restoreState(self._layout_padrao)
        for painel in (self.painel_conexoes, self.painel_trafego, self.painel_estatisticas):
            painel.setFloating(False)
            painel.show()
        self.painel_conexoes.raise_()

    def _mostrar_sobre(self) -> None:
        QMessageBox.about(self, f"Sobre o {NOME_EXIBICAO}",
                          f"<b>{NOME_EXIBICAO}</b><br>Roteia o tráfego de cada aplicativo por proxies "
                          "SOCKS5/HTTP de acordo com regras.")

    # -- bandeja do sistema -------------------------------------------------------------------

    def _criar_icone_bandeja(self) -> None:
        self.bandeja: Optional[QSystemTrayIcon] = None
        self.acao_fechar_janela.setEnabled(False)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            # Sem bandeja, fechar a janela encerra o app (veja closeEvent).
            return

        self.bandeja = QSystemTrayIcon(desenhar_icone(False), self)
        self.bandeja.setToolTip(f"{NOME_EXIBICAO} — parado")
        self.acao_fechar_janela.setEnabled(True)

        menu = QMenu(self)
        menu.addAction(f"&Abrir {NOME_EXIBICAO}", self._mostrar_janela)
        menu.addSeparator()
        self.acao_bandeja_motor = menu.addAction("Iniciar motor")
        self.acao_bandeja_motor.triggered.connect(self.alternar_motor)
        menu.addMenu(self.menu_proxy_padrao)
        menu.addSeparator()
        menu.addAction("Sai&r", self.sair)
        self.bandeja.setContextMenu(menu)
        self.bandeja.activated.connect(self._ao_ativar_bandeja)
        self.bandeja.show()

    def _ao_ativar_bandeja(self, motivo: QSystemTrayIcon.ActivationReason) -> None:
        if motivo in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._mostrar_janela()

    def _mostrar_janela(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _fechar_para_bandeja(self) -> None:
        if self.bandeja is not None:
            self.hide()

    def sair(self) -> None:
        self.ctx.encerrar()
        QApplication.instance().quit()

    def closeEvent(self, evento) -> None:  # noqa: N802 (Qt override)
        if self.bandeja is None or not self.ctx.configuracao.preferencias.minimizar_para_bandeja:
            self.ctx.encerrar()
            evento.accept()
            QApplication.instance().quit()
            return
        evento.ignore()
        self.hide()
        self.bandeja.showMessage(NOME_EXIBICAO, "Continua rodando na bandeja do sistema.",
                                 QSystemTrayIcon.MessageIcon.Information, 3000)
