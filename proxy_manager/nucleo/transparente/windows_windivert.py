"""Modo transparente no Windows.

O WinDivert (via pydivert) captura os pacotes TCP/IPv4 de saída e os reescreve para a porta
transparente local, guardando o destino original pela porta de origem.

Exige administrador e o pacote pydivert; nada disso é verificado antes de iniciar()."""
from __future__ import annotations

import platform
import threading
from typing import Optional


def suportado() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import pydivert  # noqa: F401
    except ImportError:
        return False
    return True


class ModoTransparenteWindows:
    def __init__(self, porta_transparente: int):
        self.porta_transparente = porta_transparente
        self._thread: Optional[threading.Thread] = None
        self._evento_parar = threading.Event()
        self._handle = None
        self._trava = threading.Lock()
        # porta de origem -> destino original
        self._tabela_nat: dict[int, tuple[str, int]] = {}

    def iniciar(self) -> tuple[bool, str]:
        try:
            import pydivert
        except ImportError:
            return False, "Pacote 'pydivert' não instalado. Rode: uv sync"

        filtro = (
            f"tcp and ("
            f"(outbound and !loopback and tcp.DstPort != {self.porta_transparente}) or "
            f"(loopback and tcp.SrcPort == {self.porta_transparente})"
            f")"
        )
        try:
            handle = pydivert.WinDivert(filtro)
            handle.open()
        except OSError as exc:
            return False, (f"Falha ao abrir o driver WinDivert: {exc}. Confirme que está rodando "
                           "como Administrador e que o pacote pydivert está instalado corretamente.")

        self._handle = handle
        self._evento_parar.clear()
        with self._trava:
            self._tabela_nat.clear()
        self._thread = threading.Thread(target=self._loop_captura, daemon=True,
                                        name="windivert-transparent")
        self._thread.start()
        return True, f"Modo transparente ativo, redirecionando TCP para 127.0.0.1:{self.porta_transparente}."

    def parar(self) -> tuple[bool, str]:
        self._evento_parar.set()
        if self._handle is not None:
            try:
                self._handle.close()  # desbloqueia o recv() pendente na thread de captura
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._handle = None
        with self._trava:
            self._tabela_nat.clear()
        return True, "Modo transparente desligado."

    def resolver_destino(self, escritor, porta_origem: int) -> Optional[tuple[str, int]]:
        with self._trava:
            return self._tabela_nat.get(porta_origem)

    def _loop_captura(self) -> None:
        while not self._evento_parar.is_set():
            try:
                pacote = self._handle.recv()
            except OSError:
                break  # handle fechado por stop()

            if self._traduzir(pacote):
                # Depois de reescrever endereço/porta, os checksums precisam ser recalculados.
                pacote.recalculate_checksums()

            try:
                self._handle.send(pacote)
            except OSError:
                break

    def _traduzir(self, pacote) -> bool:
        """Reescreve endereço/porta quando necessário; devolve True se alterou o pacote."""
        tcp = pacote.tcp
        if tcp is None:
            return False

        if pacote.is_outbound:
            with self._trava:
                rastreado = self._tabela_nat.get(tcp.src_port)
            eh_syn_novo = tcp.syn and not tcp.ack
            if rastreado is None and eh_syn_novo:
                rastreado = (pacote.dst_addr, pacote.dst_port)
                with self._trava:
                    self._tabela_nat[tcp.src_port] = rastreado
            if rastreado is not None:
                pacote.dst_addr = "127.0.0.1"
                pacote.dst_port = self.porta_transparente
                if tcp.fin or tcp.rst:
                    with self._trava:
                        self._tabela_nat.pop(tcp.src_port, None)
                return True
        else:
            # Resposta da porta transparente local, voltando para o processo de origem.
            if pacote.src_addr == "127.0.0.1" and tcp.src_port == self.porta_transparente:
                with self._trava:
                    original = self._tabela_nat.get(tcp.dst_port)
                if original is not None:
                    pacote.src_addr, tcp.src_port = original
                    return True
        return False
