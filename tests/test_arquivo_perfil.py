import json

import pytest

from proxy_manager.nucleo import arquivo_perfil
from proxy_manager.nucleo.configuracao import Aplicativo, CadeiaProxy, Configuracao, PerfilProxy


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
