"""Qual Forja3D está aberto: a versão e a data da atualização, para saber se a janela já tem o que mudou."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from forja3d.configuracao import EMPACOTADO

VERSAO = "0.1.0"  # Igual à de pyproject.toml. Um teste confere.


def data_da_atualizacao() -> datetime:
    """Instalado, é a data do executável. Pelo código, a do arquivo mudado por último."""
    if EMPACOTADO:
        return datetime.fromtimestamp(Path(sys.executable).stat().st_mtime)
    pasta = Path(__file__).parent
    return datetime.fromtimestamp(max(p.stat().st_mtime for p in pasta.rglob("*") if p.is_file() and "__pycache__" not in p.parts))


def texto_da_versao() -> str:
    return f"Forja3D {VERSAO}, atualização de {data_da_atualizacao():%d/%m/%Y %H:%M}"
