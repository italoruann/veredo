"""Motor do proxy: abre as portas locais (SOCKS5, HTTP, PAC e transparente), aplica as regras
a cada conexão e repassa os bytes entre o aplicativo e o destino. Datagramas UDP (UDP ASSOCIATE
da porta SOCKS ou capturados no modo transparente) passam pelas mesmas regras, por fluxo."""
from __future__ import annotations

import asyncio
import ipaddress
import platform
import threading
import time
from typing import Callable, Optional, Protocol

from . import cliente_proxy, dns, processos, proxy_http, rede, socks5, udp, verificacao_ip
from .cliente_proxy import Rota
from .configuracao import Configuracao, PerfilProxy, Preferencias
from .integracao_sistema import gerar_script_pac, iniciar_servidor_pac
from .registro import EntradaLog, Registro
from .regras import ConjuntoRegras, Resultado
from .transparente import inspecao

CallbackEstado = Callable[[bool, str], None]
# id do perfil, IP anterior ("" na primeira verificação), IP atual
CallbackIpSaida = Callable[[str, str, str], None]

# O ip-api.com limita requisições por minuto; o IP de saída é reconsultado no máximo a cada 10 min.
VALIDADE_CACHE_IP_SAIDA = 600.0


class BackendTransparente(Protocol):
    def iniciar(self) -> tuple[bool, str]: ...
    def parar(self) -> tuple[bool, str]: ...
    def resolver_destino(self, escritor: asyncio.StreamWriter, porta_origem: int) -> Optional[tuple[str, int]]: ...
    # Opcional: com "UDP pelas regras" o backend entrega cada datagrama capturado por aqui.
    # ao_receber_udp: Callable[[origem, destino, dados, responder], None]


class _AssociacaoSocks:
    """Um UDP ASSOCIATE de um programa na porta SOCKS local; identifica os fluxos dele."""
    def __init__(self, proc: processos.InfoProcesso):
        self.proc = proc


def _criar_backend_transparente(prefs: Preferencias) -> Optional[BackendTransparente]:
    argumentos = (prefs.porta_transparente, prefs.porta_dns if prefs.dns_ativo else None,
                  prefs.prevenir_vazamento, prefs.udp_pelas_regras)
    sistema = platform.system()
    if sistema == "Linux":
        from .transparente.linux_nftables import ModoTransparenteLinux
        return ModoTransparenteLinux(*argumentos)
    if sistema == "Windows":
        from .transparente.windows_windivert import ModoTransparenteWindows
        return ModoTransparenteWindows(*argumentos)
    return None


async def _abrir_servidor_local(tratador, porta: int) -> asyncio.AbstractServer:
    """Escuta em 127.0.0.1 e, se a máquina tiver IPv6, também em ::1."""
    try:
        return await asyncio.start_server(tratador, ["127.0.0.1", "::1"], porta)
    except OSError:
        return await asyncio.start_server(tratador, "127.0.0.1", porta)


def _ip_literal(host: str) -> Optional[str]:
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return None


async def _fechar_servidor(servidor: Optional[asyncio.AbstractServer]) -> None:
    if servidor is not None:
        servidor.close()
        await asyncio.wait_for(servidor.wait_closed(), timeout=3)


class MotorProxy:
    def __init__(self, configuracao: Configuracao, registro: Registro):
        self.configuracao = configuracao
        self.registro = registro
        self.conjunto_regras = ConjuntoRegras.interpretar(configuracao.texto_regras)
        self.ao_mudar_estado: Optional[CallbackEstado] = None
        self.ao_mudar_ip_saida: Optional[CallbackIpSaida] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._pronto = threading.Event()
        self._servidor_socks: Optional[asyncio.AbstractServer] = None
        self._servidor_http: Optional[asyncio.AbstractServer] = None
        self._servidor_pac: Optional[asyncio.AbstractServer] = None
        self._servidor_transparente: Optional[asyncio.AbstractServer] = None
        self.backend_transparente: Optional[BackendTransparente] = None
        self.servidor_dns: Optional[dns.ServidorDns] = None
        # Mantida entre reinícios do motor: programas guardam os IPs falsos em cache.
        self.tabela_dns = dns.TabelaIpsFalsos()
        self._escritores_ativos: set[asyncio.StreamWriter] = set()
        self._tarefas_fundo: set[asyncio.Task] = set()
        self.udp: Optional[udp.GerenciadorUdp] = None
        # id da entrada de log de um fluxo UDP -> início (para a duração)
        self._inicio_udp: dict[str, float] = {}

        # A chave do cache inclui host/porta/tipo para que editar o perfil invalide o valor.
        self._cache_ip_saida: dict[str, tuple[float, str]] = {}
        self._ip_saida_em_verificacao: set[str] = set()
        # Último IP conhecido de cada perfil, para avisar quando ele muda.
        self._ip_saida_por_perfil: dict[str, str] = {}
        # Endereço (chave do cache) de cada perfil na última revisão, para notar edições.
        self._chaves_perfis: dict[str, str] = {}
        # id da entrada -> (id do perfil ou cadeia, chave da rota ao conectar, escritores do túnel).
        self._tuneis_proxy: dict[str, tuple[str, str, tuple[asyncio.StreamWriter, ...]]] = {}

        self.conexoes_ativas = 0
        self.total_enviado = 0
        self.total_recebido = 0
        self.proxy_enviado = 0
        self.proxy_recebido = 0
        self.direto_enviado = 0
        self.direto_recebido = 0

    # -- ciclo de vida -------------------------------------------------------------

    def em_execucao(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def atualizar_configuracao(self, configuracao: Configuracao) -> None:
        self.configuracao = configuracao
        self.conjunto_regras = ConjuntoRegras.interpretar(configuracao.texto_regras)
        loop = self._loop
        if self.em_execucao() and loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._revisar_perfis)

    def _revisar_perfis(self) -> None:
        """Roda no loop do motor depois de uma edição na configuração.

        Túneis abertos por um perfil cujo endereço mudou (ou que foi removido) são encerrados,
        para que os aplicativos reconectem pelo endereço novo, e o IP de saída do perfil é
        consultado na hora, sem esperar a próxima conexão."""
        perfis = {perfil.id: perfil for perfil in self.configuracao.proxies}
        chaves = {id_perfil: self._chave_cache_ip(perfil) for id_perfil, perfil in perfis.items()}
        chaves_rotas = {id_perfil: self._chave_rota(cliente_proxy.rota_do_perfil(perfil))
                        for id_perfil, perfil in perfis.items()}
        for cadeia in self.configuracao.cadeias:
            rota = self._rota_da_cadeia(cadeia.nome)
            if rota is not None:
                chaves_rotas[cadeia.id] = self._chave_rota(rota)

        for id_entrada, (id_rota, chave, escritores) in list(self._tuneis_proxy.items()):
            if chaves_rotas.get(id_rota) != chave:
                del self._tuneis_proxy[id_entrada]
                for escritor in escritores:
                    try:
                        escritor.transport.abort()
                    except Exception:
                        pass

        for id_perfil, chave in chaves.items():
            anterior = self._chaves_perfis.get(id_perfil)
            if anterior is not None and anterior != chave and perfis[id_perfil].ativo:
                self._em_segundo_plano(self._atualizar_ip_saida(perfis[id_perfil]))
        self._chaves_perfis = chaves

    def iniciar(self) -> None:
        if self.em_execucao():
            return
        self._pronto.clear()
        self._thread = threading.Thread(target=self._executar_loop, name="motor-proxy", daemon=True)
        self._thread.start()
        self._pronto.wait(timeout=5)

    def parar(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop and loop.is_running():
            futuro = asyncio.run_coroutine_threadsafe(self._encerrar(), loop)
            try:
                futuro.result(timeout=10)
            except Exception:
                pass
            # O loop só pode parar depois que _encerrar() terminou; parar de dentro da própria
            # corrotina impedia o futuro de ser resolvido e travava esta chamada.
            loop.call_soon_threadsafe(loop.stop)
        if thread:
            thread.join(timeout=10)
            if thread.is_alive():
                return
        self._thread = None
        self._loop = None

    def _executar_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._iniciar_servidores())
            self._pronto.set()
            loop.run_forever()
        except Exception as exc:
            self._pronto.set()
            self._emitir_estado(False, f"Falha ao iniciar o motor: {exc}")
        finally:
            loop.close()

    async def _iniciar_servidores(self) -> None:
        prefs = self.configuracao.preferencias
        self.udp = udp.GerenciadorUdp(self._decidir_udp, self._finalizar_udp, self._contar)
        self.udp.iniciar()
        try:
            self._servidor_socks = await asyncio.start_server(self._atender_socks, "127.0.0.1", prefs.porta_socks)
            self._servidor_http = await asyncio.start_server(self._atender_http, "127.0.0.1", prefs.porta_http)
            self._servidor_pac = await iniciar_servidor_pac(self._texto_pac, prefs.porta_pac)
        except OSError as exc:
            self._emitir_estado(False, f"Não foi possível abrir as portas locais: {exc}")
            raise

        if prefs.modo_transparente_ativo:
            try:
                await self._ligar_transparente(prefs)
            except (OSError, RuntimeError) as exc:
                self._emitir_estado(False, f"Falha ao ativar o modo transparente: {exc}")
                raise

        self._chaves_perfis = {perfil.id: self._chave_cache_ip(perfil) for perfil in self.configuracao.proxies}
        self._emitir_estado(True, "Motor iniciado")

    def _texto_pac(self) -> str:
        prefs = self.configuracao.preferencias
        return gerar_script_pac(prefs.porta_socks, prefs.porta_http)

    async def _encerrar(self) -> None:
        servidores = [s for s in (self._servidor_socks, self._servidor_http, self._servidor_pac,
                                  self._servidor_transparente) if s is not None]
        for servidor in servidores:
            servidor.close()

        # Túneis longos (HTTPS aberto pelo navegador) são abortados; sem isso wait_closed()
        # ficaria esperando indefinidamente.
        for escritor in list(self._escritores_ativos):
            try:
                escritor.transport.abort()
            except Exception:
                pass
        self._escritores_ativos.clear()
        self._tuneis_proxy.clear()

        pendentes = list(self._tarefas_fundo)
        for tarefa in pendentes:
            tarefa.cancel()
        if pendentes:
            await asyncio.gather(*pendentes, return_exceptions=True)

        for servidor in servidores:
            try:
                await asyncio.wait_for(servidor.wait_closed(), timeout=3)
            except Exception:
                pass
        self._servidor_socks = self._servidor_http = self._servidor_pac = self._servidor_transparente = None

        await self._desligar_transparente()
        if self.udp is not None:
            self.udp.parar()
            self.udp = None
        self._emitir_estado(False, "Motor parado")

    # -- mudanças de porta com o motor ligado -----------------------------------------

    def aplicar_ao_vivo(self, nova_configuracao: Configuracao) -> tuple[bool, str]:
        """Reabre apenas as portas que mudaram, sem derrubar as conexões em andamento.

        Itens que não puderem ser aplicados voltam ao valor anterior em `nova_configuracao`.
        Com o motor parado basta `atualizar_configuracao()`."""
        loop = self._loop
        if not self.em_execucao() or loop is None or not loop.is_running():
            return False, "O motor não está em execução."
        futuro = asyncio.run_coroutine_threadsafe(self._aplicar_ao_vivo(nova_configuracao), loop)
        try:
            return futuro.result(timeout=10)
        except Exception as exc:
            return False, f"Falha ao aplicar configurações: {exc}"

    async def _aplicar_ao_vivo(self, nova_configuracao: Configuracao) -> tuple[bool, str]:
        antigas = self.configuracao.preferencias
        novas = nova_configuracao.preferencias
        aplicados: list[str] = []
        erros: list[str] = []

        if novas.porta_socks != antigas.porta_socks:
            try:
                self._servidor_socks = await self._trocar_servidor(
                    self._servidor_socks, self._atender_socks, novas.porta_socks)
                aplicados.append(f"SOCKS5 → porta {novas.porta_socks}")
            except OSError as exc:
                erros.append(f"porta SOCKS5 ({novas.porta_socks}): {exc}")
                novas.porta_socks = antigas.porta_socks

        if novas.porta_http != antigas.porta_http:
            try:
                self._servidor_http = await self._trocar_servidor(
                    self._servidor_http, self._atender_http, novas.porta_http)
                aplicados.append(f"HTTP → porta {novas.porta_http}")
            except OSError as exc:
                erros.append(f"porta HTTP ({novas.porta_http}): {exc}")
                novas.porta_http = antigas.porta_http

        if novas.porta_pac != antigas.porta_pac:
            try:
                novo = await iniciar_servidor_pac(self._texto_pac, novas.porta_pac)
                await _fechar_servidor(self._servidor_pac)
                self._servidor_pac = novo
                aplicados.append(f"PAC → porta {novas.porta_pac}")
            except OSError as exc:
                erros.append(f"porta do PAC ({novas.porta_pac}): {exc}")
                novas.porta_pac = antigas.porta_pac

        campos_transparente = ("modo_transparente_ativo", "porta_transparente", "dns_pelo_proxy",
                               "dns_excecoes", "dns_servidores", "porta_dns", "prevenir_vazamento",
                               "udp_pelas_regras")
        transparente_mudou = (
            novas.modo_transparente_ativo != antigas.modo_transparente_ativo
            or (novas.modo_transparente_ativo
                and any(getattr(novas, c) != getattr(antigas, c) for c in campos_transparente))
        )
        if transparente_mudou:
            try:
                await self._aplicar_modo_transparente(novas)
                aplicados.append("modo transparente")
            except (OSError, RuntimeError) as exc:
                erros.append(f"modo transparente: {exc}")
                for campo in campos_transparente:
                    setattr(novas, campo, getattr(antigas, campo))
                if antigas.modo_transparente_ativo:
                    # O modo antigo foi desligado na tentativa; volta a ligá-lo.
                    try:
                        await self._ligar_transparente(antigas)
                    except (OSError, RuntimeError) as exc_antigo:
                        erros.append(f"o modo transparente anterior não voltou: {exc_antigo}")
                        novas.modo_transparente_ativo = False

        self.configuracao = nova_configuracao
        self.conjunto_regras = ConjuntoRegras.interpretar(nova_configuracao.texto_regras)
        self._revisar_perfis()

        if aplicados:
            # Também faz o contexto reaplicar o PAC no sistema, caso a porta dele tenha mudado.
            self._emitir_estado(True, "Configurações aplicadas sem parar o motor: " + ", ".join(aplicados) + ".")
        if erros:
            return False, "Alguns itens não puderam ser aplicados (mantidos como estavam): " + "; ".join(erros)
        if not aplicados:
            return True, "Nada mudou nas portas/modo transparente."
        return True, "Aplicado sem parar o motor: " + ", ".join(aplicados) + "."

    @staticmethod
    async def _trocar_servidor(antigo: Optional[asyncio.AbstractServer], tratador,
                               porta: int) -> asyncio.AbstractServer:
        # Abre a porta nova antes de fechar a antiga: se falhar, a antiga continua funcionando.
        novo = await asyncio.start_server(tratador, "127.0.0.1", porta)
        await _fechar_servidor(antigo)
        return novo

    async def _aplicar_modo_transparente(self, prefs: Preferencias) -> None:
        # O backend antigo precisa sair antes: os dois disputariam as mesmas regras de captura.
        await self._desligar_transparente()
        if prefs.modo_transparente_ativo:
            await self._ligar_transparente(prefs)

    async def _ligar_transparente(self, prefs: Preferencias) -> None:
        """Abre o listener e o DNS local antes de o backend começar a desviar o tráfego."""
        backend = _criar_backend_transparente(prefs)
        if backend is None:
            raise RuntimeError("modo transparente não é suportado nesta plataforma")
        servidor = await _abrir_servidor_local(self._atender_transparente, prefs.porta_transparente)
        try:
            if prefs.dns_ativo:
                await self._ligar_dns(prefs)
            if prefs.udp_pelas_regras:
                backend.ao_receber_udp = self._udp_capturado
            ok, mensagem = await asyncio.to_thread(backend.iniciar)
            if not ok:
                raise RuntimeError(mensagem)
        except BaseException:
            await _fechar_servidor(servidor)
            self._desligar_dns()
            raise
        self._servidor_transparente = servidor
        self.backend_transparente = backend

    async def _desligar_transparente(self) -> None:
        backend, self.backend_transparente = self.backend_transparente, None
        if backend is not None:
            await asyncio.to_thread(backend.parar)
        servidor, self._servidor_transparente = self._servidor_transparente, None
        try:
            await _fechar_servidor(servidor)
        except Exception:
            pass
        self._desligar_dns()

    async def _ligar_dns(self, prefs: Preferencias) -> None:
        servidores = dns.interpretar_excecoes(prefs.dns_servidores) or dns.servidores_dns_sistema()
        if not servidores:
            raise RuntimeError("nenhum servidor DNS encontrado no sistema; informe um nas Configurações")
        real = dns.ResolvedorReal(servidores)
        servidor = dns.ServidorDns(self.tabela_dns, real, dns.interpretar_excecoes(prefs.dns_excecoes),
                                   bloquear_outros=prefs.prevenir_vazamento)
        await servidor.iniciar(prefs.porta_dns)
        self.servidor_dns = servidor
        rede.resolvedor = real.resolver

    def _desligar_dns(self) -> None:
        if self.servidor_dns is not None:
            self.servidor_dns.parar()
            self.servidor_dns = None
            rede.resolvedor = None

    def _emitir_estado(self, ativo: bool, mensagem: str) -> None:
        if self.ao_mudar_estado:
            self.ao_mudar_estado(ativo, mensagem)

    # -- decisão de roteamento ------------------------------------------------------

    def _rota_da_cadeia(self, nome: str) -> Optional[Rota]:
        cadeia = self.configuracao.buscar_cadeia(nome)
        if cadeia is None or not cadeia.ativo:
            return None
        return Rota(id=cadeia.id, nome=cadeia.nome, perfis=self.configuracao.perfis_da_cadeia(cadeia),
                    tipo=cadeia.tipo, timeout=float(cadeia.timeout), eh_cadeia=True)

    def _resolver_rota(self, resultado: Resultado) -> Optional[Rota]:
        if resultado.nome_cadeia:
            return self._rota_da_cadeia(resultado.nome_cadeia)
        if resultado.nome_proxy:
            perfil = self.configuracao.buscar_proxy(resultado.nome_proxy)
        else:
            perfil = self.configuracao.proxy_padrao()
        return cliente_proxy.rota_do_perfil(perfil) if perfil else None

    def _chave_rota(self, rota: Rota) -> str:
        return f"{rota.tipo}|" + "|".join(self._chave_cache_ip(p) for p in rota.perfis)

    def _portas_locais(self) -> set[int]:
        prefs = self.configuracao.preferencias
        portas = {prefs.porta_socks, prefs.porta_http, prefs.porta_pac}
        if prefs.modo_transparente_ativo:
            portas.add(prefs.porta_transparente)
        return portas

    def _evitar_loop(self, resultado: Resultado, host: str, porta: int) -> Resultado:
        """Bloqueia conexões às portas do próprio Veredo e manda direto as que vão ao próprio
        servidor proxy; nos dois casos a conexão voltaria ao motor sem fim."""
        destino_local = host.lower() == "localhost" or (
            _ip_literal(host) is not None and ipaddress.ip_address(host).is_loopback)
        if destino_local and porta in self._portas_locais():
            return Resultado(tipo_acao="block", nome_proxy=None, regra=None, motivo="loop")
        if resultado.tipo_acao == "proxy":
            for perfil in self.configuracao.proxies:
                if perfil.port == porta and perfil.host.lower() == host.lower():
                    return Resultado(tipo_acao="direct", nome_proxy=None, regra=None,
                                     motivo="servidor proxy")
        return resultado

    @staticmethod
    def _rotulo_regra(resultado: Resultado) -> str:
        if resultado.motivo == "loop":
            return "(loop detectado)"
        if resultado.motivo:
            return f"({resultado.motivo})"
        regra = resultado.regra
        if regra is None:
            return "(padrão)"
        return regra.alvo_completo if regra.acao == "proxy" else f"{regra.alvo_completo} +{regra.acao}"

    async def _preparar(self, host_destino: str, porta_destino: int, ip_origem: str, porta_origem: int,
                        porta_escuta: int, protocolo: str, host_regras: Optional[str] = None,
                        destino_original: Optional[tuple[str, int]] = None,
                        ) -> tuple[Resultado, Optional[Rota], EntradaLog]:
        """Identifica o processo, avalia as regras e registra a conexão.

        No modo transparente `host_destino` é sempre o IP original; `host_regras` traz o domínio
        descoberto pelo SNI/Host, e `destino_original` ajuda a achar o processo dono do socket."""
        proc = await asyncio.to_thread(processos.processo_da_conexao, ip_origem, porta_origem,
                                       porta_escuta, destino_original)
        ip_literal = _ip_literal(host_destino)
        host_para_regras = host_regras or host_destino
        resultado = self.conjunto_regras.avaliar(proc.nome, proc.caminho, host_para_regras, ip_literal,
                                                 acao_padrao=self.configuracao.preferencias.acao_padrao,
                                                 porta=porta_destino)
        resultado = self._evitar_loop(resultado, host_destino, porta_destino)
        rota: Optional[Rota] = None
        rotulo_proxy = ""
        if resultado.tipo_acao == "proxy":
            rota = self._resolver_rota(resultado)
            if rota is not None:
                rotulo_proxy = rota.nome
            elif resultado.nome_cadeia:
                rotulo_proxy = f"(cadeia '{resultado.nome_cadeia}' não encontrada)"
            else:
                rotulo_proxy = "(nenhum proxy configurado)"

        entrada = self.registro.adicionar(EntradaLog(
            pid=proc.pid, nome_processo=proc.nome, caminho_processo=proc.caminho, protocolo=protocolo,
            host_destino=host_para_regras, ip_destino=ip_literal or "", porta_destino=porta_destino,
            regra_aplicada=self._rotulo_regra(resultado), acao=resultado.tipo_acao,
            proxy_usado=rotulo_proxy,
        ))
        if ip_literal is None:
            self._em_segundo_plano(self._resolver_ip_destino(entrada.id, host_destino))
        if rota is not None and not rota.eh_cadeia:
            self._anotar_ip_saida(rota.perfis[0], entrada)
        return resultado, rota, entrada

    def _anotar_ip_saida(self, perfil: PerfilProxy, entrada: EntradaLog) -> None:
        """Põe na entrada o IP de saída em cache e, se ele estiver vencido, consulta de novo."""
        em_cache = self._cache_ip_saida.get(self._chave_cache_ip(perfil))
        if em_cache:
            self.registro.atualizar(entrada.id, ip_proxy=em_cache[1])
            if time.time() - em_cache[0] < VALIDADE_CACHE_IP_SAIDA:
                return
        self._em_segundo_plano(self._atualizar_ip_saida(perfil, entrada.id, tinha_valor=bool(em_cache)))

    def _em_segundo_plano(self, corrotina) -> None:
        tarefa = asyncio.ensure_future(corrotina)
        self._tarefas_fundo.add(tarefa)
        tarefa.add_done_callback(self._tarefas_fundo.discard)

    def restaurar_ips_saida(self, ip_atual_por_perfil: dict[str, str]) -> None:
        """Recebe os IPs de saída da sessão anterior para detectar mudanças desde então."""
        self._ip_saida_por_perfil.update(ip_atual_por_perfil)

    @staticmethod
    def _chave_cache_ip(perfil: PerfilProxy) -> str:
        # O usuário entra na chave: muitos provedores escolhem o IP de saída pelo nome de usuário.
        return f"{perfil.id}:{perfil.type}:{perfil.host}:{perfil.port}:{perfil.usuario}"

    async def _resolver_ip_destino(self, id_entrada: str, host: str) -> None:
        try:
            enderecos = await asyncio.wait_for(rede.resolver(host), timeout=2.0)
        except Exception:
            return
        if enderecos:
            self.registro.atualizar(id_entrada, ip_destino=enderecos[0])

    def _nome_do_ip_falso(self, host: str) -> Optional[str]:
        """Nome que o DNS local associou a um IP falso; None se `host` não é um IP falso."""
        if not dns.eh_ip_falso(host):
            return None
        return self.tabela_dns.nome_de(host) or ""

    async def _atualizar_ip_saida(self, perfil: PerfilProxy, id_entrada: Optional[str] = None,
                                  tinha_valor: bool = False) -> None:
        chave = self._chave_cache_ip(perfil)
        if chave in self._ip_saida_em_verificacao:
            return
        self._ip_saida_em_verificacao.add(chave)
        try:
            ip = await verificacao_ip.consultar_ip_saida(perfil)
        finally:
            self._ip_saida_em_verificacao.discard(chave)

        if ip:
            self._cache_ip_saida[chave] = (time.time(), ip)
            if id_entrada is not None:
                self.registro.atualizar(id_entrada, ip_proxy=ip)
            anterior = self._ip_saida_por_perfil.get(perfil.id, "")
            if ip != anterior:
                self._ip_saida_por_perfil[perfil.id] = ip
                if self.ao_mudar_ip_saida:
                    self.ao_mudar_ip_saida(perfil.id, anterior, ip)
        elif id_entrada is not None and not tinha_valor:
            # "?" indica que a verificação falhou; um valor antigo em cache é mantido.
            self.registro.atualizar(id_entrada, ip_proxy="?")

    async def _conectar_destino(self, resultado: Resultado, rota: Optional[Rota], host: str, porta: int,
                                entrada: EntradaLog):
        if resultado.tipo_acao == "direct":
            return await rede.abrir_conexao(host, porta)
        if rota is None:
            raise RuntimeError("nenhum perfil de proxy disponível para esta regra")
        leitor, escritor, usado = await cliente_proxy.conectar_rota(rota, host, porta)
        if rota.eh_cadeia and usado is not None:
            # Redundância/balanceamento: registra qual perfil da cadeia atendeu.
            self.registro.atualizar(entrada.id, proxy_usado=f"{rota.nome} → {usado.nome}")
            self._anotar_ip_saida(usado, entrada)
        return leitor, escritor

    # -- atendimento das conexões ----------------------------------------------------

    async def _atender_socks(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        inicio = time.monotonic()
        origem = escritor.get_extra_info("peername") or ("", 0)
        try:
            pedido = await socks5.negociar_servidor(
                leitor, escritor, comandos=(socks5.CMD_CONNECT, socks5.CMD_UDP_ASSOCIATE))
        except Exception:
            escritor.close()
            return
        if pedido.cmd == socks5.CMD_UDP_ASSOCIATE:
            await self._associar_udp(leitor, escritor, origem)
            return
        pedido.host = self._nome_do_ip_falso(pedido.host) or pedido.host

        resultado, rota, entrada = await self._preparar(
            pedido.host, pedido.port, origem[0], origem[1], self.configuracao.preferencias.porta_socks,
            "socks5" if pedido.versao == socks5.SOCKS_VERSION else "socks4")

        if resultado.tipo_acao == "block":
            await socks5.responder_cliente(escritor, socks5.REP_NOT_ALLOWED, versao=pedido.versao)
            escritor.close()
            self._finalizar_bloqueada(entrada, inicio, resultado)
            return

        try:
            leitor_destino, escritor_destino = await self._conectar_destino(
                resultado, rota, pedido.host, pedido.port, entrada)
        except Exception as exc:
            await socks5.responder_cliente(escritor, socks5.REP_HOST_UNREACHABLE, versao=pedido.versao)
            escritor.close()
            self._finalizar(entrada, inicio, estado="erro", erro=str(exc))
            return

        await socks5.responder_cliente(escritor, socks5.REP_SUCCESS, versao=pedido.versao)
        await self._tunelar(leitor, escritor, leitor_destino, escritor_destino, resultado, rota, entrada, inicio)

    async def _atender_http(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        inicio = time.monotonic()
        origem = escritor.get_extra_info("peername") or ("", 0)
        try:
            cabecalho = await proxy_http.ler_cabecalho_requisicao(leitor)
            host, porta = proxy_http.extrair_destino(cabecalho)
        except Exception:
            escritor.close()
            return
        host = self._nome_do_ip_falso(host) or host

        resultado, rota, entrada = await self._preparar(
            host, porta, origem[0], origem[1], self.configuracao.preferencias.porta_http, "http")
        eh_connect = cabecalho.metodo == "CONNECT"

        if resultado.tipo_acao == "block":
            await self._erro_http(escritor, 403, "Forbidden")
            self._finalizar_bloqueada(entrada, inicio, resultado)
            return

        try:
            leitor_destino, escritor_destino = await self._conectar_destino(resultado, rota, host, porta, entrada)
            if not eh_connect:
                # Mesmo via proxy o túnel já termina no servidor de destino, que recebe a forma
                # de origem; credenciais do proxy nunca vão nesta requisição.
                escritor_destino.write(proxy_http.requisicao_forma_origem(cabecalho))
                await escritor_destino.drain()
        except Exception as exc:
            await self._erro_http(escritor, 502, "Bad Gateway")
            self._finalizar(entrada, inicio, estado="erro", erro=str(exc))
            return

        if eh_connect:
            escritor.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await escritor.drain()
        await self._tunelar(leitor, escritor, leitor_destino, escritor_destino, resultado, rota, entrada, inicio)

    async def _atender_transparente(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        inicio = time.monotonic()
        origem = escritor.get_extra_info("peername") or ("", 0)
        backend = self.backend_transparente
        destino = backend.resolver_destino(escritor, origem[1]) if backend else None
        if destino is None:
            # Conexão que não veio do redirecionamento: não há para onde encaminhar.
            escritor.close()
            return
        host, porta = destino
        nome_falso = self._nome_do_ip_falso(host)
        if nome_falso == "":
            # IP falso de antes de o Veredo reiniciar (ou já reaproveitado): não há como saber o nome.
            escritor.close()
            return
        if nome_falso:
            host = nome_falso  # o proxy recebe o nome; uma conexão direta resolve pelo DNS real

        # Lê os primeiros bytes para descobrir o domínio (SNI ou Host); eles são repassados intactos.
        prefixo = b""
        if porta in (80, 443):
            try:
                prefixo = await asyncio.wait_for(leitor.read(4096), timeout=0.3)
            except (asyncio.TimeoutError, OSError):
                prefixo = b""
        host_detectado = None
        if prefixo and porta == 443:
            host_detectado = inspecao.extrair_sni(prefixo)
        elif prefixo and porta == 80:
            host_detectado = inspecao.extrair_host_http(prefixo)

        resultado, rota, entrada = await self._preparar(
            host, porta, origem[0], origem[1], self.configuracao.preferencias.porta_transparente,
            "transparente", host_regras=host_detectado or nome_falso, destino_original=destino)

        if resultado.tipo_acao == "block":
            escritor.close()
            self._finalizar_bloqueada(entrada, inicio, resultado)
            return

        try:
            # Conecta ao IP original para não depender de uma nova resolução de DNS.
            leitor_destino, escritor_destino = await self._conectar_destino(resultado, rota, host, porta, entrada)
            if prefixo:
                escritor_destino.write(prefixo)
                await escritor_destino.drain()
        except Exception as exc:
            escritor.close()
            self._finalizar(entrada, inicio, estado="erro", erro=str(exc))
            return

        if prefixo:
            self._contar(len(prefixo), enviado=True, via_proxy=resultado.tipo_acao == "proxy")
        await self._tunelar(leitor, escritor, leitor_destino, escritor_destino, resultado, rota, entrada, inicio,
                            ja_enviados=len(prefixo))

    # -- UDP --------------------------------------------------------------------------

    async def _associar_udp(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter,
                            origem: tuple) -> None:
        """UDP ASSOCIATE: abre um relay UDP local que vive enquanto a conexão TCP estiver aberta."""
        local = escritor.get_extra_info("sockname") or ("127.0.0.1", 0)
        proc = await asyncio.to_thread(processos.processo_da_conexao, origem[0], origem[1],
                                       self.configuracao.preferencias.porta_socks)
        associacao = _AssociacaoSocks(proc)
        gerenciador = self.udp
        cliente: list[tuple] = []  # endereço UDP do programa, fixado no primeiro datagrama

        def receber(dados: bytes, endereco: tuple) -> None:
            if endereco[0] != origem[0] or gerenciador is None:
                return  # só o programa que abriu a associação pode usá-la
            if not cliente:
                cliente.append(endereco)
            lido = udp.ler_cabecalho_socks(dados)
            if lido is None:
                return
            host, porta, carga = lido
            nome_falso = self._nome_do_ip_falso(host)
            if nome_falso == "":
                return
            host_original = host
            host = nome_falso or host

            def responder(resposta: bytes, h=host_original, p=porta) -> None:
                transporte.sendto(udp.montar_cabecalho_socks(h, p) + resposta, cliente[0])

            self._em_segundo_plano(gerenciador.datagrama(associacao, host, porta, carga, responder))

        try:
            transporte, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: udp.ProtocoloDatagramas(receber), local_addr=(local[0], 0))
        except OSError:
            await socks5.responder_cliente(escritor, socks5.REP_GENERAL_FAILURE)
            escritor.close()
            return
        vinculo = transporte.get_extra_info("sockname")
        try:
            await socks5.responder_cliente(escritor, socks5.REP_SUCCESS, vinculo[0], vinculo[1])
            self._escritores_ativos.add(escritor)
            while await leitor.read(4096):
                pass  # a associação termina quando o programa fecha a conexão TCP
        except Exception:
            pass
        finally:
            self._escritores_ativos.discard(escritor)
            transporte.close()
            escritor.close()
            if gerenciador is not None:
                gerenciador.fechar_cliente(associacao)

    def _udp_capturado(self, origem: tuple[str, int], destino: tuple[str, int], dados: bytes,
                       responder: udp.Responder) -> None:
        """Chamado pela thread do backend transparente para cada datagrama capturado."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(self._encaminhar_udp_capturado, origem, destino, dados, responder)

    def _encaminhar_udp_capturado(self, origem, destino, dados, responder) -> None:
        if self.udp is None:
            return
        host, porta = destino
        nome_falso = self._nome_do_ip_falso(host)
        if nome_falso == "":
            return
        self._em_segundo_plano(self.udp.datagrama(("transparente", origem, destino), nome_falso or host, porta,
                                                  dados, responder))

    async def _decidir_udp(self, cliente, host: str, porta: int) -> udp.Decisao:
        """Regras para um fluxo UDP novo; registra o fluxo no log como uma conexão."""
        prefs = self.configuracao.preferencias
        if isinstance(cliente, _AssociacaoSocks):
            proc, protocolo = cliente.proc, "socks5-udp"
        else:
            proc = await asyncio.to_thread(processos.processo_udp, cliente[1][1])
            protocolo = "udp"
        ip_literal = _ip_literal(host)
        resultado = self.conjunto_regras.avaliar(proc.nome, proc.caminho, host, ip_literal,
                                                 acao_padrao=prefs.acao_padrao, porta=porta)
        resultado = self._evitar_loop(resultado, host, porta)
        if resultado.tipo_acao == "direct" and prefs.prevenir_vazamento and protocolo == "udp":
            # Na prevenção de vazamento, UDP só sai pelo proxy (WebRTC revelaria o IP real).
            resultado = Resultado(tipo_acao="block", nome_proxy=None, regra=None,
                                  motivo="prevenção de vazamento")
        rota = self._resolver_rota(resultado) if resultado.tipo_acao == "proxy" else None

        entrada = self.registro.adicionar(EntradaLog(
            pid=proc.pid, nome_processo=proc.nome, caminho_processo=proc.caminho, protocolo=protocolo,
            host_destino=host, ip_destino=ip_literal or "", porta_destino=porta,
            regra_aplicada=self._rotulo_regra(resultado), acao=resultado.tipo_acao,
            proxy_usado=rota.nome if rota else "",
        ))
        if resultado.tipo_acao == "block":
            self._finalizar_bloqueada(entrada, time.monotonic(), resultado)
            return udp.Decisao(acao="block")
        self._inicio_udp[entrada.id] = time.monotonic()
        return udp.Decisao(acao=resultado.tipo_acao, rota=rota, id_entrada=entrada.id)

    def _finalizar_udp(self, id_entrada: str, mudancas: dict) -> None:
        inicio = self._inicio_udp.pop(id_entrada, None)
        if inicio is not None:
            mudancas["duracao_ms"] = int((time.monotonic() - inicio) * 1000)
        self.registro.atualizar(id_entrada, **mudancas)

    # -- utilidades -------------------------------------------------------------------

    def _finalizar_bloqueada(self, entrada: EntradaLog, inicio: float, resultado: Resultado) -> None:
        erro = "loop de conexão: o destino é o próprio Veredo" if resultado.motivo == "loop" else ""
        self._finalizar(entrada, inicio, estado="bloqueada", erro=erro)

    def _finalizar(self, entrada: EntradaLog, inicio: float, **mudancas) -> None:
        duracao = int((time.monotonic() - inicio) * 1000)
        self.registro.atualizar(entrada.id, duracao_ms=duracao, **mudancas)

    @staticmethod
    async def _erro_http(escritor: asyncio.StreamWriter, codigo: int, motivo: str) -> None:
        resposta = f"HTTP/1.1 {codigo} {motivo}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        try:
            escritor.write(resposta.encode("ascii"))
            await escritor.drain()
        finally:
            escritor.close()

    def _contar(self, n: int, enviado: bool, via_proxy: bool) -> None:
        if enviado:
            self.total_enviado += n
            if via_proxy:
                self.proxy_enviado += n
            else:
                self.direto_enviado += n
        else:
            self.total_recebido += n
            if via_proxy:
                self.proxy_recebido += n
            else:
                self.direto_recebido += n

    async def _tunelar(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter,
                       leitor_destino: asyncio.StreamReader, escritor_destino: asyncio.StreamWriter,
                       resultado: Resultado, rota: Optional[Rota], entrada: EntradaLog, inicio: float,
                       ja_enviados: int = 0) -> None:
        self.conexoes_ativas += 1
        self._escritores_ativos.update((escritor, escritor_destino))
        if resultado.tipo_acao == "proxy" and rota is not None:
            self._tuneis_proxy[entrada.id] = (rota.id, self._chave_rota(rota), (escritor, escritor_destino))
        try:
            enviados, recebidos = await self._encaminhar(
                leitor, escritor, leitor_destino, escritor_destino, via_proxy=resultado.tipo_acao == "proxy")
        finally:
            self.conexoes_ativas -= 1
            self._escritores_ativos.discard(escritor)
            self._escritores_ativos.discard(escritor_destino)
            self._tuneis_proxy.pop(entrada.id, None)
        self._finalizar(entrada, inicio, estado="concluida", bytes_enviados=enviados + ja_enviados,
                        bytes_recebidos=recebidos)

    async def _encaminhar(self, leitor_cliente: asyncio.StreamReader, escritor_cliente: asyncio.StreamWriter,
                          leitor_destino: asyncio.StreamReader, escritor_destino: asyncio.StreamWriter,
                          via_proxy: bool) -> tuple[int, int]:
        totais = {True: 0, False: 0}

        async def repassar(de: asyncio.StreamReader, para: asyncio.StreamWriter, enviado: bool) -> None:
            try:
                while bloco := await de.read(65536):
                    para.write(bloco)
                    await para.drain()
                    totais[enviado] += len(bloco)
                    self._contar(len(bloco), enviado, via_proxy)
            except (ConnectionResetError, BrokenPipeError, OSError, asyncio.IncompleteReadError):
                pass
            finally:
                try:
                    para.close()
                except Exception:
                    pass

        await asyncio.gather(
            repassar(leitor_cliente, escritor_destino, True),
            repassar(leitor_destino, escritor_cliente, False),
        )
        return totais[True], totais[False]
