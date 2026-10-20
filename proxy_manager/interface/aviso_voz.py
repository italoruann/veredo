"""Aviso falado quando o IP de saída de um proxy muda, pela síntese de voz do sistema (QtTextToSpeech).

Usa SAPI/WinRT no Windows e speech-dispatcher no Linux. Sem mecanismo de voz disponível, fica mudo."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QLocale, QObject

if TYPE_CHECKING:
    from .contexto import Contexto


FRASE_TROCA_IP = "Atenção: o IP mudou."


class AvisoVoz(QObject):
    def __init__(self, ctx: Contexto):
        super().__init__(ctx)
        self.ctx = ctx
        self._voz = None  # criada só na primeira fala: carregar o mecanismo de voz é lento
        ctx.ip_saida_mudou.connect(self._ao_mudar_ip_saida)

    def _ao_mudar_ip_saida(self, id_perfil: str, ip_anterior: str, ip_atual: str) -> None:
        # Sem IP anterior é a primeira verificação do perfil, não uma troca.
        if not ip_anterior or not self.ctx.configuracao.preferencias.aviso_voz_ip_ativo:
            return
        self.falar(FRASE_TROCA_IP)

    def falar(self, texto: str) -> bool:
        """Retorna False se não houver voz disponível no sistema."""
        voz = self._obter_voz()
        if voz is None:
            return False
        voz.say(texto)
        return True

    def _obter_voz(self):
        if self._voz is None:
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
            self._voz = voz
        return self._voz
