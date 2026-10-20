import json

import pytest

from proxy_manager.nucleo import arquivo_perfil
from proxy_manager.nucleo.configuracao import Aplicativo, CadeiaProxy, Configuracao, PerfilProxy
from proxy_manager.nucleo.regras import ConjuntoRegras

PPX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ProxifierProfile version="102" platform="Windows" product_id="0" product_minver="310">
  <ProxyList>
    <Proxy id="100" type="SOCKS5">
      <Address>10.1.1.1</Address>
      <Port>1080</Port>
      <Authentication enabled="true">
        <Username>joao</Username>
        <Password>segredo</Password>
      </Authentication>
    </Proxy>
    <Proxy id="101" type="HTTPS">
      <Address>proxy.empresa.com</Address>
      <Port>3128</Port>
    </Proxy>
  </ProxyList>
  <ChainList>
    <Chain id="102" type="redundancy">
      <Name>Reserva</Name>
      <Proxy enabled="true">100</Proxy>
      <Proxy enabled="true">101</Proxy>
    </Chain>
  </ChainList>
  <RuleList>
    <Rule enabled="true">
      <Name>Localhost</Name>
      <Targets>localhost; 127.0.0.1; %ComputerName%</Targets>
      <Action type="Direct" />
    </Rule>
    <Rule enabled="false">
      <Name>Navegadores</Name>
      <Applications>chrome.exe; "C:\\Program Files\\Mozilla Firefox\\firefox.exe"</Applications>
      <Ports>80; 443</Ports>
      <Action type="Chain">102</Action>
    </Rule>
    <Rule enabled="true">
      <Name>Rede interna</Name>
      <Targets>10.0.0.1-10.0.0.50</Targets>
      <Action type="Block" />
    </Rule>
    <Rule enabled="true">
      <Name>Default</Name>
      <Action type="Proxy">101</Action>
    </Rule>
  </RuleList>
</ProxifierProfile>
"""


def test_importa_ppx_do_proxifier(tmp_path):
    caminho = tmp_path / "perfil.ppx"
    caminho.write_text(PPX, encoding="utf-8")
    cfg = arquivo_perfil.importar(caminho)

    assert [(p.nome, p.type, p.host, p.port) for p in cfg.proxies] == [
        ("10.1.1.1:1080", "socks5", "10.1.1.1", 1080),
        ("proxy.empresa.com:3128", "http", "proxy.empresa.com", 3128),
    ]
    assert cfg.proxies[0].usuario == "joao" and cfg.proxies[0].senha == "segredo"
    assert cfg.proxies[0].padrao

    assert len(cfg.cadeias) == 1
    cadeia = cfg.cadeias[0]
    assert (cadeia.nome, cadeia.tipo) == ("Reserva", "redundancia")
    assert cadeia.proxies == [cfg.proxies[0].id, cfg.proxies[1].id]

    regras = ConjuntoRegras.interpretar(cfg.texto_regras).regras
    assert [(r.alvo, r.portas, r.acao, r.ativo) for r in regras] == [
        ("localhost", "", "direct", True),
        ("127.0.0.1", "", "direct", True),
        ("*", "80,443", "chain:Reserva", False),
        ("10.0.0.1-10.0.0.50", "", "block", True),
        ("*", "", "proxy:proxy.empresa.com:3128", True),
    ]
    assert regras[2].aplicativos == ["chrome.exe", "C:\\Program Files\\Mozilla Firefox\\firefox.exe"]


def test_exporta_e_importa_json(tmp_path):
    perfil = PerfilProxy(nome="p", host="h", port=1, usuario="u", senha="s", padrao=True)
    cfg = Configuracao(proxies=[perfil], cadeias=[CadeiaProxy(nome="c", proxies=[perfil.id])],
                       aplicativos=[Aplicativo(nome="Chrome", caminho_executavel="/x/chrome")],
                       texto_regras="* +chain:c\n")
    caminho = tmp_path / "perfil.json"

    arquivo_perfil.exportar(cfg, caminho, incluir_senhas=False)
    assert json.loads(caminho.read_text(encoding="utf-8"))["proxies"][0]["senha"] == ""
    lido = arquivo_perfil.importar(caminho)
    assert lido.proxies[0].host == "h" and lido.proxies[0].senha == ""
    assert lido.cadeias[0].proxies == [perfil.id]
    assert lido.aplicativos[0].nome == "Chrome"
    assert lido.texto_regras == "* +chain:c\n"

    arquivo_perfil.exportar(cfg, caminho, incluir_senhas=True)
    assert arquivo_perfil.importar(caminho).proxies[0].senha == "s"


def test_arquivo_que_nao_e_perfil(tmp_path):
    caminho = tmp_path / "x.json"
    caminho.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(arquivo_perfil.ErroPerfil):
        arquivo_perfil.importar(caminho)
