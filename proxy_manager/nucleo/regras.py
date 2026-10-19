"""Regras de roteamento: leitura do formato texto e avaliação de cada conexão."""
from __future__ import annotations

import fnmatch
import ipaddress
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Regra:
    aplicativos: list[str] = field(default_factory=list)  # vazio = qualquer aplicativo
    alvo: str = ""
    acao: str = "proxy"  # "direct", "block", "proxy" ou "proxy:<nome>"
    ativo: bool = True
    linha: int = 0
    linha_original: str = ""

    @property
    def tipo_acao(self) -> str:
        return self.acao if self.acao in ("direct", "block") else "proxy"

    @property
    def nome_proxy(self) -> Optional[str]:
        if self.acao.startswith("proxy:"):
            return self.acao.split(":", 1)[1]
        return None


def _normalizar_app(nome: str) -> str:
    return os.path.basename(nome.strip()).lower()


# No Linux, o comando que o usuário escolhe nem sempre é o processo que abre as conexões
# (/usr/bin/google-chrome é um script que executa /opt/google/chrome/chrome, por exemplo).
_APELIDOS_LANCADORES: dict[str, frozenset[str]] = {
    nome: frozenset(processos)
    for nomes, processos in (
        (("google-chrome", "google-chrome-stable", "google-chrome-beta", "google-chrome-unstable",
          "chromium", "chromium-browser"), ("chrome", "chromium", "chromium-browser")),
        (("microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta", "microsoft-edge-dev"),
         ("msedge",)),
        (("brave-browser", "brave-browser-stable", "brave"), ("brave",)),
        (("vivaldi", "vivaldi-stable"), ("vivaldi-bin",)),
        (("opera",), ("opera",)),
        (("firefox", "firefox-esr"), ("firefox", "firefox-bin", "firefox-esr")),
    )
    for nome in nomes
}


def _nomes_processo(app: str) -> set[str]:
    nome = _normalizar_app(app)
    return {nome, os.path.splitext(nome)[0]} | _APELIDOS_LANCADORES.get(nome, frozenset())


def app_corresponde(apps_regra: list[str], nome_app: str, caminho_app: str) -> bool:
    if not apps_regra:
        return True
    candidatos = {_normalizar_app(nome_app)}
    if caminho_app:
        candidatos.add(_normalizar_app(caminho_app))
        candidatos.add(_normalizar_app(os.path.splitext(caminho_app)[0]))
    if nome_app:
        candidatos.add(_normalizar_app(os.path.splitext(nome_app)[0]))
    return any(_nomes_processo(app) & candidatos for app in apps_regra)


def _ip_corresponde(alvo: str, ip: str) -> bool:
    try:
        if "/" in alvo:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(alvo, strict=False)
        return ipaddress.ip_address(alvo) == ipaddress.ip_address(ip)
    except ValueError:
        return False


def padrao_corresponde(alvo: str, host: Optional[str], ip: Optional[str]) -> bool:
    alvo = alvo.strip()
    if not alvo:
        return False
    if ip and _ip_corresponde(alvo, ip):
        return True
    if not host:
        return False

    host = host.lower().rstrip(".")
    alvo = alvo.lower()
    if alvo.startswith("*."):
        dominio = alvo[2:]
        return host == dominio or host.endswith("." + dominio)
    if "*" in alvo or "?" in alvo:
        return fnmatch.fnmatch(host, alvo)
    return host == alvo


@dataclass
class Resultado:
    tipo_acao: str  # "direct", "block" ou "proxy"
    nome_proxy: Optional[str]
    regra: Optional[Regra]


class ConjuntoRegras:
    def __init__(self, regras: Optional[list[Regra]] = None):
        self.regras: list[Regra] = regras or []

    def avaliar(self, nome_app: str, caminho_app: str, host: Optional[str], ip: Optional[str],
                acao_padrao: str = "direct") -> Resultado:
        for regra in self.regras:
            if (regra.ativo and app_corresponde(regra.aplicativos, nome_app, caminho_app)
                and padrao_corresponde(regra.alvo, host, ip)):
                return Resultado(tipo_acao=regra.tipo_acao, nome_proxy=regra.nome_proxy, regra=regra)
        if acao_padrao not in ("direct", "block"):
            acao_padrao = "direct"
        return Resultado(tipo_acao=acao_padrao, nome_proxy=None, regra=None)

    @staticmethod
    def interpretar(texto: str) -> ConjuntoRegras:
        regras: list[Regra] = []
        apps_atuais: list[str] = []
        # Linhas "apps:" consecutivas se somam; uma "apps:" depois de uma regra começa outro bloco.
        lendo_apps = False
        for numero, linha_original in enumerate(texto.splitlines(), start=1):
            linha = linha_original.strip()
            if not linha or linha.startswith("#"):
                continue
            if linha.lower().startswith("apps:"):
                valor = linha.split(":", 1)[1].strip()
                if valor in ("", "*"):
                    apps_atuais = []
                else:
                    novos = [v.strip() for v in valor.split(",") if v.strip()]
                    base = apps_atuais if lendo_apps else []
                    apps_atuais = base + [a for a in novos if a not in base]
                lendo_apps = True
                continue
            lendo_apps = False

            partes = linha.split()
            alvo = partes[0]
            ativo = not alvo.startswith("!")
            if not ativo:
                alvo = alvo[1:]
            acao = "proxy"
            for extra in partes[1:]:
                if extra.startswith("+"):
                    acao = extra[1:]
            regras.append(Regra(aplicativos=list(apps_atuais), alvo=alvo, acao=acao,
                                ativo=ativo, linha=numero, linha_original=linha_original))
        return ConjuntoRegras(regras)

    def para_texto(self) -> str:
        linhas: list[str] = []
        apps_anteriores: list[str] = []
        for regra in self.regras:
            if regra.aplicativos != apps_anteriores:
                linhas.append(f"apps: {', '.join(regra.aplicativos)}" if regra.aplicativos else "apps: *")
                apps_anteriores = list(regra.aplicativos)
            prefixo = "" if regra.ativo else "!"
            sufixo = "" if regra.acao == "proxy" else f" +{regra.acao}"
            linhas.append(f"{prefixo}{regra.alvo}{sufixo}")
        return "\n".join(linhas) + ("\n" if linhas else "")
