"""Interceptação HTTP/HTTPS (como o mitmproxy): o motor termina o TLS do aplicativo com um
certificado emitido por uma autoridade certificadora local, abre outro TLS até o destino e
registra cada requisição e resposta (cabeçalhos e corpos) como um fluxo."""
