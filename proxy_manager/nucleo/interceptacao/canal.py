"""Canais de bytes usados pela interceptação: o TCP já aberto pelo motor e o TLS por cima dele.

O TLS usa `ssl.MemoryBIO` em vez de `start_tls()`: assim os bytes que o motor já leu (o
ClientHello lido para descobrir o SNI, no modo transparente) entram no handshake normalmente."""
from __future__ import annotations

import asyncio
import ssl
from typing import Callable, Optional

TAMANHO_BLOCO = 65536

# Chamado com a quantidade de bytes lidos da rede, para os contadores de tráfego do motor.
Contador = Callable[[int], None]


class ErroCanal(Exception):
    pass


class Canal:
    """Leitura com buffer (para separar cabeçalhos HTTP) sobre uma fonte de bytes qualquer."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    async def _ler_bruto(self) -> bytes:
        raise NotImplementedError

    async def escrever(self, dados: bytes) -> None:
        raise NotImplementedError

    def fechar(self) -> None:
        raise NotImplementedError

    def devolver(self, dados: bytes) -> None:
        """Põe `dados` de volta no início do buffer, para a próxima leitura."""
        self._buffer[:0] = dados

    async def ler(self, n: int = TAMANHO_BLOCO) -> bytes:
        """Até `n` bytes; b"" no fim da conexão."""
        if self._buffer:
            dados = bytes(self._buffer[:n])
            del self._buffer[:n]
            return dados
        dados = await self._ler_bruto()
        if len(dados) > n:
            self._buffer.extend(dados[n:])
            dados = dados[:n]
        return dados

    async def ler_ate(self, separador: bytes, limite: int) -> bytes:
        """Lê até `separador` (incluído). Fim da conexão no meio ou mais de `limite` bytes: ErroCanal."""
        inicio_busca = 0
        while True:
            posicao = self._buffer.find(separador, inicio_busca)
            if posicao >= 0:
                fim = posicao + len(separador)
                dados = bytes(self._buffer[:fim])
                del self._buffer[:fim]
                return dados
            if len(self._buffer) > limite:
                raise ErroCanal(f"mais de {limite} bytes sem encontrar o fim do bloco")
            inicio_busca = max(len(self._buffer) - len(separador) + 1, 0)
            bloco = await self._ler_bruto()
            if not bloco:
                raise ErroCanal("a conexão terminou no meio da mensagem")
            self._buffer.extend(bloco)

    async def ler_exatamente(self, n: int) -> bytes:
        partes = []
        while n > 0:
            bloco = await self.ler(n)
            if not bloco:
                raise ErroCanal("a conexão terminou no meio da mensagem")
            partes.append(bloco)
            n -= len(bloco)
        return b"".join(partes)


class CanalPlano(Canal):
    def __init__(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter,
                 prefixo: bytes = b"", contar: Optional[Contador] = None):
        super().__init__()
        self.leitor = leitor
        self.escritor = escritor
        self.lidos = 0
        self._contar = contar
        self._buffer.extend(prefixo)

    async def _ler_bruto(self) -> bytes:
        try:
            dados = await self.leitor.read(TAMANHO_BLOCO)
        except (ConnectionResetError, BrokenPipeError, OSError):
            return b""
        self.lidos += len(dados)
        if dados and self._contar is not None:
            self._contar(len(dados))
        return dados

    async def escrever(self, dados: bytes) -> None:
        if not dados:
            return
        self.escritor.write(dados)
        await self.escritor.drain()

    def fechar(self) -> None:
        try:
            self.escritor.close()
        except Exception:
            pass


class CanalTls(Canal):
    def __init__(self, base: Canal, contexto: ssl.SSLContext, lado_servidor: bool,
                 nome_servidor: Optional[str] = None):
        super().__init__()
        self.base = base
        self._entrada = ssl.MemoryBIO()
        self._saida = ssl.MemoryBIO()
        self._ssl = contexto.wrap_bio(self._entrada, self._saida, server_side=lado_servidor,
                                      server_hostname=nome_servidor)

    async def _enviar_pendente(self) -> None:
        pendente = self._saida.read()
        if pendente:
            await self.base.escrever(pendente)

    async def _receber(self) -> bool:
        dados = await self.base.ler()
        if not dados:
            self._entrada.write_eof()
            return False
        self._entrada.write(dados)
        return True

    async def handshake(self, timeout: float = 15.0) -> None:
        async def negociar() -> None:
            while True:
                try:
                    self._ssl.do_handshake()
                    break
                except ssl.SSLWantReadError:
                    await self._enviar_pendente()
                    if not await self._receber():
                        raise ErroCanal("a conexão terminou durante o handshake TLS")
            await self._enviar_pendente()
        await asyncio.wait_for(negociar(), timeout=timeout)

    @property
    def alpn(self) -> Optional[str]:
        return self._ssl.selected_alpn_protocol()

    async def _ler_bruto(self) -> bytes:
        while True:
            try:
                return self._ssl.read(TAMANHO_BLOCO)
            except ssl.SSLWantReadError:
                await self._enviar_pendente()
                if not await self._receber():
                    try:
                        return self._ssl.read(TAMANHO_BLOCO)
                    except ssl.SSLError:
                        return b""
            except (ssl.SSLZeroReturnError, ssl.SSLEOFError):
                return b""
            except ssl.SSLError:
                return b""

    async def escrever(self, dados: bytes) -> None:
        if not dados:
            return
        self._ssl.write(dados)
        await self._enviar_pendente()

    def fechar(self) -> None:
        try:
            self._ssl.unwrap()
            pendente = self._saida.read()
            if pendente and isinstance(self.base, CanalPlano):
                self.base.escritor.write(pendente)  # close_notify, sem esperar
        except (ssl.SSLError, OSError, ValueError):
            pass
        self.base.fechar()


async def primeiro_a_falar(a: Canal, b: Canal) -> tuple[Canal, bytes]:
    """Espera o primeiro dos dois lados mandar algo. O outro não perde nada: cancelar uma
    leitura pendente não consome bytes."""
    tarefas = {asyncio.ensure_future(a.ler()): a, asyncio.ensure_future(b.ler()): b}
    try:
        prontas, pendentes = await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        for tarefa in tarefas:
            tarefa.cancel()
        raise
    for tarefa in pendentes:
        tarefa.cancel()
    await asyncio.gather(*pendentes, return_exceptions=True)
    # Se as duas terminaram juntas, a segunda leitura volta para o buffer do seu canal.
    ordem = [t for t in tarefas if t in prontas]
    escolhida = ordem[0]
    for tarefa in ordem[1:]:
        tarefas[tarefa].devolver(tarefa.result())
    return tarefas[escolhida], escolhida.result()


async def repassar_bidirecional(a: Canal, b: Canal) -> None:
    """Copia bytes nos dois sentidos até um lado fechar; então fecha os dois."""
    async def repassar(de: Canal, para: Canal) -> None:
        try:
            while bloco := await de.ler():
                await para.escrever(bloco)
        except (ConnectionResetError, BrokenPipeError, OSError, ssl.SSLError):
            pass
        finally:
            para.fechar()

    await asyncio.gather(repassar(a, b), repassar(b, a))
