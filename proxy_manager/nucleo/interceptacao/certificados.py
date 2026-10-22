"""Autoridade certificadora local da interceptação.

A CA é criada na primeira vez e guardada na pasta de dados; para o HTTPS interceptado ser aceito
o usuário instala o certificado dela (só a parte pública) como confiável. Cada site recebe um
certificado próprio, assinado pela CA e guardado em memória."""
from __future__ import annotations

import datetime
import ipaddress
import os
import ssl
import stat
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# Sem acentos: o certutil do Windows e alguns gerenciadores de certificados os mostram errado.
NOME_CA = "Veredo CA (interceptacao local)"
ARQUIVO_CERTIFICADO = "veredo-ca.pem"
ARQUIVO_CHAVE = "veredo-ca-chave.pem"
VALIDADE_CA_DIAS = 3650
# Navegadores recusam certificados de servidor com validade acima de ~398 dias.
VALIDADE_SITE_DIAS = 365
MAX_CONTEXTOS_EM_CACHE = 512


def _agora() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _gravar_privado(caminho: Path, dados: bytes) -> None:
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_bytes(dados)
    try:
        os.chmod(temporario, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(temporario, caminho)


class AutoridadeCertificadora:
    def __init__(self, pasta: Path):
        self.pasta = pasta
        self._trava = threading.Lock()
        self._chave: ec.EllipticCurvePrivateKey | None = None
        self._certificado: x509.Certificate | None = None
        # Uma única chave serve para todos os certificados de site: gerar uma por site é lento.
        self._chave_sites = ec.generate_private_key(ec.SECP256R1())
        self._contextos: OrderedDict[str, ssl.SSLContext] = OrderedDict()

    # -- a própria CA ---------------------------------------------------------------------

    def _carregar_ou_criar(self) -> None:
        if self._certificado is not None:
            return
        caminho_cert = self.pasta / ARQUIVO_CERTIFICADO
        caminho_chave = self.pasta / ARQUIVO_CHAVE
        try:
            certificado = x509.load_pem_x509_certificate(caminho_cert.read_bytes())
            chave = serialization.load_pem_private_key(caminho_chave.read_bytes(), password=None)
            if certificado.not_valid_after_utc > _agora() + datetime.timedelta(days=30):
                self._certificado, self._chave = certificado, chave  # type: ignore[assignment]
                return
        except (OSError, ValueError):
            pass
        self._chave, self._certificado = self._criar_ca()
        self.pasta.mkdir(parents=True, exist_ok=True)
        _gravar_privado(caminho_chave, self._chave.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        caminho_cert.write_bytes(self._certificado.public_bytes(serialization.Encoding.PEM))

    @staticmethod
    def _criar_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        chave = ec.generate_private_key(ec.SECP256R1())
        nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NOME_CA),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Veredo")])
        agora = _agora()
        certificado = (
            x509.CertificateBuilder()
            .subject_name(nome).issuer_name(nome)
            .public_key(chave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(agora - datetime.timedelta(days=1))
            .not_valid_after(agora + datetime.timedelta(days=VALIDADE_CA_DIAS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(chave.public_key()), critical=False)
            .sign(chave, hashes.SHA256())
        )
        return chave, certificado

    def certificado_pem(self) -> bytes:
        with self._trava:
            self._carregar_ou_criar()
            assert self._certificado is not None
            return self._certificado.public_bytes(serialization.Encoding.PEM)

    def recriar(self) -> None:
        """Gera uma CA nova; a anterior deixa de valer e precisa ser removida do sistema."""
        with self._trava:
            for nome in (ARQUIVO_CERTIFICADO, ARQUIVO_CHAVE):
                (self.pasta / nome).unlink(missing_ok=True)
            self._certificado = self._chave = None
            self._contextos.clear()
            self._carregar_ou_criar()

    # -- certificados dos sites -------------------------------------------------------------

    def contexto_servidor(self, host: str) -> ssl.SSLContext:
        """Contexto TLS para apresentar ao aplicativo um certificado válido para `host`."""
        host = host.lower().rstrip(".")
        with self._trava:
            contexto = self._contextos.get(host)
            if contexto is not None:
                self._contextos.move_to_end(host)
                return contexto
            self._carregar_ou_criar()
            contexto = self._criar_contexto(host)
            self._contextos[host] = contexto
            if len(self._contextos) > MAX_CONTEXTOS_EM_CACHE:
                self._contextos.popitem(last=False)
            return contexto

    def _certificado_site(self, host: str) -> x509.Certificate:
        assert self._chave is not None and self._certificado is not None
        try:
            nome_alternativo: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            nome_alternativo = x509.DNSName(host)
        agora = _agora()
        return (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host[:64] or "veredo")]))
            .issuer_name(self._certificado.subject)
            .public_key(self._chave_sites.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(agora - datetime.timedelta(days=1))
            .not_valid_after(agora + datetime.timedelta(days=VALIDADE_SITE_DIAS))
            .add_extension(x509.SubjectAlternativeName([nome_alternativo]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self._chave.public_key()),
                           critical=False)
            .sign(self._chave, hashes.SHA256())
        )

    def _criar_contexto(self, host: str) -> ssl.SSLContext:
        assert self._certificado is not None
        cadeia = (self._certificado_site(host).public_bytes(serialization.Encoding.PEM)
                  + self._certificado.public_bytes(serialization.Encoding.PEM))
        chave = self._chave_sites.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption())
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Só HTTP/1.1: o HTTP/2 não é decodificado, e os aplicativos aceitam voltar ao 1.1.
        contexto.set_alpn_protocols(["http/1.1"])
        # O ssl só carrega certificados de arquivos; eles existem apenas durante a chamada.
        with tempfile.TemporaryDirectory() as pasta:
            caminho_cadeia = Path(pasta) / "cadeia.pem"
            caminho_chave = Path(pasta) / "chave.pem"
            caminho_cadeia.write_bytes(cadeia)
            _gravar_privado(caminho_chave, chave)
            contexto.load_cert_chain(caminho_cadeia, caminho_chave)
        return contexto


def contexto_cliente(verificar: bool, tem_nome: bool = True) -> ssl.SSLContext:
    """Contexto TLS para a conexão do motor com o destino. Sem nome (destino só por IP, sem SNI)
    a cadeia do certificado ainda é verificada, mas não o nome."""
    contexto = ssl.create_default_context()
    if not verificar or not tem_nome:
        contexto.check_hostname = False
    if not verificar:
        contexto.verify_mode = ssl.CERT_NONE
    contexto.set_alpn_protocols(["http/1.1"])
    return contexto
