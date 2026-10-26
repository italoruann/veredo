"""Proxy do sistema no macOS, com um networksetup simulado."""
import subprocess

import pytest

from proxy_manager.nucleo import integracao_sistema as mod

URL = "http://127.0.0.1:58090/proxy.pac"


class _NetworksetupFalso:
    """Guarda o PAC de cada serviço como o macOS guardaria. `pede_admin`: recusa as mudanças
    feitas sem administrador, como acontece em alguns Macs."""

    def __init__(self, pede_admin=False):
        self.servicos = {"Wi-Fi": ["(null)", False], "Ethernet": ["http://empresa/proxy.pac", True],
                         "Bluetooth PAN": ["(null)", False]}
        self.desabilitados = {"Bluetooth PAN"}
        self.pede_admin = pede_admin
        self.pedidos_senha = []

    def _networksetup(self, cmd, admin):
        acao = cmd[1]
        if acao == "-listallnetworkservices":
            linhas = ["An asterisk (*) denotes that a network service is disabled."]
            linhas += [("*" if s in self.desabilitados else "") + s for s in self.servicos]
            return 0, "\n".join(linhas) + "\n"
        servico = cmd[2]
        if acao == "-getautoproxyurl":
            url, ligado = self.servicos[servico]
            return 0, f"URL: {url}\nEnabled: {'Yes' if ligado else 'No'}\n"
        if self.pede_admin and not admin:
            return 0, "** Error: Command requires admin privileges.\n"
        if acao == "-setautoproxyurl":
            self.servicos[servico] = [cmd[3], True]
        elif acao == "-setautoproxystate":
            self.servicos[servico][1] = cmd[3] == "on"
        return 0, ""

    def run(self, cmd, capture_output=True, text=True, timeout=None):
        if cmd[0] == "osascript":
            script = cmd[2]
            self.pedidos_senha.append(script)
            assert script.endswith("with administrator privileges")
            corpo = script[len('do shell script "'):-len('" with administrator privileges')]
            import shlex
            for parte in corpo.split(" && "):
                self._networksetup(shlex.split(parte), admin=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        codigo, saida = self._networksetup(cmd, admin=False)
        return subprocess.CompletedProcess(cmd, codigo, saida, "")


@pytest.fixture()
def macos(monkeypatch, tmp_path):
    def criar(pede_admin=False):
        falso = _NetworksetupFalso(pede_admin)
        monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(mod.subprocess, "run", falso.run)
        monkeypatch.setattr(mod, "pasta_dados", lambda: tmp_path)
        return falso
    return criar


def test_aplica_o_pac_em_todos_os_servicos_habilitados_e_restaura_ao_remover(macos):
    falso = macos()
    ok, mensagem = mod.aplicar_proxy_sistema(URL, 58081)
    assert ok, mensagem
    assert falso.servicos["Wi-Fi"] == [URL, True]
    assert falso.servicos["Ethernet"] == [URL, True]
    assert falso.servicos["Bluetooth PAN"] == ["(null)", False]  # desabilitado: intocado
    # reaplicar (o motor reinicia) não troca o PAC guardado do usuário pelo do Veredo
    assert mod.aplicar_proxy_sistema(URL, 58081)[0]

    ok, _ = mod.remover_proxy_sistema()
    assert ok
    assert falso.servicos["Wi-Fi"][1] is False  # não tinha PAC: desligado
    assert falso.servicos["Ethernet"] == ["http://empresa/proxy.pac", True]  # o do usuário voltou
    assert not falso.pedidos_senha


def test_remover_nao_mexe_no_proxy_trocado_pelo_usuario(macos):
    falso = macos()
    mod.aplicar_proxy_sistema(URL, 58081)
    falso.servicos["Wi-Fi"] = ["http://outro/proxy.pac", True]
    mod.remover_proxy_sistema()
    assert falso.servicos["Wi-Fi"] == ["http://outro/proxy.pac", True]


def test_sem_permissao_pede_a_senha_uma_vez_so(macos):
    falso = macos(pede_admin=True)
    ok, mensagem = mod.aplicar_proxy_sistema(URL, 58081)
    assert ok, mensagem
    assert len(falso.pedidos_senha) == 1
    assert falso.servicos["Wi-Fi"] == [URL, True] and falso.servicos["Ethernet"] == [URL, True]


def test_aspas_do_applescript():
    assert mod._aspas_applescript('a "b" \\c') == '"a \\"b\\" \\\\c"'
