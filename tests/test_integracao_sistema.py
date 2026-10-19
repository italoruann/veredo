"""Testes da integração de proxy do sistema no Linux: mocka subprocess/which (nunca mexe no
gsettings de verdade) pra rodar em qualquer SO."""
import subprocess

import pytest

from proxy_manager.nucleo import navegadores
from proxy_manager.nucleo import integracao_sistema as mod

PAC = "http://127.0.0.1:58093/proxy.pac"


class _Concluido:
    def __init__(self, stdout: str = ""):
        self.returncode = 0
        self.stdout = stdout.encode()
        self.stderr = b""


@pytest.fixture(autouse=True)
def _somente_gsettings(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_tem_binario", lambda nome: nome == "gsettings")
    monkeypatch.setattr(mod, "_uid_usuario", lambda: None)
    monkeypatch.setattr(mod, "pasta_dados", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("KDE_SESSION_VERSION", raising=False)
    # Sem navegadores instalados: a parte dos navegadores é testada em test_navegadores.py.
    monkeypatch.setattr(mod, "_home_usuario", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "share"))
    monkeypatch.setattr(navegadores, "_PASTAS_APPS_EXTRAS", ())


def _gsettings_falso(monkeypatch, gravado: bool):
    store: dict[str, str] = {}

    def run_falso(cmd, check=False, capture_output=False, timeout=None, input=None):
        if cmd[1] == "set":
            if gravado:
                store[cmd[3]] = cmd[4]
            return _Concluido()
        # backend em memória: um processo novo sempre relê o valor padrão
        padroes = {"mode": "none", "autoconfig-url": ""}
        return _Concluido(f"'{store.get(cmd[3], padroes[cmd[3]])}'\n")

    monkeypatch.setattr(mod.subprocess, "run", run_falso)


def test_gsettings_configurado_quando_valor_persiste(monkeypatch):
    _gsettings_falso(monkeypatch, gravado=True)
    ok, mensagem = mod._aplicar_linux(PAC, 58091)
    assert ok is True
    assert "gsettings configurado" in mensagem


def test_gsettings_em_memoria_conta_como_falha(monkeypatch):
    _gsettings_falso(monkeypatch, gravado=False)
    ok, mensagem = mod._aplicar_linux(PAC, 58091)
    assert ok is False
    assert "dconf" in mensagem


def test_gsettings_sem_schema_explica_solucao(monkeypatch):
    def run_falso(cmd, check=False, capture_output=False, timeout=None, input=None):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"No such schema \"org.gnome.system.proxy\"")

    monkeypatch.setattr(mod.subprocess, "run", run_falso)
    ok, mensagem = mod._aplicar_linux(PAC, 58091)
    assert ok is False
    assert "No such schema" in mensagem
    assert "gsettings-desktop-schemas" in mensagem


def test_avisa_que_chromium_ignora_proxy_no_xfce_sem_atalhos(monkeypatch):
    _gsettings_falso(monkeypatch, gravado=True)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "XFCE")
    ok, mensagem = mod._aplicar_linux(PAC, 58091)
    assert f"--proxy-pac-url={PAC}" in mensagem


def test_gnome_dispensa_flag_no_navegador(monkeypatch):
    _gsettings_falso(monkeypatch, gravado=True)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    ok, mensagem = mod._aplicar_linux(PAC, 58091)
    assert ok is True
    assert "--proxy-pac-url" not in mensagem


def test_script_env_exporta_pac_para_chromium(tmp_path):
    caminho = mod._gravar_script_env(58091, PAC)
    assert f'export auto_proxy="{PAC}"' in caminho.read_text(encoding="utf-8")


def _kde_falso(monkeypatch, gsettings_gravado: bool = True):
    """Sem kwriteconfig: simula o cat/tee do kioslaverc em Python (roda em qualquer SO)."""
    gsettings: dict[str, str] = {}
    signals: list[list[str]] = []

    def run_falso(cmd, check=False, capture_output=False, timeout=None, input=None):
        if cmd[0] == "gsettings":
            if cmd[1] == "set":
                if gsettings_gravado:
                    gsettings[cmd[3]] = cmd[4]
                return _Concluido()
            return _Concluido(f"'{gsettings.get(cmd[3], '')}'\n")
        if cmd[0] == "sh":
            caminho = mod.Path(cmd[-1])
            return _Concluido(caminho.read_text(encoding="utf-8") if caminho.exists() else "")
        if cmd[0] == "tee":
            mod.Path(cmd[-1]).write_bytes(input)
            return _Concluido()
        if cmd[0] == "dbus-send":
            signals.append(cmd)
            return _Concluido()
        raise AssertionError(cmd)

    monkeypatch.setattr(mod.subprocess, "run", run_falso)
    monkeypatch.setattr(mod, "_tem_binario", lambda nome: nome in ("gsettings", "dbus-send"))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    return signals


def test_kde_sem_kwriteconfig_edita_kioslaverc(monkeypatch, tmp_path):
    (tmp_path / "kioslaverc").write_text(
        "[Other]\nfoo=bar\n\n[Proxy Settings]\nProxyType=0\nNoProxyFor=\n", encoding="utf-8")
    signals = _kde_falso(monkeypatch)

    ok, mensagem = mod._aplicar_linux(PAC, 58091)

    assert ok is True
    assert "KDE/kioslaverc configurado" in mensagem
    valores = mod._ler_kioslaverc((tmp_path / "kioslaverc").read_text(encoding="utf-8"))
    assert valores == {"ProxyType": "2", "NoProxyFor": "", "Proxy Config Script": PAC}
    assert "[Other]\nfoo=bar" in (tmp_path / "kioslaverc").read_text(encoding="utf-8")
    assert signals and "reparseSlaveConfiguration" in signals[0][4]


def test_kde_cria_kioslaverc_e_reverte(monkeypatch, tmp_path):
    _kde_falso(monkeypatch)
    mod._aplicar_linux(PAC, 58091)
    ok, mensagem = mod._remover_linux()
    assert "KDE/kioslaverc revertido" in mensagem
    assert mod._ler_kioslaverc((tmp_path / "kioslaverc").read_text(encoding="utf-8"))["ProxyType"] == "0"


def test_kde_funciona_mesmo_com_gsettings_falhando(monkeypatch):
    _kde_falso(monkeypatch, gsettings_gravado=False)
    ok, mensagem = mod._aplicar_linux(PAC, 58091)
    assert ok is True  # o Chrome ainda pega pelo kioslaverc
    assert "KDE/kioslaverc configurado" in mensagem


def test_kde_detectado_pelo_kioslaverc_sem_variaveis(monkeypatch, tmp_path):
    (tmp_path / "kioslaverc").write_text("", encoding="utf-8")
    assert mod._kde_presente() is True


def test_editar_kioslaverc_acrescenta_grupo_ausente():
    texto = mod._editar_kioslaverc("[General]\na=1\n", {"ProxyType": "2"})
    assert texto == "[General]\na=1\n\n[Proxy Settings]\nProxyType=2\n"
