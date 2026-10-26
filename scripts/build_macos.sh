#!/bin/sh
# Gera dist/Veredo.app: standalone (nao precisa de Python instalado no Mac de destino).
# O PyInstaller nao faz cross-compile: rode no proprio Mac. O app sai para a arquitetura deste
# Mac (Intel ou Apple Silicon).
#
#   ./scripts/build_macos.sh               so gera dist/Veredo.app
#   ./scripts/build_macos.sh --install     gera e instala em /Applications (ou ~/Applications)
#   ./scripts/build_macos.sh --uninstall   fecha o app e remove as instalacoes
#
# Sempre fecha o Veredo se estiver aberto e apaga o build anterior.
# Usa o uv se estiver instalado; senao cria/usa o .venv com o pip.
set -e

usage() {
    cat <<'AJUDA'
Uso: ./scripts/build_macos.sh [opcoes]

Gera dist/Veredo.app, um app que roda sem Python instalado, e opcionalmente o instala.
Antes de comecar, sempre fecha o Veredo aberto e apaga o build anterior. Rode no proprio Mac
(o PyInstaller nao faz cross-compile); o app sai para a arquitetura deste Mac.

Opcoes:
  (nenhuma)      So gera dist/Veredo.app.

  --install      Instala em /Applications (ou em ~/Applications, se /Applications nao aceitar
                 escrita). Nao pede senha.

  --uninstall    Fecha o Veredo e remove as instalacoes. Os dados (perfis, regras, log) ficam
                 em ~/Library/Application Support/Veredo.

  -h, --help     Mostra esta ajuda.

No macOS o Veredo funciona no modo explicito: ative em Configuracoes a opcao de configurar o
proxy do sistema (o PAC vai para o Wi-Fi/Ethernet pelo networksetup), ou aponte os programas
para o SOCKS5 127.0.0.1:58080 ou o HTTP 127.0.0.1:58081. O modo transparente e o servico do
sistema existem so no Windows e no Linux. Exige macOS 13 ou mais novo.
AJUDA
}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "Este script e para macOS. No Linux use ./scripts/build_linux.sh; no Windows, scripts\\build_windows.ps1." >&2
    exit 1
fi

INSTALL=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --install) INSTALL=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Opcao desconhecida: $arg (use --help)" >&2; exit 1 ;;
    esac
done

SYSTEM_APP=/Applications/Veredo.app
USER_APP="$HOME/Applications/Veredo.app"

# --- Requisitos -----------------------------------------------------------------

versao_macos="$(sw_vers -productVersion)"
if [ "${versao_macos%%.*}" -lt 13 ]; then
    echo "O Veredo exige o macOS 13 ou mais novo (este Mac tem o $versao_macos): o PySide6 nao roda em versoes anteriores." >&2
    exit 1
fi

# Um OPENSSL_DIR vazio (de um "export OPENSSL_DIR=$(brew --prefix ...)" que falhou) faz
# qualquer compilacao do cryptography falhar com "include directory does not exist".
if [ -n "${OPENSSL_DIR+x}" ] && [ -z "$OPENSSL_DIR" ]; then
    unset OPENSSL_DIR
fi

# --- Ambiente Python ------------------------------------------------------------

setup_python() {
    # O instalador do uv poe ele em ~/.local/bin, que nem sempre esta no PATH do terminal atual.
    UV="$(command -v uv || true)"
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        [ -z "$UV" ] && [ -x "$candidate" ] && UV="$candidate"
    done

    if [ -n "$UV" ]; then
        # Dependencias do app + grupo "build" (PyInstaller), travadas no uv.lock. O cryptography
        # nunca e compilado: o uv.lock escolhe uma versao com wheel para este Mac, e compilar do
        # fonte exigiria Rust e OpenSSL; se faltar a wheel, e melhor falhar na hora.
        "$UV" sync --locked --group build --no-build-package cryptography
        run_python() { "$UV" run --no-sync python "$@"; }
    else
        if [ ! -x .venv/bin/python ]; then
            echo "Criando .venv..."
            PY="$(command -v python3.14 || command -v python3 || true)"
            [ -n "$PY" ] || { echo "Python 3.14+ nao encontrado. Instale o uv: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }
            "$PY" -m venv .venv
        fi
        .venv/bin/python -m pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip --upgrade
        # O cryptography 49+ nao tem wheel para Mac Intel; o pyproject ja limita, e --only-binary
        # garante que o pip nao tente compilar.
        .venv/bin/python -m pip install --quiet --only-binary cryptography -e . "pyinstaller>=6.16"
        run_python() { .venv/bin/python "$@"; }
    fi
}

# --- Remocao da versao anterior -------------------------------------------------

is_running() { pgrep -x Veredo >/dev/null 2>&1; }

# Fecha o app com SIGTERM, que ele trata saindo pelo caminho normal (para o motor).
stop_running() {
    is_running || return 0
    echo "Fechando o Veredo em execucao..."
    pkill -TERM -x Veredo 2>/dev/null || true
    i=0
    while is_running && [ "$i" -lt 20 ]; do
        sleep 0.5
        i=$((i + 1))
    done
    if is_running; then
        pkill -KILL -x Veredo 2>/dev/null || true
        sleep 1
    fi
}

uninstall_all() {
    for app in "$SYSTEM_APP" "$USER_APP"; do
        if [ -e "$app" ]; then
            echo "Removendo $app..."
            rm -rf "$app"
        fi
    done
}

stop_running

if [ "$UNINSTALL" -eq 1 ]; then
    uninstall_all
    echo "Veredo desinstalado."
    exit 0
fi

setup_python

echo "Removendo o build anterior..."
rm -rf build dist/Veredo dist/Veredo.app

# --- Build ----------------------------------------------------------------------

echo "Gerando icone..."
run_python packaging/gerar_icone.py
# O .icns sai do PNG com as ferramentas do proprio macOS (sips e iconutil).
ICONSET=build/Veredo.iconset
mkdir -p "$ICONSET"
for tamanho in 16 32 128 256; do
    sips -z "$tamanho" "$tamanho" packaging/icon.png --out "$ICONSET/icon_${tamanho}x${tamanho}.png" >/dev/null
    dobro=$((tamanho * 2))
    sips -z "$dobro" "$dobro" packaging/icon.png --out "$ICONSET/icon_${tamanho}x${tamanho}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o build/Veredo.icns
export VEREDO_ICNS="$ROOT/build/Veredo.icns"

echo ""
echo "Rodando PyInstaller..."
run_python -m PyInstaller packaging/veredo.spec --noconfirm --distpath dist --workpath build

# Assinatura ad hoc do pacote inteiro: sem ela, o macOS (sobretudo no Apple Silicon) pode recusar
# abrir o app ou dizer que ele esta danificado. Nao substitui a assinatura de um Developer ID.
codesign --force --deep --sign - dist/Veredo.app
xattr -dr com.apple.quarantine dist/Veredo.app 2>/dev/null || true

echo ""
echo "App gerado em dist/Veredo.app"

if [ "$INSTALL" -eq 0 ]; then
    echo "Para abrir: open dist/Veredo.app"
    echo "Para instalar em Aplicativos: ./scripts/build_macos.sh --install"
    exit 0
fi

# --- Instalacao -----------------------------------------------------------------

uninstall_all

if [ -w /Applications ]; then
    DESTINO="$SYSTEM_APP"
else
    mkdir -p "$HOME/Applications"
    DESTINO="$USER_APP"
fi
# ditto preserva os links simbolicos e a assinatura do pacote (cp -R nem sempre).
ditto dist/Veredo.app "$DESTINO"

echo "Instalado em $DESTINO. Abra 'Veredo' pelo Launchpad ou com: open \"$DESTINO\""
