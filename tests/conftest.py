import pytest

from proxy_manager.nucleo import dns

# A função de verdade, para o teste dela mesma.
LIMPAR_CACHE_SISTEMA = dns.limpar_cache_sistema


@pytest.fixture(autouse=True)
def limpezas_cache_dns(monkeypatch):
    """Os testes não limpam o cache DNS de verdade da máquina; registram as chamadas."""
    chamadas: list[None] = []
    monkeypatch.setattr(dns, "limpar_cache_sistema", lambda: chamadas.append(None))
    return chamadas
