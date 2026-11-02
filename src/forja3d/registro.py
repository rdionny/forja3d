"""Logs em arquivo, com rotação e sem a chave da API."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from forja3d.configuracao import pasta_usuario


class FiltroChave(logging.Filter):
    """Troca a chave por um marcador, mesmo que ela apareça em uma mensagem de biblioteca."""

    def __init__(self, *chaves: str) -> None:
        super().__init__()
        self._chaves = [c for c in chaves if c]

    def filter(self, registro: logging.LogRecord) -> bool:
        if self._chaves:
            mensagem = registro.getMessage()
            limpa = mensagem
            for chave in self._chaves:
                limpa = limpa.replace(chave, "***")
            if limpa != mensagem:
                registro.msg = limpa
                registro.args = ()
        return True


def proteger_chaves(*chaves: str) -> None:
    """Chaves novas, de um provedor adicionado com o app aberto, também saem dos logs."""
    for manipulador in logging.getLogger().handlers:
        if isinstance(manipulador, RotatingFileHandler):
            manipulador.addFilter(FiltroChave(*chaves))


def configurar_logs(*chaves: str) -> None:
    pasta = pasta_usuario() / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = RotatingFileHandler(pasta / "forja3d.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    arquivo.addFilter(FiltroChave(*chaves))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.handlers = [arquivo]
    # As bibliotecas HTTP registram cabeçalhos em DEBUG: nunca descer desse nível para elas.
    for nome in ("httpx", "httpcore", "openai"):
        logging.getLogger(nome).setLevel(logging.WARNING)
