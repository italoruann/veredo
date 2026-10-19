# Veredo

Cliente de proxy para Windows e Linux. Você diz quais programas e quais sites passam pelo
proxy, quais vão direto e quais são bloqueados, e acompanha cada conexão em tempo real.

Funciona com proxies SOCKS5 e HTTP, com ou sem usuário e senha, e permite ter vários perfis
cadastrados ao mesmo tempo.

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
| `site.com +block`        | a conexão é recusada                                         |
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
  próprio `exemplo.com`), um IP (`192.168.0.10`) ou uma faixa CIDR (`10.0.0.0/8`). `*` sozinho
  pega qualquer destino.
- O programa é identificado pelo nome do executável, sem diferença entre maiúsculas e
  minúsculas e com ou sem `.exe`. Na seção **Aplicativos cadastrados** da tela de regras dá para
  escolher o executável em vez de digitar o nome.
- Duas linhas `apps:` seguidas se somam: `apps: chrome` e `apps: firefox` em sequência valem
  para os dois.

## Como o tráfego chega ao Veredo

### Modo explícito (padrão)

O Veredo abre três portas locais em `127.0.0.1`:

| Porta | Uso                                                        |
| ----- | ---------------------------------------------------------- |
| 58080 | proxy SOCKS5                                               |
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

Este modo exige administrador (Windows) ou root (Linux). Veja abaixo como gerar o executável
com essa permissão.

## Gerar o executável

Os scripts geram um executável que roda sem Python instalado. Eles fecham o Veredo se estiver
aberto e apagam o build anterior antes de começar.

**Windows** (gera `dist\Veredo.exe`):

```powershell
.\scripts\build_windows.ps1          # roda como usuário comum
.\scripts\build_windows.ps1 -Admin   # pede permissão de administrador ao abrir (modo transparente)
```

**Linux** (gera `dist/veredo`; rode no próprio Linux):

```bash
./scripts/build_linux.sh                    # só gera o executável
./scripts/build_linux.sh --install          # instala para o seu usuário, com atalho no menu
./scripts/build_linux.sh --install --admin  # instala em /opt e abre pedindo senha (modo transparente)
./scripts/build_linux.sh --uninstall        # remove as instalações
```

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

- Só TCP sobre IPv4. QUIC/HTTP3 e IPv6 não passam pelo proxy. No modo transparente do Linux
  eles são bloqueados para que os programas voltem ao TCP/IPv4.
- Regras por domínio em conexões SOCKS5 só funcionam se o programa enviar o nome do site ao
  proxy (DNS remoto). Se ele mandar só o IP, valem apenas as regras por IP e CIDR.
- No Linux, navegadores baseados em Chromium só pegam o proxy quando abertos pelo menu ou por
  um link de outro programa. Pelo terminal, passe `--proxy-pac-url=http://127.0.0.1:58090/proxy.pac`.
- O modo transparente no Windows e o pacote para Linux ainda não foram testados em máquinas
  reais. Use com cuidado.

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
