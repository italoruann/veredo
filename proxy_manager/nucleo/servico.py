"""Modo serviço: o motor roda sem interface, iniciado com o sistema (antes de qualquer login), e a
janela vira um cliente dele por um canal de controle local.

- `--servico` roda o motor em primeiro plano, sem Qt. A configuração (com as senhas, que o
  serviço não consegue ler do cofre do usuário) chega pelo canal e fica num arquivo que só o
  administrador lê.
- O canal escuta em 127.0.0.1:PORTA_CONTROLE e fala JSON, uma linha por mensagem. Cada pedido
  leva o token gravado em `controle.token`, legível pelos usuários da máquina: isso impede que
  páginas da web (que conseguem enviar texto para portas locais) comandem o serviço, mas
  qualquer usuário local pode configurá-lo, como no próprio Proxifier.
- No Windows o serviço é uma tarefa agendada que roda como SYSTEM na inicialização (um script
  Python não responde ao gerenciador de serviços); no Linux, uma unidade do systemd."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import platform
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from .configuracao import NOME_APP, Configuracao
from .interceptacao.fluxos import ArmazemFluxos, Fluxo
from .registro import EntradaLog, Registro

PORTA_CONTROLE = 58098
NOME_TAREFA = "Veredo"
NOME_UNIDADE = "veredo.service"
CAMINHO_UNIDADE = Path("/etc/systemd/system") / NOME_UNIDADE
# Configurações e contadores enviados ao cliente que acompanha o serviço.
INTERVALO_CONTADORES = 1.0
LIMITE_LINHA = 16 * 1024 * 1024


class ErroServico(Exception):
    pass


# -- arquivos do serviço -------------------------------------------------------------------------

def pasta_servico() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / NOME_APP
    return Path("/var/lib") / NOME_APP.lower()


def arquivo_token() -> Path:
    return pasta_servico() / "controle.token"


def arquivo_config_servico() -> Path:
    return pasta_servico() / "servico.json"


def _restringir_ao_administrador(caminho: Path) -> None:
    """O arquivo da configuração tem as senhas dos proxies: só o administrador o lê."""
    if platform.system() == "Windows":
        # SYSTEM (S-1-5-18) e Administradores (S-1-5-32-544), sem herdar a leitura dos Usuários.
        subprocess.run(["icacls", str(caminho), "/inheritance:r", "/grant:r", "*S-1-5-18:F",
                        "*S-1-5-32-544:F"], capture_output=True, timeout=10)
    else:
        os.chmod(caminho, 0o600)


def carregar_config_servico() -> Configuracao:
    try:
        with open(arquivo_config_servico(), "r", encoding="utf-8") as arq:
            return Configuracao.de_dict(json.load(arq))
    except (OSError, json.JSONDecodeError):
        return Configuracao()


def salvar_config_servico(cfg: Configuracao) -> None:
    caminho = arquivo_config_servico()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    with open(temporario, "w", encoding="utf-8") as arq:
        json.dump(cfg.para_dict(), arq, ensure_ascii=False, indent=2)
    _restringir_ao_administrador(temporario)
    os.replace(temporario, caminho)


def _gravar_token(token: str) -> None:
    caminho = arquivo_token()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(token, encoding="ascii")
    if platform.system() != "Windows":
        os.chmod(caminho, 0o644)


def ler_token() -> str:
    try:
        return arquivo_token().read_text(encoding="ascii").strip()
    except OSError:
        return ""


# -- lado do serviço -----------------------------------------------------------------------------

class ServidorControle:
    """Atende a janela: estado, configuração, ligar/desligar e o log ao vivo."""

    def __init__(self, motor, registro: Registro, token: str,
                 salvar: Callable[[Configuracao], None] = salvar_config_servico):
        self.motor = motor
        self.registro = registro
        self.token = token
        self.salvar = salvar
        self.ativo = False
        self.mensagem = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._servidor: Optional[asyncio.AbstractServer] = None
        self._ouvintes: set[asyncio.Queue] = set()
        motor.ao_mudar_estado = self._ao_mudar_estado
        motor.ao_mudar_ip_saida = self._ao_mudar_ip_saida
        registro.inscrever(self._ao_registrar)
        motor.fluxos.inscrever(self._ao_interceptar)

    async def iniciar(self, porta: int = PORTA_CONTROLE) -> None:
        self._loop = asyncio.get_running_loop()
        self._servidor = await asyncio.start_server(self._atender, "127.0.0.1", porta, limit=LIMITE_LINHA)

    async def parar(self) -> None:
        if self._servidor is not None:
            self._servidor.close()
            self._servidor = None
        for fila in list(self._ouvintes):
            fila.put_nowait(None)

    # Callbacks da thread do motor: só repassam ao loop do canal.
    def _difundir(self, mensagem: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._entregar, mensagem)

    def _entregar(self, mensagem: dict) -> None:
        for fila in self._ouvintes:
            if fila.qsize() < 20000:  # cliente lento perde eventos em vez de travar o serviço
                fila.put_nowait(mensagem)

    def _ao_mudar_estado(self, ativo: bool, mensagem: str) -> None:
        self.ativo, self.mensagem = ativo, mensagem
        self._difundir({"tipo": "estado", "ativo": ativo, "mensagem": mensagem})

    def _ao_mudar_ip_saida(self, id_perfil: str, anterior: str, atual: str) -> None:
        self._difundir({"tipo": "ip_saida", "id": id_perfil, "anterior": anterior, "atual": atual})

    def _ao_registrar(self, evento: str, entrada: EntradaLog) -> None:
        self._difundir({"tipo": "log", "evento": evento, "entrada": asdict(entrada)})

    def _ao_interceptar(self, _evento: str, fluxo: Fluxo) -> None:
        self._difundir({"tipo": "fluxo", "fluxo": fluxo.para_dict()})

    def _contadores(self) -> dict:
        m = self.motor
        return {"tipo": "contadores", "conexoes_ativas": m.conexoes_ativas, "total_enviado": m.total_enviado,
                "total_recebido": m.total_recebido, "proxy_enviado": m.proxy_enviado,
                "proxy_recebido": m.proxy_recebido, "direto_enviado": m.direto_enviado,
                "direto_recebido": m.direto_recebido}

    def _estado(self) -> dict:
        return {"tipo": "estado", "ativo": self.motor.em_execucao(), "mensagem": self.mensagem}

    async def _atender(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            pedido = json.loads(await leitor.readline())
            if not isinstance(pedido, dict) or not hmac.compare_digest(str(pedido.get("token", "")), self.token):
                await self._responder(escritor, {"erro": "token inválido"})
                return
            if pedido.get("comando") == "acompanhar":
                await self._acompanhar(escritor)
                return
            try:
                resposta = await self._executar(pedido)
            except Exception as exc:
                resposta = {"erro": str(exc) or type(exc).__name__}
            await self._responder(escritor, resposta)
        except (ValueError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            escritor.close()

    @staticmethod
    async def _responder(escritor: asyncio.StreamWriter, mensagem: dict) -> None:
        escritor.write(json.dumps(mensagem, ensure_ascii=False).encode("utf-8") + b"\n")
        await escritor.drain()

    async def _executar(self, pedido: dict) -> dict:
        comando = pedido.get("comando")
        if comando == "estado":
            return {**self._estado(), **self._contadores()}
        if comando == "iniciar":
            await asyncio.to_thread(self.motor.iniciar)
            return self._estado()
        if comando == "parar":
            await asyncio.to_thread(self.motor.parar)
            return self._estado()
        if comando == "configurar":
            nova = Configuracao.de_dict(pedido["configuracao"])
            ok, mensagem = True, ""
            if self.motor.em_execucao() and pedido.get("ao_vivo"):
                ok, mensagem = await asyncio.to_thread(self.motor.aplicar_ao_vivo, nova)
            else:
                self.motor.atualizar_configuracao(nova)
            self.registro.dias_retencao = nova.preferencias.dias_retencao_log
            await asyncio.to_thread(self.salvar, nova)
            # O motor devolve às preferências os valores que não conseguiu aplicar.
            return {"ok": ok, "mensagem": mensagem, "preferencias": nova.preferencias.para_dict()}
        if comando == "restaurar_ips":
            self.motor.restaurar_ips_saida(dict(pedido.get("ips", {})))
            return {"ok": True}
        if comando == "certificado_ca":
            if pedido.get("recriar"):
                await asyncio.to_thread(self.motor.recriar_ca)
            return {"pem": (await asyncio.to_thread(self.motor.certificado_ca)).decode("ascii")}
        raise ErroServico(f"comando desconhecido: {comando}")

    async def _acompanhar(self, escritor: asyncio.StreamWriter) -> None:
        fila: asyncio.Queue = asyncio.Queue()
        self._ouvintes.add(fila)
        try:
            await self._responder(escritor, self._estado())
            while True:
                try:
                    mensagem = await asyncio.wait_for(fila.get(), timeout=INTERVALO_CONTADORES)
                except asyncio.TimeoutError:
                    mensagem = self._contadores()
                if mensagem is None:
                    return
                await self._responder(escritor, mensagem)
        finally:
            self._ouvintes.discard(fila)


def executar_servico(porta_controle: int = PORTA_CONTROLE) -> int:
    """`python -m proxy_manager --servico`: roda até receber SIGTERM/Ctrl+C."""
    from .motor import MotorProxy

    cfg = carregar_config_servico()
    registro = Registro(dias_retencao=cfg.preferencias.dias_retencao_log)
    motor = MotorProxy(cfg, registro)
    token = secrets.token_urlsafe(32)
    _gravar_token(token)

    async def principal() -> None:
        controle = ServidorControle(motor, registro, token)
        await controle.iniciar(porta_controle)
        parar = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGBREAK", None)):
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, parar.set)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(parar.set))
        await asyncio.to_thread(motor.iniciar)
        print(f"{NOME_APP}: serviço ativo; canal de controle em 127.0.0.1:{porta_controle}", flush=True)
        await parar.wait()
        await controle.parar()
        await asyncio.to_thread(motor.parar)

    asyncio.run(principal())
    return 0


# -- lado da janela ------------------------------------------------------------------------------

def pedir(comando: str, porta: int = PORTA_CONTROLE, timeout: float = 15.0, **dados) -> dict:
    token = ler_token()
    if not token:
        raise ErroServico("serviço não instalado (token de controle ausente)")
    with socket.create_connection(("127.0.0.1", porta), timeout=timeout) as sock:
        sock.sendall(json.dumps({"token": token, "comando": comando, **dados}).encode("utf-8") + b"\n")
        with sock.makefile("rb") as arq:
            linha = arq.readline(LIMITE_LINHA)
    if not linha:
        raise ErroServico("o serviço fechou a conexão")
    resposta = json.loads(linha)
    if "erro" in resposta:
        raise ErroServico(resposta["erro"])
    return resposta


def servico_disponivel(porta: int = PORTA_CONTROLE) -> bool:
    try:
        pedir("estado", porta, timeout=1.0)
        return True
    except (OSError, ValueError, ErroServico):
        return False


class MotorRemoto:
    """Mesma interface que a janela usa do MotorProxy, atendida pelo serviço."""

    remoto = True

    def __init__(self, registro: Registro, porta: int = PORTA_CONTROLE,
                 fluxos: Optional[ArmazemFluxos] = None):
        self.registro = registro
        self.porta = porta
        self.fluxos = fluxos if fluxos is not None else ArmazemFluxos()
        self.ao_mudar_estado: Optional[Callable[[bool, str], None]] = None
        self.ao_mudar_ip_saida: Optional[Callable[[str, str, str], None]] = None
        self._ativo = False
        self._conectado = False
        self.conexoes_ativas = 0
        self.total_enviado = self.total_recebido = 0
        self.proxy_enviado = self.proxy_recebido = 0
        self.direto_enviado = self.direto_recebido = 0
        self._encerrar = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._thread = threading.Thread(target=self._acompanhar, name="servico-acompanhar", daemon=True)
        self._thread.start()

    def em_execucao(self) -> bool:
        return self._ativo

    def _pedir(self, comando: str, **dados) -> dict:
        return pedir(comando, self.porta, **dados)

    def iniciar(self) -> None:
        try:
            self._aplicar_estado(self._pedir("iniciar"))
        except (OSError, ValueError, ErroServico) as exc:
            self._emitir(False, f"Serviço indisponível: {exc}")

    def parar(self) -> None:
        try:
            self._aplicar_estado(self._pedir("parar"))
        except (OSError, ValueError, ErroServico) as exc:
            self._emitir(False, f"Serviço indisponível: {exc}")

    def atualizar_configuracao(self, configuracao: Configuracao) -> None:
        try:
            self._pedir("configurar", configuracao=configuracao.para_dict(), ao_vivo=False)
        except (OSError, ValueError, ErroServico):
            pass

    def aplicar_ao_vivo(self, configuracao: Configuracao) -> tuple[bool, str]:
        try:
            resposta = self._pedir("configurar", configuracao=configuracao.para_dict(), ao_vivo=True)
        except (OSError, ValueError, ErroServico) as exc:
            return False, f"Serviço indisponível: {exc}"
        for campo, valor in resposta.get("preferencias", {}).items():
            if hasattr(configuracao.preferencias, campo):
                setattr(configuracao.preferencias, campo, valor)
        return bool(resposta.get("ok")), resposta.get("mensagem", "")

    def restaurar_ips_saida(self, ips: dict[str, str]) -> None:
        try:
            self._pedir("restaurar_ips", ips=ips)
        except (OSError, ValueError, ErroServico):
            pass

    def certificado_ca(self) -> bytes:
        """Levanta ErroServico/OSError se o serviço não responder."""
        return self._pedir("certificado_ca")["pem"].encode("ascii")

    def recriar_ca(self) -> None:
        self._pedir("certificado_ca", recriar=True)

    def desconectar(self) -> None:
        """Fecha a janela sem parar o serviço."""
        self._encerrar.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=3)

    def _emitir(self, ativo: bool, mensagem: str) -> None:
        self._ativo = ativo
        if self.ao_mudar_estado:
            self.ao_mudar_estado(ativo, mensagem)

    def _aplicar_estado(self, mensagem: dict) -> None:
        ativo = bool(mensagem.get("ativo"))
        if ativo != self._ativo or mensagem.get("mensagem"):
            self._emitir(ativo, mensagem.get("mensagem") or ("Motor ativo no serviço" if ativo else "Motor parado"))

    def _acompanhar(self) -> None:
        while not self._encerrar.is_set():
            try:
                token = ler_token()
                self._sock = socket.create_connection(("127.0.0.1", self.porta), timeout=5)
                self._sock.settimeout(None)
                self._sock.sendall(json.dumps({"token": token, "comando": "acompanhar"}).encode("utf-8") + b"\n")
                with self._sock.makefile("rb") as arq:
                    for linha in arq:
                        self._tratar(json.loads(linha))
            except (OSError, ValueError):
                pass
            finally:
                if self._sock is not None:
                    self._sock.close()
                    self._sock = None
            if self._encerrar.is_set():
                return
            if self._conectado:
                self._conectado = False
                self._emitir(False, "Conexão com o serviço perdida; tentando de novo...")
            self._encerrar.wait(2.0)

    def _tratar(self, mensagem: dict) -> None:
        tipo = mensagem.get("tipo")
        if "erro" in mensagem:
            raise ValueError(mensagem["erro"])
        self._conectado = True
        if tipo == "estado":
            self._aplicar_estado(mensagem)
        elif tipo == "contadores":
            for campo, valor in mensagem.items():
                if campo != "tipo":
                    setattr(self, campo, valor)
        elif tipo == "ip_saida" and self.ao_mudar_ip_saida:
            self.ao_mudar_ip_saida(mensagem["id"], mensagem["anterior"], mensagem["atual"])
        elif tipo == "log":
            dados = {k: v for k, v in mensagem["entrada"].items() if k in EntradaLog.__dataclass_fields__}
            if mensagem.get("evento") == "nova":
                self.registro.adicionar(EntradaLog(**dados))
            elif self.registro.atualizar(dados["id"], **dados) is None:
                self.registro.adicionar(EntradaLog(**dados))  # a janela abriu com a conexão em andamento
        elif tipo == "fluxo":
            self.fluxos.receber(Fluxo.de_dict(mensagem["fluxo"]))


# -- instalação ----------------------------------------------------------------------------------

def comando_servico() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--servico"]
    return [sys.executable, "-m", "proxy_manager", "--servico"]


def pasta_projeto() -> str:
    # No executável do PyInstaller, __file__ fica na pasta temporária de extração, que some
    # quando o processo termina: o serviço usa a pasta do próprio executável.
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parents[2])


def unidade_systemd() -> str:
    execucao = " ".join(_aspas_shell(parte) for parte in comando_servico())
    return (
        "[Unit]\n"
        f"Description={NOME_APP} (proxy por regras)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={execucao}\n"
        f"WorkingDirectory={pasta_projeto()}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _aspas_shell(texto: str) -> str:
    return f'"{texto}"' if " " in texto else texto


def _escapar_xml(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def tarefa_windows() -> str:
    """XML da tarefa agendada: na inicialização, como SYSTEM, sem limite de tempo e também na bateria."""
    programa, *argumentos = comando_servico()
    argumentos_texto = subprocess.list2cmdline(argumentos)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{NOME_APP}: motor do proxy sem interface</Description></RegistrationInfo>
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals>
    <Principal id="Servico"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Servico">
    <Exec>
      <Command>{_escapar_xml(programa)}</Command>
      <Arguments>{_escapar_xml(argumentos_texto)}</Arguments>
      <WorkingDirectory>{_escapar_xml(pasta_projeto())}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _rodar(comando: list[str]) -> None:
    resultado = subprocess.run(comando, capture_output=True, timeout=60)
    if resultado.returncode != 0:
        saida = (resultado.stderr or resultado.stdout or b"").decode(errors="replace").strip()
        raise ErroServico(f"{' '.join(comando[:3])}: {saida or f'código {resultado.returncode}'}")


def instalar_servico() -> tuple[bool, str]:
    """Precisa de administrador/root. Registra o serviço e já o inicia."""
    try:
        if platform.system() == "Windows":
            with tempfile.TemporaryDirectory() as pasta:
                xml = Path(pasta) / "tarefa.xml"
                xml.write_text(tarefa_windows(), encoding="utf-16")
                _rodar(["schtasks", "/Create", "/F", "/TN", NOME_TAREFA, "/XML", str(xml)])
            _rodar(["schtasks", "/Run", "/TN", NOME_TAREFA])
        elif platform.system() == "Linux":
            CAMINHO_UNIDADE.write_text(unidade_systemd(), encoding="utf-8")
            _rodar(["systemctl", "daemon-reload"])
            _rodar(["systemctl", "enable", "--now", NOME_UNIDADE])
        else:
            return False, "Modo serviço disponível só no Windows e no Linux."
    except (ErroServico, OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Falha ao instalar o serviço: {exc}"
    return True, "Serviço instalado; ele inicia junto com o sistema."


def remover_servico() -> tuple[bool, str]:
    try:
        if platform.system() == "Windows":
            subprocess.run(["schtasks", "/End", "/TN", NOME_TAREFA], capture_output=True, timeout=30)
            _rodar(["schtasks", "/Delete", "/F", "/TN", NOME_TAREFA])
        elif platform.system() == "Linux":
            subprocess.run(["systemctl", "disable", "--now", NOME_UNIDADE], capture_output=True, timeout=60)
            CAMINHO_UNIDADE.unlink(missing_ok=True)
            _rodar(["systemctl", "daemon-reload"])
        else:
            return False, "Modo serviço disponível só no Windows e no Linux."
    except (ErroServico, OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Falha ao remover o serviço: {exc}"
    for arquivo in (arquivo_token(), arquivo_config_servico()):
        try:
            arquivo.unlink(missing_ok=True)
        except OSError:
            pass
    return True, "Serviço removido."


def servico_instalado() -> bool:
    if platform.system() == "Windows":
        try:
            return subprocess.run(["schtasks", "/Query", "/TN", NOME_TAREFA], capture_output=True,
                                  timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return CAMINHO_UNIDADE.exists()


def executar_como_administrador(argumento: str) -> tuple[bool, str]:
    """Roda `proxy_manager <argumento>` elevado (UAC no Windows, pkexec no Linux) e espera."""
    comando = comando_servico()[:-1] + [argumento]
    try:
        if platform.system() == "Windows":
            programa, *argumentos = comando
            lista = ",".join("'" + a.replace("'", "''") + "'" for a in argumentos)
            script = (f"$p = Start-Process -FilePath '{programa}' -ArgumentList {lista} -Verb RunAs "
                      f"-WorkingDirectory '{pasta_projeto()}' -WindowStyle Hidden -Wait -PassThru; "
                      "exit $p.ExitCode")
            resultado = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                       capture_output=True, timeout=300)
        else:
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                comando = ["pkexec", "env", f"PYTHONPATH={pasta_projeto()}"] + comando
            resultado = subprocess.run(comando, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    saida = (resultado.stdout or b"").decode(errors="replace").strip()
    erro = (resultado.stderr or b"").decode(errors="replace").strip()
    if resultado.returncode != 0:
        return False, erro or saida or "operação cancelada"
    return True, saida


def esperar_servico(porta: int = PORTA_CONTROLE, timeout: float = 20.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if servico_disponivel(porta):
            return True
        time.sleep(0.5)
    return False
