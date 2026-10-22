"""Teste da interface em modo offscreen: abre a janela, os painéis e os diálogos."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile

import pytest

PASTA_CONFIG_TEMP = tempfile.mkdtemp(prefix="veredo_teste_")
# No Windows o platformdirs ignora LOCALAPPDATA; só estas variáveis evitam usar a config real.
os.environ["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = PASTA_CONFIG_TEMP
os.environ["WIN_PD_OVERRIDE_APPDATA"] = PASTA_CONFIG_TEMP
os.environ["XDG_CONFIG_HOME"] = PASTA_CONFIG_TEMP
os.environ["XDG_DATA_HOME"] = PASTA_CONFIG_TEMP

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

from proxy_manager.nucleo.configuracao import PerfilProxy
from proxy_manager.nucleo.registro import EntradaLog
from proxy_manager.nucleo.regras import ConjuntoRegras
from proxy_manager.interface.contexto import Contexto
from proxy_manager.interface.janela_principal import JanelaPrincipal
from proxy_manager.interface.paginas.proxies import PaginaProxies

# Evita que caixas de diálogo modais (QMessageBox) travem o teste esperando um clique humano.
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(app):
    ctx = Contexto()
    win = JanelaPrincipal(ctx)
    yield win
    ctx.motor.parar()
    win.close()


def test_janela_monta_layout_principal(window):
    """Menu e barra na mesma linha, painéis em abas acima do log central e Proxies/Regras/
    Configurações como diálogos do menu Perfil."""
    from proxy_manager.interface.janela_principal import DIALOGOS_PERFIL
    from proxy_manager.interface.componentes.visualizador_log import VisualizadorLog

    assert isinstance(window.centralWidget(), VisualizadorLog)
    assert window.barra_principal.widgetForAction(window.barra_principal.actions()[0]) is window.barra_menus
    menus = [acao.text() for acao in window.barra_menus.actions()]
    assert menus == ["&Arquivo", "&Perfil", "&Log", "&Exibir", "Aj&uda"]
    em_abas = window.tabifiedDockWidgets(window.painel_conexoes)
    assert set(em_abas) == {window.painel_trafego, window.painel_estatisticas}

    assert len(window.dialogos) == len(DIALOGOS_PERFIL)
    for i in range(len(DIALOGOS_PERFIL)):
        window.abrir_dialogo_perfil(i)
        assert window.dialogos[i].isVisible()
        window.dialogos[i].close()


def test_painel_fechado_volta_ao_restaurar_layout(window):
    window.show()
    window.painel_trafego.close()
    window.painel_estatisticas.setFloating(True)
    assert window.painel_trafego.isHidden()

    window.redefinir_layout()
    assert not window.painel_trafego.isHidden()
    assert not window.painel_estatisticas.isFloating()


def test_caixa_com_quebra_mantem_comportamento(app):
    from PySide6.QtWidgets import QCheckBox, QLabel

    from proxy_manager.interface.componentes.responsivo import caixa_com_quebra

    caixa = QCheckBox("Um texto bem comprido que precisa quebrar linha no painel estreito")
    linha = caixa_com_quebra(caixa)
    rotulo = linha.findChild(QLabel)
    assert caixa.text() == "" and rotulo.wordWrap()

    QTest.mouseClick(rotulo, Qt.MouseButton.LeftButton)
    assert caixa.isChecked()
    caixa.setEnabled(False)
    assert not rotulo.isEnabled()
    QTest.mouseClick(rotulo, Qt.MouseButton.LeftButton)
    assert caixa.isChecked()  # desabilitado: clicar no texto não muda nada


def test_painel_conexoes_mantem_encerradas_por_um_tempo(app):
    from proxy_manager.interface.modelo_conexoes import COL_REGRA, COL_ESTADO, ModeloConexoes

    agora = [1000.0]
    modelo = ModeloConexoes(segundos_apos_fechar=3, relogio=lambda: agora[0])
    ativo = EntradaLog(nome_processo="chrome.exe", host_destino="a.com", porta_destino=443, acao="proxy",
                       proxy_usado="Proxy SP", regra_aplicada="chrome", momento=935.0)
    fechando = EntradaLog(nome_processo="curl", host_destino="b.com", porta_destino=80, acao="proxy",
                          proxy_usado="Proxy SP")
    direta = EntradaLog(nome_processo="edge", host_destino="c.com", porta_destino=443, acao="direct")
    bloqueada = EntradaLog(nome_processo="edge", host_destino="d.com", porta_destino=443, acao="block",
                           estado="bloqueada")
    modelo.adicionar_entradas([ativo, direta, fechando, bloqueada])
    assert modelo.rowCount() == 2  # diretas e bloqueadas não aparecem no painel
    assert modelo.data(modelo.index(0, COL_ESTADO)) == "01:05"
    assert modelo.data(modelo.index(0, COL_REGRA)) == "chrome : Proxy SP"
    assert modelo.data(modelo.index(1, COL_REGRA)) == "padrão : Proxy SP"

    encerrada = EntradaLog(**{**fechando.__dict__, "estado": "concluida"})
    modelo.atualizar_entradas([encerrada])
    assert modelo.data(modelo.index(1, COL_ESTADO)) == "Fechada"
    agora[0] += 1
    modelo.atualizar_tempos()
    assert modelo.rowCount() == 2  # ainda aparece (em cinza) logo depois de fechar
    agora[0] += 3
    modelo.atualizar_tempos()
    assert modelo.rowCount() == 1 and modelo.entrada_em(0).id == ativo.id


def test_painel_conexoes_filtra_por_acao(app):
    from proxy_manager.interface.modelo_conexoes import ModeloConexoes

    agora = [1000.0]
    modelo = ModeloConexoes(segundos_apos_fechar=3, relogio=lambda: agora[0])
    via_proxy = EntradaLog(nome_processo="chrome.exe", host_destino="a.com", acao="proxy")
    direta = EntradaLog(nome_processo="edge", host_destino="c.com", acao="direct")
    bloqueada = EntradaLog(nome_processo="edge", host_destino="d.com", acao="block", estado="bloqueada")
    modelo.adicionar_entradas([via_proxy, direta, bloqueada])

    def ids():
        return [modelo.entrada_em(linha).id for linha in range(modelo.rowCount())]

    assert ids() == [via_proxy.id]
    modelo.definir_filtro("todas")
    assert ids() == [via_proxy.id, direta.id, bloqueada.id]
    modelo.definir_filtro("direct")
    assert ids() == [direta.id]

    # Diretas atualizadas enquanto ocultas aparecem com o estado novo e também expiram.
    modelo.definir_filtro("proxy")
    modelo.atualizar_entradas([EntradaLog(**{**direta.__dict__, "estado": "concluida"})])
    modelo.definir_filtro("todas")
    assert modelo.entrada_em(1).estado == "concluida"
    agora[0] += 3
    modelo.atualizar_tempos()
    assert ids() == [via_proxy.id]


def test_log_escreve_uma_linha_por_evento(window):
    log = window.log
    log.limpar_log()
    entrada = EntradaLog(nome_processo="chrome.exe", host_destino="a.com", porta_destino=443, acao="proxy",
                         proxy_usado="Proxy SP")
    window.ctx.log_adicionado.emit([entrada])
    concluida = EntradaLog(**{**entrada.__dict__, "estado": "concluida", "bytes_enviados": 2048,
                              "bytes_recebidos": 10, "duracao_ms": 65000})
    window.ctx.log_atualizado.emit([concluida])
    window.ctx.log_atualizado.emit([concluida])  # atualização repetida não duplica a linha
    falha = EntradaLog(nome_processo="curl", host_destino="b.com", porta_destino=80, acao="proxy",
                       proxy_usado="Proxy SP", estado="erro", erro="recusada")
    window.ctx.log_adicionado.emit([falha])
    # conexões diretas e bloqueadas não entram no log
    window.ctx.log_adicionado.emit([
        EntradaLog(nome_processo="d", host_destino="d.com", porta_destino=1, acao="direct"),
        EntradaLog(nome_processo="e", host_destino="e.com", porta_destino=1, acao="block", estado="bloqueada"),
    ])

    linhas = log.toPlainText().splitlines()
    assert linhas[0].endswith("chrome.exe - a.com:443 aberta através do proxy Proxy SP")
    assert linhas[1].endswith("chrome.exe - a.com:443 fechada, 2048 bytes (2.0 KB) enviados, "
                              "10 bytes recebidos, duração 01:05")
    assert linhas[-1].endswith("curl - b.com:80 erro : recusada")
    assert len(linhas) == 4

    window.definir_nivel_log("errors")
    window.ctx.log_adicionado.emit([EntradaLog(nome_processo="x", host_destino="c.com", porta_destino=1, acao="proxy")])
    assert len(log.toPlainText().splitlines()) == 4


def test_estatisticas_e_trafego_acompanham_motor(window):
    window.ctx.log_adicionado.emit([
        EntradaLog(acao="proxy"), EntradaLog(acao="direct"), EntradaLog(acao="block", estado="bloqueada"),
        EntradaLog(acao="proxy", estado="erro", erro="x"),
    ])
    window.ctx.motor.total_recebido += 4096
    window.ctx.motor.proxy_recebido += 1024
    window._a_cada_segundo()
    window._atualizar_trafego()

    valores = {chave: rotulo.text() for chave, rotulo in window.estatisticas.valores.items()}
    assert valores["total"] == ": 4" and valores["proxy"] == ": 2" and valores["blocked"] == ": 1"
    assert valores["failed"] == ": 1"
    assert valores["recv"].startswith(": 4.0 KB") and "via proxy: 1.0 KB" in valores["recv"]
    assert window.grafico_trafego.ultimos()[0] > 0
    assert window.status_download.text().startswith("Download 4.0 KB/s")

    window.zerar_estatisticas()
    assert window.estatisticas.valores["total"].text() == ": 0"
    for widget in (window.grafico_trafego, window.estatisticas):
        widget.resize(400, 200)
        assert not widget.grab().isNull()  # exercita o paintEvent


def test_menu_proxy_padrao_troca_perfil(window):
    ctx = window.ctx
    ctx.configuracao.proxies[:] = [PerfilProxy(nome="A", host="a", padrao=True), PerfilProxy(nome="B", host="b")]
    ctx.aplicar_mudancas()
    actions = window.menu_proxy_padrao.actions()
    assert [a.text() for a in actions] == ["A", "B"] and actions[0].isChecked()

    actions[1].trigger()
    assert ctx.configuracao.proxy_padrao().nome == "B"
    assert window.menu_proxy_padrao.actions()[1].isChecked()


def test_opcoes_do_menu_espelham_configuracoes(window):
    window.acao_icone_bandeja.setChecked(True)
    assert window.pagina_configuracoes.caixa_minimizar_bandeja.isChecked()
    assert window.ctx.configuracao.preferencias.minimizar_para_bandeja is True
    window.pagina_configuracoes.caixa_minimizar_bandeja.setChecked(False)
    assert not window.acao_icone_bandeja.isChecked()


def test_adicionar_e_remover_perfil(window):
    window.abrir_dialogo_perfil(0)
    pagina = window.pagina_proxies
    contagem_inicial = len(pagina.ctx.configuracao.proxies)

    pagina._ao_criar()
    assert len(pagina.ctx.configuracao.proxies) == contagem_inicial + 1

    pagina.campo_host.setText("proxy.example.com")
    pagina.campo_porta.setValue(1080)
    pagina._ao_salvar()
    assert pagina.ctx.configuracao.proxies[-1].host == "proxy.example.com"


def test_formulario_proxies_editavel_com_perfil_existente(app):
    """Com um perfil salvo, o formulário já abre habilitado."""
    ctx = Contexto()
    # Na primeira posição, que é a selecionada ao abrir a página.
    ctx.configuracao.proxies.insert(0, PerfilProxy(nome="Já existente", host="proxy.exemplo.com"))

    pagina = PaginaProxies(ctx)

    assert pagina.campo_host.isEnabled()
    assert pagina.campo_nome.isEnabled()
    assert pagina.botao_salvar.isEnabled()
    assert pagina.campo_host.text() == "proxy.exemplo.com"


def test_tabela_regras_ida_e_volta(window):
    window.abrir_dialogo_perfil(1)
    pagina = window.pagina_regras
    linhas_iniciais = pagina.tabela.rowCount()

    pagina._ao_adicionar_linha()
    pagina.tabela.item(linhas_iniciais, 2).setText("*.example.com")
    pagina._ao_salvar_tabela()

    assert "*.example.com" in pagina.ctx.configuracao.texto_regras


def test_botao_app_com_proxy_cria_regra_geral(window):
    """O botão '+ App com proxy padrão' precisa criar uma regra alvo='*' pronta para o usuário
    só digitar o nome do executável — atalho para 'todo o Chrome pelo proxy'."""
    window.abrir_dialogo_perfil(1)
    pagina = window.pagina_regras
    linha = pagina.tabela.rowCount()

    pagina._ao_adicionar_app_geral()
    assert pagina.tabela.item(linha, 2).text() == "*"

    pagina.tabela.item(linha, 1).setText("chrome.exe")
    pagina.tabela.setCurrentCell(linha, 2)  # sai do modo de edição da célula de Aplicativos

    conjunto_regras = pagina._regras_da_tabela()
    nova_regra = conjunto_regras.regras[-1]
    assert nova_regra.aplicativos == ["chrome.exe"]
    assert nova_regra.alvo == "*"
    assert nova_regra.acao == "proxy"


def test_botao_adicionar_app_desabilitado_sem_executavel(window):
    """O botão Adicionar só é habilitado depois de escolher o executável."""
    window.abrir_dialogo_perfil(1)
    pagina = window.pagina_regras
    assert not pagina.botao_adicionar_app.isEnabled()

    pagina.campo_caminho_app.setText(r"C:\Chrome\chrome.exe")
    assert pagina.botao_adicionar_app.isEnabled()

    pagina.campo_caminho_app.clear()
    assert not pagina.botao_adicionar_app.isEnabled()


def test_app_cadastrado_aparece_na_tabela(window):
    window.abrir_dialogo_perfil(1)
    pagina = window.pagina_regras
    linhas_iniciais = pagina.tabela_apps.rowCount()

    pagina.campo_nome_app.setText("Chrome")
    pagina.campo_caminho_app.setText(r"C:\Chrome\chrome.exe")
    pagina._ao_adicionar_app()

    assert pagina.tabela_apps.rowCount() == linhas_iniciais + 1
    assert pagina.ctx.configuracao.aplicativos[-1].nome == "Chrome"
    assert pagina.ctx.configuracao.aplicativos[-1].nome_processo == "chrome.exe"
    # os campos do formulário devem ser limpos após adicionar, prontos pro próximo cadastro
    assert pagina.campo_nome_app.text() == ""
    assert pagina.campo_caminho_app.text() == ""


def test_cadastrar_app_cria_regra_geral_se_nao_houver(window):
    """Cadastrar um app sem regras cria uma regra que manda todo o tráfego dele pelo proxy."""
    window.abrir_dialogo_perfil(1)
    pagina = window.pagina_regras

    pagina.campo_nome_app.setText("Chrome")
    pagina.campo_caminho_app.setText(r"C:\Chrome\chrome.exe")
    pagina._ao_adicionar_app()

    conjunto_regras = ConjuntoRegras.interpretar(pagina.ctx.configuracao.texto_regras)
    resultado = conjunto_regras.avaliar("chrome.exe", r"C:\Chrome\chrome.exe", "qualquerdominio.com", None,
                                        acao_padrao="direct")
    assert resultado.tipo_acao == "proxy"


def test_cadastrar_app_nao_duplica_regra(window):
    window.abrir_dialogo_perfil(1)
    pagina = window.pagina_regras

    pagina.ctx.configuracao.texto_regras = "apps: chrome.exe\n*.paypal.com +direct\n"
    pagina.ctx.aplicar_mudancas()

    pagina.campo_nome_app.setText("Chrome")
    pagina.campo_caminho_app.setText(r"C:\Chrome\chrome.exe")
    pagina._ao_adicionar_app()

    conjunto_regras = ConjuntoRegras.interpretar(pagina.ctx.configuracao.texto_regras)
    regras_chrome = [r for r in conjunto_regras.regras if "chrome.exe" in r.aplicativos]
    assert len(regras_chrome) == 1  # não deve ter criado uma segunda regra pra esse app


def test_app_com_proxy_oferece_apps_cadastrados(window, monkeypatch):
    """Com pelo menos um aplicativo já cadastrado, o botão '+ App com proxy padrão' deve deixar
    escolher ele numa lista, em vez de abrir a célula para digitação manual."""
    from PySide6.QtWidgets import QInputDialog

    window.abrir_dialogo_perfil(1)
    pagina = window.pagina_regras

    pagina.campo_nome_app.setText("Chrome")
    pagina.campo_caminho_app.setText(r"C:\Chrome\chrome.exe")
    pagina._ao_adicionar_app()

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (a[3][0], True)))

    linha = pagina.tabela.rowCount()
    pagina._ao_adicionar_app_geral()

    assert pagina.tabela.item(linha, 1).text() == "chrome.exe"
    assert pagina.tabela.item(linha, 2).text() == "*"


def test_opcoes_da_pagina_configuracoes(window):
    window.abrir_dialogo_perfil(2)
    pagina = window.pagina_configuracoes
    pagina.combo_acao_padrao.setCurrentIndex(1)
    assert pagina.ctx.configuracao.preferencias.acao_padrao == "block"
    pagina.campo_retencao.setValue(30)
    assert pagina.ctx.configuracao.preferencias.dias_retencao_log == 30


def test_aviso_voz_ao_trocar_ip_saida(window, monkeypatch):
    from proxy_manager.interface.aviso_voz import FRASE_TROCA_IP

    ctx = window.ctx
    ctx.configuracao.proxies = [PerfilProxy(id="p1", nome="Casa")]
    falas = []
    monkeypatch.setattr(ctx.aviso_voz, "falar", lambda texto: falas.append(texto) or True)

    ctx.aviso_voz._ao_mudar_ip_saida("p1", "", "10.0.0.1")  # primeira verificação: não é troca
    ctx.aviso_voz._ao_mudar_ip_saida("p1", "10.0.0.1", "10.0.0.2")
    assert falas == [FRASE_TROCA_IP]

    window.abrir_dialogo_perfil(2)
    window.pagina_configuracoes.caixa_aviso_voz.setChecked(False)
    assert not ctx.configuracao.preferencias.aviso_voz_ip_ativo
    ctx.aviso_voz._ao_mudar_ip_saida("p1", "10.0.0.2", "10.0.0.3")
    assert len(falas) == 1


def test_iniciar_e_parar_motor_pelo_menu(window):
    ctx = window.ctx
    ctx.configuracao.preferencias.porta_socks = 58480
    ctx.configuracao.preferencias.porta_http = 58481
    ctx.configuracao.preferencias.porta_pac = 58490

    window.acao_motor.trigger()
    QTest.qWait(300)
    assert ctx.motor.em_execucao()
    assert window.acao_motor.text() == "&Parar motor"
    assert window.status_motor.text() == "● Motor ativo"

    window.acao_motor.trigger()
    QTest.qWait(300)
    assert not ctx.motor.em_execucao()
    assert window.acao_motor.text() == "&Iniciar motor"


class _EventoFecharFalso:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


def test_trocar_porta_sem_parar_motor(window):
    """Trocar a porta com o motor ligado reabre só aquela porta."""
    ctx = window.ctx
    ctx.configuracao.preferencias.porta_socks = 58910
    ctx.configuracao.preferencias.porta_http = 58911
    ctx.configuracao.preferencias.porta_pac = 58912

    window.alternar_motor()
    QTest.qWait(300)
    assert ctx.motor.em_execucao()

    window.abrir_dialogo_perfil(2)
    pagina = window.pagina_configuracoes
    pagina.campo_porta_socks.setValue(58910)  # sem mudança
    pagina.campo_porta_http.setValue(58911)  # sem mudança
    pagina.campo_porta_pac.setValue(58913)  # só o PAC muda
    pagina._ao_salvar_portas()
    QTest.qWait(200)

    # o motor nunca parou (é exatamente o que "trocar sem derrubar" significa)
    assert pagina.ctx.motor.em_execucao()
    assert pagina.ctx.configuracao.preferencias.porta_pac == 58913


def test_fechar_sem_bandeja_encerra_app(window, app, monkeypatch):
    """Sem bandeja, fechar a janela encerra o app em vez de escondê-lo."""
    window.bandeja = None
    chamadas_parar = []
    monkeypatch.setattr(window.ctx.motor, "parar", lambda: chamadas_parar.append(True))
    chamadas_sair = []
    monkeypatch.setattr(app, "quit", lambda: chamadas_sair.append(True))

    evento = _EventoFecharFalso()
    window.closeEvent(evento)

    assert chamadas_parar == [True]
    assert chamadas_sair == [True]
    assert evento.accepted is True
    assert evento.ignored is False


def test_fechar_com_bandeja_encerra_por_padrao(window, app, monkeypatch):
    """Por padrão fechar encerra o app, mesmo com bandeja disponível."""
    assert window.ctx.configuracao.preferencias.minimizar_para_bandeja is False
    window.bandeja = object()
    monkeypatch.setattr(window.ctx.motor, "parar", lambda: None)
    chamadas_sair = []
    monkeypatch.setattr(app, "quit", lambda: chamadas_sair.append(True))

    evento = _EventoFecharFalso()
    window.closeEvent(evento)

    assert chamadas_sair == [True]
    assert evento.accepted is True


def test_fechar_com_bandeja_esconde_quando_ativado(window, monkeypatch):
    """Com a opção ligada nas Configurações, fechar só esconde a janela na bandeja."""
    monkeypatch.setattr(window.ctx.configuracao.preferencias, "minimizar_para_bandeja", True)
    chamadas_esconder = []
    monkeypatch.setattr(window, "hide", lambda: chamadas_esconder.append(True))
    chamadas_mensagem = []

    class _BandejaFalsa:
        def showMessage(self, *args, **kwargs):
            chamadas_mensagem.append((args, kwargs))

    window.bandeja = _BandejaFalsa()

    evento = _EventoFecharFalso()
    window.closeEvent(evento)

    assert chamadas_esconder == [True]
    assert chamadas_mensagem
    assert evento.ignored is True
    assert evento.accepted is False


def test_proxy_sistema_acompanha_motor(window, monkeypatch):
    """O proxy do sistema liga e desliga com o motor.

    Sem qWait de propósito: a remoção precisa terminar antes de parar() retornar."""
    from proxy_manager.nucleo import integracao_sistema

    chamadas = []
    monkeypatch.setattr(integracao_sistema, "aplicar_proxy_sistema",
                        lambda url, port: chamadas.append(("apply", url, port)) or (True, "ok"))
    monkeypatch.setattr(integracao_sistema, "remover_proxy_sistema",
                        lambda **kw: chamadas.append(("remove",)) or (True, "ok"))

    ctx = window.ctx
    ctx.configuracao.preferencias.integracao_sistema_ativa = True
    ctx.configuracao.preferencias.porta_socks = 58680
    ctx.configuracao.preferencias.porta_http = 58681
    ctx.configuracao.preferencias.porta_pac = 58690

    ctx.motor.iniciar()
    assert any(c[0] == "apply" for c in chamadas), "deveria aplicar o proxy do sistema antes de start() retornar"

    chamadas.clear()
    ctx.motor.parar()
    assert any(c[0] == "remove" for c in chamadas), "deveria remover o proxy do sistema antes de stop() retornar"


def test_sair_para_o_motor_do_servico(window):
    chamadas = []

    class MotorRemotoFalso:
        remoto = True

        def parar(self):
            chamadas.append("parar")

        def desconectar(self):
            chamadas.append("desconectar")

    motor_local = window.ctx.motor
    window.ctx.motor = MotorRemotoFalso()
    try:
        window.ctx.encerrar()
    finally:
        window.ctx.motor = motor_local
    assert chamadas == ["parar", "desconectar"]
