#!/bin/sh
# Abre o Veredo já elevado, via pkexec (PolicyKit) -- pede a senha graficamente, sem
# precisar de sudo nem terminal. É o equivalente, no Linux, ao UAC do Windows.
#
# O executável não usa bit SUID: em binários do PyInstaller isso permitiria escalar privilégio.
#
# O pkexec limpa o ambiente e o root não tem acesso ao display do usuário. Por isso o script
# libera o root no display via xhost, repassa DISPLAY/XAUTHORITY e usa o backend xcb do Qt,
# que também funciona no Wayland pelo XWayland.

DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$DIR/veredo"
if [ ! -x "$BIN" ]; then
    BIN="/opt/veredo/veredo"
fi

LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/veredo"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/veredo.log"

# Aberto pelo menu não há terminal pra mostrar erro: vai pro log e, se der, vira notificação.
fail() {
    echo "$(date '+%F %T') $1" >>"$LOG"
    echo "$1" >&2
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i dialog-error "Veredo" "$1"
    fi
    exit 1
}

if [ ! -x "$BIN" ]; then
    fail "Não encontrei o executável do Veredo (procurei em $DIR e /opt/veredo). Rode ./scripts/build_linux.sh --install --admin primeiro."
fi

if [ -z "$DISPLAY" ]; then
    fail "Nenhum display X11/XWayland disponível (DISPLAY vazio) -- o root não consegue abrir a janela."
fi

if ! command -v xhost >/dev/null 2>&1; then
    fail "Comando 'xhost' não encontrado. Instale-o (Fedora: sudo dnf install xhost; Debian/Ubuntu: sudo apt install x11-xserver-utils)."
fi

xhost +SI:localuser:root >/dev/null

echo "$(date '+%F %T') iniciando $BIN" >>"$LOG"
# XDG_CURRENT_DESKTOP/KDE_SESSION_VERSION: o app precisa saber se é Plasma para configurar o KDE.
pkexec env DISPLAY="$DISPLAY" XAUTHORITY="${XAUTHORITY:-}" QT_QPA_PLATFORM=xcb \
    XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-}" KDE_SESSION_VERSION="${KDE_SESSION_VERSION:-}" \
    "$BIN" "$@" >>"$LOG" 2>&1
status=$?

# Revoga o acesso do root ao display assim que o app fecha.
xhost -SI:localuser:root >/dev/null 2>&1

case "$status" in
    0) ;;
    126|127) echo "$(date '+%F %T') autenticação cancelada ou negada (pkexec saiu com $status)" >>"$LOG" ;;
    *) fail "O Veredo fechou com erro (código $status). Detalhes em $LOG" ;;
esac
exit "$status"
