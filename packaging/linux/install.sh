#!/bin/sh
# Instala o Veredo em /opt/veredo (dono root, sem escrita para outros usuários, como o pkexec
# exige) e registra o atalho que pede a senha ao abrir. Rode com sudo.
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "Rode este instalador com sudo:" >&2
    echo "  sudo $0" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="$ROOT/dist/veredo"
if [ ! -x "$BIN" ]; then
    echo "Não encontrei $BIN -- rode ./scripts/build_linux.sh --install --admin." >&2
    exit 1
fi

INSTALL_DIR=/opt/veredo
mkdir -p "$INSTALL_DIR"
install -o root -g root -m 755 "$BIN" "$INSTALL_DIR/veredo"
install -o root -g root -m 644 "$ROOT/packaging/icon.png" "$INSTALL_DIR/icon.png"
install -o root -g root -m 755 "$ROOT/packaging/linux/veredo.sh" "$INSTALL_DIR/veredo.sh"
install -o root -g root -m 644 "$ROOT/packaging/linux/veredo.desktop" /usr/share/applications/veredo.desktop

echo "Instalado em $INSTALL_DIR."
echo "Abra 'Veredo' no menu de aplicativos do seu ambiente gráfico, ou rode:"
echo "  $INSTALL_DIR/veredo.sh"
echo "Os dois pedem a senha graficamente via pkexec antes de abrir."
