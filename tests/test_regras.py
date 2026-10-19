from proxy_manager.nucleo.regras import ConjuntoRegras, padrao_corresponde, app_corresponde

EXEMPLO = """
# comentário deve ser ignorado
*.example.com
*.example.net

*.paypal.com +direct
*.stripe.com +direct

apps: chrome.exe, msedge.exe
*.internal.corp +proxy:vpn
ads.tracker.com +block
"""


def test_interpretar_regras_basicas():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    assert len(conjunto.regras) == 6
    assert conjunto.regras[0].alvo == "*.example.com"
    assert conjunto.regras[0].acao == "proxy"
    assert conjunto.regras[0].aplicativos == []


def test_sufixo_direct():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    resultado = conjunto.avaliar("firefox", "/usr/bin/firefox", "checkout.stripe.com", None)
    assert resultado.tipo_acao == "direct"


def test_sem_sufixo_usa_proxy_padrao():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    resultado = conjunto.avaliar("firefox", "/usr/bin/firefox", "westus.example.com", None)
    assert resultado.tipo_acao == "proxy"
    assert resultado.nome_proxy is None  # usa o proxy padrão


def test_sufixo_proxy_com_nome():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    resultado = conjunto.avaliar("chrome.exe", "C:/chrome.exe", "vpn.internal.corp", None)
    assert resultado.tipo_acao == "proxy"
    assert resultado.nome_proxy == "vpn"


def test_sufixo_block():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    resultado = conjunto.avaliar("chrome.exe", "C:/chrome.exe", "ads.tracker.com", None)
    assert resultado.tipo_acao == "block"


def test_escopo_de_app_restringe():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    # ads.tracker.com só está no escopo "chrome.exe, msedge.exe"; firefox não deve casar
    resultado = conjunto.avaliar("firefox", "/usr/bin/firefox", "ads.tracker.com", None, acao_padrao="direct")
    assert resultado.tipo_acao == "direct"  # cai no default, não bate a regra de bloqueio


def test_sem_regra_usa_acao_padrao():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    resultado = conjunto.avaliar("curl", "/usr/bin/curl", "example.org", None, acao_padrao="block")
    assert resultado.tipo_acao == "block"


def test_curinga_casa_dominio_e_subdominio():
    assert padrao_corresponde("*.example.com", "example.com", None)
    assert padrao_corresponde("*.example.com", "sub.example.com", None)
    assert not padrao_corresponde("*.example.com", "notexample.com", None)


def test_cidr_casa_ip():
    assert padrao_corresponde("10.0.0.0/8", None, "10.1.2.3")
    assert not padrao_corresponde("10.0.0.0/8", None, "11.1.2.3")


def test_ip_exato():
    assert padrao_corresponde("1.2.3.4", None, "1.2.3.4")
    assert not padrao_corresponde("1.2.3.4", None, "1.2.3.5")


def test_app_casa_pelo_nome_sem_diferenciar_maiusculas():
    assert app_corresponde(["chrome.exe"], "Chrome.exe", "C:/Program Files/Chrome.exe")
    assert app_corresponde(["chrome"], "chrome.exe", "/usr/bin/chrome")
    assert not app_corresponde(["firefox"], "chrome.exe", "/usr/bin/chrome")


def test_regra_desativada_e_ignorada():
    conjunto = ConjuntoRegras.interpretar("!*.disabled.com +direct\n*.disabled.com +block")
    resultado = conjunto.avaliar("app", "", "sub.disabled.com", None)
    assert resultado.tipo_acao == "block"


def test_regra_geral_de_app_vale_so_para_ele():
    """Cenário real de uso: 'todo o tráfego do Chrome vai pelo proxy, o resto vai direto' — sem
    precisar de uma regra por domínio, só declarando o app uma vez com um alvo coringa."""
    conjunto = ConjuntoRegras.interpretar("apps: chrome.exe\n*\n")

    resultado_chrome = conjunto.avaliar("chrome.exe", "C:/Program Files/Google/Chrome/chrome.exe",
                                        "example.org", None, acao_padrao="direct")
    assert resultado_chrome.tipo_acao == "proxy"

    resultado_outro = conjunto.avaliar("spotify.exe", "C:/Program Files/Spotify/Spotify.exe",
                                       "example.org", None, acao_padrao="direct")
    assert resultado_outro.tipo_acao == "direct"


def test_texto_ida_e_volta():
    conjunto = ConjuntoRegras.interpretar(EXEMPLO)
    text2 = conjunto.para_texto()
    conjunto2 = ConjuntoRegras.interpretar(text2)
    assert len(conjunto.regras) == len(conjunto2.regras)
    for regra1, regra2 in zip(conjunto.regras, conjunto2.regras):
        assert regra1.alvo == regra2.alvo
        assert regra1.acao == regra2.acao
        assert regra1.aplicativos == regra2.aplicativos


def test_app_casa_apelidos_de_lancadores_linux():
    # /usr/bin/google-chrome é um script que faz exec de /opt/google/chrome/chrome
    assert app_corresponde(["google-chrome"], "chrome", "/opt/google/chrome/chrome")
    # Chromium em snap (Ubuntu): /snap/bin/chromium roda um processo "chrome"
    assert app_corresponde(["chromium"], "chrome", "/snap/chromium/3000/usr/lib/chromium-browser/chrome")
    assert app_corresponde(["microsoft-edge"], "msedge", "/opt/microsoft/msedge/msedge")
    assert not app_corresponde(["google-chrome"], "firefox", "/usr/lib/firefox/firefox")


def test_linhas_apps_seguidas_se_somam():
    """Regressão: "apps: chrome" seguido de "apps: firefox" só valia para o Firefox (a segunda
    linha substituía a primeira). Linhas apps: seguidas precisam somar."""
    conjunto = ConjuntoRegras.interpretar("apps: chrome\napps: firefox\n*\n")
    assert conjunto.regras[0].aplicativos == ["chrome", "firefox"]
    assert conjunto.avaliar("chrome.exe", "", "example.org", None).tipo_acao == "proxy"
    assert conjunto.avaliar("firefox.exe", "", "example.org", None).tipo_acao == "proxy"
    assert conjunto.avaliar("spotify.exe", "", "example.org", None).tipo_acao == "direct"


def test_linha_apps_apos_regra_inicia_novo_bloco():
    conjunto = ConjuntoRegras.interpretar("apps: chrome\n*.a.com\napps: firefox\n*.b.com\napps: firefox\napps: *\n*.c.com\n")
    assert [r.aplicativos for r in conjunto.regras] == [["chrome"], ["firefox"], []]
    reinterpretado = ConjuntoRegras.interpretar(conjunto.para_texto())
    assert [r.aplicativos for r in reinterpretado.regras] == [["chrome"], ["firefox"], []]
