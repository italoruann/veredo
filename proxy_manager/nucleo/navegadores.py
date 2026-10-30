"""Configura os navegadores do Linux para usar o PAC, independente do ambiente gráfico.

- Firefox e derivados: um bloco no user.js de cada perfil, com os valores anteriores guardados
  para restaurar depois.
- Navegadores baseados em Chromium: uma cópia do atalho .desktop em ~/.local/share/applications
  com --proxy-pac-url. A cópia continua quando o motor para (sem o PAC o navegador conecta
  direto) e só é apagada ao desativar a integração, com o navegador fechado.

Quando o proxy do sistema (gsettings/kioslaverc) foi gravado e o navegador o lê, ele é usado no
lugar do PAC fixo: o Firefox (modo "usar o proxy do sistema") e o Chromium (no GNOME/KDE) observam
essa configuração e trocam de proxy com o navegador aberto. O PAC fixo (user.js, --proxy-pac-url)
só é lido ao abrir o navegador, e o Chromium que o abriu com o motor parado só tenta baixá-lo de
novo horas depois.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import integracao_sistema as si

_ERROS = (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError)


# Relativos à HOME. Firefox 147+ cria perfis novos em ~/.config/mozilla/firefox (XDG).
_RAIZES_FIREFOX = (
    ".mozilla/firefox",
    ".config/mozilla/firefox",
    ".var/app/org.mozilla.firefox/.mozilla/firefox",
    ".var/app/org.mozilla.firefox/config/mozilla/firefox",
    "snap/firefox/common/.mozilla/firefox",
    ".librewolf",
    ".var/app/io.gitlab.librewolf-community/.librewolf",
    ".waterfox",
    ".floorp",
    ".var/app/one.ablaze.floorp/.floorp",
    ".zen",
    ".var/app/app.zen_browser.zen/.zen",
)

_INICIO_BLOCO = "// >>> Veredo (gerado automaticamente, não edite este bloco)"
_FIM_BLOCO = "// <<< Veredo"
_PREFIXO_ORIGINAL = "// original: "
_PREFS_FIREFOX = ("network.proxy.type", "network.proxy.autoconfig_url")
_PADROES_FIREFOX = {"network.proxy.type": "5", "network.proxy.autoconfig_url": '""'}
_PREF_RE = re.compile(r'^\s*user_pref\("([^"]+)",\s*(.*?)\);\s*$')


def perfis_firefox() -> list[Path]:
    home = si._home_usuario()
    perfis: list[Path] = []
    for raiz in _RAIZES_FIREFOX:
        base = home / raiz
        if not base.is_dir():
            continue
        perfis.extend(p for p in sorted(base.iterdir()) if (p / "prefs.js").is_file())
    return perfis


def aplicar_firefox(perfil: Path, url: str, pelo_sistema: bool = False) -> None:
    user_js = perfil / "user.js"
    bloco, resto = _separar_bloco(si._ler_arquivo_usuario(user_js))
    if bloco is not None:
        originais = _originais_do_bloco(bloco)
    else:
        atual = _ler_prefs(si._ler_arquivo_usuario(perfil / "prefs.js"))
        originais = {nome: atual.get(nome) for nome in _PREFS_FIREFOX}
    if pelo_sistema:
        prefs = {"network.proxy.type": "5"}
    else:
        prefs = {"network.proxy.type": "2", "network.proxy.autoconfig_url": json.dumps(url)}
    si._gravar_arquivo_usuario(user_js, _juntar(resto, _bloco_firefox(prefs, originais)))


def remover_firefox(perfil: Path) -> bool:
    user_js = perfil / "user.js"
    bloco, resto = _separar_bloco(si._ler_arquivo_usuario(user_js))
    if bloco is None:
        return False
    originais = _originais_do_bloco(bloco)
    if _firefox_aberto(perfil):
        # Ao fechar, o Firefox regrava o prefs.js; o user.js com os valores originais vence isso.
        restaurar = {nome: valor if valor is not None else _PADROES_FIREFOX[nome]
                     for nome, valor in originais.items()}
        si._gravar_arquivo_usuario(user_js, _juntar(resto, _bloco_firefox(restaurar, originais)))
        return True
    if resto.strip():
        si._gravar_arquivo_usuario(user_js, resto.rstrip("\n") + "\n")
    else:
        si._apagar_arquivo_usuario(user_js)
    prefs_js = perfil / "prefs.js"
    si._gravar_arquivo_usuario(prefs_js, _definir_prefs(si._ler_arquivo_usuario(prefs_js), originais))
    return True


def _firefox_aberto(perfil: Path) -> bool:
    # O symlink "lock" só existe enquanto o Firefox está com o perfil aberto.
    return os.path.lexists(perfil / "lock")


def _separar_bloco(texto: str) -> tuple[list[str] | None, str]:
    linhas = texto.splitlines()
    inicio = next((i for i, linha in enumerate(linhas) if linha.startswith(_INICIO_BLOCO[:10])), None)
    if inicio is None:
        return None, texto
    fim = next((i for i in range(inicio, len(linhas)) if linhas[i].startswith(_FIM_BLOCO)), len(linhas) - 1)
    resto = linhas[:inicio] + linhas[fim + 1:]
    return linhas[inicio:fim + 1], "\n".join(resto) + ("\n" if resto else "")


def _originais_do_bloco(bloco: list[str]) -> dict[str, str | None]:
    for linha in bloco:
        if linha.startswith(_PREFIXO_ORIGINAL):
            try:
                salvos = json.loads(linha[len(_PREFIXO_ORIGINAL):])
                return {nome: salvos.get(nome) for nome in _PREFS_FIREFOX}
            except (ValueError, AttributeError):
                break
    return {nome: None for nome in _PREFS_FIREFOX}


def _bloco_firefox(prefs: dict[str, str], originais: dict[str, str | None]) -> str:
    linhas = [_INICIO_BLOCO, _PREFIXO_ORIGINAL + json.dumps(originais)]
    linhas += [f'user_pref("{nome}", {valor});' for nome, valor in prefs.items()]
    linhas.append(_FIM_BLOCO)
    return "\n".join(linhas)


def _juntar(resto: str, bloco: str) -> str:
    resto = resto.rstrip("\n")
    return (resto + "\n" if resto else "") + bloco + "\n"


def _ler_prefs(texto: str) -> dict[str, str]:
    prefs: dict[str, str] = {}
    for linha in texto.splitlines():
        resultado = _PREF_RE.match(linha)
        if resultado:
            prefs[resultado.group(1)] = resultado.group(2)
    return prefs


def _definir_prefs(texto: str, valores: dict[str, str | None]) -> str:
    """Troca/remove as prefs gerenciadas no prefs.js (None = volta ao padrão do Firefox)."""
    mantidas = [linha for linha in texto.splitlines()
                if not ((m := _PREF_RE.match(linha)) and m.group(1) in valores)]
    mantidas += [f'user_pref("{nome}", {valor});' for nome, valor in valores.items() if valor is not None]
    return "\n".join(mantidas) + "\n"



_ATALHOS_CHROMIUM = (
    "google-chrome.desktop", "google-chrome-beta.desktop", "google-chrome-unstable.desktop",
    "com.google.Chrome.desktop", "com.google.ChromeDev.desktop",
    "chromium.desktop", "chromium-browser.desktop", "org.chromium.Chromium.desktop",
    "chromium_chromium.desktop", "io.github.ungoogled_software.ungoogled_chromium.desktop",
    "microsoft-edge.desktop", "microsoft-edge-beta.desktop", "microsoft-edge-dev.desktop",
    "com.microsoft.Edge.desktop",
    "brave-browser.desktop", "brave-browser-beta.desktop", "brave-browser-nightly.desktop",
    "com.brave.Browser.desktop", "brave_brave.desktop",
    "vivaldi-stable.desktop", "vivaldi-snapshot.desktop", "com.vivaldi.Vivaldi.desktop",
    "opera.desktop", "com.opera.Opera.desktop", "opera_opera.desktop",
)
_PASTAS_APPS_EXTRAS = (Path("/var/lib/flatpak/exports/share/applications"),
                       Path("/var/lib/snapd/desktop/applications"))
_MARCA_ATALHO = "X-Veredo-Override=true"
# Primeiro código de campo (%U, %u, %F, %f) ou marcador de repasse de arquivos do Flatpak (@@u, @@):
# a flag entra antes dele, junto dos argumentos do navegador.
_CODIGO_CAMPO_RE = re.compile(r"\s(?:%[uUfF]|@@u?)(?=\s|$)")


def _pasta_apps_usuario() -> Path:
    return si._home_usuario() / ".local" / "share" / "applications"


def _pastas_apps_sistema() -> list[Path]:
    home = si._home_usuario()
    pastas_dados = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    pastas = [home / ".local/share/flatpak/exports/share/applications",
              *(Path(d) / "applications" for d in pastas_dados.split(":") if d),
              *_PASTAS_APPS_EXTRAS]
    pasta_usuario = _pasta_apps_usuario()
    return [d for d in dict.fromkeys(pastas) if d != pasta_usuario]


def com_flag_pac(atalho: str, url: str) -> str:
    flag = f"--proxy-pac-url={url}"
    saida: list[str] = []
    for linha in atalho.splitlines():
        if linha.startswith("Exec="):
            resultado = _CODIGO_CAMPO_RE.search(linha)
            linha = f"{linha[:resultado.start()]} {flag}{linha[resultado.start():]}" if resultado else f"{linha} {flag}"
        elif linha.startswith("DBusActivatable="):
            linha = "DBusActivatable=false"  # senão o menu ignora o Exec= e a flag
        saida.append(linha)
        if linha.strip() == "[Desktop Entry]":
            saida.append(_MARCA_ATALHO)
    return "\n".join(saida) + "\n"


def _nome_atalho(atalho: str) -> str | None:
    return next((linha[5:].strip() for linha in atalho.splitlines() if linha.startswith("Name=")), None)


# Nomes dos processos dos navegadores acima (nativos, Flatpak e Snap rodam com esses nomes).
_PROCESSOS_CHROMIUM = frozenset({"chrome", "chromium", "chromium-browser", "msedge", "brave",
                                 "vivaldi-bin", "opera"})


def _chromium_aberto() -> bool:
    """Trocar o .desktop com o navegador aberto faz as janelas sumirem do painel do GNOME/KDE."""
    try:
        import psutil
        return any((proc.info.get("name") or "").lower() in _PROCESSOS_CHROMIUM
                   for proc in psutil.process_iter(["name"]))
    except Exception:
        return False


def _atualizar_base_atalhos() -> None:
    if not si._tem_binario("update-desktop-database"):
        return
    try:
        si._executar_como_usuario(["update-desktop-database", str(_pasta_apps_usuario())])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass



@dataclass
class ResultadoNavegadores:
    perfis_firefox: int = 0
    chromium: list[str] = field(default_factory=list)
    mensagens: list[str] = field(default_factory=list)

    @property
    def configurou_algum(self) -> bool:
        return bool(self.perfis_firefox or self.chromium)


def aplicar(url: str, firefox_pelo_sistema: bool = False, chromium_pelo_sistema: bool = False) -> ResultadoNavegadores:
    """Com `*_pelo_sistema` o navegador segue o proxy do sistema já gravado (veja o início do módulo)."""
    retorno = ResultadoNavegadores()

    for perfil in perfis_firefox():
        try:
            aplicar_firefox(perfil, url, firefox_pelo_sistema)
            retorno.perfis_firefox += 1
        except _ERROS as exc:
            retorno.mensagens.append(f"Firefox ({perfil}): falhou: {si._erro_comando(exc)}")
    if retorno.perfis_firefox and firefox_pelo_sistema:
        retorno.mensagens.append(
            f"Firefox: {retorno.perfis_firefox} perfil(is) seguindo o proxy do sistema; aberto, ele troca "
            f"sozinho quando o motor liga (se estava com outro proxy configurado, feche e abra uma vez).")
    elif retorno.perfis_firefox:
        retorno.mensagens.append(
            f"Firefox: {retorno.perfis_firefox} perfil(is) configurado(s). Feche e abra o Firefox para valer.")

    if chromium_pelo_sistema:
        # A flag faria o navegador ignorar o proxy do sistema; atalhos de versões anteriores saem.
        retorno.mensagens.extend(_remover_atalhos())
        return retorno

    pasta_usuario = _pasta_apps_usuario()
    pastas_sistema = _pastas_apps_sistema()
    mudou = False
    for nome in _ATALHOS_CHROMIUM:
        origem = next((d / nome for d in pastas_sistema if (d / nome).is_file()), None)
        if origem is None:
            continue
        alvo = pasta_usuario / nome
        try:
            existente = si._ler_arquivo_usuario(alvo)
            if existente and _MARCA_ATALHO not in existente:
                retorno.mensagens.append(
                    f"{alvo} é um atalho seu e não foi alterado; adicione --proxy-pac-url={url} na linha Exec= dele.")
                continue
            entrada = si._ler_arquivo_usuario(origem)
            desejado = com_flag_pac(entrada, url)
            # Regravar sem necessidade faz o painel do GNOME/KDE perder as janelas abertas.
            if existente != desejado:
                si._gravar_arquivo_usuario(alvo, desejado)
                mudou = True
            retorno.chromium.append(_nome_atalho(entrada) or nome)
        except _ERROS as exc:
            retorno.mensagens.append(f"{nome}: falhou: {si._erro_comando(exc)}")
    if mudou:
        _atualizar_base_atalhos()
    if retorno.chromium:
        retorno.mensagens.append(
            f"{', '.join(retorno.chromium)}: atalho do menu ajustado com --proxy-pac-url. Feche TODAS as "
            f"janelas (e o ícone da bandeja, se houver) e abra de novo pelo menu.")
    return retorno


def remover(manter_atalhos: bool = False) -> list[str]:
    """Com `manter_atalhos=True` (motor parando) só o Firefox é restaurado."""
    mensagens: list[str] = []
    restaurados = 0
    for perfil in perfis_firefox():
        try:
            restaurados += remover_firefox(perfil)
        except _ERROS as exc:
            mensagens.append(f"Firefox ({perfil}): falhou ao restaurar: {si._erro_comando(exc)}")
    if restaurados:
        mensagens.append(f"Firefox: proxy restaurado em {restaurados} perfil(is).")

    if manter_atalhos:
        return mensagens
    return mensagens + _remover_atalhos()


def _remover_atalhos() -> list[str]:
    mensagens: list[str] = []
    removidos: list[str] = []
    pasta_usuario = _pasta_apps_usuario()
    atalhos: list[tuple[Path, str]] = []
    for nome in _ATALHOS_CHROMIUM:
        alvo = pasta_usuario / nome
        try:
            entrada = si._ler_arquivo_usuario(alvo)
        except _ERROS as exc:
            mensagens.append(f"{nome}: falhou ao restaurar: {si._erro_comando(exc)}")
            continue
        if _MARCA_ATALHO in entrada:
            atalhos.append((alvo, entrada))
    if atalhos and _chromium_aberto():
        mensagens.append("Navegador aberto: o atalho ajustado do menu será restaurado quando você "
                         "fechá-lo e ligar o motor ou remover a integração de novo (sem o motor, ele "
                         "conecta direto).")
        atalhos = []
    for alvo, entrada in atalhos:
        try:
            si._apagar_arquivo_usuario(alvo)
            removidos.append(_nome_atalho(entrada) or alvo.name)
        except _ERROS as exc:
            mensagens.append(f"{alvo.name}: falhou ao restaurar: {si._erro_comando(exc)}")
    if removidos:
        _atualizar_base_atalhos()
        mensagens.append(f"{', '.join(removidos)}: atalho original do menu restaurado.")
    return mensagens
