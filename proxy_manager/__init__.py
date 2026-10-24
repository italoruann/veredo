import sys

__version__ = "0.1.0"

# Python 3.15+: o gancho de import do PySide6 deixa vazar os objetos dos imports preguiçosos
# da stdlib (ex.: `concurrent.futures.ThreadPoolExecutor` vira um 'lazy_import' não chamável,
# e o asyncio quebra). Precisa rodar antes do PySide6 ser importado: força tudo a ser eager.
if hasattr(sys, "set_lazy_imports_filter"):
    sys.set_lazy_imports_filter(lambda *_: False)
