"""Importação e exportação do perfil (proxies, cadeias, aplicativos e regras) em arquivo.

O formato próprio é JSON. Também lê o .ppx do Proxifier (XML), para facilitar a migração.
As preferências locais (portas, modo transparente...) não fazem parte do perfil."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .configuracao import Aplicativo, CadeiaProxy, Configuracao, PerfilProxy
from .regras import ConjuntoRegras, Regra, dividir_alvo

FORMATO = "veredo-perfil"
VERSAO = 1


class ErroPerfil(Exception):
    pass


def exportar(cfg: Configuracao, caminho: str | Path, incluir_senhas: bool) -> None:
    dados: dict[str, Any] = {
        "formato": FORMATO,
        "versao": VERSAO,
        "proxies": [p.para_dict() for p in cfg.proxies],
        "cadeias": [c.para_dict() for c in cfg.cadeias],
        "aplicativos": [a.para_dict() for a in cfg.aplicativos],
        "texto_regras": cfg.texto_regras,
    }
    if not incluir_senhas:
        for proxy in dados["proxies"]:
            proxy["senha"] = ""
    with open(caminho, "w", encoding="utf-8") as arq:
        json.dump(dados, arq, indent=2, ensure_ascii=False)


def importar(caminho: str | Path) -> Configuracao:
    """Lê um perfil do Veredo (.json) ou do Proxifier (.ppx). Só os campos do perfil vêm
    preenchidos; as preferências ficam no padrão e devem ser mantidas pelo chamador."""
    try:
        texto = Path(caminho).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ErroPerfil(f"não foi possível ler o arquivo: {exc}") from exc
    if texto.lstrip().startswith("<"):
        return importar_ppx(texto)
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ErroPerfil(f"arquivo inválido: {exc}") from exc
    if not isinstance(dados, dict) or dados.get("formato") != FORMATO:
        raise ErroPerfil("o arquivo não é um perfil do Veredo")
    return Configuracao(
        proxies=[PerfilProxy.de_dict(p) for p in dados.get("proxies", [])],
        cadeias=[CadeiaProxy.de_dict(c) for c in dados.get("cadeias", [])],
        aplicativos=[Aplicativo.de_dict(a) for a in dados.get("aplicativos", [])],
        texto_regras=dados.get("texto_regras", ""),
    )


# -- Proxifier (.ppx) ---------------------------------------------------------------

# No Proxifier, "HTTPS" é um proxy HTTP que aceita CONNECT, não um proxy com TLS.
_TIPOS_PPX = {"SOCKS5": "socks5", "SOCKS4": "socks4", "HTTP": "http", "HTTPS": "http"}
_CADEIAS_PPX = {"simple": "simples", "redundancy": "redundancia", "load_balancing": "balanceamento"}


def _texto(elemento: ET.Element | None, caminho: str) -> str:
    if elemento is None:
        return ""
    achado = elemento.find(caminho)
    return (achado.text or "").strip() if achado is not None else ""


def _lista_ppx(texto: str) -> list[str]:
    """Os campos do Proxifier separam itens por ";"; aplicativos podem vir entre aspas."""
    itens = []
    for item in texto.replace("\n", ";").split(";"):
        item = item.strip().strip('"').strip()
        if item:
            itens.append(item)
    return itens


def importar_ppx(texto: str) -> Configuracao:
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError as exc:
        raise ErroPerfil(f"XML inválido: {exc}") from exc
    if raiz.tag != "ProxifierProfile":
        raise ErroPerfil("o arquivo não é um perfil do Proxifier")

    proxies: list[PerfilProxy] = []
    nomes_proxy: dict[str, str] = {}
    for elemento in raiz.iterfind("ProxyList/Proxy"):
        endereco = _texto(elemento, "Address")
        porta = int(_texto(elemento, "Port") or 0)
        tipo = _TIPOS_PPX.get(elemento.get("type", "").upper())
        if not endereco or not porta or tipo is None:
            continue
        # O Proxifier não dá nome aos proxies; o nome não pode ter espaços (vai nas regras).
        nome = elemento.get("label") or f"{endereco}:{porta}"
        nome = nome.replace(" ", "_")
        auth = elemento.find("Authentication")
        com_auth = auth is not None and auth.get("enabled", "false").lower() == "true"
        perfil = PerfilProxy(nome=nome, type=tipo, host=endereco, port=porta,
                             usuario=_texto(auth, "Username") if com_auth else "",
                             senha=_texto(auth, "Password") if com_auth else "")
        proxies.append(perfil)
        nomes_proxy[elemento.get("id", "")] = perfil.nome
    if proxies:
        proxies[0].padrao = True

    cadeias: list[CadeiaProxy] = []
    nomes_cadeia: dict[str, str] = {}
    ids_por_nome = {p.nome: p.id for p in proxies}
    for elemento in raiz.iterfind("ChainList/Chain"):
        nome = (_texto(elemento, "Name") or f"Cadeia{len(cadeias) + 1}").replace(" ", "_")
        membros = [ids_por_nome[nomes_proxy[p.text.strip()]] for p in elemento.iterfind("Proxy")
                   if p.text and p.text.strip() in nomes_proxy
                   and p.get("enabled", "true").lower() == "true"]
        cadeia = CadeiaProxy(nome=nome, tipo=_CADEIAS_PPX.get(elemento.get("type", "simple"), "simples"),
                             proxies=membros)
        cadeias.append(cadeia)
        nomes_cadeia[elemento.get("id", "")] = cadeia.nome

    regras: list[Regra] = []
    for elemento in raiz.iterfind("RuleList/Rule"):
        ativo = elemento.get("enabled", "true").lower() == "true"
        acao_xml = elemento.find("Action")
        tipo_acao = (acao_xml.get("type", "") if acao_xml is not None else "").lower()
        ref = (acao_xml.text or "").strip() if acao_xml is not None else ""
        if tipo_acao == "direct":
            acao = "direct"
        elif tipo_acao == "block":
            acao = "block"
        elif tipo_acao == "chain" and ref in nomes_cadeia:
            acao = f"chain:{nomes_cadeia[ref]}"
        elif tipo_acao == "proxy" and ref in nomes_proxy:
            acao = f"proxy:{nomes_proxy[ref]}"
        else:
            acao = "proxy"
        aplicativos = _lista_ppx(_texto(elemento, "Applications"))
        portas = ",".join(p.replace(" ", "") for p in _lista_ppx(_texto(elemento, "Ports")))
        alvos = [a for a in _lista_ppx(_texto(elemento, "Targets")) if "%" not in a] or ["*"]
        for alvo in alvos:
            alvo, portas_alvo = dividir_alvo(alvo)
            regras.append(Regra(aplicativos=aplicativos, alvo=alvo, portas=portas_alvo or portas,
                                acao=acao, ativo=ativo))

    return Configuracao(proxies=proxies, cadeias=cadeias, aplicativos=[],
                        texto_regras=ConjuntoRegras(regras).para_texto())
