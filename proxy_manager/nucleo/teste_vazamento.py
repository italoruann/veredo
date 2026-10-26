"""Página de teste de vazamento, servida pelo motor em http://127.0.0.1:<porta do PAC>/teste.

Aberta no navegador, a página consulta o IP pelo mesmo caminho que o navegador usa (proxy do
sistema, PAC ou modo transparente), coleta os endereços que o WebRTC revela pelo STUN e compara
tudo com o IP real da máquina e os IPs de saída dos proxies, que o motor põe na página."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

CAMINHO_TESTE = "/teste"


@dataclass
class DadosTeste:
    ip_real: str = ""
    ip_real_v6: str = ""
    ips_proxy: list[str] = field(default_factory=list)
    modo_transparente: bool = False
    modo_dns: str = "sistema"  # "sistema", "proxy" ou "personalizado", como o motor está usando
    prevenir_vazamento: bool = False
    proxy_sistema: bool = False


def url_teste(porta_pac: int) -> str:
    return f"http://127.0.0.1:{porta_pac}{CAMINHO_TESTE}"


def gerar_pagina(dados: DadosTeste) -> str:
    # "<" escapado para que um valor nunca feche a tag <script>.
    bruto = json.dumps(asdict(dados), ensure_ascii=False).replace("<", "\\u003c")
    return _PAGINA.replace("__DADOS__", bruto)


_PAGINA = """\
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Teste de vazamento</title>
<style>
:root { --fundo: #f6f7f9; --cartao: #ffffff; --texto: #1d2129; --suave: #5f6673; --borda: #dde1e7;
        --ok: #1a7f45; --aviso: #a86200; --falha: #c62828; }
@media (prefers-color-scheme: dark) {
  :root { --fundo: #15171c; --cartao: #1f2229; --texto: #e6e8ec; --suave: #9aa1ad; --borda: #30343d;
          --ok: #4cc27f; --aviso: #f0a93b; --falha: #ef6b6b; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--fundo); color: var(--texto);
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--suave); margin: 0 0 24px; }
.resumo { padding: 16px 18px; border-radius: 10px; border: 1px solid var(--borda); background: var(--cartao);
          font-weight: 600; margin-bottom: 20px; border-left: 5px solid var(--suave); }
.resumo.ok { border-left-color: var(--ok); } .resumo.aviso { border-left-color: var(--aviso); }
.resumo.falha { border-left-color: var(--falha); }
.item { background: var(--cartao); border: 1px solid var(--borda); border-radius: 10px; padding: 14px 18px;
        margin-bottom: 12px; }
.topo { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; flex-wrap: wrap; }
.nome { font-weight: 600; }
.selo { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; }
.selo.ok { color: var(--ok); } .selo.aviso { color: var(--aviso); } .selo.falha { color: var(--falha); }
.selo.esperando { color: var(--suave); }
.detalhe { color: var(--suave); margin-top: 4px; overflow-wrap: anywhere; }
code { font: 13px ui-monospace, Consolas, monospace; }
button { margin-top: 8px; padding: 8px 16px; border-radius: 8px; border: 1px solid var(--borda);
         background: var(--cartao); color: var(--texto); font: inherit; cursor: pointer; }
a { color: inherit; }
</style>
</head>
<body>
<main>
  <h1>Teste de vazamento do Veredo</h1>
  <p class="sub">Verifica o que os sites conseguem ver deste navegador.</p>
  <div id="resumo" class="resumo">Testando...</div>
  <div id="itens"></div>
  <p class="sub">Comparação: IP real <code id="real"></code> · IPs de saída dos proxies
    <code id="proxies"></code></p>
  <p class="sub">Para uma segunda opinião sobre DNS, use o teste estendido do
    <a href="https://www.dnsleaktest.com" target="_blank" rel="noopener">dnsleaktest.com</a>.</p>
  <button id="repetir" type="button">Testar de novo</button>
</main>
<script id="dados" type="application/json">__DADOS__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("dados").textContent);
const reais = [D.ip_real, D.ip_real_v6].filter(Boolean);
document.getElementById("real").textContent = reais.join(", ") || "não foi possível consultar";
document.getElementById("proxies").textContent = D.ips_proxy.join(", ") || "ainda não consultados";

const TESTES = [
  ["ipv4", "IP visto pelos sites (IPv4)"],
  ["ipv6", "IP visto pelos sites (IPv6)"],
  ["webrtc", "WebRTC"],
  ["dns", "DNS"],
];

function montar() {
  const lista = document.getElementById("itens");
  lista.textContent = "";
  for (const [id, nome] of TESTES) {
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = '<div class="topo"><span class="nome"></span><span class="selo esperando">testando</span></div>' +
                     '<div class="detalhe"></div>';
    item.querySelector(".nome").textContent = nome;
    item.id = "t-" + id;
    lista.appendChild(item);
  }
}

const ROTULOS = { ok: "ok", aviso: "atenção", falha: "vazamento" };

function marcar(id, estado, texto) {
  const item = document.getElementById("t-" + id);
  const selo = item.querySelector(".selo");
  selo.className = "selo " + estado;
  selo.textContent = ROTULOS[estado];
  item.querySelector(".detalhe").textContent = texto;
  return estado;
}

async function ipPorFetch(url) {
  const controle = new AbortController();
  const prazo = setTimeout(() => controle.abort(), 8000);
  try {
    const resposta = await fetch(url + "?format=json&_=" + Date.now(), { cache: "no-store", signal: controle.signal });
    return (await resposta.json()).ip || null;
  } catch (e) {
    return null;
  } finally {
    clearTimeout(prazo);
  }
}

function classificarIp(ip) {
  if (reais.includes(ip)) return "real";
  if (D.ips_proxy.includes(ip)) return "proxy";
  return "outro";
}

async function testarIpv4() {
  const ip = await ipPorFetch("https://api.ipify.org");
  if (!ip) {
    return marcar("ipv4", "aviso", "Não foi possível consultar. O proxy pode estar fora do ar ou uma regra " +
                  "está bloqueando o navegador.");
  }
  const tipo = classificarIp(ip);
  if (tipo === "real") {
    const dica = D.proxy_sistema || D.modo_transparente
      ? "Confira se as regras mandam este navegador para o proxy."
      : "O proxy do sistema está desativado: ative-o nas Configurações ou configure o navegador.";
    return marcar("ipv4", "falha", ip + " é o seu IP real: este navegador não está passando pelo proxy. " + dica);
  }
  if (tipo === "proxy") return marcar("ipv4", "ok", ip + " é o IP de saída do proxy.");
  if (!D.ip_real) {
    return marcar("ipv4", "aviso", ip + ". O IP real não pôde ser consultado, então não dá para comparar.");
  }
  return marcar("ipv4", "ok", ip + " não é o seu IP real. Pode ser um proxy rotativo ou um perfil que " +
                "o Veredo ainda não consultou.");
}

async function testarIpv6() {
  const ip = await ipPorFetch("https://api6.ipify.org");
  if (!ip) return marcar("ipv6", "ok", "Nenhum endereço IPv6 chega aos sites por este navegador.");
  if (reais.includes(ip)) {
    return marcar("ipv6", "falha", ip + " é o seu IPv6 real: as conexões IPv6 saem sem proxy.");
  }
  return marcar("ipv6", "ok", ip + " não é o seu IPv6 real.");
}

function coletarWebrtc() {
  return new Promise((resolver) => {
    const ips = new Set();
    let conexao;
    try {
      conexao = new RTCPeerConnection({ iceServers: [
        { urls: "stun:stun.l.google.com:19302" }, { urls: "stun:stun.cloudflare.com:3478" }] });
    } catch (e) {
      resolver(null);
      return;
    }
    const terminar = () => { try { conexao.close(); } catch (e) {} resolver([...ips]); };
    const prazo = setTimeout(terminar, 6000);
    conexao.onicecandidate = (evento) => {
      if (!evento.candidate) { clearTimeout(prazo); terminar(); return; }
      const partes = evento.candidate.candidate.split(" ");
      const tipo = partes[partes.indexOf("typ") + 1];
      if (tipo === "srflx" || tipo === "prflx") ips.add(partes[4]);
    };
    conexao.createDataChannel("teste");
    conexao.createOffer().then((oferta) => conexao.setLocalDescription(oferta)).catch(terminar);
  });
}

async function testarWebrtc() {
  const ips = await coletarWebrtc();
  if (ips === null) return marcar("webrtc", "ok", "O WebRTC está desativado neste navegador.");
  if (!ips.length) {
    return marcar("webrtc", "ok", "O WebRTC não conseguiu descobrir nenhum IP público (UDP bloqueado).");
  }
  const vazados = ips.filter((ip) => reais.includes(ip));
  if (vazados.length) {
    const dica = D.modo_transparente
      ? "Ative a Prevenção de vazamento ou o UDP pelas regras nas Configurações."
      : "No modo explícito o UDP não passa pelo Veredo: ative o Modo transparente com a Prevenção de " +
        "vazamento, ou desative o WebRTC no navegador.";
    return marcar("webrtc", "falha", "Revela o seu IP real (" + vazados.join(", ") + "). " + dica);
  }
  if (!D.ip_real) {
    return marcar("webrtc", "aviso", "Revela " + ips.join(", ") + ". O IP real não pôde ser consultado " +
                  "para comparar.");
  }
  return marcar("webrtc", "ok", "Revela apenas " + ips.join(", ") + ", que não é o seu IP real.");
}

function testarDns() {
  if (D.modo_transparente && D.modo_dns === "proxy") {
    return marcar("dns", "ok", "As consultas DNS são respondidas pelo Veredo e os nomes são resolvidos " +
                  "pelo proxy.");
  }
  if (D.modo_transparente && D.modo_dns === "personalizado") {
    return marcar("dns", "ok", "As consultas DNS vão aos servidores do DNS personalizado, pelo caminho " +
                  "que as regras mandam para eles (pelo proxy ou direto).");
  }
  if (D.modo_transparente) {
    return marcar("dns", "aviso", "As consultas DNS vão direto ao seu provedor, que vê os sites acessados. " +
                  "Escolha o DNS pelo proxy ou um DNS personalizado nas Configurações.");
  }
  return marcar("dns", "aviso", "No modo explícito o navegador manda o nome do site ao proxy, mas ainda " +
                "pode fazer consultas DNS próprias (pré-carregamento, DNS seguro). Para garantir, use o Modo " +
                "transparente com o DNS pelo proxy.");
}

async function testar() {
  montar();
  const resumo = document.getElementById("resumo");
  resumo.className = "resumo";
  resumo.textContent = "Testando...";
  const estados = await Promise.all([testarIpv4(), testarIpv6(), testarWebrtc(), testarDns()]);
  if (estados.includes("falha")) {
    resumo.className = "resumo falha";
    resumo.textContent = "Vazamento encontrado: os sites conseguem ver o seu IP real.";
  } else if (estados.includes("aviso")) {
    resumo.className = "resumo aviso";
    resumo.textContent = "Nenhum vazamento de IP encontrado, mas há pontos de atenção.";
  } else {
    resumo.className = "resumo ok";
    resumo.textContent = "Nenhum vazamento encontrado.";
  }
}

// Recarrega para o motor consultar de novo o IP real e os IPs dos proxies.
document.getElementById("repetir").addEventListener("click", () => location.reload());
testar();
</script>
</body>
</html>
"""
