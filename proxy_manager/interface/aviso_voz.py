"""Aviso falado quando o IP de saída de um proxy muda.

A fala usa a voz feminina em português do Google (a mesma do Google Tradutor). O áudio de cada
frase é baixado uma única vez e guardado na pasta de dados; depois toca sem internet. Só o texto
fixo da frase vai ao Google, nunca o IP. Se o download falhar, a síntese de voz do sistema
(QtTextToSpeech: SAPI/WinRT no Windows, speech-dispatcher no Linux) fala no lugar."""
from __future__ import annotations

import hashlib
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QLocale, QObject, QUrl, Signal

from ..nucleo.configuracao import pasta_dados

if TYPE_CHECKING:
    from .contexto import Contexto


FRASE_TROCA_IP = "Atenção: o endereço IP de saída do proxy foi alterado."

URL_VOZ_GOOGLE = "https://translate.google.com/translate_tts"
IDIOMA_VOZ = "pt-BR"
TIMEOUT_DOWNLOAD = 10.0


def caminho_audio(texto: str) -> Path:
    resumo = hashlib.sha1(f"{IDIOMA_VOZ}|{texto}".encode("utf-8")).hexdigest()[:16]
    return pasta_dados() / "voz" / f"{resumo}.mp3"


def baixar_audio(texto: str) -> Optional[Path]:
    """Áudio da frase na voz do Google, do cache ou baixado agora; None se não deu para baixar."""
    caminho = caminho_audio(texto)
    if caminho.exists() and caminho.stat().st_size > 0:
        return caminho
    parametros = urllib.parse.urlencode({"ie": "UTF-8", "client": "tw-ob", "tl": IDIOMA_VOZ, "q": texto})
    pedido = urllib.request.Request(f"{URL_VOZ_GOOGLE}?{parametros}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT_DOWNLOAD) as resposta:
            if "audio" not in resposta.headers.get("Content-Type", ""):
                return None
            dados = resposta.read()
    except (OSError, ValueError):
        return None
    if not dados:
        return None
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_bytes(dados)
    temporario.replace(caminho)
    return caminho


class AvisoVoz(QObject):
    # (texto, caminho do áudio ou "" se o download falhou), vindo da thread de download
    _audio_pronto = Signal(str, str)

    def __init__(self, ctx: Contexto):
        super().__init__(ctx)
        self.ctx = ctx
        self._voz_sistema = None  # criadas só na primeira fala: carregar os mecanismos é lento
        self._player = None
        self._saida_audio = None
        self._audio_pronto.connect(self._tocar_ou_sintetizar)
        ctx.ip_saida_mudou.connect(self._ao_mudar_ip_saida)
        if ctx.configuracao.preferencias.aviso_voz_ip_ativo:
            # Baixa já, para o primeiro aviso não esperar a rede.
            threading.Thread(target=baixar_audio, args=(FRASE_TROCA_IP,), daemon=True).start()

    def _ao_mudar_ip_saida(self, id_perfil: str, ip_anterior: str, ip_atual: str) -> None:
        # Sem IP anterior é a primeira verificação do perfil, não uma troca.
        if not ip_anterior or not self.ctx.configuracao.preferencias.aviso_voz_ip_ativo:
            return
        self.falar(FRASE_TROCA_IP)

    def falar(self, texto: str) -> bool:
        """Retorna False se não houver como falar (sem áudio em cache e sem voz no sistema)."""
        caminho = caminho_audio(texto)
        if caminho.exists() and caminho.stat().st_size > 0:
            return self._tocar(caminho) or self._sintetizar(texto)

        def baixar() -> None:
            baixado = baixar_audio(texto)
            self._audio_pronto.emit(texto, str(baixado) if baixado else "")

        threading.Thread(target=baixar, daemon=True).start()
        return True

    def _tocar_ou_sintetizar(self, texto: str, caminho: str) -> None:
        if not (caminho and self._tocar(Path(caminho))):
            self._sintetizar(texto)

    def _tocar(self, caminho: Path) -> bool:
        if self._player is None:
            try:
                from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            except ImportError:
                return False
            self._saida_audio = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._saida_audio)
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(caminho)))
        self._player.play()
        return True

    def _sintetizar(self, texto: str) -> bool:
        voz = self._obter_voz_sistema()
        if voz is None:
            return False
        voz.say(texto)
        return True

    def _obter_voz_sistema(self):
        if self._voz_sistema is None:
            try:
                from PySide6.QtTextToSpeech import QTextToSpeech
                voz = QTextToSpeech(self)
            except Exception:
                return None
            if voz.state() == QTextToSpeech.State.Error:
                return None
            portugues = [l for l in voz.availableLocales() if l.language() == QLocale.Language.Portuguese]
            if portugues:
                voz.setLocale(next((l for l in portugues if l.territory() == QLocale.Country.Brazil), portugues[0]))
            self._voz_sistema = voz
        return self._voz_sistema
