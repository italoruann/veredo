# -*- mode: python ; coding: utf-8 -*-
"""Spec do PyInstaller para Windows, Linux e macOS. Use pelos scripts em scripts/.

Com VEREDO_ADMIN=1 o executável do Windows pede elevação (UAC) ao abrir, o que só é necessário
para o modo transparente. No Linux a elevação é feita pelo pkexec (build_linux.sh --admin).

No macOS sai dist/Veredo.app, em pasta (onedir): um .app de arquivo único precisaria se extrair
a cada abertura e o PyInstaller já o trata como obsoleto. O ícone .icns é gerado pelo
build_macos.sh (VEREDO_ICNS); sem ele o app fica com o ícone padrão.
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 -- SPECPATH é injetado pelo PyInstaller
ICON_ICO = str(ROOT / "packaging" / "icon.ico")
ICON_PNG = str(ROOT / "packaging" / "icon.png")
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
ICON_ICNS = os.environ.get("VEREDO_ICNS", "")
VERSAO = "0.1.0"
REQUIRE_ADMIN = os.environ.get("VEREDO_ADMIN") == "1"

# O keyring carrega os backends por entry points, que o PyInstaller não detecta sozinho.
HIDDEN_IMPORTS = [
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
    "keyring.backends.macOS",
]

a = Analysis(
    [str(ROOT / "packaging" / "inicializador.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if IS_MACOS:
    icone = ICON_ICNS if ICON_ICNS and os.path.exists(ICON_ICNS) else None
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Veredo",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icone,
    )
    pasta = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Veredo")
    app = BUNDLE(
        pasta,
        name="Veredo.app",
        icon=icone,
        bundle_identifier="io.github.italoruann.veredo",
        version=VERSAO,
        info_plist={
            "CFBundleDisplayName": "Veredo",
            "CFBundleShortVersionString": VERSAO,
            "NSHighResolutionCapable": True,
            # PySide6 6.11 exige o macOS 13 ou mais novo.
            "LSMinimumSystemVersion": "13.0",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="Veredo" if IS_WINDOWS else "veredo",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON_ICO if IS_WINDOWS else ICON_PNG,
        uac_admin=IS_WINDOWS and REQUIRE_ADMIN,
    )
