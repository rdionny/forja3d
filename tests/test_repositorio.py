"""Garante que segredos e arquivos pesados não entram no git."""

import subprocess
import tomllib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def git(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argumentos], check=False, cwd=RAIZ, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize(
    "caminho",
    [
        ".venv/",
        "ferramentas/",
        "saida/",
        "projetos/",
        "config/ia_padrao.toml",
        "PLANEJAMENTO.md",
        "PROMPT_DESENVOLVIMENTO.md",
        "src/forja3d/__pycache__/",
    ],
)
def test_caminho_fora_do_git(caminho):
    assert git("check-ignore", "-q", caminho).returncode == 0


@pytest.mark.parametrize(
    "caminho",
    [
        "config/impressora_ender3_v3_se.json",
        "config/orca/maquina.json",
        "config/ia_padrao.example.toml",
        "PROMPT_AGENTE_3D.md",
        "README.md",
        "src/forja3d/projetos/armazenamento.py",
        "src/forja3d/agente/orquestrador.py",
    ],
)
def test_caminho_dentro_do_git(caminho):
    assert git("check-ignore", "-q", caminho).returncode == 1


def test_chave_nao_esta_em_nada_que_vai_para_o_git():
    config = RAIZ / "config" / "ia_padrao.toml"
    if not config.is_file():
        pytest.skip("config/ia_padrao.toml não existe nesta máquina")
    chave = tomllib.loads(config.read_text(encoding="utf-8"))["ia"]["api_key"].encode()
    arquivos = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout.split("\0")
    com_chave = [a for a in arquivos if a and (RAIZ / a).is_file() and chave in (RAIZ / a).read_bytes()]
    assert com_chave == []
