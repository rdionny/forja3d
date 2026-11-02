"""Confere se o prompt do agente acompanha o perfil da máquina e as ferramentas."""

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PERFIL = json.loads((RAIZ / "config" / "impressora_ender3_v3_se.json").read_text(encoding="utf-8"))
PROMPT = (RAIZ / "src" / "forja3d" / "ia" / "prompts" / "agente_3d.md").read_text(encoding="utf-8")
FERRAMENTAS = json.loads((RAIZ / "src" / "forja3d" / "agente" / "ferramentas.json").read_text(encoding="utf-8"))


def linha_de_material(rotulo: str, formato) -> str:
    colunas = " | ".join(formato(PERFIL["materiais"][m]) for m in ("PLA", "PETG", "TPU"))
    return f"| {rotulo} | {colunas} |"


def numero(valor: float) -> str:
    return f"{valor:g}".replace(".", ",")


def test_prompt_igual_ao_de_docs():
    assert PROMPT == (RAIZ / "docs" / "PROMPT_AGENTE_3D.md").read_text(encoding="utf-8")


def test_calibracao_tem_um_unico_ponto_de_insercao():
    assert PROMPT.count("{{CALIBRACAO_USUARIO}}") == 1


def test_volumes_iguais_ao_perfil():
    volume = PERFIL["volume_mm"]
    for chave in ("fisico", "seguro"):
        dados = volume[chave]
        assert f"{dados['x']} × {dados['y']} × {dados['z']} mm" in PROMPT
    assert f"acima de {volume['maximo_fatiador_xy']} mm" in PROMPT


@pytest.mark.parametrize(
    ("rotulo", "formato"),
    [
        ("Bico", lambda m: f"{m['bico_c']} °C ({m['bico_min_c']}–{m['bico_max_c']})"),
        ("Mesa", lambda m: f"{m['mesa_c']} °C"),
        ("Vazão máxima", lambda m: f"{numero(m['vazao_max_mm3_s'])} mm³/s"),
        ("Retração", lambda m: f"{numero(m['retracao_mm'])} mm"),
        ("Densidade", lambda m: f"{numero(m['densidade_g_cm3'])} g/cm³"),
    ],
)
def test_tabela_de_materiais_igual_ao_perfil(rotulo, formato):
    assert linha_de_material(rotulo, formato) in PROMPT


def test_limites_de_temperatura_iguais_ao_perfil():
    assert f"| Temperatura máxima do bico | {PERFIL['bico']['temp_max_c']} °C" in PROMPT
    assert f"| Temperatura máxima da mesa | {PERFIL['mesa']['temp_max_c']} °C" in PROMPT


@pytest.mark.parametrize("nome", [item["function"]["name"] for item in FERRAMENTAS])
def test_prompt_explica_cada_ferramenta(nome):
    assert f"| `{nome}` |" in PROMPT


@pytest.mark.parametrize("qualidade", PERFIL["qualidades"])
def test_prompt_explica_cada_qualidade(qualidade):
    camada = f"{PERFIL['qualidades'][qualidade]['camada_mm']:.2f}".replace(".", ",")
    assert f"| `{qualidade}` | {camada} mm |" in PROMPT
