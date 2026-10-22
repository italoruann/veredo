"""Instala e remove o certificado da CA do Veredo como confiável no sistema.

- Windows: repositório "Raiz confiável" do usuário atual (`certutil -user`, sem administrador;
  o Windows pede confirmação). Chrome, Edge e Firefox (por padrão) confiam nele.
- Linux: pasta de âncoras da distribuição mais o comando que atualiza o repositório, com pkexec.
  O Firefox no Linux usa um repositório próprio: o certificado precisa ser importado nele."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes

NOME_ARQUIVO_LINUX = "veredo-ca.crt"
# (pasta de âncoras, comando que atualiza o repositório), na ordem em que são procurados.
_REPOSITORIOS_LINUX = (
    ("/usr/local/share/ca-certificates", "update-ca-certificates"),  # Debian, Ubuntu
    ("/etc/pki/ca-trust/source/anchors", "update-ca-trust"),  # Fedora, RHEL
    ("/etc/ca-certificates/trust-source/anchors", "update-ca-trust"),  # Arch
    ("/etc/pki/trust/anchors", "update-ca-certificates"),  # openSUSE
)


def impressao_sha1(pem: bytes) -> str:
    """Identificador que o certutil aceita para achar o certificado."""
    return x509.load_pem_x509_certificate(pem).fingerprint(hashes.SHA1()).hex()


def impressao_sha256(pem: bytes) -> str:
    """Para o usuário conferir qual certificado está instalado."""
    return x509.load_pem_x509_certificate(pem).fingerprint(hashes.SHA256()).hex(":").upper()


def _repositorio_linux() -> Optional[tuple[Path, str]]:
    for pasta, comando in _REPOSITORIOS_LINUX:
        if Path(pasta).is_dir() and shutil.which(comando):
            return Path(pasta), comando
    return None


def _como_root(script: str) -> list[str]:
    comando = ["sh", "-c", script]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        comando = ["pkexec"] + comando
    return comando


def _rodar(comando: list[str], timeout: float = 120) -> tuple[bool, str]:
    try:
        resultado = subprocess.run(comando, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    saida = (resultado.stderr or resultado.stdout or b"").decode(errors="replace").strip()
    return resultado.returncode == 0, saida


def instalar(pem: bytes) -> tuple[bool, str]:
    sistema = platform.system()
    if sistema == "Windows":
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "veredo-ca.cer"
            arquivo.write_bytes(pem)
            ok, saida = _rodar(["certutil", "-user", "-addstore", "Root", str(arquivo)])
        if not ok:
            return False, f"O Windows não instalou o certificado: {saida or 'operação cancelada'}"
        return True, "Certificado instalado nas autoridades raiz confiáveis do usuário."
    if sistema == "Linux":
        repositorio = _repositorio_linux()
        if repositorio is None:
            return False, ("Não encontrei o repositório de certificados desta distribuição. Exporte o "
                           "certificado e instale-o manualmente.")
        pasta, atualizar = repositorio
        with tempfile.NamedTemporaryFile("wb", suffix=".crt", delete=False) as temporario:
            temporario.write(pem)
        try:
            destino = pasta / NOME_ARQUIVO_LINUX
            ok, saida = _rodar(_como_root(f"install -m 644 '{temporario.name}' '{destino}' && {atualizar}"))
        finally:
            os.unlink(temporario.name)
        if not ok:
            return False, f"Falha ao instalar o certificado: {saida or 'operação cancelada'}"
        return True, (f"Certificado instalado em {destino}. O Firefox usa um repositório próprio: importe "
                      "o certificado em Configurações > Privacidade e segurança > Certificados.")
    return False, f"Instalação automática não suportada em {sistema}."


def remover(pem: bytes) -> tuple[bool, str]:
    sistema = platform.system()
    if sistema == "Windows":
        ok, saida = _rodar(["certutil", "-user", "-delstore", "Root", impressao_sha1(pem)])
        return (True, "Certificado removido.") if ok else (False, f"Falha ao remover: {saida}")
    if sistema == "Linux":
        repositorio = _repositorio_linux()
        if repositorio is None:
            return False, "Não encontrei o repositório de certificados desta distribuição."
        pasta, atualizar = repositorio
        ok, saida = _rodar(_como_root(f"rm -f '{pasta / NOME_ARQUIVO_LINUX}' && {atualizar}"))
        return (True, "Certificado removido.") if ok else (False, f"Falha ao remover: {saida or 'operação cancelada'}")
    return False, f"Remoção automática não suportada em {sistema}."


def instalada(pem: bytes) -> bool:
    sistema = platform.system()
    if sistema == "Windows":
        return _rodar(["certutil", "-user", "-verifystore", "Root", impressao_sha1(pem)], timeout=20)[0]
    if sistema == "Linux":
        repositorio = _repositorio_linux()
        if repositorio is None:
            return False
        try:
            return (repositorio[0] / NOME_ARQUIVO_LINUX).read_bytes().strip() == pem.strip()
        except OSError:
            return False
    return False
