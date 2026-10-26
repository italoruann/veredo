"""Modo serviço: canal de controle com um motor de verdade e a janela como cliente (MotorRemoto)."""
import asyncio
import json
import threading
import time
import xml.etree.ElementTree as ET

import pytest

from proxy_manager.nucleo import servico
from proxy_manager.nucleo.configuracao import Configuracao, PerfilProxy, Preferencias
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro

PORTA_CONTROLE = 58498


class _Servico:
    """O serviço rodando numa thread, como faria `--servico`, com os arquivos numa pasta temporária."""

    def __init__(self, configuracao: Configuracao, prazo_sem_janela: float = servico.PRAZO_SEM_JANELA):
        self.registro = Registro(dias_retencao=1)
        self.motor = MotorProxy(configuracao, self.registro)
        self.salvas: list[Configuracao] = []
        self.controle = servico.ServidorControle(self.motor, self.registro, "segredo", salvar=self.salvas.append,
                                                 prazo_sem_janela=prazo_sem_janela)
        self._pronto = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._rodar()), daemon=True)

    async def _rodar(self):
        self._loop = asyncio.get_running_loop()
        self._fim = asyncio.Event()
        await self.controle.iniciar(PORTA_CONTROLE)
        self._pronto.set()
        await self._fim.wait()
        await self.controle.parar()

    def __enter__(self):
        self._thread.start()
        assert self._pronto.wait(5)
        return self

    def __exit__(self, *_):
        self.motor.parar()
        self._loop.call_soon_threadsafe(self._fim.set)
        self._thread.join(5)


@pytest.fixture()
def pasta_servico(tmp_path, monkeypatch):
    monkeypatch.setattr(servico, "pasta_servico", lambda: tmp_path)
    (tmp_path / "controle.token").write_text("segredo", encoding="ascii")
    return tmp_path


def _configuracao(texto_regras="* +direct\n") -> Configuracao:
    prefs = Preferencias(porta_socks=58480, porta_http=58481, porta_pac=58490)
    return Configuracao(texto_regras=texto_regras, preferencias=prefs)


def _esperar(condicao, timeout=5.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicao():
            return True
        time.sleep(0.05)
    return False


def test_token_errado_e_recusado(pasta_servico):
    with _Servico(_configuracao()):
        (pasta_servico / "controle.token").write_text("outro", encoding="ascii")
        with pytest.raises(servico.ErroServico, match="token"):
            servico.pedir("estado", PORTA_CONTROLE)
        assert not servico.servico_disponivel(PORTA_CONTROLE)


def test_janela_controla_o_servico_e_recebe_o_log(pasta_servico):
    with _Servico(_configuracao()) as srv:
        assert servico.servico_disponivel(PORTA_CONTROLE)
        registro_janela = Registro(dias_retencao=1)
        remoto = servico.MotorRemoto(registro_janela, PORTA_CONTROLE)
        estados = []
        remoto.ao_mudar_estado = lambda ativo, mensagem: estados.append((ativo, mensagem))
        try:
            # a janela manda a configuração dela (com senha) e liga o motor do serviço
            cfg = _configuracao("localhost +direct\n* +block\n")
            cfg.proxies.append(PerfilProxy(nome="p", host="10.0.0.1", senha="s3nha"))
            remoto.atualizar_configuracao(cfg)
            assert srv.salvas[-1].proxies[0].senha == "s3nha"
            remoto.iniciar()
            assert remoto.em_execucao() and srv.motor.em_execucao()

            # conexão bloqueada pelas regras novas, feita ao serviço, aparece no log da janela
            async def conectar():
                leitor, escritor = await asyncio.open_connection("127.0.0.1", 58481)
                escritor.write(b"CONNECT 10.9.9.9:443 HTTP/1.1\r\n\r\n")
                await escritor.drain()
                return await leitor.read(-1)

            assert b" 403 " in asyncio.run(conectar())
            assert _esperar(lambda: any(e.host_destino == "10.9.9.9" and e.estado == "bloqueada"
                                        for e in registro_janela.recentes()))
            assert _esperar(lambda: remoto.total_enviado == srv.motor.total_enviado)

            # aplicar ao vivo devolve as preferências que o serviço não conseguiu aplicar
            cfg.preferencias.porta_http = 58481  # sem mudança
            ok, _ = remoto.aplicar_ao_vivo(cfg)
            assert ok

            remoto.parar()
            assert not remoto.em_execucao() and not srv.motor.em_execucao()
            # o estado chega pela resposta e pelo acompanhamento, mas a janela registra uma vez só
            time.sleep(0.5)
            assert estados.count((True, "Motor iniciado")) == 1
            assert estados.count((False, "Motor parado")) == 1
        finally:
            remoto.desconectar()
        # fechar a janela não para o serviço
        assert servico.servico_disponivel(PORTA_CONTROLE)


def test_janela_percebe_o_servico_cair(pasta_servico):
    srv = _Servico(_configuracao())
    with srv:
        remoto = servico.MotorRemoto(Registro(dias_retencao=1), PORTA_CONTROLE)
        mensagens = []
        remoto.ao_mudar_estado = lambda ativo, mensagem: mensagens.append(mensagem)
        remoto.iniciar()
        assert _esperar(lambda: remoto._conectado)
    assert _esperar(lambda: any("perdida" in m for m in mensagens))
    assert not remoto.em_execucao()
    remoto.desconectar()


def test_configuracao_do_servico_ida_e_volta(pasta_servico, monkeypatch):
    monkeypatch.setattr(servico, "_restringir_ao_administrador", lambda caminho: None)
    cfg = _configuracao()
    cfg.proxies.append(PerfilProxy(nome="p", host="h", senha="x"))
    servico.salvar_config_servico(cfg)
    lida = servico.carregar_config_servico()
    assert lida.proxies[0].senha == "x" and lida.preferencias.porta_http == 58481
    assert json.loads((pasta_servico / "servico.json").read_text(encoding="utf-8"))["texto_regras"] == "* +direct\n"


def test_tarefa_do_windows_roda_como_system_sem_limite_de_tempo():
    raiz = ET.fromstring(servico.tarefa_windows().split("\n", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert raiz.find(".//t:UserId", ns).text == "S-1-5-18"
    assert raiz.find(".//t:ExecutionTimeLimit", ns).text == "PT0S"
    assert raiz.find(".//t:DisallowStartIfOnBatteries", ns).text == "false"
    assert raiz.find(".//t:BootTrigger", ns) is not None
    assert raiz.find(".//t:Arguments", ns).text.endswith("--servico")


def test_unidade_do_systemd():
    unidade = servico.unidade_systemd()
    assert "WantedBy=multi-user.target" in unidade
    linha = next(l for l in unidade.splitlines() if l.startswith("ExecStart="))
    assert linha.endswith("--servico")
    # se o processo cair, o systemd desfaz as regras de rede; "-": uma falha na limpeza não conta
    limpeza = next(l for l in unidade.splitlines() if l.startswith("ExecStopPost="))
    assert limpeza.startswith("ExecStopPost=-") and limpeza.endswith("--limpar-rede")
    # o motor começa parado: não há por que atrasar o serviço até a rede subir
    assert "network-online" not in unidade


def test_executavel_empacotado_usa_a_propria_pasta(monkeypatch, tmp_path):
    # A pasta de extração do PyInstaller some depois; o serviço não pode depender dela.
    executavel = tmp_path / "veredo"
    monkeypatch.setattr(servico.sys, "frozen", True, raising=False)
    monkeypatch.setattr(servico.sys, "executable", str(executavel))
    unidade = servico.unidade_systemd()
    assert f"ExecStart={executavel} --servico" in unidade
    assert f"WorkingDirectory={tmp_path}" in unidade


def test_fluxos_interceptados_e_certificado_chegam_a_janela(pasta_servico):
    from proxy_manager.nucleo.interceptacao.fluxos import Fluxo

    with _Servico(_configuracao()) as srv:
        remoto = servico.MotorRemoto(Registro(dias_retencao=1), PORTA_CONTROLE)
        try:
            assert _esperar(lambda: remoto._conectado or remoto._sock is not None)
            time.sleep(0.2)  # a inscrição do "acompanhar" precisa chegar antes do evento
            fluxo = srv.motor.fluxos.adicionar(Fluxo(metodo="GET", host="exemplo.com", caminho="/",
                                                     corpo_resposta=b"\x00binario"))
            srv.motor.fluxos.atualizar(fluxo, status=200, estado="concluido")
            assert _esperar(lambda: (f := remoto.fluxos.obter(fluxo.id)) is not None and f.status == 200)
            assert remoto.fluxos.obter(fluxo.id).corpo_resposta == b"\x00binario"
            assert remoto.certificado_ca() == srv.motor.certificado_ca()
        finally:
            remoto.desconectar()


def test_motor_desliga_quando_nenhuma_janela_acompanha(pasta_servico):
    with _Servico(_configuracao(), prazo_sem_janela=0.6) as srv:
        remoto = servico.MotorRemoto(Registro(dias_retencao=1), PORTA_CONTROLE)
        try:
            remoto.iniciar()
            assert _esperar(lambda: remoto._conectado)
            # com a janela acompanhando, o motor fica ligado bem além do prazo
            time.sleep(1.5)
            assert srv.motor.em_execucao()
        finally:
            remoto.desconectar()  # a janela some sem pedir para parar (travou, foi morta)
        assert _esperar(lambda: not srv.motor.em_execucao(), timeout=5)
        # o serviço continua atendendo, e uma janela nova religa o motor
        assert servico.servico_disponivel(PORTA_CONTROLE)


def test_servico_nao_liga_o_motor_sozinho(pasta_servico):
    with _Servico(_configuracao(), prazo_sem_janela=0.4) as srv:
        time.sleep(1.0)
        assert not srv.motor.em_execucao()


def test_reinstalar_reinicia_o_servico(monkeypatch, tmp_path):
    # "enable --now" não reinicia um serviço já rodando: ele seguiria com o executável antigo.
    comandos = []
    monkeypatch.setattr(servico.platform, "system", lambda: "Linux")
    monkeypatch.setattr(servico, "CAMINHO_UNIDADE", tmp_path / "veredo.service")
    monkeypatch.setattr(servico, "_rodar", comandos.append)
    ok, _ = servico.instalar_servico()
    assert ok
    assert ["systemctl", "restart", servico.NOME_UNIDADE] in comandos
    assert not any("--now" in c for c in comandos)
