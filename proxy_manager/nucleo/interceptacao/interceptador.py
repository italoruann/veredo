"""Atende uma conexão já liberada pelas regras (direta ou por proxy) no lugar do repasse cego
de bytes: decifra o TLS, lê cada requisição e resposta HTTP/1.x, repassa e registra o fluxo.

Quem fala primeiro decide o que fazer: o servidor (SMTP, SSH...) ou bytes que não são TLS nem
HTTP fazem a conexão voltar ao repasse comum. Hosts fora do filtro também. Assim ligar a
interceptação nunca quebra protocolos que ela não entende."""
from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..configuracao import Preferencias
from ..transparente import inspecao
from . import http1
from .canal import Canal, CanalPlano, CanalTls, ErroCanal, primeiro_a_falar, repassar_bidirecional
from .certificados import AutoridadeCertificadora, contexto_cliente
from .fluxos import ArmazemFluxos, Fluxo

# Chamado com (bytes, enviado) para os contadores de tráfego do motor.
ContadorTrafego = Callable[[int, bool], None]
TIMEOUT_REQUISICAO_APOS_FALHA = 10.0
ERROS_CONEXAO = (ErroCanal, http1.ErroHttp, OSError, ssl.SSLError, asyncio.TimeoutError,
                 asyncio.IncompleteReadError)


def interpretar_padroes(texto: str) -> list[str]:
    return [p.strip().lower() for p in texto.replace(",", ";").replace("\n", ";").split(";") if p.strip()]


def host_casa(host: str, padrao: str) -> bool:
    """`*.exemplo.com` vale para exemplo.com e qualquer subdomínio; `*` e `?` como no shell."""
    host = host.lower().rstrip(".")
    if padrao.startswith("*.") and host == padrao[2:]:
        return True
    return fnmatch.fnmatchcase(host, padrao)


def _eh_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@dataclass
class _Conexao:
    host: str
    porta: int
    processo: str
    id_conexao: str


class Interceptador:
    def __init__(self, autoridade: AutoridadeCertificadora, armazem: ArmazemFluxos,
                 preferencias: Callable[[], Preferencias]):
        self.autoridade = autoridade
        self.armazem = armazem
        self._preferencias = preferencias

    @property
    def ativo(self) -> bool:
        return self._preferencias().interceptacao_ativa

    def deve_interceptar(self, host: str) -> bool:
        prefs = self._preferencias()
        if not prefs.interceptacao_ativa:
            return False
        if any(host_casa(host, p) for p in interpretar_padroes(prefs.interceptacao_excecoes)):
            return False
        hosts = interpretar_padroes(prefs.interceptacao_hosts)
        return not hosts or any(host_casa(host, p) for p in hosts)

    async def atender(self, leitor_cliente: asyncio.StreamReader, escritor_cliente: asyncio.StreamWriter,
                      leitor_destino: asyncio.StreamReader, escritor_destino: asyncio.StreamWriter, *,
                      host: str, porta: int, processo: str = "", id_conexao: str = "",
                      prefixo_cliente: bytes = b"", contar: Optional[ContadorTrafego] = None) -> tuple[int, int]:
        """Retorna (bytes lidos do aplicativo, bytes lidos do destino), sem contar `prefixo_cliente`."""
        cliente = CanalPlano(leitor_cliente, escritor_cliente, prefixo_cliente,
                             (lambda n: contar(n, True)) if contar else None)
        destino = CanalPlano(leitor_destino, escritor_destino, b"",
                             (lambda n: contar(n, False)) if contar else None)
        try:
            await self._atender(cliente, destino, _Conexao(host, porta, processo, id_conexao))
        except ERROS_CONEXAO:
            pass
        finally:
            cliente.fechar()
            destino.fechar()
        return cliente.lidos, destino.lidos

    async def _atender(self, cliente: CanalPlano, destino: CanalPlano, cx: _Conexao) -> None:
        quem, dados = await primeiro_a_falar(cliente, destino)
        quem.devolver(dados)
        if not dados:
            return
        if quem is cliente and dados[0] == 0x16:
            nome = inspecao.extrair_sni(dados) or cx.host
            if self.deve_interceptar(nome):
                await self._atender_tls(cliente, destino, cx, nome)
                return
        elif quem is cliente and http1.parece_http(dados) and self.deve_interceptar(cx.host):
            await self._sessao(cliente, destino, cx, "http")
            return
        await repassar_bidirecional(cliente, destino)

    # -- TLS --------------------------------------------------------------------------------

    async def _atender_tls(self, cliente: Canal, destino: Canal, cx: _Conexao, nome: str) -> None:
        cx.host = nome
        contexto = await asyncio.to_thread(self.autoridade.contexto_servidor, nome)
        tls_cliente = CanalTls(cliente, contexto, lado_servidor=True)
        try:
            await tls_cliente.handshake()
        except ERROS_CONEXAO as exc:
            self.armazem.adicionar(Fluxo(
                id_conexao=cx.id_conexao, processo=cx.processo, esquema="https", host=nome, porta=cx.porta,
                estado="erro",
                erro=f"o aplicativo recusou o certificado do Veredo ({_descrever(exc)}). Instale o "
                     "certificado da CA do Veredo como confiável ou coloque este host nas exceções "
                     "da interceptação (aplicativos com certificado fixo nunca aceitam)."))
            return

        nome_sni = None if _eh_ip(nome) else nome
        verificar = self._preferencias().interceptacao_verificar_destino
        tls_destino = CanalTls(destino, contexto_cliente(verificar, nome_sni is not None),
                               lado_servidor=False, nome_servidor=nome_sni)
        try:
            await tls_destino.handshake()
        except ERROS_CONEXAO as exc:
            await self._recusar_por_falha_destino(tls_cliente, cx, f"falha no TLS com o destino: {_descrever(exc)}")
            return
        try:
            await self._sessao(tls_cliente, tls_destino, cx, "https")
        finally:
            tls_cliente.fechar()
            tls_destino.fechar()

    async def _recusar_por_falha_destino(self, cliente: Canal, cx: _Conexao, erro: str) -> None:
        """O aplicativo já confia no TLS do Veredo: recebe um 502 com o motivo, e o fluxo fica no log."""
        fluxo = Fluxo(id_conexao=cx.id_conexao, processo=cx.processo, esquema="https", host=cx.host,
                      porta=cx.porta, estado="erro", erro=erro)
        try:
            req = await asyncio.wait_for(http1.ler_requisicao(cliente), timeout=TIMEOUT_REQUISICAO_APOS_FALHA)
        except ERROS_CONEXAO:
            req = None
        if req is not None:
            fluxo.metodo, fluxo.caminho, fluxo.versao_http = req.metodo, req.caminho_e_host()[0], req.versao
            fluxo.cabecalhos_requisicao = list(req.cabecalho.campos)
            fluxo.status, fluxo.motivo = 502, "Bad Gateway"
            try:
                await cliente.escrever(http1.resposta_erro(502, "Bad Gateway", erro))
            except ERROS_CONEXAO:
                pass
        self.armazem.adicionar(fluxo)

    # -- HTTP -------------------------------------------------------------------------------

    async def _sessao(self, cliente: Canal, destino: Canal, cx: _Conexao, esquema: str) -> None:
        # Por dentro do TLS o protocolo pode não ser HTTP (IMAPS, por exemplo): de novo, quem
        # fala primeiro decide.
        quem, dados = await primeiro_a_falar(cliente, destino)
        quem.devolver(dados)
        if not dados:
            return
        if quem is destino or not http1.parece_http(dados):
            await repassar_bidirecional(cliente, destino)
            return
        while await self._trocar(cliente, destino, cx, esquema):
            pass

    async def _trocar(self, cliente: Canal, destino: Canal, cx: _Conexao, esquema: str) -> bool:
        """Uma requisição e a sua resposta. Retorna se a conexão continua aberta para a próxima."""
        req = await http1.ler_requisicao(cliente)
        if req is None:
            return False
        inicio = time.monotonic()
        caminho, host_absoluto, porta_absoluta = req.caminho_e_host()
        if host_absoluto:
            req.atualizar_primeira_linha(caminho)  # forma absoluta (proxy explícito) -> forma de origem
        for nome in ("Proxy-Connection", "Proxy-Authorization"):
            req.cabecalho.remover(nome)
        _sem_brotli(req.cabecalho)
        espera_continue = (req.cabecalho.valor("Expect") or "").lower() == "100-continue"
        if espera_continue:
            # O próprio Veredo libera o envio do corpo; o servidor recebe a requisição inteira.
            req.cabecalho.remover("Expect")

        fluxo = self.armazem.adicionar(Fluxo(
            id_conexao=cx.id_conexao, processo=cx.processo, esquema=esquema, metodo=req.metodo,
            host=host_absoluto or _host_do_cabecalho(req.cabecalho.valor("Host")) or cx.host,
            porta=porta_absoluta or cx.porta, caminho=caminho, versao_http=req.versao,
            cabecalhos_requisicao=list(req.cabecalho.campos)))
        limite = max(self._preferencias().interceptacao_limite_corpo_kb, 0) * 1024
        captura_req = http1.Captura(limite)
        captura_resp = http1.Captura(limite)

        def concluir(**mudancas) -> None:
            self.armazem.atualizar(fluxo, corpo_requisicao=bytes(captura_req.dados),
                                   tamanho_requisicao=captura_req.tamanho,
                                   corpo_resposta=bytes(captura_resp.dados), tamanho_resposta=captura_resp.tamanho,
                                   duracao_ms=int((time.monotonic() - inicio) * 1000), **mudancas)

        try:
            modo, tamanho = http1.enquadramento_requisicao(req)
            await destino.escrever(req.cabecalho.bruto())
            if espera_continue and modo != "nenhum":
                await cliente.escrever(b"HTTP/1.1 100 Continue\r\n\r\n")
            await http1.repassar_corpo(cliente, destino, modo, tamanho, captura_req)
            self.armazem.atualizar(fluxo, corpo_requisicao=bytes(captura_req.dados),
                                   tamanho_requisicao=captura_req.tamanho)

            resp = await http1.ler_resposta(destino)
            while 100 <= resp.status < 200 and resp.status != 101:
                await cliente.escrever(resp.cabecalho.bruto())  # respostas informativas
                resp = await http1.ler_resposta(destino)
            self.armazem.atualizar(fluxo, status=resp.status, motivo=resp.motivo,
                                   cabecalhos_resposta=list(resp.cabecalho.campos))
            await cliente.escrever(resp.cabecalho.bruto())
            modo_resp, tamanho_resp = http1.enquadramento_resposta(resp, req.metodo)
            await http1.repassar_corpo(destino, cliente, modo_resp, tamanho_resp, captura_resp)
        except ERROS_CONEXAO as exc:
            concluir(estado="erro", erro=_descrever(exc))
            if fluxo.status == 0:
                try:
                    await cliente.escrever(http1.resposta_erro(502, "Bad Gateway", _descrever(exc)))
                except ERROS_CONEXAO:
                    pass
            return False

        if resp.status == 101:
            concluir(estado="concluido", websocket=resp.cabecalho.tem_token("Upgrade", "websocket"))
            await repassar_bidirecional(cliente, destino)
            return False
        concluir(estado="concluido")
        return _mantem_conexao(req, resp, modo_resp)


def _mantem_conexao(req: http1.Requisicao, resp: http1.Resposta, modo_resp: str) -> bool:
    if modo_resp == "ate_fechar":
        return False
    for cabecalho, versao in ((req.cabecalho, req.versao), (resp.cabecalho, resp.versao)):
        if cabecalho.tem_token("Connection", "close"):
            return False
        if versao == "HTTP/1.0" and not cabecalho.tem_token("Connection", "keep-alive"):
            return False
    return True


def _sem_brotli(cabecalho: http1.Cabecalho) -> None:
    """Tira o "br" do Accept-Encoding: o Python não descomprime brotli, e o corpo ficaria
    ilegível no inspetor. O servidor responde com gzip/zstd, que são mostrados normalmente."""
    valor = cabecalho.valor("Accept-Encoding")
    if valor is None:
        return
    restantes = [p.strip() for p in valor.split(",") if p.strip().split(";")[0].strip().lower() != "br"]
    cabecalho.definir("Accept-Encoding", ", ".join(restantes) or "identity")


def _host_do_cabecalho(valor: Optional[str]) -> str:
    if not valor:
        return ""
    if valor.startswith("["):
        return valor[1:].partition("]")[0]
    return valor.rpartition(":")[0] if valor.count(":") == 1 else valor


def _descrever(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "tempo esgotado"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return f"certificado do servidor inválido: {exc.verify_message or exc}"
    return str(exc) or type(exc).__name__
