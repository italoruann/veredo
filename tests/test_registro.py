"""Testes do registro de conexões."""
import pytest

from proxy_manager.nucleo import registro as modulo_registro
from proxy_manager.nucleo.registro import EntradaLog, Registro


@pytest.fixture()
def pasta_isolada(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_registro, "pasta_dados", lambda: tmp_path)
    return tmp_path


def test_memoria_respeita_o_limite(pasta_isolada):
    registro = Registro(dias_retencao=1, max_em_memoria=10)
    for i in range(100):
        registro.adicionar(EntradaLog(host_destino=f"host{i}.example.com"))

    assert len(registro._entradas) == 10
    assert len(registro._ordem) == 10
    recentes = registro.recentes(limite=10)
    assert [e.host_destino for e in recentes] == [f"host{i}.example.com" for i in range(90, 100)]


def test_atualizar_entrada_descartada_nao_a_recria(pasta_isolada):
    registro = Registro(dias_retencao=1, max_em_memoria=2)
    primeira = registro.adicionar(EntradaLog(host_destino="a.example.com"))
    registro.adicionar(EntradaLog(host_destino="b.example.com"))
    registro.adicionar(EntradaLog(host_destino="c.example.com"))

    assert primeira.id not in registro._entradas
    assert registro.atualizar(primeira.id, estado="concluida") is None


def test_conexao_encerrada_vai_para_o_historico(pasta_isolada):
    registro = Registro(dias_retencao=1)
    entrada = registro.adicionar(EntradaLog(host_destino="a.example.com", porta_destino=443))
    registro.atualizar(entrada.id, estado="concluida", bytes_enviados=10, bytes_recebidos=20)

    historico = registro.historico()
    assert len(historico) == 1
    assert historico[0].host_destino == "a.example.com"
    assert historico[0].bytes_recebidos == 20

    registro.limpar_historico()
    assert registro.historico() == []
