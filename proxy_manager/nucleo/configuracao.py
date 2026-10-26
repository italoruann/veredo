"""Configuração persistida: perfis de proxy, aplicativos, regras e preferências."""
from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from platformdirs import user_config_dir, user_data_dir

try:
    import keyring
except ImportError:  # pragma: no cover
    keyring = None  # type: ignore[assignment]

NOME_EXIBICAO = "Veredo"
NOME_APP = "Veredo"
AUTOR_APP = "Veredo"

# Gravado no lugar da senha quando ela foi guardada no cofre do sistema.
_MARCADOR_COFRE = "\u0000keyring\u0000"
_SERVICO_COFRE = "Veredo"

TEXTO_REGRAS_PADRAO = """\
# Regras avaliadas de cima para baixo; vale a primeira que casar.
#
#   apps: chrome, firefox          limita as regras seguintes a esses aplicativos
#   apps: *                        volta a valer para qualquer aplicativo
#   *.exemplo.com                  sem sufixo: vai pelo proxy padrão
#   *.exemplo.com +direct          conexão direta
#   *.exemplo.com +proxy:Nome      usa um perfil de proxy específico
#   *.exemplo.com +chain:Nome      usa uma cadeia de proxies
#   *.exemplo.com +block           bloqueia
#   *.exemplo.com:443              só na porta 443 (também 80,443 ou 8000-8100)
#   10.0.0.1-10.0.0.50 +direct     faixa de IPs
#   !*.exemplo.com                 regra desativada

*.example.com
"""


def pasta_configuracao() -> Path:
    caminho = Path(user_config_dir(NOME_APP, AUTOR_APP))
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def pasta_dados() -> Path:
    caminho = Path(user_data_dir(NOME_APP, AUTOR_APP))
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def arquivo_configuracao() -> Path:
    return pasta_configuracao() / "config.json"


def _filtrar_campos(classe, dados: dict[str, Any]) -> dict[str, Any]:
    return {nome: dados[nome] for nome in classe.__dataclass_fields__ if nome in dados}


@dataclass
class PerfilProxy:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    nome: str = "Meu Proxy"
    type: str = "socks5"  # "socks5", "socks4", "http" ou "https"
    host: str = ""
    port: int = 1080
    usuario: str = ""
    senha: str = ""
    ativo: bool = True
    padrao: bool = False

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def de_dict(dados: dict[str, Any]) -> PerfilProxy:
        return PerfilProxy(**_filtrar_campos(PerfilProxy, dados))


TIPOS_CADEIA = ("simples", "redundancia", "balanceamento")


@dataclass
class CadeiaProxy:
    """Vários perfis usados juntos.

    simples: um túnel dentro do outro, na ordem (o último proxy é o que acessa o destino).
    redundancia: tenta um perfil por vez, na ordem, até um conectar.
    balanceamento: reveza o perfil inicial entre as conexões e tenta os outros se ele falhar."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    nome: str = "Cadeia"
    tipo: str = "simples"
    proxies: list[str] = field(default_factory=list)  # ids dos perfis, em ordem
    timeout: int = 10  # segundos para desistir de um perfil (redundância e balanceamento)
    ativo: bool = True

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def de_dict(dados: dict[str, Any]) -> CadeiaProxy:
        return CadeiaProxy(**_filtrar_campos(CadeiaProxy, dados))


@dataclass
class Aplicativo:
    """Programa cadastrado pelo usuário para facilitar a escrita das regras."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    nome: str = "Novo aplicativo"
    caminho_executavel: str = ""

    @property
    def nome_processo(self) -> str:
        return os.path.basename(self.caminho_executavel) if self.caminho_executavel else ""

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def de_dict(dados: dict[str, Any]) -> Aplicativo:
        return Aplicativo(**_filtrar_campos(Aplicativo, dados))


MODOS_DNS = ("sistema", "proxy", "personalizado")


@dataclass
class Preferencias:
    porta_socks: int = 58080
    porta_http: int = 58081
    porta_pac: int = 58090
    integracao_sistema_ativa: bool = False
    acao_padrao: str = "direct"  # "direct" ou "block"
    dias_retencao_log: int = 14
    iniciar_minimizado: bool = False
    minimizar_para_bandeja: bool = False
    modo_transparente_ativo: bool = False
    porta_transparente: int = 58095
    aviso_voz_ip_ativo: bool = True
    # DNS no modo transparente (as consultas dos programas são desviadas para o Veredo):
    #   "sistema": não desvia; vale o DNS do sistema.
    #   "proxy": nomes viram IPs falsos e o proxy resolve do lado dele.
    #   "personalizado": as consultas vão aos `dns_servidores`, pelo caminho que as regras mandarem
    #   para o IP de cada servidor (pelo proxy, por TCP, ou direto, por UDP).
    modo_dns: str = "sistema"
    dns_excecoes: str = "localhost; *.local; *.lan; *.home.arpa; *.localdomain"
    # No DNS pelo proxy: os servidores reais do próprio Veredo e das exceções (vazio = os do
    # sistema). No personalizado: os servidores escolhidos (obrigatório).
    dns_servidores: str = ""
    porta_dns: int = 58053
    # Prevenção de vazamento: DNS pelo proxy, consultas DNS que não são de endereço respondidas
    # vazias e todo UDP de saída bloqueado (WebRTC, QUIC).
    prevenir_vazamento: bool = False
    # UDP pelas regras (só no modo transparente): cada fluxo UDP vai direto, é bloqueado ou passa
    # por um proxy SOCKS5, como o TCP. Desligado, só o QUIC é bloqueado.
    udp_pelas_regras: bool = False
    # Interceptação: as conexões liberadas pelas regras têm o HTTP/HTTPS decifrado e registrado.
    interceptacao_ativa: bool = False
    interceptacao_hosts: str = ""  # vazio = todos os hosts
    interceptacao_excecoes: str = ""
    interceptacao_limite_corpo_kb: int = 1024  # por corpo; o resto é repassado sem ser guardado
    interceptacao_verificar_destino: bool = True  # recusar certificados inválidos dos servidores

    @property
    def modo_dns_efetivo(self) -> str:
        """A prevenção de vazamento sempre usa o DNS pelo proxy."""
        if self.prevenir_vazamento:
            return "proxy"
        return self.modo_dns if self.modo_dns in MODOS_DNS else "sistema"

    @property
    def dns_ativo(self) -> bool:
        """As consultas DNS dos programas são desviadas para o servidor DNS local."""
        return self.modo_transparente_ativo and self.modo_dns_efetivo != "sistema"

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def de_dict(dados: dict[str, Any]) -> Preferencias:
        campos = _filtrar_campos(Preferencias, dados)
        if "modo_dns" not in dados and dados.get("dns_pelo_proxy"):
            campos["modo_dns"] = "proxy"  # configuração de antes da escolha do modo
        return Preferencias(**campos)


@dataclass
class Configuracao:
    proxies: list[PerfilProxy] = field(default_factory=list)
    aplicativos: list[Aplicativo] = field(default_factory=list)
    texto_regras: str = TEXTO_REGRAS_PADRAO
    preferencias: Preferencias = field(default_factory=Preferencias)
    cadeias: list[CadeiaProxy] = field(default_factory=list)

    def proxy_padrao(self) -> Optional[PerfilProxy]:
        ativos = [p for p in self.proxies if p.ativo]
        return next((p for p in ativos if p.padrao), ativos[0] if ativos else None)

    def buscar_proxy(self, nome: str) -> Optional[PerfilProxy]:
        return next((p for p in self.proxies if p.nome == nome), None)

    def buscar_cadeia(self, nome: str) -> Optional[CadeiaProxy]:
        return next((c for c in self.cadeias if c.nome == nome), None)

    def perfis_da_cadeia(self, cadeia: CadeiaProxy) -> list[PerfilProxy]:
        """Perfis habilitados da cadeia, na ordem dela; ids de perfis removidos são ignorados."""
        por_id = {p.id: p for p in self.proxies}
        return [por_id[i] for i in cadeia.proxies if i in por_id and por_id[i].ativo]

    def garantir_padrao_unico(self) -> None:
        """Deixa só um perfil marcado como padrão (o primeiro marcado vence)."""
        achou = False
        for p in self.proxies:
            if p.padrao:
                p.padrao = not achou
                achou = True
        if not achou and self.proxies:
            self.proxies[0].padrao = True

    def para_dict(self) -> dict[str, Any]:
        return {
            "proxies": [p.para_dict() for p in self.proxies],
            "aplicativos": [a.para_dict() for a in self.aplicativos],
            "texto_regras": self.texto_regras,
            "preferencias": self.preferencias.para_dict(),
            "cadeias": [c.para_dict() for c in self.cadeias],
        }

    @staticmethod
    def de_dict(dados: dict[str, Any]) -> Configuracao:
        return Configuracao(
            proxies=[PerfilProxy.de_dict(p) for p in dados.get("proxies", [])],
            aplicativos=[Aplicativo.de_dict(a) for a in dados.get("aplicativos", [])],
            texto_regras=dados.get("texto_regras", TEXTO_REGRAS_PADRAO),
            preferencias=Preferencias.de_dict(dados.get("preferencias", {})),
            cadeias=[CadeiaProxy.de_dict(c) for c in dados.get("cadeias", [])],
        )


def _cofre_gravar(id_perfil: str, senha: str) -> bool:
    """Retorna False se não houver cofre disponível; nesse caso a senha fica no JSON."""
    if keyring is None:
        return False
    try:
        keyring.set_password(_SERVICO_COFRE, id_perfil, senha)
        return True
    except Exception:
        return False


def _cofre_ler(id_perfil: str) -> Optional[str]:
    if keyring is None:
        return None
    try:
        return keyring.get_password(_SERVICO_COFRE, id_perfil)
    except Exception:
        return None


def _cofre_apagar(id_perfil: str) -> None:
    if keyring is None:
        return
    try:
        keyring.delete_password(_SERVICO_COFRE, id_perfil)
    except Exception:
        pass


def _ids_proxies_salvos(caminho: Path) -> set[str]:
    try:
        with open(caminho, "r", encoding="utf-8") as arq:
            dados = json.load(arq)
        return {p["id"] for p in dados.get("proxies", []) if p.get("id")}
    except (OSError, json.JSONDecodeError, KeyError):
        return set()


def _caminho_backup(caminho: Path) -> Path:
    return caminho.with_suffix(".json.bak")


def _tentar_carregar(caminho: Path) -> Optional[Configuracao]:
    if not caminho.exists():
        return None
    try:
        with open(caminho, "r", encoding="utf-8") as arq:
            cfg = Configuracao.de_dict(json.load(arq))
    except (json.JSONDecodeError, OSError):
        return None
    for perfil in cfg.proxies:
        if perfil.senha == _MARCADOR_COFRE:
            perfil.senha = _cofre_ler(perfil.id) or ""
    return cfg


def carregar_configuracao() -> Configuracao:
    caminho = arquivo_configuracao()
    cfg = _tentar_carregar(caminho)
    if cfg is not None:
        return cfg

    # Arquivo ausente ou corrompido: tenta o backup antes de voltar ao padrão.
    backup = _tentar_carregar(_caminho_backup(caminho))
    if backup is not None:
        salvar_configuracao(backup)
        return backup

    cfg = Configuracao()
    salvar_configuracao(cfg)
    return cfg


def salvar_configuracao(cfg: Configuracao) -> None:
    caminho = arquivo_configuracao()
    temporario = caminho.with_suffix(".json.tmp")
    ids_removidos = _ids_proxies_salvos(caminho) - {p.id for p in cfg.proxies}

    dados = cfg.para_dict()
    for dados_proxy, perfil in zip(dados["proxies"], cfg.proxies):
        if perfil.senha and _cofre_gravar(perfil.id, perfil.senha):
            dados_proxy["senha"] = _MARCADOR_COFRE

    with open(temporario, "w", encoding="utf-8") as arq:
        json.dump(dados, arq, indent=2, ensure_ascii=False)

    if caminho.exists():
        try:
            shutil.copy2(caminho, _caminho_backup(caminho))
        except OSError:
            pass

    os.replace(temporario, caminho)
    try:
        os.chmod(caminho, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    for id_perfil in ids_removidos:
        _cofre_apagar(id_perfil)
