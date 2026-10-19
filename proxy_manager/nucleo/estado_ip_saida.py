"""Guarda o último IP de saída de cada perfil (anterior e atual) entre uma execução e outra."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .configuracao import pasta_dados

EstadoIpSaida = dict[str, tuple[str, str]]  # id do perfil -> (ip anterior, ip atual)


def _arquivo_estado() -> Path:
    return pasta_dados() / "ip_saida.json"


def carregar_ips_saida() -> EstadoIpSaida:
    caminho = _arquivo_estado()
    if not caminho.exists():
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as arq:
            bruto: dict[str, Any] = json.load(arq)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        id_perfil: (entrada.get("anterior", ""), entrada["atual"])
        for id_perfil, entrada in bruto.items()
        if isinstance(entrada, dict) and entrada.get("atual")
    }


def salvar_ips_saida(estado: EstadoIpSaida) -> None:
    caminho = _arquivo_estado()
    dados = {id_perfil: {"anterior": anterior, "atual": atual}
             for id_perfil, (anterior, atual) in estado.items()}
    temporario = caminho.with_suffix(".json.tmp")
    try:
        with open(temporario, "w", encoding="utf-8") as arq:
            json.dump(dados, arq, indent=2, ensure_ascii=False)
        temporario.replace(caminho)
    except OSError:
        pass
