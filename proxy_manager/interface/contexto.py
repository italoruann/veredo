"""Estado compartilhado da interface: configuração, registro e motor, expostos por sinais Qt.

O motor roda em outra thread; os callbacks dele são convertidos em sinais aqui."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal

from ..nucleo import integracao_sistema, servico
from ..nucleo.configuracao import Configuracao, carregar_configuracao, salvar_configuracao
from ..nucleo.estado_ip_saida import carregar_ips_saida, salvar_ips_saida
from ..nucleo.interceptacao.fluxos import ArmazemFluxos, Fluxo
from ..nucleo.motor import MotorProxy
from ..nucleo.registro import EntradaLog, Registro
from .aviso_voz import AvisoVoz
from .modelo_conexoes import ModeloConexoes
from .modelo_fluxos import ModeloFluxos

# Os eventos de log são acumulados e entregues em lote; um sinal por conexão travava a interface.
INTERVALO_DESCARGA_MS = 200
# Quanto a janela espera por um serviço instalado que ainda não respondeu (segundos).
ESPERA_SERVICO = 10.0


class Contexto(QObject):
    log_adicionado = Signal(list)
    log_atualizado = Signal(list)
    estado_mudou = Signal(bool, str)
    configuracao_mudou = Signal()
    ip_saida_mudou = Signal(str, str, str)  # id do perfil, IP anterior, IP atual
    fluxos_mudaram = Signal(list)  # fluxos interceptados novos ou atualizados

    def __init__(self) -> None:
        super().__init__()
        self.configuracao: Configuracao = carregar_configuracao()
        self.registro = Registro(dias_retencao=self.configuracao.preferencias.dias_retencao_log)
        self.registro.inscrever(self._ao_registrar)
        self.fluxos = ArmazemFluxos()
        self.fluxos.inscrever(self._ao_interceptar)
        # id do perfil -> (IP anterior, IP atual), preservado entre execuções.
        self.ips_saida: dict[str, tuple[str, str]] = carregar_ips_saida()
        # Com o serviço do sistema rodando, a janela só o controla; senão o motor roda aqui. Aberta
        # no login, a janela pode chegar antes do serviço: sem esperá-lo, o motor local (sem root)
        # disputaria as portas com ele e o modo transparente falharia.
        if servico.servico_disponivel() or (servico.servico_instalado()
                                            and servico.esperar_servico(timeout=ESPERA_SERVICO)):
            self._usar_motor(servico.MotorRemoto(self.registro, fluxos=self.fluxos))
            self.motor.atualizar_configuracao(self.configuracao)
            # O serviço só mantém o motor ligado com a janela aberta; abri-la o liga, como antes
            # fazia o boot. Depois do laço de eventos, para a janela já receber o estado.
            QTimer.singleShot(0, self.motor.iniciar)
        else:
            self._usar_motor(MotorProxy(self.configuracao, self.registro, self.fluxos))
        self.aviso_voz = AvisoVoz(self)

        self._trava_pendentes = threading.Lock()
        self._pendentes_novos: list[EntradaLog] = []
        self._pendentes_atualizados: dict[str, EntradaLog] = {}
        self._fluxos_pendentes: dict[str, Fluxo] = {}

        self.modelo_conexoes = ModeloConexoes()
        self.log_adicionado.connect(self.modelo_conexoes.adicionar_entradas)
        self.log_atualizado.connect(self.modelo_conexoes.atualizar_entradas)
        self.modelo_fluxos = ModeloFluxos()
        self.fluxos_mudaram.connect(self.modelo_fluxos.receber)

        self._temporizador_descarga = QTimer(self)
        self._temporizador_descarga.setInterval(INTERVALO_DESCARGA_MS)
        self._temporizador_descarga.timeout.connect(self._descarregar_pendentes)
        self._temporizador_descarga.start()

        if self.configuracao.preferencias.integracao_sistema_ativa:
            # Se a última execução terminou sem limpar, o sistema ainda aponta para um PAC que não
            # responde. Remove agora; a configuração volta quando o motor for iniciado.
            self._sincronizar_proxy_sistema(aplicando=False)

    @property
    def usando_servico(self) -> bool:
        return getattr(self.motor, "remoto", False)

    def _usar_motor(self, motor) -> None:
        self.motor = motor
        self.motor.ao_mudar_estado = self._ao_mudar_estado
        self.motor.ao_mudar_ip_saida = self._ao_mudar_ip_saida
        self.motor.restaurar_ips_saida({id_perfil: atual for id_perfil, (_, atual) in self.ips_saida.items()})

    def conectar_servico(self) -> None:
        """Passa a controlar o serviço recém-instalado: o motor local para e o serviço recebe a configuração."""
        if self.usando_servico:
            return
        self.motor.parar()
        self._usar_motor(servico.MotorRemoto(self.registro, fluxos=self.fluxos))
        self.motor.atualizar_configuracao(self.configuracao)
        self.motor.iniciar()

    def desconectar_servico(self) -> None:
        """Volta ao motor local (parado), depois de remover o serviço."""
        if not self.usando_servico:
            return
        self.motor.desconectar()
        self._usar_motor(MotorProxy(self.configuracao, self.registro, self.fluxos))
        self.estado_mudou.emit(False, "Motor parado")

    def encerrar(self) -> None:
        """Ao fechar o app: para o motor, local ou do serviço. O processo do serviço continua, com
        o motor parado e sem tocar na rede, até a janela abrir de novo. Se este pedido não chegar
        (a janela travou ou foi morta), o vigia do serviço desliga o motor sozinho."""
        self.motor.parar()
        if self.usando_servico:
            self.motor.desconectar()

    def _ao_registrar(self, evento: str, entrada: EntradaLog) -> None:
        # Thread do motor: só enfileira.
        with self._trava_pendentes:
            if evento == "nova":
                self._pendentes_novos.append(entrada)
            else:
                self._pendentes_atualizados[entrada.id] = entrada

    def _ao_interceptar(self, _evento: str, fluxo: Fluxo) -> None:
        # Thread do motor: só enfileira. Vários eventos do mesmo fluxo viram um.
        with self._trava_pendentes:
            self._fluxos_pendentes[fluxo.id] = fluxo

    def _descarregar_pendentes(self) -> None:
        with self._trava_pendentes:
            if not self._pendentes_novos and not self._pendentes_atualizados and not self._fluxos_pendentes:
                return
            novos, self._pendentes_novos = self._pendentes_novos, []
            atualizados = list(self._pendentes_atualizados.values())
            self._pendentes_atualizados = {}
            fluxos = list(self._fluxos_pendentes.values())
            self._fluxos_pendentes = {}
        if fluxos:
            self.fluxos_mudaram.emit(fluxos)
        if novos:
            self.log_adicionado.emit(novos)
        if atualizados:
            self.log_atualizado.emit(atualizados)

    def _ao_mudar_ip_saida(self, id_perfil: str, ip_anterior: str, ip_atual: str) -> None:
        # Thread do motor: o sinal chega à interface pela conexão enfileirada do Qt.
        self.ips_saida[id_perfil] = (ip_anterior, ip_atual)
        salvar_ips_saida(self.ips_saida)
        self.ip_saida_mudou.emit(id_perfil, ip_anterior, ip_atual)

    def _ao_mudar_estado(self, ativo: bool, mensagem: str) -> None:
        self.estado_mudou.emit(ativo, mensagem)
        # O proxy do sistema acompanha o motor, para nunca apontar para um proxy parado.
        if self.configuracao.preferencias.integracao_sistema_ativa:
            self._sincronizar_proxy_sistema(aplicando=ativo)

    def _sincronizar_proxy_sistema(self, aplicando: bool) -> None:
        # Síncrono de propósito: numa thread, fechar o app logo após parar o motor podia
        # interromper a remoção e deixar o sistema apontando para o PAC.
        preferencias = self.configuracao.preferencias
        try:
            if aplicando:
                integracao_sistema.aplicar_proxy_sistema(
                    integracao_sistema.url_pac(preferencias.porta_pac), preferencias.porta_http)
            else:
                integracao_sistema.remover_proxy_sistema(manter_atalhos=True)
        except Exception:
            pass

    def salvar(self) -> None:
        salvar_configuracao(self.configuracao)

    def aplicar_mudancas(self) -> None:
        """Chamar depois de qualquer edição em `self.configuracao`."""
        self.motor.atualizar_configuracao(self.configuracao)
        self.registro.dias_retencao = self.configuracao.preferencias.dias_retencao_log
        self._limpar_ips_orfaos()
        self.salvar()
        self.configuracao_mudou.emit()

    def _limpar_ips_orfaos(self) -> None:
        orfaos = set(self.ips_saida) - {p.id for p in self.configuracao.proxies}
        if orfaos:
            for id_perfil in orfaos:
                del self.ips_saida[id_perfil]
            salvar_ips_saida(self.ips_saida)

    def aplicar_ao_vivo(self) -> tuple[bool, str]:
        """Como `aplicar_mudancas`, mas reabre as portas alteradas com o motor ligado."""
        ok, mensagem = self.motor.aplicar_ao_vivo(self.configuracao)
        self.registro.dias_retencao = self.configuracao.preferencias.dias_retencao_log
        self.salvar()
        self.configuracao_mudou.emit()
        return ok, mensagem
