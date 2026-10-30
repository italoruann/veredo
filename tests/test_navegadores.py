"""Testes da configuração direta dos navegadores no Linux: tudo numa HOME falsa em tmp_path, sem
subprocess (rodando como o próprio usuário os arquivos são lidos/gravados direto)."""
import pytest

from proxy_manager.nucleo import navegadores as bp
from proxy_manager.nucleo import integracao_sistema as si

PAC = "http://127.0.0.1:58093/proxy.pac"

ATALHO_CHROME = """\
[Desktop Entry]
Name=Google Chrome
Exec=/usr/bin/google-chrome-stable %U
Type=Application

[Desktop Action new-private-window]
Name=New Incognito Window
Exec=/usr/bin/google-chrome-stable --incognito
"""

ATALHO_BRAVE_FLATPAK = """\
[Desktop Entry]
Name=Brave
Exec=/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=brave --file-forwarding com.brave.Browser @@u %U @@
DBusActivatable=true
"""


@pytest.fixture
def home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(si, "_uid_usuario", lambda: None)
    monkeypatch.setattr(si, "_home_usuario", lambda: home)
    monkeypatch.setattr(si, "_tem_binario", lambda nome: False)
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "usr-share"))
    monkeypatch.setattr(bp, "_PASTAS_APPS_EXTRAS", (tmp_path / "flatpak-apps",))
    monkeypatch.setattr(bp, "_chromium_aberto", lambda: False)
    return home


def _perfil(home, raiz=".mozilla/firefox", nome="abc.default-release", prefs=""):
    perfil = home / raiz / nome
    perfil.mkdir(parents=True)
    (perfil / "prefs.js").write_text(prefs, encoding="utf-8")
    return perfil


def _preferencias(perfil):
    return bp._ler_prefs((perfil / "prefs.js").read_text(encoding="utf-8"))


def test_perfis_firefox_nativo_flatpak_e_snap(home):
    nativo = _perfil(home)
    flatpak = _perfil(home, ".var/app/org.mozilla.firefox/.mozilla/firefox")
    snap = _perfil(home, "snap/firefox/common/.mozilla/firefox")
    (home / ".mozilla/firefox/Crash Reports").mkdir()
    assert bp.perfis_firefox() == [nativo, flatpak, snap]


def test_firefox_aplica_user_js_e_restaura_original(home):
    perfil = _perfil(home, prefs='user_pref("network.proxy.type", 1);\nuser_pref("browser.x", true);\n')
    (perfil / "user.js").write_text('user_pref("minha.pref", 1);\n', encoding="utf-8")

    retorno = bp.aplicar(PAC)
    assert retorno.perfis_firefox == 1
    user_js = (perfil / "user.js").read_text(encoding="utf-8")
    assert user_js.startswith('user_pref("minha.pref", 1);\n')
    assert bp._ler_prefs(user_js) == {
        "minha.pref": "1", "network.proxy.type": "2", "network.proxy.autoconfig_url": f'"{PAC}"'}

    # O Firefox grava no prefs.js o que leu do user.js; a remoção precisa desfazer isso.
    (perfil / "prefs.js").write_text(
        f'user_pref("browser.x", true);\nuser_pref("network.proxy.type", 2);\n'
        f'user_pref("network.proxy.autoconfig_url", "{PAC}");\n', encoding="utf-8")
    bp.aplicar(PAC)  # reaplicar não pode perder o original
    bp.remover()

    assert (perfil / "user.js").read_text(encoding="utf-8") == 'user_pref("minha.pref", 1);\n'
    assert _preferencias(perfil) == {"browser.x": "true", "network.proxy.type": "1"}


def test_firefox_remover_apaga_user_js_criado(home):
    perfil = _perfil(home)
    bp.aplicar(PAC)
    bp.remover()
    assert not (perfil / "user.js").exists()
    assert _preferencias(perfil) == {}


def test_firefox_aberto_mantem_bloco_de_restauracao(home):
    perfil = _perfil(home)
    bp.aplicar(PAC)
    (perfil / "lock").write_text("", encoding="utf-8")
    bp.remover()
    restaurar = bp._ler_prefs((perfil / "user.js").read_text(encoding="utf-8"))
    assert restaurar == {"network.proxy.type": "5", "network.proxy.autoconfig_url": '""'}

    (perfil / "lock").unlink()
    bp.remover()
    assert not (perfil / "user.js").exists()


def test_atalhos_chromium_recebem_flag_e_sao_removidos(home, tmp_path):
    (tmp_path / "usr-share/applications").mkdir(parents=True)
    (tmp_path / "usr-share/applications/google-chrome.desktop").write_text(ATALHO_CHROME, encoding="utf-8")
    (tmp_path / "flatpak-apps").mkdir()
    (tmp_path / "flatpak-apps/com.brave.Browser.desktop").write_text(ATALHO_BRAVE_FLATPAK, encoding="utf-8")

    retorno = bp.aplicar(PAC)
    assert retorno.chromium == ["Google Chrome", "Brave"]
    aplicativos = home / ".local/share/applications"
    chrome = (aplicativos / "google-chrome.desktop").read_text(encoding="utf-8")
    assert f"Exec=/usr/bin/google-chrome-stable --proxy-pac-url={PAC} %U" in chrome
    assert f"Exec=/usr/bin/google-chrome-stable --incognito --proxy-pac-url={PAC}" in chrome
    brave = (aplicativos / "com.brave.Browser.desktop").read_text(encoding="utf-8")
    assert f"com.brave.Browser --proxy-pac-url={PAC} @@u %U @@" in brave
    assert "DBusActivatable=false" in brave

    bp.aplicar(PAC)  # reaplicar não duplica a flag
    assert (aplicativos / "google-chrome.desktop").read_text(encoding="utf-8").count("--proxy-pac-url") == 2

    bp.remover()
    assert not (aplicativos / "google-chrome.desktop").exists()
    assert not (aplicativos / "com.brave.Browser.desktop").exists()


def test_atalho_do_usuario_nunca_e_sobrescrito(home, tmp_path):
    (tmp_path / "usr-share/applications").mkdir(parents=True)
    (tmp_path / "usr-share/applications/chromium.desktop").write_text(ATALHO_CHROME, encoding="utf-8")
    meu = home / ".local/share/applications/chromium.desktop"
    meu.parent.mkdir(parents=True)
    meu.write_text("[Desktop Entry]\nExec=meu-chromium\n", encoding="utf-8")

    retorno = bp.aplicar(PAC)
    assert retorno.chromium == []
    assert any("não foi alterado" in m for m in retorno.mensagens)
    bp.remover()
    assert meu.read_text(encoding="utf-8") == "[Desktop Entry]\nExec=meu-chromium\n"


def _instalar_chrome(tmp_path):
    (tmp_path / "usr-share/applications").mkdir(parents=True)
    (tmp_path / "usr-share/applications/google-chrome.desktop").write_text(ATALHO_CHROME, encoding="utf-8")


def test_parar_motor_mantem_atalhos_chromium(home, tmp_path):
    """Regressão: apagar o .desktop com o navegador aberto fazia o GNOME/KDE sumir com as janelas
    dele (pareciam minimizadas/fechadas). Parar o motor não mexe mais nos atalhos."""
    _instalar_chrome(tmp_path)
    bp.aplicar(PAC)
    bp.remover(manter_atalhos=True)
    assert (home / ".local/share/applications/google-chrome.desktop").exists()


def test_aplicar_nao_regrava_atalho_inalterado(home, tmp_path, monkeypatch):
    _instalar_chrome(tmp_path)
    bp.aplicar(PAC)
    gravacoes = []
    gravar_real = si._gravar_arquivo_usuario
    monkeypatch.setattr(si, "_gravar_arquivo_usuario", lambda p, t: gravacoes.append(p) or gravar_real(p, t))
    retorno = bp.aplicar(PAC)
    assert retorno.chromium == ["Google Chrome"]
    assert not [p for p in gravacoes if p.suffix == ".desktop"]


def test_desativar_integracao_espera_navegador_fechar(home, tmp_path, monkeypatch):
    _instalar_chrome(tmp_path)
    bp.aplicar(PAC)
    alvo = home / ".local/share/applications/google-chrome.desktop"

    monkeypatch.setattr(bp, "_chromium_aberto", lambda: True)
    mensagens = bp.remover()
    assert alvo.exists()
    assert any("Navegador aberto" in m for m in mensagens)

    monkeypatch.setattr(bp, "_chromium_aberto", lambda: False)
    bp.remover()
    assert not alvo.exists()


def test_firefox_pelo_sistema_segue_o_proxy_do_sistema(home):
    perfil = _perfil(home, prefs='user_pref("network.proxy.type", 1);\n')
    retorno = bp.aplicar(PAC, firefox_pelo_sistema=True)
    assert retorno.perfis_firefox == 1
    assert bp._ler_prefs((perfil / "user.js").read_text(encoding="utf-8")) == {"network.proxy.type": "5"}
    assert "troca sozinho" in " ".join(retorno.mensagens)

    bp.remover()
    assert not (perfil / "user.js").exists()
    assert _preferencias(perfil) == {"network.proxy.type": "1"}


def test_chromium_pelo_sistema_nao_cria_e_remove_atalhos(home, tmp_path):
    (tmp_path / "usr-share/applications").mkdir(parents=True)
    (tmp_path / "usr-share/applications/google-chrome.desktop").write_text(ATALHO_CHROME, encoding="utf-8")
    bp.aplicar(PAC)
    atalho = home / ".local/share/applications/google-chrome.desktop"
    assert atalho.exists()

    retorno = bp.aplicar(PAC, chromium_pelo_sistema=True)
    assert retorno.chromium == []
    assert not atalho.exists()
