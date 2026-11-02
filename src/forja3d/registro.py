"""Logs em arquivo, com rotação e sem a chave da API."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from forja3d.configuracao import pasta_usuario


class FiltroChave(logging.Filter):
    """Troca a chave por um marcador, mesmo que ela apareça em uma mensagem de biblioteca."""

    def __init__(self, chave: str) -> None:
        super().__init__()
        self._chave = chave

    def filter(self, registro: logging.LogRecord) -> bool:
        if self._chave:
            mensagem = registro.getMessage()
            if self._chave in mensagem:
                registro.msg = mensagem.replace(self._chave, "***")
                registro.args = ()
        return True


def configurar_logs(chave: str) -> None:
    pasta = pasta_usuario() / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = RotatingFileHandler(pasta / "forja3d.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    arquivo.addFilter(FiltroChave(chave))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.handlers = [arquivo]
    # As bibliotecas HTTP registram cabeçalhos em DEBUG: nunca descer desse nível para elas.
    for nome in ("httpx", "httpcore", "openai"):
        logging.getLogger(nome).setLevel(logging.WARNING)
