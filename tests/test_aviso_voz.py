"""Download e cache do áudio do aviso de troca de IP (sem rede: urlopen simulado)."""
import io

from proxy_manager.interface import aviso_voz


class _RespostaFalsa(io.BytesIO):
    def __init__(self, dados: bytes, tipo: str):
        super().__init__(dados)
        self.headers = {"Content-Type": tipo}


def test_baixa_uma_vez_e_depois_usa_o_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(aviso_voz, "pasta_dados", lambda: tmp_path)
    pedidos = []

    def urlopen_falso(pedido, timeout):
        pedidos.append(pedido.full_url)
        return _RespostaFalsa(b"ID3audio", "audio/mpeg")

    monkeypatch.setattr(aviso_voz.urllib.request, "urlopen", urlopen_falso)
    caminho = aviso_voz.baixar_audio(aviso_voz.FRASE_TROCA_IP)
    assert caminho.read_bytes() == b"ID3audio"
    assert aviso_voz.baixar_audio(aviso_voz.FRASE_TROCA_IP) == caminho
    assert len(pedidos) == 1
    assert "tl=pt-BR" in pedidos[0] and "translate_tts" in pedidos[0]


def test_falha_no_download_nao_grava_nada(tmp_path, monkeypatch):
    monkeypatch.setattr(aviso_voz, "pasta_dados", lambda: tmp_path)

    def sem_rede(pedido, timeout):
        raise OSError("sem rede")

    monkeypatch.setattr(aviso_voz.urllib.request, "urlopen", sem_rede)
    assert aviso_voz.baixar_audio("frase") is None
    monkeypatch.setattr(aviso_voz.urllib.request, "urlopen",
                        lambda pedido, timeout: _RespostaFalsa(b"<html>", "text/html"))
    assert aviso_voz.baixar_audio("frase") is None
    assert not aviso_voz.caminho_audio("frase").exists()
