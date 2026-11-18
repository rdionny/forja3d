"""Confere as ferramentas oferecidas ao agente, em especial o que ele não pode fazer."""

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PERFIL = json.loads((RAIZ / "config" / "impressora_ender3_v3_se.json").read_text(encoding="utf-8"))
LISTA = json.loads((RAIZ / "src" / "forja3d" / "agente" / "ferramentas.json").read_text(encoding="utf-8"))
FERRAMENTAS = {item["function"]["name"]: item["function"] for item in LISTA}

OBRIGATORIOS = {
    "definir_especificacao": {
        "nome",
        "finalidade",
        "material",
        "carga",
        "dimensoes",
        "pecas",
        "furos",
        "orientacao_impressao",
        "premissas",
    },
    "executar_cad": {"codigo", "resumo_alteracao"},
    "editar_cad": {"trocas", "resumo_alteracao"},
    "verificar_peca": set(),
    "renderizar_vistas": set(),
    "definir_cores": {"parte"},
    "fatiar_peca": {"material", "qualidade", "paredes", "preenchimento_pct", "suporte", "brim"},
    "pesquisar_na_web": {"consulta"},
    "ler_pagina_web": {"url"},
}


def test_lista_de_ferramentas():
    assert set(FERRAMENTAS) == set(OBRIGATORIOS)
    assert all(item["type"] == "function" for item in LISTA)


@pytest.mark.parametrize("nome", OBRIGATORIOS)
def test_campos_obrigatorios(nome):
    assert set(FERRAMENTAS[nome]["parameters"].get("required", [])) == OBRIGATORIOS[nome]


def test_furos_descrevem_diametro_nominal_e_modelado():
    furos = FERRAMENTAS["definir_especificacao"]["parameters"]["properties"]["furos"]["items"]
    assert set(furos["required"]) == {"quantidade", "diametro_nominal_mm", "diametro_modelado_mm", "eixo", "passante"}


@pytest.mark.parametrize("nome", ["definir_especificacao", "fatiar_peca"])
def test_materiais_iguais_ao_perfil(nome):
    assert set(FERRAMENTAS[nome]["parameters"]["properties"]["material"]["enum"]) == set(PERFIL["materiais"])


def test_qualidades_iguais_ao_perfil():
    assert set(FERRAMENTAS["fatiar_peca"]["parameters"]["properties"]["qualidade"]["enum"]) == set(PERFIL["qualidades"])


def test_cores_iguais_a_paleta():
    from forja3d.fatiamento.cores import PALETA

    nomes = [nome for nome, _ in PALETA]
    propriedades = FERRAMENTAS["definir_cores"]["parameters"]["properties"]
    assert propriedades["cor"]["enum"] == nomes
    assert propriedades["trocas"]["items"]["properties"]["cor"]["enum"] == nomes
    assert propriedades["corpos"]["items"]["properties"]["cor"]["enum"] == nomes


def test_agente_nao_tem_como_mexer_em_gcode_temperatura_ou_exportacao():
    texto = json.dumps(
        {nome: sorted(dados["parameters"].get("properties", {})) for nome, dados in FERRAMENTAS.items()}
    ).lower()
    for proibido in ("gcode", "g-code", "export", "temperatura", "temp_", "bico", "mesa"):
        assert proibido not in texto
