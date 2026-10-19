"""Modo transparente: intercepta o tráfego no sistema, sem configurar proxy nos aplicativos.

Exige root/administrador. Linux usa nftables + SO_ORIGINAL_DST; Windows usa o WinDivert.
Apenas TCP/IPv4.
"""
