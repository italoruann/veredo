"""DNS pelo proxy: servidor DNS local com IPs falsos e um resolvedor próprio para o motor.

No modo transparente as consultas DNS dos programas são desviadas para cá. Cada nome recebe um IP
falso da faixa 198.18.0.0/15 (reservada para testes, nunca usada na internet); quando o programa
conecta nesse IP, o motor sabe qual era o nome, aplica as regras por domínio e manda o nome ao
proxy, que resolve do lado dele. Assim nenhuma consulta DNS sai da máquina pela rede local.

O próprio motor não pode usar o resolvedor do sistema enquanto isso estiver ativo (receberia os
IPs falsos de volta), então consulta os servidores DNS reais diretamente com `ResolvedorReal`."""
from __future__ import annotations

import asyncio
import ipaddress
import platform
import socket
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import rede
from .regras import dividir_alvo, padrao_corresponde

TIPO_A = 1
TIPO_CNAME = 5
TIPO_AAAA = 28
TIPO_SVCB = 64
TIPO_HTTPS = 65
CLASSE_IN = 1

RCODE_OK = 0
RCODE_FALHA = 2

REDE_FALSA = ipaddress.ip_network("198.18.0.0/15")
TTL_FALSO = 300

EXCECOES_PADRAO = "localhost; *.local; *.lan; *.home.arpa; *.localdomain"


# -- formato das mensagens DNS (RFC 1035) -------------------------------------------------

@dataclass
class Consulta:
    id: int
    flags: int
    nome: str
    tipo: int
    classe: int
    pergunta: bytes  # seção de pergunta crua, devolvida igual na resposta


def _ler_nome(dados: bytes, pos: int) -> tuple[str, int]:
    """Lê um nome (com ponteiros de compressão); devolve o nome e a posição depois dele."""
    partes: list[str] = []
    fim: Optional[int] = None
    saltos = 0
    while True:
        tamanho = dados[pos]
        if tamanho & 0xC0 == 0xC0:
            if fim is None:
                fim = pos + 2
            pos = ((tamanho & 0x3F) << 8) | dados[pos + 1]
            saltos += 1
            if saltos > 32:
                raise ValueError("ponteiros DNS em loop")
            continue
        pos += 1
        if tamanho == 0:
            break
        partes.append(dados[pos:pos + tamanho].decode("ascii", errors="replace"))
        pos += tamanho
    return ".".join(partes).lower(), (fim if fim is not None else pos)


def ler_consulta(dados: bytes) -> Optional[Consulta]:
    try:
        id_, flags, qtd_perguntas = struct.unpack("!3H", dados[:6])
        if flags & 0x8000 or qtd_perguntas != 1:  # é resposta, ou mais de uma pergunta
            return None
        nome, pos = _ler_nome(dados, 12)
        tipo, classe = struct.unpack("!HH", dados[pos:pos + 4])
    except (struct.error, IndexError, ValueError):
        return None
    return Consulta(id=id_, flags=flags, nome=nome, tipo=tipo, classe=classe, pergunta=dados[12:pos + 4])


def montar_resposta(consulta: Consulta, rcode: int = RCODE_OK,
                    respostas: list[tuple[int, bytes]] | tuple = (), ttl: int = TTL_FALSO) -> bytes:
    # QR=1, RD copiado da consulta, RA=1.
    flags = 0x8000 | (consulta.flags & 0x0100) | 0x0080 | rcode
    corpo = consulta.pergunta
    for tipo, rdata in respostas:
        corpo += b"\xc0\x0c" + struct.pack("!HHIH", tipo, CLASSE_IN, ttl, len(rdata)) + rdata
    return struct.pack("!6H", consulta.id, flags, 1, len(respostas), 0, 0) + corpo


def montar_consulta(nome: str, tipo: int, id_: int) -> bytes:
    rotulos = b"".join(bytes([len(p)]) + p for p in (r.encode("idna") for r in nome.rstrip(".").split(".")) if p)
    return struct.pack("!6H", id_, 0x0100, 1, 0, 0, 0) + rotulos + b"\x00" + struct.pack("!HH", tipo, CLASSE_IN)


def ler_enderecos(resposta: bytes) -> list[str]:
    """Endereços A/AAAA da seção de respostas."""
    try:
        qtd_perguntas, qtd_respostas = struct.unpack("!HH", resposta[4:8])
        pos = 12
        for _ in range(qtd_perguntas):
            _, pos = _ler_nome(resposta, pos)
            pos += 4
        enderecos = []
        for _ in range(qtd_respostas):
            _, pos = _ler_nome(resposta, pos)
            tipo, _classe, _ttl, tamanho = struct.unpack("!HHIH", resposta[pos:pos + 10])
            pos += 10
            rdata = resposta[pos:pos + tamanho]
            pos += tamanho
            if tipo == TIPO_A and tamanho == 4:
                enderecos.append(str(ipaddress.IPv4Address(rdata)))
            elif tipo == TIPO_AAAA and tamanho == 16:
                enderecos.append(str(ipaddress.IPv6Address(rdata)))
        return enderecos
    except (struct.error, IndexError, ValueError):
        return []


# -- IPs falsos ------------------------------------------------------------------------------

def eh_ip_falso(host: str) -> bool:
    try:
        return ipaddress.ip_address(host) in REDE_FALSA
    except ValueError:
        return False


class TabelaIpsFalsos:
    """Associa nomes a IPs falsos. Quando a faixa enche, o nome usado há mais tempo é liberado."""

    def __init__(self, rede_falsa: ipaddress.IPv4Network = REDE_FALSA):
        self._base = int(rede_falsa.network_address) + 1
        self._capacidade = rede_falsa.num_addresses - 2
        self._trava = threading.Lock()
        self._por_nome: OrderedDict[str, int] = OrderedDict()
        self._por_ip: dict[int, str] = {}

    def ip_para(self, nome: str) -> str:
        nome = nome.lower().rstrip(".")
        with self._trava:
            numero = self._por_nome.get(nome)
            if numero is not None:
                self._por_nome.move_to_end(nome)
            else:
                if len(self._por_nome) >= self._capacidade:
                    _antigo, numero = self._por_nome.popitem(last=False)
                else:
                    numero = self._base + len(self._por_nome)
                self._por_nome[nome] = numero
                self._por_ip[numero] = nome
            return str(ipaddress.IPv4Address(numero))

    def nome_de(self, ip: str) -> Optional[str]:
        try:
            numero = int(ipaddress.IPv4Address(ip))
        except ValueError:
            return None
        with self._trava:
            return self._por_ip.get(numero)

    def __len__(self) -> int:
        return len(self._por_nome)


# -- resolvedor próprio do motor -------------------------------------------------------------

def servidores_dns_sistema() -> list[str]:
    """Servidores DNS configurados no sistema, sem caches locais (systemd-resolved, Dnscache)."""
    if platform.system() == "Windows":
        return _servidores_windows()
    return _servidores_linux()


def _servidores_linux() -> list[str]:
    for caminho in ("/etc/resolv.conf", "/run/systemd/resolve/resolv.conf"):
        servidores = _ler_resolv_conf(caminho)
        # 127.0.0.53 é o stub do systemd-resolved; o arquivo de /run lista os servidores reais.
        servidores = [s for s in servidores if not ipaddress.ip_address(s).is_loopback]
        if servidores:
            return servidores
    return []


def _ler_resolv_conf(caminho: str) -> list[str]:
    try:
        linhas = Path(caminho).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    servidores = []
    for linha in linhas:
        partes = linha.split()
        if len(partes) >= 2 and partes[0] == "nameserver":
            endereco = partes[1].split("%", 1)[0]
            try:
                ipaddress.ip_address(endereco)
            except ValueError:
                continue
            servidores.append(endereco)
    return servidores


def _servidores_windows() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    servidores: list[str] = []
    for chave_base in (r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces",
                       r"SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters\Interfaces"):
        try:
            interfaces = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, chave_base)
        except OSError:
            continue
        with interfaces:
            indice = 0
            while True:
                try:
                    nome = winreg.EnumKey(interfaces, indice)
                except OSError:
                    break
                indice += 1
                try:
                    with winreg.OpenKey(interfaces, nome) as interface:
                        for valor in ("NameServer", "DhcpNameServer"):
                            try:
                                texto, _ = winreg.QueryValueEx(interface, valor)
                            except OSError:
                                continue
                            for endereco in str(texto).replace(",", " ").split():
                                try:
                                    ip = ipaddress.ip_address(endereco)
                                except ValueError:
                                    continue
                                if not ip.is_loopback and not ip.is_link_local and endereco not in servidores:
                                    servidores.append(endereco)
                except OSError:
                    continue
    # IPv4 primeiro: os servidores IPv6 de site local costumam ser os menos confiáveis.
    return sorted(servidores, key=lambda s: ":" in s)


class ResolvedorReal:
    """Consulta servidores DNS diretamente, sem passar pelo cache do sistema."""

    def __init__(self, servidores: list[str], timeout: float = 2.0, validade_cache: float = 60.0):
        self.servidores = servidores
        self.timeout = timeout
        self.validade_cache = validade_cache
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._proximo_id = 1

    def _novo_id(self) -> int:
        self._proximo_id = (self._proximo_id % 0xFFFF) + 1
        return self._proximo_id

    async def consultar(self, consulta: bytes) -> Optional[bytes]:
        """Envia a consulta crua ao primeiro servidor que responder."""
        loop = asyncio.get_running_loop()
        for texto in self.servidores:
            servidor, porta = dividir_alvo(texto)
            porta = int(porta) if porta.isdigit() else 53
            familia = socket.AF_INET6 if ":" in servidor else socket.AF_INET
            sock = socket.socket(familia, socket.SOCK_DGRAM)
            porta_local = 0
            try:
                sock.setblocking(False)
                sock.bind(("::" if familia == socket.AF_INET6 else "0.0.0.0", 0))
                porta_local = sock.getsockname()[1]
                rede.reservar_porta(porta_local)
                await loop.sock_sendto(sock, consulta, (servidor, porta))
                fim = loop.time() + self.timeout
                while True:
                    dados, _origem = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096),
                                                            timeout=max(fim - loop.time(), 0.01))
                    if dados[:2] == consulta[:2]:
                        return dados
            except (OSError, asyncio.TimeoutError):
                continue
            finally:
                if porta_local:
                    rede.liberar_porta(porta_local)
                sock.close()
        return None

    async def resolver(self, nome: str) -> list[str]:
        """Endereços IPv4 (ou IPv6, se não houver IPv4) de `nome`."""
        nome = nome.lower().rstrip(".")
        try:
            ipaddress.ip_address(nome)
            return [nome]
        except ValueError:
            pass
        if nome == "localhost":
            return ["127.0.0.1"]
        agora = time.monotonic()
        em_cache = self._cache.get(nome)
        if em_cache and agora - em_cache[0] < self.validade_cache:
            return em_cache[1]
        enderecos: list[str] = []
        for tipo in (TIPO_A, TIPO_AAAA):
            try:
                consulta = montar_consulta(nome, tipo, self._novo_id())
            except UnicodeError:
                return []
            resposta = await self.consultar(consulta)
            enderecos = ler_enderecos(resposta) if resposta else []
            if enderecos:
                break
        if enderecos:
            self._cache[nome] = (agora, enderecos)
        return enderecos


# -- servidor DNS local --------------------------------------------------------------------

def interpretar_excecoes(texto: str) -> list[str]:
    return [p.strip() for p in texto.replace(",", ";").replace("\n", ";").split(";") if p.strip()]


class ServidorDns:
    """Responde consultas com IPs falsos. Nomes nas exceções vão ao DNS real.

    `bloquear_outros`: consultas que não são de endereço (MX, TXT, SRV...) recebem resposta vazia
    em vez de irem ao DNS real, para que nada vaze."""

    def __init__(self, tabela: TabelaIpsFalsos, real: ResolvedorReal, excecoes: list[str],
                 bloquear_outros: bool = False):
        self.tabela = tabela
        self.real = real
        self.excecoes = excecoes
        self.bloquear_outros = bloquear_outros
        self.consultas = 0
        self._transportes: list[asyncio.DatagramTransport] = []
        self._tarefas: set[asyncio.Task] = set()

    def eh_excecao(self, nome: str) -> bool:
        return any(padrao_corresponde(padrao, nome, None) for padrao in self.excecoes)

    async def responder(self, dados: bytes) -> Optional[bytes]:
        consulta = ler_consulta(dados)
        if consulta is None:
            return None
        self.consultas += 1
        if consulta.classe != CLASSE_IN or self.eh_excecao(consulta.nome):
            return await self.real.consultar(dados) or montar_resposta(consulta, RCODE_FALHA)
        if consulta.tipo == TIPO_A:
            ip = self.tabela.ip_para(consulta.nome)
            return montar_resposta(consulta, respostas=[(TIPO_A, ipaddress.IPv4Address(ip).packed)])
        if consulta.tipo in (TIPO_AAAA, TIPO_HTTPS, TIPO_SVCB) or self.bloquear_outros:
            # Sem resposta de endereço IPv6 o programa usa o IPv4 falso; HTTPS/SVCB poderiam
            # trazer IPs reais como dica.
            return montar_resposta(consulta)
        return await self.real.consultar(dados) or montar_resposta(consulta, RCODE_FALHA)

    async def iniciar(self, porta: int) -> None:
        loop = asyncio.get_running_loop()
        for endereco in ("127.0.0.1", "::1"):
            try:
                transporte, _ = await loop.create_datagram_endpoint(
                    lambda: _ProtocoloDns(self), local_addr=(endereco, porta))
            except OSError:
                if endereco == "127.0.0.1":
                    raise
                continue  # sem IPv6 na máquina
            self._transportes.append(transporte)

    def parar(self) -> None:
        for transporte in self._transportes:
            transporte.close()
        self._transportes.clear()
        for tarefa in self._tarefas:
            tarefa.cancel()


class _ProtocoloDns(asyncio.DatagramProtocol):
    def __init__(self, servidor: ServidorDns):
        self.servidor = servidor
        self.transporte: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport) -> None:
        self.transporte = transport

    def datagram_received(self, dados: bytes, endereco) -> None:
        tarefa = asyncio.ensure_future(self._responder(dados, endereco))
        self.servidor._tarefas.add(tarefa)
        tarefa.add_done_callback(self.servidor._tarefas.discard)

    async def _responder(self, dados: bytes, endereco) -> None:
        resposta = await self.servidor.responder(dados)
        if resposta is not None and self.transporte is not None and not self.transporte.is_closing():
            self.transporte.sendto(resposta, endereco)

    def error_received(self, exc) -> None:
        pass
