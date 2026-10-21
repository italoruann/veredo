# Veredo

Cliente de proxy para Windows e Linux. Você diz quais programas e quais sites passam pelo
proxy, quais vão direto e quais são bloqueados, e acompanha cada conexão em tempo real.

Funciona com proxies SOCKS5, SOCKS4/4A, HTTP e HTTPS, com ou sem usuário e senha, permite ter
vários perfis cadastrados ao mesmo tempo e encadeá-los (cadeias de proxy).

> O projeto está em fase de testes. Se encontrar algum problema, abra uma issue contando o
> sistema operacional, o que você fez e o que apareceu no log.

## Instalação

Você precisa do [uv](https://docs.astral.sh/uv/), que cuida do Python e das dependências.
Se ainda não tiver:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Depois, baixe o projeto e instale as dependências:

```bash
git clone https://github.com/italoruann/veredo.git
cd veredo
uv sync
```

O projeto usa Python 3.14. Se ele não estiver instalado, o `uv` baixa automaticamente.

Para abrir:

```bash
uv run python -m proxy_manager
```

No Linux, se faltar alguma biblioteca que o Qt precisa (a mais comum é a `libxcb-cursor`), o
programa tenta instalá-la pelo gerenciador de pacotes da distribuição (apt, dnf, pacman ou
zypper) e pede a senha. Para desligar esse comportamento, defina `VEREDO_SEM_DEPENDENCIAS=1`.

## Primeiros passos

1. Abra **Perfil > Servidores proxy** (Ctrl+1) e cadastre o seu proxy. O botão **Testar conexão**
   confere se o endereço e a senha estão certos.
2. Abra **Perfil > Regras de proxificação** (Ctrl+2) e defina o que deve passar pelo proxy.
3. Inicie o motor em **Arquivo > Iniciar motor** (Ctrl+E).
4. Para que os navegadores usem o Veredo sem configuração manual, ative em
   **Perfil > Configurações** (Ctrl+3) a opção de configurar o proxy do sistema.

A janela principal mostra as conexões abertas, o gráfico de tráfego, as estatísticas da sessão
e um log com uma linha por conexão. Na barra de status aparece o IP de saída do proxy, que é
consultado no [ip-api.com](https://ip-api.com) pelo próprio proxy.

Ao começar, nenhum proxy vem cadastrado e as regras de exemplo não afetam sites reais. Cada
pessoa monta a sua configuração.

## Teste de vazamento

Com o motor ligado, **Arquivo > Testar vazamentos** abre no navegador padrão a página
`http://127.0.0.1:58090/teste` (a porta é a do PAC). Para testar outro navegador, abra esse
endereço nele. A página confere, de dentro do navegador:

- **IP (IPv4 e IPv6):** o endereço que os sites veem, comparado com o IP real da máquina, que o
  Veredo consulta por conexão direta, e com os IPs de saída dos proxies.
- **WebRTC:** os endereços públicos que o navegador descobre por STUN. No modo explícito o UDP não
  passa pelo Veredo, então é comum o IP real aparecer aqui.
- **DNS:** a situação conforme a configuração (DNS pelo proxy no modo transparente ou não).

## Regras

As regras podem ser editadas em uma tabela ou como texto. As duas formas mostram a mesma coisa.

```
# Tudo do Chrome e do Firefox vai pelo proxy padrão
apps: chrome, firefox
*

# Para os demais programas
apps: *
*.empresa.com.br +proxy:Trabalho
*.meubanco.com.br +direct
10.0.0.0/8 +direct
anuncios.exemplo.com +block
!*.teste.com
```

| Linha                    | Efeito                                                       |
| ------------------------ | ------------------------------------------------------------ |
| `site.com`               | vai pelo proxy padrão                                        |
| `site.com +direct`       | vai direto, sem proxy                                        |
| `site.com +proxy:Nome`   | vai pelo perfil de proxy com esse nome                       |
| `site.com +chain:Nome`   | vai pela cadeia de proxy com esse nome                       |
| `site.com +block`        | a conexão é recusada                                         |
| `site.com:443`           | só a porta 443 (também `:80;443`, `:8000-8100`)               |
| `10.0.0.1-10.0.0.50`     | faixa de IPs                                                 |
| `apps: a, b`             | as linhas seguintes valem só para esses programas            |
| `apps: *`                | as linhas seguintes voltam a valer para qualquer programa    |
| `!site.com`              | regra desativada (fica no texto, mas é ignorada)             |
| `# texto`                | comentário                                                   |

Alguns detalhes:

- As regras são lidas de cima para baixo e vale a primeira que casar. Coloque as mais
  específicas antes das mais gerais.
- Conexões que não casam com nenhuma regra seguem a ação padrão, definida nas Configurações
  (direto ou bloquear).
- O alvo pode ser um domínio (`exemplo.com`), um curinga (`*.exemplo.com`, que também pega o
  próprio `exemplo.com`), um IP (`192.168.0.10`, ou `[2001:db8::1]` em IPv6), uma faixa CIDR
  (`10.0.0.0/8`) ou um intervalo (`10.0.0.1-10.0.0.50`). `*` sozinho pega qualquer destino.
  Depois do alvo podem vir portas: `*:22`, `*.exemplo.com:80;443`, `*:5000-6000`.
- Conexões para as portas do próprio Veredo são bloqueadas (evita loop), e as que vão ao
  endereço de um proxy cadastrado saem direto.
- O programa é identificado pelo nome do executável, sem diferença entre maiúsculas e
  minúsculas e com ou sem `.exe`. Na seção **Aplicativos cadastrados** da tela de regras dá para
  escolher o executável em vez de digitar o nome.
- Duas linhas `apps:` seguidas se somam: `apps: chrome` e `apps: firefox` em sequência valem
  para os dois.

## Cadeias de proxy

Em **Perfil > Cadeias de proxy** (Ctrl+4) você junta perfis numa cadeia e a usa nas regras com
`+chain:Nome`. Há três tipos:

- **Simples:** a conexão passa por todos os proxies, um dentro do outro, na ordem da lista.
- **Redundância:** usa o primeiro que responder; se ele cair, tenta o próximo.
- **Balanceamento:** alterna o proxy de saída a cada conexão, pulando os que não respondem.

## Importar e exportar perfil

**Arquivo > Exportar perfil** grava proxies, cadeias, aplicativos e regras num arquivo JSON (as
senhas vão junto só se você pedir). **Arquivo > Importar perfil** lê esse arquivo ou um perfil
`.ppx` do Proxifier, substituindo a configuração atual depois de confirmar.

## Como o tráfego chega ao Veredo

### Modo explícito (padrão)

O Veredo abre três portas locais em `127.0.0.1`:

| Porta | Uso                                                        |
| ----- | ---------------------------------------------------------- |
| 58080 | proxy SOCKS5 (também aceita SOCKS4/4A e UDP ASSOCIATE)      |
| 58081 | proxy HTTP/HTTPS                                           |
| 58090 | arquivo PAC (`http://127.0.0.1:58090/proxy.pac`)           |

Qualquer programa que aceite configurar proxy pode apontar para essas portas. Com a opção de
proxy do sistema ativada, o Veredo faz isso sozinho enquanto o motor estiver ligado e desfaz
quando ele para:

- **Windows:** configura o PAC nas opções de internet do sistema, que Chrome, Edge e Firefox
  seguem.
- **Linux:** configura diretamente os navegadores. No Firefox (e derivados, inclusive Flatpak e
  Snap) o PAC é gravado no `user.js` de cada perfil, guardando os valores anteriores. Nos
  navegadores baseados em Chromium, é criada uma cópia do atalho do menu em
  `~/.local/share/applications` com `--proxy-pac-url`. Também são configurados o GNOME
  (gsettings) e o KDE (kioslaverc), e é gerado um `env.sh` com as variáveis `http_proxy` e
  `https_proxy` para programas de terminal. Depois de ligar o motor, feche o navegador e abra de
  novo pelo menu.

Não é preciso rodar como administrador neste modo.

### Modo transparente

Ativado em **Configurações > Modo transparente**. As conexões são interceptadas no próprio
sistema, então até programas que ignoram a configuração de proxy passam pelas regras. No Linux
é usado o `nftables`; no Windows, o driver WinDivert.

TCP sobre IPv4 e IPv6 é interceptado. Opções do mesmo cartão:

- **DNS pelo proxy:** um servidor DNS local responde aos programas com endereços falsos
  (`198.18.x.x`). Quando o programa conecta num deles, o Veredo sabe o nome do site, aplica as
  regras por domínio e manda o nome para o proxy resolver. Os nomes da lista de exceções vão ao
  DNS normal.
- **Prevenção de vazamento:** liga o DNS pelo proxy, responde vazio às consultas DNS que não são
  de endereço e bloqueia o UDP que sairia direto (o WebRTC revelaria o seu IP).
- **UDP pelas regras:** cada fluxo UDP (jogos, chamadas, QUIC) segue as regras: direto,
  bloqueado ou por um proxy SOCKS5 que aceite UDP. Desligado, só o QUIC é bloqueado, para os
  navegadores usarem TCP. No Linux usa TPROXY.

Este modo exige administrador (Windows) ou root (Linux). Veja abaixo como gerar o executável
com essa permissão, ou use o serviço do sistema.

### Serviço do sistema

Em **Configurações > Serviço do sistema**, **Instalar serviço** faz o motor rodar sem a janela,
iniciado com o computador e antes de qualquer login. No Windows é uma tarefa agendada que roda
como SYSTEM; no Linux, a unidade `veredo.service` do systemd. A janela passa a controlar o
serviço: liga e desliga o motor, envia a configuração e mostra o log ao vivo. Fechar a janela
não para o serviço.

Também dá para usar pela linha de comando (como administrador/root):

```bash
uv run python -m proxy_manager --instalar-servico
uv run python -m proxy_manager --remover-servico
uv run python -m proxy_manager --servico   # roda o serviço em primeiro plano
```

O serviço guarda a configuração, com as senhas, em `%ProgramData%\Veredo\servico.json` ou
`/var/lib/veredo/servico.json`, legível só pelo administrador. O canal de controle escuta em
`127.0.0.1:58098` e exige o token de `controle.token`, na mesma pasta: isso impede páginas da
web de comandar o serviço, mas qualquer usuário da máquina pode configurá-lo.

## Gerar o executável

Os scripts geram um executável que roda sem Python instalado. Eles fecham o Veredo se estiver
aberto e apagam o build anterior antes de começar.

**Windows** (gera `dist\Veredo.exe`):

```powershell
.\scripts\build_windows.ps1          # roda como usuário comum
.\scripts\build_windows.ps1 -Admin   # pede permissão de administrador ao abrir (modo transparente)
.\scripts\build_windows.ps1 -Root    # como -Admin, e instala em Program Files com atalho no menu Iniciar
.\scripts\build_windows.ps1 -Uninstall  # remove a instalação
```

Com `-Root`, o executável é instalado em `C:\Program Files\Veredo`, pasta em que só o
administrador escreve, e o atalho **Veredo** vai para o menu Iniciar. Ao abrir, ele pede o UAC e
roda como administrador, com o modo transparente e as demais opções avançadas liberados.

**Linux** (gera `dist/veredo`; rode no próprio Linux):

```bash
./scripts/build_linux.sh                    # só gera o executável
./scripts/build_linux.sh --install          # instala para o seu usuário, com atalho no menu
./scripts/build_linux.sh --install --admin  # instala em /opt e abre pedindo senha (modo transparente)
./scripts/build_linux.sh --root             # instala em /opt com o motor como serviço root
./scripts/build_linux.sh --uninstall        # remove as instalações (e o serviço)
```

Com `--root`, a senha do `sudo` é pedida uma vez, na instalação. O motor passa a rodar como root
no serviço `veredo.service`, iniciado com o sistema, e o modo transparente funciona sem pedir
senha. O atalho do menu abre só a janela, como usuário comum, e ela controla o serviço (veja
[Serviço do sistema](#serviço-do-sistema)).

## Onde ficam os dados

Tudo fica na pasta do seu usuário. **Arquivo > Abrir pasta de dados** abre o local certo.

| Sistema | Pasta                                                     |
| ------- | --------------------------------------------------------- |
| Windows | `%LOCALAPPDATA%\Veredo\Veredo`                            |
| Linux   | `~/.config/Veredo` (configuração) e `~/.local/share/Veredo` (dados) |

- `config.json`: perfis, aplicativos, regras e preferências (com uma cópia `config.json.bak`).
- `conexoes.db`: histórico das conexões, apagado automaticamente depois do número de dias
  definido nas Configurações.
- `ip_saida.json`: último IP de saída de cada perfil.

As senhas dos proxies são guardadas no cofre do sistema (Gerenciador de Credenciais no Windows,
Secret Service no Linux). Se não houver cofre disponível, ficam no `config.json`.

## Limitações

- UDP só passa por proxies SOCKS5, e não por cadeias simples (um proxy dentro do outro). No
  modo explícito, UDP só passa se o programa usar o UDP ASSOCIATE do SOCKS5.
- Regras por domínio em conexões SOCKS5 só funcionam se o programa enviar o nome do site ao
  proxy (DNS remoto). Se ele mandar só o IP, valem apenas as regras por IP e CIDR. No modo
  transparente, o DNS pelo proxy resolve isso.
- Autenticação NTLM/Kerberos em proxies HTTP não é suportada (só usuário e senha).
- No Linux, o modo transparente não intercepta os programas do mesmo usuário que roda o Veredo
  (é assim que ele evita capturar a si mesmo). Rodando como root ou como serviço, os programas
  do root saem direto.
- No Linux, navegadores baseados em Chromium só pegam o proxy quando abertos pelo menu ou por
  um link de outro programa. Pelo terminal, passe `--proxy-pac-url=http://127.0.0.1:58090/proxy.pac`.
- O modo transparente (Windows e Linux), o DNS pelo proxy, o UDP pelas regras, o serviço do
  sistema e o pacote para Linux ainda não foram testados em máquinas reais. Use com cuidado.

## Desenvolvimento

```bash
uv run pytest                        # todos os testes
uv run pytest tests/test_regras.py   # um arquivo
```

O código fica em `proxy_manager/`:

- `nucleo/`: motor, protocolos SOCKS5 e HTTP, regras, configuração, registro de conexões e
  integração com o sistema. Não depende do Qt.
- `nucleo/transparente/`: modo transparente (`linux_nftables.py`, `windows_windivert.py`) e a
  leitura de SNI/Host (`inspecao.py`).
- `interface/`: janela principal, painéis e as telas de proxies, regras e configurações
  (PySide6).

Para adicionar ou remover dependências use `uv add` e `uv remove`, assim o `uv.lock` continua
em sincronia com o `pyproject.toml`.

## Licença

Distribuído sob a licença MIT. Veja o arquivo [LICENSE](LICENSE).
