"""Interceptação: HTTP/1.x, fluxos e o motor decifrando HTTP e HTTPS de verdade em 127.0.0.1."""
import asyncio
import gzip
import ssl

from proxy_manager.nucleo.configuracao import Configuracao, Preferencias
from proxy_manager.nucleo.interceptacao import http1
from proxy_manager.nucleo.interceptacao.canal import Canal
from proxy_manager.nucleo.interceptacao.certificados import AutoridadeCertificadora
from proxy_manager.nucleo.interceptacao.fluxos import (
    ArmazemFluxos, Fluxo, comando_curl, decodificar_corpo, exportar_har,
)
from proxy_manager.nucleo.interceptacao.interceptador import host_casa
from proxy_manager.nucleo.motor import MotorProxy
from proxy_manager.nucleo.registro import Registro
from proxy_manager.nucleo.socks5 import conectar_via_socks5

PORTA_SOCKS = 58680
PORTA_HTTP = 58681
PORTA_PAC = 58690


class CanalMemoria(Canal):
    def __init__(self, blocos: list[bytes]):
        super().__init__()
        self.blocos = list(blocos)
        self.escrito = bytearray()

    async def _ler_bruto(self) -> bytes:
        return self.blocos.pop(0) if self.blocos else b""

    async def escrever(self, dados: bytes) -> None:
        self.escrito.extend(dados)

    def fechar(self) -> None:
        pass


# -- HTTP/1.x ------------------------------------------------------------------------------------

def test_requisicao_chunked_em_pedacos_e_repassada_intacta():
    async def cenario():
        bruto = (b"POST /api HTTP/1.1\r\nHost: exemplo.com\r\nTransfer-Encoding: chunked\r\n\r\n"
                 b"5\r\nhello\r\n6;ext=1\r\n world\r\n0\r\nX-Fim: 1\r\n\r\n")
        # Quebrado em pedaços de 7 bytes para exercitar o buffer.
        origem = CanalMemoria([bruto[i:i + 7] for i in range(0, len(bruto), 7)])
        destino = CanalMemoria([])
        req = await http1.ler_requisicao(origem)
        assert req.metodo == "POST" and req.cabecalho.valor("host") == "exemplo.com"
        modo, tamanho = http1.enquadramento_requisicao(req)
        captura = http1.Captura(1024)
        await http1.repassar_corpo(origem, destino, modo, tamanho, captura)
        assert bytes(captura.dados) == b"hello world"
        assert bytes(destino.escrito) == bruto.split(b"\r\n\r\n", 1)[1]
    asyncio.run(cenario())


def test_captura_respeita_o_limite_e_conta_o_total():
    captura = http1.Captura(4)
    captura.adicionar(b"abc")
    captura.adicionar(b"defgh")
    assert bytes(captura.dados) == b"abcd" and captura.tamanho == 8 and captura.truncada


def test_enquadramento_da_resposta():
    def resp(status: int, *campos) -> http1.Resposta:
        return http1.Resposta(http1.Cabecalho("HTTP/1.1", list(campos)), "HTTP/1.1", status, "")
    assert http1.enquadramento_resposta(resp(200, ("Content-Length", "3")), "GET") == ("tamanho", 3)
    assert http1.enquadramento_resposta(resp(200, ("Content-Length", "3")), "HEAD") == ("nenhum", 0)
    assert http1.enquadramento_resposta(resp(304), "GET") == ("nenhum", 0)
    assert http1.enquadramento_resposta(resp(200), "GET") == ("ate_fechar", 0)


def test_parece_http():
    assert http1.parece_http(b"GET / HTTP/1.1\r\n")
    assert not http1.parece_http(b"\x16\x03\x01\x02\x00")
    assert not http1.parece_http(b"SSH-2.0-OpenSSH")


def test_filtro_de_hosts():
    assert host_casa("api.exemplo.com", "*.exemplo.com")
    assert host_casa("exemplo.com", "*.exemplo.com")
    assert not host_casa("exemplo.com.br", "*.exemplo.com")
    assert host_casa("Exemplo.COM.", "exemplo.com")


# -- fluxos --------------------------------------------------------------------------------------

def test_armazem_descarta_os_mais_antigos():
    armazem = ArmazemFluxos(max_fluxos=2)
    eventos = []
    armazem.inscrever(lambda evento, fluxo: eventos.append(evento))
    fluxos = [armazem.adicionar(Fluxo(metodo="GET", caminho=f"/{i}")) for i in range(3)]
    assert [f.caminho for f in armazem.todos()] == ["/1", "/2"]
    armazem.atualizar(fluxos[2], status=200)
    assert eventos == ["novo", "novo", "novo", "atualizado"]


def test_fluxo_ida_e_volta_pelo_dicionario():
    fluxo = Fluxo(metodo="POST", host="exemplo.com", caminho="/x", corpo_requisicao=b"\x00\xffbin",
                  cabecalhos_requisicao=[("A", "1")])
    copia = Fluxo.de_dict(fluxo.para_dict())
    assert copia == fluxo


def test_decodificar_gzip_e_desconhecido():
    assert decodificar_corpo(gzip.compress(b"ola"), "gzip")[0] == b"ola"
    corpo, nota = decodificar_corpo(b"xx", "br")
    assert corpo == b"xx" and "br" in nota


def test_curl_e_har():
    fluxo = Fluxo(esquema="https", metodo="POST", host="exemplo.com", porta=443, caminho="/a?b=1",
                  cabecalhos_requisicao=[("Host", "exemplo.com"), ("X-Teste", "sim")],
                  corpo_requisicao=b'{"a": 1}', status=201, motivo="Created",
                  cabecalhos_resposta=[("Content-Type", "application/json")], corpo_resposta=b"{}")
    curl = comando_curl(fluxo)
    assert "https://exemplo.com/a?b=1" in curl and "X-Teste: sim" in curl and "--data-raw" in curl
    har = exportar_har([fluxo])
    entrada = har["log"]["entries"][0]
    assert entrada["request"]["queryString"] == [{"name": "b", "value": "1"}]
    assert entrada["response"]["status"] == 201 and entrada["response"]["content"]["text"] == "{}"


# -- motor com interceptação -------------------------------------------------------------------

async def _servidor_eco(ssl_contexto=None, saudacao: bytes = b""):
    """Responde cada requisição (com keep-alive) em chunked, ecoando o corpo recebido."""
    async def atender(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            if saudacao:
                escritor.write(saudacao)
                await escritor.drain()
                escritor.write(await leitor.readline())
                await escritor.drain()
                return
            while True:
                cabecalho = await leitor.readuntil(b"\r\n\r\n")
                tamanho = 0
                for linha in cabecalho.split(b"\r\n"):
                    if linha.lower().startswith(b"content-length:"):
                        tamanho = int(linha.split(b":")[1])
                corpo = await leitor.readexactly(tamanho) if tamanho else b""
                resposta = b"eco:" + corpo
                escritor.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n"
                               + f"{len(resposta):x}\r\n".encode() + resposta + b"\r\n0\r\n\r\n")
                await escritor.drain()
                if b"connection: close" in cabecalho.lower():
                    break
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            escritor.close()

    servidor = await asyncio.start_server(atender, "127.0.0.1", 0, ssl=ssl_contexto)
    return servidor, servidor.sockets[0].getsockname()[1]


def _criar_motor(**preferencias) -> tuple[MotorProxy, ArmazemFluxos]:
    prefs = Preferencias(porta_socks=PORTA_SOCKS, porta_http=PORTA_HTTP, porta_pac=PORTA_PAC,
                         acao_padrao="block", interceptacao_ativa=True, **preferencias)
    armazem = ArmazemFluxos()
    motor = MotorProxy(Configuracao(texto_regras="127.0.0.1 +direct\n", preferencias=prefs),
                       Registro(dias_retencao=1), armazem)
    return motor, armazem


async def _esperar_fluxos(armazem: ArmazemFluxos, quantidade: int, estado: str = "concluido") -> list[Fluxo]:
    for _ in range(50):
        fluxos = [f for f in armazem.todos() if f.estado == estado]
        if len(fluxos) >= quantidade:
            return fluxos
        await asyncio.sleep(0.05)
    raise AssertionError(f"esperava {quantidade} fluxos '{estado}': {armazem.todos()}")


def test_http_pelo_proxy_explicito_registra_dois_fluxos_na_mesma_conexao():
    async def cenario():
        servidor, porta = await _servidor_eco()
        motor, armazem = _criar_motor()
        motor.iniciar()
        try:
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            escritor.write(f"POST http://127.0.0.1:{porta}/um HTTP/1.1\r\nHost: 127.0.0.1:{porta}\r\n"
                           f"Proxy-Connection: keep-alive\r\nContent-Length: 3\r\n\r\nabc".encode())
            await escritor.drain()
            assert (await leitor.readuntil(b"0\r\n\r\n")).endswith(b"eco:abc\r\n0\r\n\r\n")
            escritor.write(f"GET /dois HTTP/1.1\r\nHost: 127.0.0.1:{porta}\r\nConnection: close\r\n\r\n".encode())
            await escritor.drain()
            assert b"eco:" in await leitor.read(-1)

            primeiro, segundo = await _esperar_fluxos(armazem, 2)
            assert (primeiro.metodo, primeiro.caminho, primeiro.status) == ("POST", "/um", 200)
            assert primeiro.corpo_requisicao == b"abc" and primeiro.corpo_resposta == b"eco:abc"
            assert primeiro.url == f"http://127.0.0.1:{porta}/um"
            # O cabeçalho do proxy local não chega ao servidor nem aparece no fluxo.
            assert primeiro.cabecalho_requisicao("Proxy-Connection") is None
            assert segundo.caminho == "/dois" and segundo.id_conexao == primeiro.id_conexao
        finally:
            motor.parar()
            servidor.close()
    asyncio.run(cenario())


def test_https_decifrado_com_a_ca_do_veredo(tmp_path):
    async def cenario():
        ca_destino = AutoridadeCertificadora(tmp_path)
        servidor, porta = await _servidor_eco(ca_destino.contexto_servidor("localhost"))
        # O destino usa uma CA de teste, que o motor não conhece.
        motor, armazem = _criar_motor(interceptacao_verificar_destino=False)
        motor.iniciar()
        try:
            contexto = ssl.create_default_context(cadata=motor.certificado_ca().decode())
            leitor, escritor = await asyncio.open_connection("127.0.0.1", PORTA_HTTP)
            escritor.write(f"CONNECT 127.0.0.1:{porta} HTTP/1.1\r\nHost: 127.0.0.1:{porta}\r\n\r\n".encode())
            await escritor.drain()
            assert (await leitor.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
            await escritor.start_tls(contexto, server_hostname="localhost")
            escritor.write(b"PUT /seguro HTTP/1.1\r\nHost: localhost\r\nContent-Length: 6\r\n"
                           b"Accept-Encoding: gzip, br\r\nConnection: close\r\n\r\nsecret")
            await escritor.drain()
            assert b"eco:secret" in await leitor.read(-1)

            [fluxo] = await _esperar_fluxos(armazem, 1)
            assert (fluxo.esquema, fluxo.host, fluxo.metodo) == ("https", "localhost", "PUT")
            assert fluxo.corpo_requisicao == b"secret" and fluxo.corpo_resposta == b"eco:secret"
            assert fluxo.cabecalho_requisicao("Accept-Encoding") == "gzip"
        finally:
            motor.parar()
            servidor.close()
    asyncio.run(cenario())


def test_aplicativo_que_recusa_o_certificado_vira_fluxo_com_erro(tmp_path):
    async def cenario():
        servidor, porta = await _servidor_eco(AutoridadeCertificadora(tmp_path).contexto_servidor("localhost"))
        motor, armazem = _criar_motor(interceptacao_verificar_destino=False)
        motor.iniciar()
        try:
            leitor, escritor = await conectar_via_socks5("127.0.0.1", PORTA_SOCKS, "127.0.0.1", porta)
            try:
                await escritor.start_tls(ssl.create_default_context(), server_hostname="localhost")
                raise AssertionError("o certificado não devia ser aceito sem a CA instalada")
            except ssl.SSLCertVerificationError:
                pass
            [fluxo] = await _esperar_fluxos(armazem, 1, estado="erro")
            assert fluxo.esquema == "https" and "recusou o certificado" in fluxo.erro
        finally:
            motor.parar()
            servidor.close()
    asyncio.run(cenario())


def test_protocolo_em_que_o_servidor_fala_primeiro_passa_sem_interceptar():
    async def cenario():
        servidor, porta = await _servidor_eco(saudacao=b"220 ola\r\n")
        motor, armazem = _criar_motor()
        motor.iniciar()
        try:
            leitor, escritor = await conectar_via_socks5("127.0.0.1", PORTA_SOCKS, "127.0.0.1", porta)
            assert await leitor.readline() == b"220 ola\r\n"
            escritor.write(b"QUIT\r\n")
            await escritor.drain()
            assert await leitor.readline() == b"QUIT\r\n"
            assert armazem.todos() == []
        finally:
            motor.parar()
            servidor.close()
    asyncio.run(cenario())


def test_host_nas_excecoes_nao_e_interceptado():
    async def cenario():
        servidor, porta = await _servidor_eco()
        motor, armazem = _criar_motor(interceptacao_excecoes="127.0.0.1")
        motor.iniciar()
        try:
            leitor, escritor = await conectar_via_socks5("127.0.0.1", PORTA_SOCKS, "127.0.0.1", porta)
            escritor.write(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            await escritor.drain()
            assert b"eco:" in await leitor.read(-1)
            await asyncio.sleep(0.2)
            assert armazem.todos() == []
        finally:
            motor.parar()
            servidor.close()
    asyncio.run(cenario())
