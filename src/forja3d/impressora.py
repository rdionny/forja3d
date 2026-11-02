"""Perfil da impressora: a fonte única de limites do projeto."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from forja3d.configuracao import PASTA_CONFIG

CAMINHO_PERFIL = PASTA_CONFIG / "impressora_ender3_v3_se.json"
CHAVES_OBRIGATORIAS = ("volume_mm", "bico", "mesa", "materiais", "qualidades", "validacao_gcode", "orca")


@lru_cache(maxsize=1)
def carregar_perfil(caminho: Path = CAMINHO_PERFIL) -> dict[str, Any]:
    perfil = json.loads(Path(caminho).read_text(encoding="utf-8"))
    faltando = [c for c in CHAVES_OBRIGATORIAS if c not in perfil]
    if faltando:
        raise ValueError(f"Perfil da impressora incompleto: faltam {faltando}")
    return perfil
