"""Compara a peça com a especificação e com as regras de impressão.

Determinístico: nada aqui depende de IA. É o que impede que um script válido, mas
errado, seja entregue como certo.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from forja3d.agente.especificacao import Especificacao
from forja3d.verificacao.geometria import analisar_malha

BLOQUEIO, ALERTA, INFO = "BLOQUEIO", "ALERTA", "INFO"
EIXOS = {"X": [1.0, 0.0, 0.0], "Y": [0.0, 1.0, 0.0], "Z": [0.0, 0.0, 1.0]}
TOLERANCIA_DIAMETRO = 0.05
AREA_MINIMA_DE_BALANCO = 25.0
AREA_MINIMA_DE_BASE = 400.0
FRACAO_FINA_ALERTA = 0.01
FRACAO_FINA_BLOQUEIO = 0.10


def _item(codigo: str, severidade: str, mensagem: str, parte: str | None = None, **valores: Any) -> dict[str, Any]:
    item = {"codigo": codigo, "severidade": severidade, "mensagem": mensagem, "valores": valores}
    if parte and parte != "modelo":
        item["parte"] = parte
    return item


def _mesmo_eixo(eixo: list[float], letra: str) -> bool:
    return all(abs(a - b) < 0.01 for a, b in zip(eixo, EIXOS[letra]))


def _comparar_furos(partes: list[dict[str, Any]], especificacao: Especificacao) -> list[dict[str, Any]]:
    itens = []
    disponiveis = [f for p in partes for f in p["furos"]]
    for pedido in especificacao.furos:
        encontrados = [
            f for f in disponiveis
            if _mesmo_eixo(f["eixo"], pedido.eixo) and abs(f["diametro"] - pedido.diametro_modelado_mm) <= TOLERANCIA_DIAMETRO
        ]
        usados = encontrados[: pedido.quantidade]
        for furo in usados:
            disponiveis.remove(furo)
        faltam = pedido.quantidade - len(usados)
        if not faltam:
            continue
        no_eixo = [f["diametro"] for f in disponiveis if _mesmo_eixo(f["eixo"], pedido.eixo)]
        if no_eixo:
            itens.append(
                _item(
                    "DIAMETRO_DIVERGENTE", BLOQUEIO,
                    f"Esperados {pedido.quantidade} furos de Ø{pedido.diametro_modelado_mm:g} mm no eixo {pedido.eixo}. "
                    f"Faltam {faltam}, e os furos restantes nesse eixo têm Ø {sorted(no_eixo)} mm.",
                    faltam=faltam, diametros_no_eixo=sorted(no_eixo),
                )
            )
        else:
            itens.append(
                _item(
                    "FURO_AUSENTE", BLOQUEIO,
                    f"Faltam {faltam} de {pedido.quantidade} furos de Ø{pedido.diametro_modelado_mm:g} mm no eixo "
                    f"{pedido.eixo}. O corte pode não ter cruzado a peça: confira as coordenadas.",
                    faltam=faltam, esperados=pedido.quantidade,
                )
            )
    if disponiveis:
        itens.append(
            _item(
                "FURO_EXTRA", ALERTA,
                f"A peça tem {len(disponiveis)} furo(s) além do especificado: Ø {sorted(f['diametro'] for f in disponiveis)} mm.",
                extras=len(disponiveis),
            )
        )
    return itens


def _comparar_tamanho(partes: list[dict[str, Any]], especificacao: Especificacao) -> list[dict[str, Any]]:
    maior = max(partes, key=lambda p: p["volume_mm3"])
    d = especificacao.dimensoes
    esperado = [d.x_mm, d.y_mm, d.z_mm]
    if all(abs(a - b) <= d.tolerancia_mm for a, b in zip(maior["tamanho"], esperado)):
        return []
    return [
        _item(
            "DIMENSAO_DIVERGENTE", BLOQUEIO,
            f"A peça mede {maior['tamanho']} mm, e a especificação pede {esperado} mm (X, Y, Z), "
            f"com tolerância de {d.tolerancia_mm:g} mm.",
            parte=maior["nome"], medido=maior["tamanho"], esperado=esperado,
        )
    ]


def _regras_da_parte(parte: dict[str, Any], analise: dict[str, Any], material: dict[str, Any], perfil: dict[str, Any]) -> list[dict[str, Any]]:
    nome = parte["nome"]
    volume = perfil["volume_mm"]
    maximo_xy = volume["maximo_fatiador_xy"]
    seguro = volume["seguro"]
    x, y, z = parte["tamanho"]
    itens = []
    if not parte["valido"]:
        itens.append(_item("SOLIDO_INVALIDO", BLOQUEIO, "O sólido é inválido no kernel CAD.", nome))
    if not analise["fechada"]:
        itens.append(_item("MALHA_ABERTA", BLOQUEIO, "A malha não é fechada. O fatiador pode gerar defeitos.", nome))
    if parte["volume_mm3"] <= 0:
        itens.append(_item("VOLUME_ZERO", BLOQUEIO, "O volume da peça não é positivo.", nome))
    if x > maximo_xy or y > maximo_xy:
        itens.append(_item("FORA_DO_VOLUME_FISICO", BLOQUEIO, f"A peça mede {x:g} × {y:g} mm: o fatiador recusa acima de {maximo_xy} mm em X ou Y.", nome, x=x, y=y))
    elif x > seguro["x"] or y > seguro["y"]:
        itens.append(_item("FORA_DO_VOLUME_SEGURO", ALERTA, f"A peça mede {x:g} × {y:g} mm, acima dos {seguro['x']} mm seguros. Precisa sair sem brim, e o validador de G-code decide.", nome, x=x, y=y))
    if z > seguro["z"]:
        itens.append(_item("ALTURA_ACIMA_DO_LIMITE", BLOQUEIO, f"A peça tem {z:g} mm de altura. Acima de {seguro['z']} mm, a elevação final passaria de {volume['fisico']['z']} mm.", nome, z=z))
    if abs(parte["caixa"]["z"][0]) > 0.01:
        itens.append(_item("NAO_APOIADA", BLOQUEIO, f"A base está em Z = {parte['caixa']['z'][0]:g} mm: a peça precisa apoiar em Z = 0.", nome, zmin=parte["caixa"]["z"][0]))

    for furo in parte["furos"]:
        if furo["diametro"] < 2.0:
            itens.append(_item("FURO_MUITO_PEQUENO", ALERTA, f"Furo de Ø{furo['diametro']:g} mm: pode sair impreciso e precisar de broca.", nome, diametro=furo["diametro"]))

    fracao = analise["fracao_parede_fina"]
    if fracao >= FRACAO_FINA_ALERTA:
        severidade = BLOQUEIO if fracao >= FRACAO_FINA_BLOQUEIO else ALERTA
        itens.append(_item("PAREDE_FINA", severidade, f"Cerca de {fracao:.0%} da superfície está em paredes abaixo de 0,9 mm.", nome, fracao=fracao))
    if analise["area_balanco_mm2"] >= AREA_MINIMA_DE_BALANCO:
        limite = material["balanco_max_graus"]
        itens.append(_item("BALANCO", ALERTA, f"{analise['area_balanco_mm2']:g} mm² em balanço acima de {limite}°. Considere chanfrar, reorientar ou usar suporte.", nome, area=analise["area_balanco_mm2"]))
    if analise["maior_vao_mm"] > material["ponte_max_mm"]:
        itens.append(_item("PONTE_LONGA", ALERTA, f"Vão horizontal de {analise['maior_vao_mm']:g} mm, acima dos {material['ponte_max_mm']} mm deste material.", nome, vao=analise["maior_vao_mm"]))
    if analise["area_base_mm2"] < AREA_MINIMA_DE_BASE:
        itens.append(_item("BASE_PEQUENA", ALERTA, f"A base tem {analise['area_base_mm2']:g} mm². Abaixo de {AREA_MINIMA_DE_BASE:.0f} mm², use brim.", nome, area=analise["area_base_mm2"]))
    menor_base = min(x, y)
    if menor_base > 0 and z > 3 * menor_base:
        itens.append(_item("RAZAO_ALTURA_BASE", ALERTA, f"A altura ({z:g} mm) passa de 3× a menor dimensão da base ({menor_base:g} mm). Use brim e alinhe o lado longo com Y.", nome, altura=z, base=menor_base))
    return itens


def verificar_peca(partes: list[dict[str, Any]], especificacao: Especificacao, perfil: dict[str, Any], pasta: Path) -> dict[str, Any]:
    material = perfil["materiais"][especificacao.material]
    itens: list[dict[str, Any]] = []
    analises = {}
    for parte in partes:
        analise = analisar_malha(pasta / parte["stl"], material["balanco_max_graus"])
        analises[parte["nome"]] = analise
        itens += _regras_da_parte(parte, analise, material, perfil)

    solidos = sum(p["solidos"] for p in partes)
    if solidos != especificacao.pecas:
        itens.append(_item("QTD_SOLIDOS", BLOQUEIO, f"A especificação pede {especificacao.pecas} sólido(s), e o modelo tem {solidos}.", esperado=especificacao.pecas, medido=solidos))
    itens += _comparar_tamanho(partes, especificacao)
    itens += _comparar_furos(partes, especificacao)

    bloqueios = [i for i in itens if i["severidade"] == BLOQUEIO]
    alertas = [i for i in itens if i["severidade"] == ALERTA]
    return {
        "aprovado": not bloqueios,
        "bloqueios": bloqueios,
        "alertas": alertas,
        "medidas": [
            {
                "parte": p["nome"],
                "tamanho": p["tamanho"],
                "volume_mm3": p["volume_mm3"],
                "furos": [{"diametro": f["diametro"], "eixo": f["eixo"]} for f in p["furos"]],
                **{k: analises[p["nome"]][k] for k in ("area_base_mm2", "area_balanco_mm2", "maior_vao_mm", "espessura_p5_mm")},
            }
            for p in partes
        ],
    }
