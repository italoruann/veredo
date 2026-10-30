"""Importação e exportação do perfil (proxies, cadeias, aplicativos e regras) em arquivo.

O formato é JSON. As preferências locais (modo transparente, logs...) não fazem parte do perfil."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .configuracao import Aplicativo, CadeiaProxy, Configuracao, PerfilProxy

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
    """Lê um perfil do Veredo (.json). Só os campos do perfil vêm
    preenchidos; as preferências ficam no padrão e devem ser mantidas pelo chamador."""
    try:
        texto = Path(caminho).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ErroPerfil(f"não foi possível ler o arquivo: {exc}") from exc
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

