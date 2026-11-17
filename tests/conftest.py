from __future__ import annotations

from pathlib import Path

import pytest

from forja3d.agente.especificacao import Especificacao
from forja3d.impressora import carregar_perfil

DADOS = Path(__file__).parent / "dados"


@pytest.fixture(autouse=True)
def codex_de_mentira(tmp_path, monkeypatch):
    """Nenhum teste pode mexer no config.toml do Codex de verdade: salvar as configurações liga o Forja3D nele."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_de_teste"))


@pytest.fixture
def perfil():
    return carregar_perfil()


@pytest.fixture
def espec_suporte():
    """O pedido original do suporte em L: 2 furos de Ø4 na base."""
    return Especificacao(
        nome="suporte_l",
        finalidade="teste",
        material="PLA",
        carga="leve",
        dimensoes={"x_mm": 40, "y_mm": 30, "z_mm": 34},
        pecas=1,
        furos=[{"quantidade": 2, "diametro_nominal_mm": 4, "diametro_modelado_mm": 4, "eixo": "Z", "passante": True}],
        orientacao_impressao="base na mesa",
    )
