"""Proxy do sistema: gera e serve o arquivo PAC e o aplica/remove no Windows e no Linux."""
from __future__ import annotations

import asyncio
import os
import platform
import subprocess
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .configuracao import NOME_EXIBICAO, pasta_dados
from .teste_vazamento import CAMINHO_TESTE

FornecedorPac = Callable[[], str]
FornecedorPagina = Callable[[], Awaitable[str]]
FornecedorCertificado = Callable[[], bytes]

# Certificado da CA da interceptação, para instalar em navegadores e outros aparelhos.
CAMINHO_CERTIFICADO_CA = "/veredo-ca.crt"


def gerar_script_pac(porta_socks: int, porta_http: int) -> str:
    """O PAC manda tudo para o proxy local; quem decide o destino são as regras. Assim toda
    conexão aparece no log, inclusive as diretas. Só o loopback vai direto: o motor recusaria
    como loop o acesso às portas do próprio Veredo (a página de teste, por exemplo)."""
    return f"""\
function FindProxyForURL(url, host) {{
    if (host === "localhost" || host === "::1" || host === "[::1]" || shExpMatch(host, "127.*")) {{
        return "DIRECT";
    }}
    return "PROXY 127.0.0.1:{porta_http}; SOCKS5 127.0.0.1:{porta_socks}; DIRECT";
}}
"""


async def iniciar_servidor_pac(fornecedor_pac: FornecedorPac, porta: int,
                               fornecedor_teste: Optional[FornecedorPagina] = None,
                               fornecedor_ca: Optional[FornecedorCertificado] = None) -> asyncio.AbstractServer:
    """Serve o PAC em qualquer caminho, em `CAMINHO_TESTE` a página de teste de vazamento e em
    `CAMINHO_CERTIFICADO_CA` o certificado da CA da interceptação."""
    async def atender(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            cabecalho = await asyncio.wait_for(leitor.readuntil(b"\r\n\r\n"), timeout=5)
        except Exception:
            escritor.close()
            return
        partes = cabecalho.split(b"\r\n", 1)[0].split()
        caminho = partes[1].decode("latin-1").split("?", 1)[0] if len(partes) > 1 else ""
        if fornecedor_teste is not None and caminho == CAMINHO_TESTE:
            try:
                corpo = (await fornecedor_teste()).encode("utf-8")
            except Exception:
                escritor.close()
                return
            tipo = b"text/html; charset=utf-8\r\nCache-Control: no-store"
        elif fornecedor_ca is not None and caminho == CAMINHO_CERTIFICADO_CA:
            try:
                corpo = await asyncio.to_thread(fornecedor_ca)
            except Exception:
                escritor.close()
                return
            tipo = b"application/x-x509-ca-cert\r\nContent-Disposition: attachment; filename=veredo-ca.crt"
        else:
            corpo = fornecedor_pac().encode("utf-8")
            tipo = b"application/x-ns-proxy-autoconfig"
        resposta = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: " + tipo + b"\r\n"
            b"Content-Length: " + str(len(corpo)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + corpo
        )
        try:
            escritor.write(resposta)
            await escritor.drain()
        finally:
            escritor.close()

    return await asyncio.start_server(atender, "127.0.0.1", porta)


def url_pac(porta_pac: int) -> str:
    return f"http://127.0.0.1:{porta_pac}/proxy.pac"


def aplicar_proxy_sistema(url: str, porta_http: int) -> tuple[bool, str]:
    sistema = platform.system()
    if sistema == "Windows":
        return _aplicar_windows(url)
    if sistema == "Linux":
        return _aplicar_linux(url, porta_http)
    return False, f"Sistema operacional não suportado para integração automática: {sistema}"


def remover_proxy_sistema(manter_atalhos: bool = False) -> tuple[bool, str]:
    """`manter_atalhos=True` quando só o motor está parando: no Linux os atalhos ajustados do
    Chromium continuam (veja `navegadores.remover`)."""
    sistema = platform.system()
    if sistema == "Windows":
        return _remover_windows()
    if sistema == "Linux":
        return _remover_linux(manter_atalhos)
    return False, f"Sistema operacional não suportado para integração automática: {sistema}"


def _aplicar_windows(url: str) -> tuple[bool, str]:
    try:
        import winreg
        caminho_chave = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho_chave, 0, winreg.KEY_SET_VALUE) as chave:
            winreg.SetValueEx(chave, "AutoConfigURL", 0, winreg.REG_SZ, url)
        _avisar_windows()
        return True, "Proxy automático (PAC) configurado no Windows."
    except OSError as exc:
        return False, f"Falha ao configurar o registro do Windows: {exc}"


def _remover_windows() -> tuple[bool, str]:
    try:
        import winreg
        caminho_chave = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho_chave, 0, winreg.KEY_SET_VALUE) as chave:
            try:
                winreg.DeleteValue(chave, "AutoConfigURL")
            except FileNotFoundError:
                pass
        _avisar_windows()
        return True, "Configuração de proxy do Windows removida."
    except OSError as exc:
        return False, f"Falha ao remover configuração do Windows: {exc}"


def _avisar_windows() -> None:
    try:
        import ctypes
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        wininet = ctypes.windll.Wininet  # type: ignore[attr-defined]
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass


# Plasma 6 (Arch, Fedora 40+, ...) só traz o kwriteconfig6; Plasma 5 só o kwriteconfig5.
_BINARIOS_KWRITECONFIG = ("kwriteconfig6", "kwriteconfig5")


def _aplicar_linux(url: str, porta_http: int) -> tuple[bool, str]:
    """Cada ambiente gráfico guarda o proxy num lugar e cada navegador lê de outro. Por isso os
    navegadores são configurados diretamente (user.js do Firefox, atalhos do Chromium) e o proxy
    do sistema fica como complemento para os demais programas."""
    from . import navegadores

    mensagens: list[str] = []
    configurados = navegadores.aplicar(url)
    mensagens.extend(configurados.mensagens)
    algum_ok = configurados.configurou_algum

    if _tem_binario("gsettings"):
        try:
            _executar_como_usuario(["gsettings", "set", "org.gnome.system.proxy", "mode", "auto"])
            _executar_como_usuario(["gsettings", "set", "org.gnome.system.proxy", "autoconfig-url", url])
            if _gsettings_gravado(url):
                mensagens.append("GNOME/gsettings configurado.")
                algum_ok = True
            else:
                mensagens.append(_GSETTINGS_NAO_GRAVADO)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            mensagens.append(f"gsettings falhou: {_erro_comando(exc)} {_GSETTINGS_NAO_GRAVADO}")

    if _kde_presente():
        try:
            _definir_proxy_kde({"ProxyType": "2", "Proxy Config Script": url})
            mensagens.append("KDE/kioslaverc configurado.")
            algum_ok = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
            mensagens.append(f"KDE/kioslaverc falhou: {_erro_comando(exc)}")

    caminho_env = _gravar_script_env(porta_http, url)
    mensagens.append(f"Script de variáveis de ambiente gerado em {caminho_env} (use 'source' para apps de terminal).")

    ambiente = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if not configurados.chromium and ambiente and not _chromium_le_proxy_sistema(ambiente):
        mensagens.append(
            f"Atenção: no ambiente {ambiente}, Chrome/Chromium/Edge/Brave ignoram o proxy do sistema e "
            f"nenhum atalho deles foi encontrado para ajustar. Abra o navegador com --proxy-pac-url={url}."
        )
    if not algum_ok:
        mensagens.append(
            "Nenhum navegador ou ambiente gráfico foi configurado automaticamente; "
            f"configure manualmente a URL do PAC ({url}) no navegador."
        )
    return algum_ok, " ".join(mensagens)


def _remover_linux(manter_atalhos: bool = False) -> tuple[bool, str]:
    from . import navegadores

    mensagens: list[str] = list(navegadores.remover(manter_atalhos=manter_atalhos))
    if _tem_binario("gsettings"):
        try:
            _executar_como_usuario(["gsettings", "set", "org.gnome.system.proxy", "mode", "none"])
            mensagens.append("GNOME/gsettings revertido para 'sem proxy'.")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            mensagens.append(f"gsettings falhou: {exc}")
    if _kde_presente():
        try:
            _definir_proxy_kde({"ProxyType": "0"})
            mensagens.append("KDE/kioslaverc revertido.")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
            mensagens.append(f"KDE/kioslaverc falhou: {_erro_comando(exc)}")
    return True, " ".join(mensagens) if mensagens else "Nenhuma alteração de sistema para reverter."


# Sem o schema org.gnome.system.proxy ou sem o dconf, o gsettings usa um backend em memória:
# o comando funciona, mas nada é gravado.
_GSETTINGS_NAO_GRAVADO = (
    "A configuração de proxy do gsettings não foi gravada: faltam o schema e/ou o backend dconf "
    "(Arch: sudo pacman -S gsettings-desktop-schemas dconf; depois reinicie a sessão e o navegador)."
)


def _gsettings_gravado(url: str) -> bool:
    """Relê os valores num processo novo: com o backend em memória, ele volta ao padrão."""
    try:
        modo = _capturar_como_usuario(["gsettings", "get", "org.gnome.system.proxy", "mode"])
        pac = _capturar_como_usuario(["gsettings", "get", "org.gnome.system.proxy", "autoconfig-url"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return modo.strip().strip("'") == "auto" and pac.strip().strip("'") == url


def _erro_comando(exc: Exception) -> str:
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    return (stderr or "").strip() or str(exc)


def _achar_kwriteconfig() -> str | None:
    return next((nome for nome in _BINARIOS_KWRITECONFIG if _tem_binario(nome)), None)


_GRUPO_KIOSLAVERC = "Proxy Settings"


def _kde_presente() -> bool:
    """Via pkexec o ambiente vem sem XDG_CURRENT_DESKTOP, então também procura o kwriteconfig
    e o kioslaverc."""
    ambientes = {d.strip().lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")}
    if "kde" in ambientes or os.environ.get("KDE_SESSION_VERSION") or _achar_kwriteconfig():
        return True
    try:
        return _caminho_kioslaverc().exists()
    except (KeyError, OSError):
        return False


def _caminho_kioslaverc() -> Path:
    if _uid_usuario() is not None:
        return _home_usuario() / ".config" / "kioslaverc"
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home_usuario() / ".config") / "kioslaverc"


def _definir_proxy_kde(valores: dict[str, str]) -> None:
    """Grava as chaves no kioslaverc (pelo kwriteconfig ou editando o arquivo) e confere."""
    kwriteconfig = _achar_kwriteconfig()
    caminho = _caminho_kioslaverc()
    if kwriteconfig:
        for chave, valor in valores.items():
            _executar_como_usuario([kwriteconfig, "--file", "kioslaverc", "--group", _GRUPO_KIOSLAVERC,
                                    "--key", chave, valor])
    else:
        _gravar_arquivo_usuario(caminho, _editar_kioslaverc(_ler_arquivo_usuario(caminho), valores))

    atual = _ler_kioslaverc(_ler_arquivo_usuario(caminho))
    errados = [chave for chave, valor in valores.items() if atual.get(chave) != valor]
    if errados:
        raise RuntimeError(f"{caminho} não ficou com {', '.join(errados)} gravado.")
    _avisar_kde()


def _home_usuario() -> Path:
    uid = _uid_usuario()
    if uid is not None:
        import pwd
        return Path(pwd.getpwuid(uid).pw_dir)
    return Path.home()


# Rodando elevado, arquivos da HOME são lidos e gravados como o próprio usuário, para que um link
# simbólico ali não leve o root a sobrescrever arquivos do sistema.
def _ler_arquivo_usuario(caminho: Path) -> str:
    if _uid_usuario() is None:
        try:
            return caminho.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
    return _capturar_como_usuario(["sh", "-c", 'cat -- "$1" 2>/dev/null || true', "sh", str(caminho)])


def _gravar_arquivo_usuario(caminho: Path, texto: str) -> None:
    if _uid_usuario() is None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
        return
    _capturar_como_usuario(["sh", "-c", 'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"', "sh", str(caminho)],
                           stdin=texto.encode("utf-8"))


def _apagar_arquivo_usuario(caminho: Path) -> None:
    if _uid_usuario() is None:
        caminho.unlink(missing_ok=True)
        return
    _capturar_como_usuario(["rm", "-f", "--", str(caminho)])


def _ler_kioslaverc(texto: str) -> dict[str, str]:
    valores: dict[str, str] = {}
    no_grupo = False
    for linha_texto in texto.splitlines():
        limpa = linha_texto.strip()
        if limpa.startswith("["):
            no_grupo = limpa == f"[{_GRUPO_KIOSLAVERC}]"
        elif no_grupo and "=" in limpa:
            chave, valor = limpa.split("=", 1)
            valores[chave.strip()] = valor.strip()
    return valores


def _editar_kioslaverc(texto: str, valores: dict[str, str]) -> str:
    """Atualiza/insere as chaves no grupo [Proxy Settings], preservando o resto do arquivo."""
    cabecalho = f"[{_GRUPO_KIOSLAVERC}]"
    pendentes = dict(valores)
    saida: list[str] = []
    no_grupo = achou = False
    for linha_texto in texto.splitlines():
        limpa = linha_texto.strip()
        if limpa.startswith("["):
            if no_grupo:
                while saida and not saida[-1].strip():
                    saida.pop()
                saida.extend(f"{k}={v}" for k, v in pendentes.items())
                pendentes.clear()
                saida.append("")
            no_grupo = limpa == cabecalho
            achou = achou or no_grupo
        elif no_grupo and "=" in limpa:
            chave = limpa.split("=", 1)[0].strip()
            if chave in pendentes:
                linha_texto = f"{chave}={pendentes.pop(chave)}"
        saida.append(linha_texto)
    if not achou:
        if saida and saida[-1].strip():
            saida.append("")
        saida.append(cabecalho)
    saida.extend(f"{k}={v}" for k, v in pendentes.items())
    return "\n".join(saida) + "\n"


def _avisar_kde() -> None:
    """Pede ao KIO para reler o kioslaverc, como faz o painel de configurações do Plasma."""
    if not _tem_binario("dbus-send"):
        return
    try:
        _executar_como_usuario(["dbus-send", "--session", "--type=signal", "/KIO/Scheduler",
                                "org.kde.KIO.Scheduler.reparseSlaveConfiguration", "string:"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass


def _uid_usuario() -> int | None:
    """UID do dono da sessão quando rodando elevado (pkexec/sudo); None caso contrário."""
    if os.geteuid() != 0:
        return None
    for var in ("PKEXEC_UID", "SUDO_UID"):
        valor = os.environ.get(var, "")
        if valor.isdigit() and int(valor) != 0:
            return int(valor)
    return None


def _executar_como_usuario(cmd: list[str]) -> None:
    _capturar_como_usuario(cmd)


def _capturar_como_usuario(cmd: list[str], stdin: bytes | None = None) -> str:
    """Executa o comando como o usuário da sessão, com o D-Bus dele. Sem isso, rodando elevado,
    gsettings e kwriteconfig configurariam o proxy do root."""
    uid = _uid_usuario()
    if uid is not None:
        import pwd
        usuario = pwd.getpwuid(uid)
        cmd = ["runuser", "-u", usuario.pw_name, "--", "env",
               f"HOME={usuario.pw_dir}", f"XDG_RUNTIME_DIR=/run/user/{uid}",
               f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus", *cmd]
    retorno = subprocess.run(cmd, check=True, capture_output=True, timeout=5, input=stdin)
    return retorno.stdout.decode(errors="replace")


# Ambientes em que o Chromium lê o proxy do sistema (gsettings/kioslaverc); nos demais (Xfce,
# LXQt, i3, Hyprland...) ele só olha variáveis de ambiente e flags de linha de comando.
_AMBIENTES_PROXY_SISTEMA_CHROMIUM = ("gnome", "unity", "cinnamon", "x-cinnamon", "pantheon", "deepin",
                                     "ukui", "kde")


def _chromium_le_proxy_sistema(xdg_current_desktop: str) -> bool:
    ambientes = {d.strip().lower() for d in xdg_current_desktop.split(":")}
    return bool(ambientes & set(_AMBIENTES_PROXY_SISTEMA_CHROMIUM))


def _gravar_script_env(porta_http: int, pac: str = "") -> Path:
    caminho = pasta_dados() / "env.sh"
    # Ferramentas de terminal não entendem PAC; apontam direto para o proxy HTTP local.
    endereco_proxy = f"http://127.0.0.1:{porta_http}"
    conteudo = (
        f"# Gerado pelo {NOME_EXIBICAO}. Use 'source' neste arquivo para que programas de\n"
        "# terminal (curl, wget, git...) passem pelo proxy.\n"
        f'export http_proxy="{endereco_proxy}"\n'
        f'export https_proxy="{endereco_proxy}"\n'
        f'export HTTP_PROXY="{endereco_proxy}"\n'
        f'export HTTPS_PROXY="{endereco_proxy}"\n'
        'export no_proxy="localhost,127.0.0.1"\n'
        # Chrome/Chromium fora de GNOME/KDE usam o PAC desta variável.
        + (f'export auto_proxy="{pac}"\n' if pac else "")
    )
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def _tem_binario(nome: str) -> bool:
    from shutil import which
    return which(nome) is not None
