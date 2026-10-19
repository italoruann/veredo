"""Testes do parser de SNI (TLS ClientHello) e do peek de Host (HTTP) usados pelo modo
transparente pra descobrir o domínio pretendido sem terminar a conexão."""
import struct

from proxy_manager.nucleo.transparente.inspecao import extrair_host_http, extrair_sni


def _montar_client_hello(hostname: str | None) -> bytes:
    """Monta um ClientHello TLS 1.2 minimamente válido, com (ou sem) a extensão SNI, seguindo a
    mesma estrutura que extract_sni() espera — não precisa ser criptograficamente real."""
    extensoes = b""
    if hostname is not None:
        name_bytes = hostname.encode("idna")
        server_name_entry = bytes([0x00]) + struct.pack("!H", len(name_bytes)) + name_bytes
        server_name_list = struct.pack("!H", len(server_name_entry)) + server_name_entry
        sni_extension = struct.pack("!HH", 0x0000, len(server_name_list)) + server_name_list
        extensoes += sni_extension

    corpo = b""
    corpo += b"\x03\x03"  # client_version (TLS 1.2)
    corpo += b"\x00" * 32  # random
    corpo += bytes([0])  # session_id vazio
    corpo += struct.pack("!H", 2) + b"\x00\x2f"  # 1 cipher suite
    corpo += bytes([1]) + b"\x00"  # 1 compression method (null)
    corpo += struct.pack("!H", len(extensoes)) + extensoes

    handshake = bytes([0x01]) + len(corpo).to_bytes(3, "big") + corpo
    record = bytes([0x16]) + b"\x03\x01" + struct.pack("!H", len(handshake)) + handshake
    return record


def test_extrai_sni_de_client_hello_valido():
    client_hello = _montar_client_hello("www.paypal.com")
    assert extrair_sni(client_hello) == "www.paypal.com"


def test_sni_none_sem_extensao():
    client_hello = _montar_client_hello(None)
    assert extrair_sni(client_hello) is None


def test_sni_none_para_lixo():
    assert extrair_sni(b"nao e nem perto de um client client_hello") is None
    assert extrair_sni(b"") is None


def test_sni_none_para_client_hello_cortado():
    client_hello = _montar_client_hello("www.paypal.com")
    assert extrair_sni(client_hello[:20]) is None


def test_extrai_host_de_requisicao_http():
    requisicao = b"GET / HTTP/1.1\r\nHost: www.example.com\r\nUser-Agent: teste\r\n\r\n"
    assert extrair_host_http(requisicao) == "www.example.com"


def test_host_http_sem_porta():
    requisicao = b"GET / HTTP/1.1\r\nHost: www.example.com:8080\r\n\r\n"
    assert extrair_host_http(requisicao) == "www.example.com"


def test_host_none_com_cabecalhos_incompletos():
    requisicao = b"GET / HTTP/1.1\r\nHost: www.example.com"
    assert extrair_host_http(requisicao) is None


def test_host_none_sem_cabecalho():
    requisicao = b"GET / HTTP/1.1\r\nUser-Agent: teste\r\n\r\n"
    assert extrair_host_http(requisicao) is None
