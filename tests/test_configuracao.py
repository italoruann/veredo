"""Testes de persistência: round-trip básico, backup automático e recuperação a partir dele
quando o config.json principal está corrompido ou ausente."""
import json

import pytest

from proxy_manager.nucleo import configuracao as config_module
from proxy_manager.nucleo.configuracao import (
    Configuracao,
    Aplicativo,
    PerfilProxy,
    Preferencias,
    carregar_configuracao,
    salvar_configuracao,
)


@pytest.fixture()
def pasta_config_isolada(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "pasta_configuracao", lambda: tmp_path)
    monkeypatch.setattr(config_module, "pasta_dados", lambda: tmp_path)
    return tmp_path


class CofreFalso:
    """Substitui o keyring real do SO nos testes: mesma interface, guardada em memória. Sem
    isso, os testes gravariam segredos de verdade no Credential Manager/Secret Service da
    máquina que roda a suíte."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, usuario, senha):
        self.store[(service, usuario)] = senha

    def get_password(self, service, usuario):
        return self.store.get((service, usuario))

    def delete_password(self, service, usuario):
        self.store.pop((service, usuario), None)


@pytest.fixture()
def cofre_falso(monkeypatch):
    falso = CofreFalso()
    monkeypatch.setattr(config_module, "keyring", falso)
    return falso


@pytest.fixture(autouse=True)
def _isolar_cofre_real(monkeypatch):
    """Nenhum teste usa o cofre real; por padrão simula "sem cofre disponível"."""
    monkeypatch.setattr(config_module, "keyring", None)


def _config_exemplo() -> Configuracao:
    proxy = PerfilProxy(nome="Trabalho", type="socks5", host="proxy.exemplo.com", port=1080,
                        usuario="italo", senha="segredo", padrao=True)
    preferencias = Preferencias(acao_padrao="block", dias_retencao_log=30)
    return Configuracao(proxies=[proxy], texto_regras="*.exemplo.com +direct\n", preferencias=preferencias)


def test_salvar_e_carregar_preserva_tudo(pasta_config_isolada):
    cfg = _config_exemplo()
    salvar_configuracao(cfg)

    carregada = carregar_configuracao()
    assert len(carregada.proxies) == 1
    assert carregada.proxies[0].host == "proxy.exemplo.com"
    assert carregada.proxies[0].senha == "segredo"
    assert carregada.texto_regras == "*.exemplo.com +direct\n"
    assert carregada.preferencias.acao_padrao == "block"
    assert carregada.preferencias.dias_retencao_log == 30


def test_salvar_cria_backup_da_versao_anterior(pasta_config_isolada):
    salvar_configuracao(_config_exemplo())  # primeira versão, ainda sem backup
    caminho_backup = pasta_config_isolada / "config.json.bak"
    assert not caminho_backup.exists()

    segunda = _config_exemplo()
    segunda.proxies[0].nome = "Trabalho v2"
    salvar_configuracao(segunda)  # agora deve ter feito backup da versão anterior

    assert caminho_backup.exists()
    with open(caminho_backup, encoding="utf-8") as arq:
        copia = json.load(arq)
    assert copia["proxies"][0]["nome"] == "Trabalho"  # a versão ANTERIOR, não a nova


def test_carregar_recupera_do_backup_se_arquivo_corrompido(pasta_config_isolada):
    salvar_configuracao(_config_exemplo())
    salvar_configuracao(_config_exemplo())  # garante que exista um .bak válido

    caminho_config = pasta_config_isolada / "config.json"
    caminho_config.write_text("{ isso não é json válido", encoding="utf-8")

    recuperada = carregar_configuracao()
    assert len(recuperada.proxies) == 1
    assert recuperada.proxies[0].host == "proxy.exemplo.com"

    # a recuperação também deve ter promovido o backup de volta a um config.json válido
    with open(caminho_config, encoding="utf-8") as arq:
        json.load(arq)  # não deve levantar exceção


def test_carregar_usa_padrao_sem_nada_recuperavel(pasta_config_isolada):
    cfg = carregar_configuracao()
    assert cfg.proxies == []
    assert cfg.texto_regras  # o texto de exemplo padrão


def test_nome_processo_vem_do_caminho_do_executavel():
    app = Aplicativo(nome="Chrome", caminho_executavel=r"C:\Program Files\Google\Chrome\chrome.exe")
    assert app.nome_processo == "chrome.exe"


def test_aplicativos_sao_preservados(pasta_config_isolada):
    cfg = _config_exemplo()
    cfg.aplicativos.append(Aplicativo(nome="Chrome", caminho_executavel=r"C:\Chrome\chrome.exe"))
    salvar_configuracao(cfg)

    carregada = carregar_configuracao()
    assert len(carregada.aplicativos) == 1
    assert carregada.aplicativos[0].nome == "Chrome"
    assert carregada.aplicativos[0].nome_processo == "chrome.exe"


def test_senha_fica_no_json_sem_cofre_disponivel(pasta_config_isolada):
    """Sem backend de keyring disponível (Linux headless, etc.) a senha continua indo pro JSON,
    exatamente como antes — é a rede de segurança que evita perder a credencial."""
    salvar_configuracao(_config_exemplo())

    with open(pasta_config_isolada / "config.json", encoding="utf-8") as arq:
        bruto = json.load(arq)
    assert bruto["proxies"][0]["senha"] == "segredo"

    assert carregar_configuracao().proxies[0].senha == "segredo"


def test_senha_vai_para_o_cofre_quando_disponivel(pasta_config_isolada, cofre_falso):
    salvar_configuracao(_config_exemplo())

    with open(pasta_config_isolada / "config.json", encoding="utf-8") as arq:
        bruto = json.load(arq)
    proxy_id = bruto["proxies"][0]["id"]
    assert bruto["proxies"][0]["senha"] != "segredo"  # não fica mais em texto puro no arquivo
    assert cofre_falso.store[("Veredo", proxy_id)] == "segredo"

    carregada = carregar_configuracao()
    assert carregada.proxies[0].senha == "segredo"  # continua utilizável em memória


def test_remover_proxy_apaga_senha_do_cofre(pasta_config_isolada, cofre_falso):
    cfg = _config_exemplo()
    salvar_configuracao(cfg)
    proxy_id = cfg.proxies[0].id
    assert ("Veredo", proxy_id) in cofre_falso.store

    cfg.proxies = []
    salvar_configuracao(cfg)

    assert ("Veredo", proxy_id) not in cofre_falso.store
