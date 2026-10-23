"""Conexões de saída do motor (rede.abrir_conexao)."""
import asyncio

from proxy_manager.nucleo import rede


def test_endereco_mudo_passa_a_vez_ao_proximo(monkeypatch):
    monkeypatch.setattr(rede, "TEMPO_POR_ENDERECO", 0.2)

    async def cenario():
        async def atender(leitor, escritor):
            escritor.write(b"ok")
            await escritor.drain()
            escritor.close()

        servidor = await asyncio.start_server(atender, "127.0.0.1", 0)
        porta = servidor.sockets[0].getsockname()[1]

        async def resolver_falso(host):
            # o primeiro endereço do nome não responde (SYN perdido); o segundo funciona
            return ["192.0.2.1", "127.0.0.1"]

        loop = asyncio.get_running_loop()
        conectar_original = loop.sock_connect

        async def conectar(sock, endereco):
            if endereco[0] == "192.0.2.1":
                await asyncio.sleep(30)
            return await conectar_original(sock, endereco)

        monkeypatch.setattr(rede, "resolvedor", resolver_falso)
        monkeypatch.setattr(loop, "sock_connect", conectar)
        try:
            inicio = loop.time()
            leitor, escritor = await rede.abrir_conexao("nome.exemplo", porta, timeout=5)
            assert await leitor.read() == b"ok"
            assert loop.time() - inicio < 2
            escritor.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(cenario())
