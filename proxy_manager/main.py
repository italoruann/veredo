"""Ponto de entrada: `python -m proxy_manager`."""
from __future__ import annotations

import sys


def main() -> None:
    if _executar_comando_servico(sys.argv[1:]):
        return

    from .dependencias_linux import garantir_bibliotecas_qt

    garantir_bibliotecas_qt()  # antes de o Qt carregar o plugin de plataforma

    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from .nucleo.configuracao import NOME_EXIBICAO
    from .interface.contexto import Contexto
    from .interface.icone import desenhar_icone
    from .interface.janela_principal import JanelaPrincipal
    from .interface.tema import aplicar_tema

    app = QApplication(sys.argv)
    app.setApplicationName(NOME_EXIBICAO)
    app.setQuitOnLastWindowClosed(False)
    aplicar_tema(app)
    app.setWindowIcon(desenhar_icone(False))

    ctx = Contexto()
    janela = JanelaPrincipal(ctx)

    preferencias = ctx.configuracao.preferencias
    iniciar_minimizado = "--minimizado" in sys.argv or preferencias.iniciar_minimizado
    # Sem ícone na bandeja não haveria como reabrir a janela.
    if not (iniciar_minimizado and preferencias.minimizar_para_bandeja and QSystemTrayIcon.isSystemTrayAvailable()):
        janela.show()

    _sair_nos_sinais_de_termino(app, janela.sair)
    sys.exit(app.exec())


AJUDA = """\
Uso: veredo [opção]   (ou: uv run python -m proxy_manager [opção])

Sem opção, abre a janela do Veredo.

  --minimizado          abre direto na bandeja (se o ícone na bandeja estiver ativado)
  --servico             roda só o motor, sem janela, até receber SIGTERM/Ctrl+C; é o que o
                        serviço do sistema executa
  --instalar-servico    registra o motor como serviço (systemd no Linux, tarefa agendada como
                        SYSTEM no Windows) e o inicia; exige root/administrador
  --remover-servico     para e remove o serviço; exige root/administrador
  -h, --help            mostra esta ajuda

Portas locais padrão: SOCKS5 58080, HTTP 58081, PAC e teste de vazamento 58090
(http://127.0.0.1:58090/teste). Detalhes no README.md."""


def _executar_comando_servico(argumentos: list[str]) -> bool:
    """Ajuda, modo serviço e instalação dele, sem carregar o Qt. True se tratou a linha de comando."""
    if "--help" in argumentos or "-h" in argumentos:
        print(AJUDA)
        sys.exit(0)

    from .nucleo import servico

    if "--servico" in argumentos:
        sys.exit(servico.executar_servico())
    for opcao, acao in (("--instalar-servico", servico.instalar_servico),
                        ("--remover-servico", servico.remover_servico)):
        if opcao in argumentos:
            ok, mensagem = acao()
            print(mensagem, file=sys.stdout if ok else sys.stderr)
            sys.exit(0 if ok else 1)
    return False


def _sair_nos_sinais_de_termino(app, sair) -> None:
    """SIGTERM e Ctrl+C saem pelo mesmo caminho do menu "Sair", desfazendo o proxy do sistema."""
    import signal

    from PySide6.QtCore import QTimer

    def tratador(_signum, _frame) -> None:
        sair()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, tratador)
        except (ValueError, OSError):
            pass
    # Sinais só são tratados quando o Python retoma o controle; o timer garante isso.
    temporizador = QTimer(app)
    temporizador.timeout.connect(lambda: None)
    temporizador.start(300)


if __name__ == "__main__":
    main()
