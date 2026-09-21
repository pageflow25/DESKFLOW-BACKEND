"""Log em console (UTF-8) e em arquivo com rotação diária."""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

FORMATO = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configurar_logging(diretorio: str = "logs", nivel: str = "INFO") -> None:
    raiz = logging.getLogger()
    if getattr(raiz, "_deskflow2_configurado", False):
        return

    raiz.setLevel(nivel.upper())
    formatador = logging.Formatter(FORMATO)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatador)
    raiz.addHandler(console)

    os.makedirs(diretorio, exist_ok=True)
    # Rotação à meia-noite: o nome do arquivo não fica preso à data em que o
    # processo subiu (o serviço roda por semanas).
    arquivo = TimedRotatingFileHandler(
        os.path.join(diretorio, "deskflow2.log"), when="midnight", backupCount=30, encoding="utf-8"
    )
    arquivo.setFormatter(formatador)
    raiz.addHandler(arquivo)

    erros = TimedRotatingFileHandler(
        os.path.join(diretorio, "deskflow2_erros.log"), when="midnight", backupCount=30, encoding="utf-8"
    )
    erros.setLevel(logging.ERROR)
    erros.setFormatter(formatador)
    raiz.addHandler(erros)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    raiz._deskflow2_configurado = True
