#!/bin/sh
# Gera dist/veredo: standalone (nao precisa de Python instalado na maquina de destino).
# O PyInstaller nao faz cross-compile: rode no proprio Linux.
#
#   ./scripts/build_linux.sh                    so gera dist/veredo
#   ./scripts/build_linux.sh --install          gera e instala pro seu usuario (~/.local), sem sudo,
#                                               com atalho no menu -- roda como usuario comum
#   ./scripts/build_linux.sh --install --admin  gera e instala em /opt (pede sudo) com atalho que
#                                               abre via pkexec -- pede a senha toda vez que abre
#   ./scripts/build_linux.sh --root             gera e instala em /opt (pede sudo uma vez) com o
#                                               motor como servico do systemd, rodando como root
#                                               desde o boot; o atalho abre so a janela, sem senha
#   ./scripts/build_linux.sh --uninstall        fecha o app e remove todas as instalacoes
#
# Sempre fecha o Veredo se estiver aberto e apaga o build anterior. Com --install, remove
# antes qualquer instalacao anterior (de usuario e de admin), pra nunca ficarem duas no menu.
#
# Usa o uv se estiver instalado; senao cria/usa o .venv com o pip.
set -e

usage() {
    cat <<'AJUDA'
Uso: ./scripts/build_linux.sh [opcoes]

Gera dist/veredo, um executavel que roda sem Python instalado, e opcionalmente o instala.
Antes de comecar, sempre fecha o Veredo aberto e apaga o build anterior. Rode no proprio Linux
(o PyInstaller nao faz cross-compile).

Opcoes:
  (nenhuma)          So gera dist/veredo.

  --install          Instala para o seu usuario em ~/.local/share/veredo, com atalho no menu.
                     Nao pede sudo. Roda como usuario comum: modo explicito (PAC), sem o modo
                     transparente.

  --install --admin  Instala em /opt/veredo (pede sudo). O atalho abre pelo pkexec e pede a
                     senha toda vez; a janela roda como root, com o modo transparente.

  --root             Instala em /opt/veredo e registra o motor como servico do systemd
                     (veredo.service), rodando como root desde o boot. O sudo e pedido uma vez,
                     na instalacao; o atalho abre so a janela, sem senha, e ela controla o
                     servico. Sair do Veredo para o motor; ele volta no proximo boot ou pela
                     janela.

  --uninstall        Fecha o Veredo e remove todas as instalacoes, o servico e o inicio
                     automatico.

  -h, --help         Mostra esta ajuda.

Cada --install/--root remove antes as instalacoes anteriores, para nunca ficarem duas no menu.
Usa o uv se estiver instalado; senao cria o .venv com o pip.

Conferir depois de instalar:
  systemctl status veredo.service      estado do servico (--root)
  journalctl -u veredo.service -f      log do servico (--root)
  ~/.local/state/veredo/veredo.log     log do atalho com pkexec (--install --admin)
AJUDA
}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INSTALL=0
ADMIN=0
SERVICO=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --install) INSTALL=1 ;;
        --admin) ADMIN=1 ;;
        --root) INSTALL=1; SERVICO=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Opcao desconhecida: $arg (use --help)" >&2; exit 1 ;;
    esac
done

USER_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/veredo"
USER_DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/veredo.desktop"
ADMIN_DIR=/opt/veredo
ADMIN_DESKTOP=/usr/share/applications/veredo.desktop
SERVICE_UNIT=/etc/systemd/system/veredo.service
AUTOSTART="$HOME/.config/autostart/veredo.desktop"

# --- Ambiente Python ------------------------------------------------------------

setup_python() {
    # O instalador do uv poe ele em ~/.local/bin, que nem sempre esta no PATH do terminal atual.
    UV="$(command -v uv || true)"
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        [ -z "$UV" ] && [ -x "$candidate" ] && UV="$candidate"
    done

    if [ -n "$UV" ]; then
        # Dependencias do app + grupo "build" (PyInstaller), travadas no uv.lock.
        "$UV" sync --locked --group build
        run_python() { "$UV" run --no-sync python "$@"; }
    else
        if [ ! -x .venv/bin/python ]; then
            echo "Criando .venv..."
            PY="$(command -v python3.14 || command -v python3 || true)"
            [ -n "$PY" ] || { echo "Python 3.14+ nao encontrado." >&2; exit 1; }
            "$PY" -m venv .venv
        fi
        # Um .venv criado pelo uv nao traz o pip.
        .venv/bin/python -m pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip --upgrade
        .venv/bin/python -m pip install --quiet -e . "pyinstaller>=6.16"
        run_python() { .venv/bin/python "$@"; }
    fi
}

# --- Remocao da versao anterior -------------------------------------------------

is_running() { pgrep -x veredo >/dev/null 2>&1; }

# Fecha o app com SIGTERM, que ele trata saindo pelo caminho normal (para o motor e desfaz o proxy
# dos navegadores). So mata a forca se nao sair em 10s -- e ai desfaz o proxy por aqui.
stop_running() {
    is_running || return 0
    echo "Fechando o Veredo em execucao..."
    SUDO=""
    if pgrep -x -u root veredo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
        SUDO="sudo"  # instalacao de admin (pkexec) roda como root
    fi
    $SUDO pkill -TERM -x veredo 2>/dev/null || true
    i=0
    while is_running && [ "$i" -lt 20 ]; do
        sleep 0.5
        i=$((i + 1))
    done
    if is_running; then
        $SUDO pkill -KILL -x veredo 2>/dev/null || true
        sleep 1
        echo "Desfazendo o proxy dos navegadores/sistema..."
        run_python -c "from proxy_manager.nucleo.integracao_sistema import remover_proxy_sistema; print(remover_proxy_sistema(manter_atalhos=True)[1])" || true
    fi
}

uninstall_service() {
    [ -e "$SERVICE_UNIT" ] || return 0
    echo "Removendo o servico do sistema (pede sudo)..."
    if [ -x "$ADMIN_DIR/veredo" ]; then
        sudo "$ADMIN_DIR/veredo" --remover-servico || true
    fi
    # Sem o executavel (ou se ele falhar), desfaz pelo systemctl.
    if [ -e "$SERVICE_UNIT" ]; then
        sudo systemctl disable --now veredo.service 2>/dev/null || true
        sudo rm -f "$SERVICE_UNIT"
        sudo systemctl daemon-reload || true
    fi
}

uninstall_all() {
    uninstall_service
    if [ -e "$USER_DIR" ] || [ -e "$USER_DESKTOP" ]; then
        echo "Removendo a instalacao anterior em $USER_DIR..."
        rm -rf "$USER_DIR"
        rm -f "$USER_DESKTOP"
    fi
    if [ -e "$ADMIN_DIR" ] || [ -e "$ADMIN_DESKTOP" ]; then
        echo "Removendo a instalacao anterior em $ADMIN_DIR (pede sudo)..."
        sudo rm -rf "$ADMIN_DIR" "$ADMIN_DESKTOP"
    fi
}

setup_python
stop_running

if [ "$UNINSTALL" -eq 1 ]; then
    uninstall_all
    # O inicio automatico apontava para o executavel instalado, que nao existe mais.
    if [ -f "$AUTOSTART" ] && grep -qE "$USER_DIR|$ADMIN_DIR" "$AUTOSTART"; then
        rm -f "$AUTOSTART"
    fi
    echo "Veredo desinstalado."
    exit 0
fi

echo "Removendo o build anterior..."
rm -rf build dist/veredo

# --- Build ----------------------------------------------------------------------

echo "Gerando icone..."
run_python packaging/gerar_icone.py

echo ""
echo "Rodando PyInstaller..."
run_python -m PyInstaller packaging/veredo.spec --noconfirm --distpath dist --workpath build

echo ""
echo "Executavel gerado em dist/veredo"

if [ "$INSTALL" -eq 0 ]; then
    echo "Para instalar com atalho no menu: ./scripts/build_linux.sh --install"
    exit 0
fi

# --- Instalacao -----------------------------------------------------------------

uninstall_all

if [ "$SERVICO" -eq 1 ]; then
    sudo packaging/linux/install.sh --servico
    exit 0
fi

if [ "$ADMIN" -eq 1 ]; then
    sudo packaging/linux/install.sh
    exit 0
fi

mkdir -p "$USER_DIR" "$(dirname "$USER_DESKTOP")"
install -m 755 dist/veredo "$USER_DIR/veredo"
install -m 644 packaging/icon.png "$USER_DIR/icon.png"
cat >"$USER_DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Veredo
Comment=Gerenciador de proxy com regras por aplicativo e domínio
Exec="$USER_DIR/veredo"
Icon=$USER_DIR/icon.png
Terminal=false
Categories=Network;
EOF
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$(dirname "$USER_DESKTOP")" >/dev/null 2>&1 || true

echo "Instalado em $USER_DIR, com atalho 'Veredo' no menu de aplicativos."
