#!/bin/sh
# Instala o Veredo em /opt/veredo (dono root, sem escrita para outros usuários, como o pkexec e
# o serviço exigem). Rode com sudo.
#
#   sudo packaging/linux/install.sh            atalho que pede a senha ao abrir (pkexec)
#   sudo packaging/linux/install.sh --servico  motor como serviço do systemd, rodando como root
#                                              desde o boot; o atalho abre só a janela, sem senha
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "Rode este instalador com sudo:" >&2
    echo "  sudo $0 $*" >&2
    exit 1
fi

SERVICO=0
for arg in "$@"; do
    case "$arg" in
        --servico) SERVICO=1 ;;
        *) echo "Opção desconhecida: $arg" >&2; exit 1 ;;
    esac
done

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="$ROOT/dist/veredo"
if [ ! -x "$BIN" ]; then
    echo "Não encontrei $BIN -- rode ./scripts/build_linux.sh --root ou --install --admin." >&2
    exit 1
fi

INSTALL_DIR=/opt/veredo
DESKTOP=/usr/share/applications/veredo.desktop
mkdir -p "$INSTALL_DIR"
install -o root -g root -m 755 "$BIN" "$INSTALL_DIR/veredo"
install -o root -g root -m 644 "$ROOT/packaging/icon.png" "$INSTALL_DIR/icon.png"

if [ "$SERVICO" -eq 0 ]; then
    install -o root -g root -m 755 "$ROOT/packaging/linux/veredo.sh" "$INSTALL_DIR/veredo.sh"
    install -o root -g root -m 644 "$ROOT/packaging/linux/veredo.desktop" "$DESKTOP"
    echo "Instalado em $INSTALL_DIR."
    echo "Abra 'Veredo' no menu de aplicativos do seu ambiente gráfico, ou rode:"
    echo "  $INSTALL_DIR/veredo.sh"
    echo "Os dois pedem a senha graficamente via pkexec antes de abrir."
    exit 0
fi

# O serviço faz a parte que exige root (modo transparente); a janela roda como usuário comum e
# só o controla, por isso o atalho abre o executável direto, sem pkexec.
rm -f "$INSTALL_DIR/veredo.sh"
sed "s|^Exec=.*|Exec=$INSTALL_DIR/veredo|" "$ROOT/packaging/linux/veredo.desktop" >"$DESKTOP"
chmod 644 "$DESKTOP"
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$(dirname "$DESKTOP")" >/dev/null 2>&1 || true

"$INSTALL_DIR/veredo" --instalar-servico

echo "Instalado em $INSTALL_DIR, com o motor rodando como serviço (veredo.service)."
echo "Abra 'Veredo' no menu de aplicativos: a janela abre sem pedir senha e controla o serviço."
