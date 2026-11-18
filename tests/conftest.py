from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import pytest

from forja3d.agente.especificacao import Especificacao
from forja3d.impressora import carregar_perfil

DADOS = Path(__file__).parent / "dados"


@pytest.fixture(autouse=True)
def codex_de_mentira(tmp_path, monkeypatch):
    """Nenhum teste pode mexer no config.toml do Codex de verdade: salvar as configurações liga o Forja3D nele."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_de_teste"))


@pytest.fixture(autouse=True)
def lixo_coletado_na_thread_principal():
    """A janela que um teste fecha vira lixo. Se a coleta cair na thread de trabalho do teste seguinte,
    o Qt destrói a janela fora da thread principal e o processo cai: no Linux, com falha de segmentação."""
    yield
    gc.collect()


def pytest_sessionfinish(session, exitstatus):
    session.config.codigo_de_saida = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # shortcut: no Linux, o PySide 6.12 às vezes derruba o Python na coleta de lixo final, depois de todos os testes
    # ("bool_dealloc"). Sair sem a finalização mantém o código de saída real. Tirar quando o PySide corrigir.
    if sys.platform.startswith("linux") and hasattr(config, "codigo_de_saida"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(config.codigo_de_saida)


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
