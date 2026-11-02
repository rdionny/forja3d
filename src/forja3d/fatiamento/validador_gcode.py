"""Validador determinístico de G-code. Não depende de IA.

As regras saíram da análise de G-code real na Fase 0. Veja docs/decisoes-fase0.md.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

BLOQUEIO, ALERTA, INFO = "BLOQUEIO", "ALERTA", "INFO"
LISTA_BRANCA = frozenset(
    ["G0", "G1", "G2", "G3", "G4", "G21", "G28", "G90", "G91", "G92", "M25", "M73", "M82", "M83", "M84", "M104", "M105", "M106", "M107", "M109", "M117", "M140", "M190", "M201", "M203", "M204", "M205", "M220", "M221", "M400", "M600"]
)
_PALAVRA = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")
_MARCA_CAMADA = re.compile(r"^;\s*(LAYER_CHANGE|CHANGE_LAYER)")
_TEMPO = re.compile(r"estimated printing time.*=\s*(.+)$")
_FILAMENTO_G = re.compile(r"total filament used \[g\]\s*=\s*([\d.]+)")
_FILAMENTO_M = re.compile(r";\s*Filament used:\s*([\d.]+)m")


def _item(codigo: str, severidade: str, mensagem: str, linha: int | None = None) -> dict[str, Any]:
    item = {"codigo": codigo, "severidade": severidade, "mensagem": mensagem}
    if linha:
        item["linha"] = linha
    return item


def validar_gcode(
    caminho: Path,
    perfil: dict[str, Any],
    material: str,
    camada_inicial_mm: float = 0.2,
    tamanho_peca_xy: tuple[float, float] | None = None,
    brim_mm: float = 0.0,
) -> dict[str, Any]:
    limites = perfil["validacao_gcode"]
    dados_material = perfil["materiais"][material]
    bico_max_maquina = perfil["bico"]["temp_max_c"]
    mesa_max = perfil["mesa"]["temp_max_c"]
    movimento = perfil["movimento"]

    itens: dict[str, dict[str, Any]] = {}

    def registrar(codigo: str, severidade: str, mensagem: str, linha: int | None = None) -> None:
        # Só o primeiro de cada tipo: um defeito costuma se repetir em milhares de linhas.
        itens.setdefault(codigo, _item(codigo, severidade, mensagem, linha))

    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0, "E": 0.0}
    xyz_relativo = False
    e_relativo = False
    antes_da_primeira_camada = True
    camadas = 0
    extrudiu = False
    g28_visto = False
    z_primeira = None
    temperaturas_bico: list[float] = []
    temperaturas_mesa: list[float] = []
    bico_desligado_depois = False
    mesa_desligada_depois = False
    extrusao = {"X": [float("inf"), float("-inf")], "Y": [float("inf"), float("-inf")], "Z": [float("inf"), float("-inf")]}
    z_maximo = 0.0
    excesso_velocidade = False
    resumo: dict[str, Any] = {}

    with open(caminho, encoding="utf-8", errors="replace") as arquivo:
        for numero, bruta in enumerate(arquivo, 1):
            linha = bruta.strip()
            if not linha:
                continue
            if linha.startswith(";"):
                if _MARCA_CAMADA.match(linha):
                    camadas += 1
                    antes_da_primeira_camada = False
                elif "tempo" not in resumo and (m := _TEMPO.search(linha)):
                    resumo["tempo"] = m.group(1).strip()
                elif "filamento_g" not in resumo and (m := _FILAMENTO_G.search(linha)):
                    resumo["filamento_g"] = float(m.group(1))
                elif "filamento_m" not in resumo and (m := _FILAMENTO_M.search(linha)):
                    resumo["filamento_m"] = float(m.group(1))
                continue

            codigo_linha = linha.split(";", 1)[0].strip()
            if not codigo_linha:
                continue
            partes = codigo_linha.split(None, 1)
            comando = partes[0].upper()
            if comando not in LISTA_BRANCA:
                registrar("COMANDO_PROIBIDO", BLOQUEIO, f"Comando fora da lista branca: {comando}.", numero)
                continue
            args = {m[0].upper(): float(m[1]) for m in _PALAVRA.findall(partes[1] if len(partes) > 1 else "")}

            if comando == "G90":
                # No Marlin, G90 também devolve o eixo E ao modo absoluto.
                xyz_relativo = False
                e_relativo = False
            elif comando == "G91":
                xyz_relativo = True
                e_relativo = True
            elif comando == "M82":
                e_relativo = False
            elif comando == "M83":
                e_relativo = True
            elif comando == "G92":
                for eixo, valor in args.items():
                    if eixo in pos:
                        pos[eixo] = valor
            elif comando == "G28":
                g28_visto = True
            elif comando in ("M104", "M109"):
                temperatura = args.get("S", 0.0)
                if temperatura > 0:
                    temperaturas_bico.append(temperatura)
                    bico_desligado_depois = False
                    if temperatura > bico_max_maquina:
                        registrar("BICO_ACIMA_DO_LIMITE", BLOQUEIO, f"Bico em {temperatura:g} °C, acima dos {bico_max_maquina} °C da máquina.", numero)
                    elif not dados_material["bico_min_c"] <= temperatura <= dados_material["bico_max_c"]:
                        registrar("BICO_FORA_DA_FAIXA", BLOQUEIO, f"Bico em {temperatura:g} °C, fora da faixa de {material} ({dados_material['bico_min_c']}–{dados_material['bico_max_c']} °C).", numero)
                else:
                    bico_desligado_depois = True
            elif comando in ("M140", "M190"):
                temperatura = args.get("S", 0.0)
                if temperatura > 0:
                    temperaturas_mesa.append(temperatura)
                    mesa_desligada_depois = False
                    if temperatura > mesa_max:
                        registrar("MESA_ACIMA_DO_LIMITE", BLOQUEIO, f"Mesa em {temperatura:g} °C, acima dos {mesa_max} °C da máquina.", numero)
                else:
                    mesa_desligada_depois = True
            elif comando in ("G0", "G1", "G2", "G3"):
                for eixo in "XYZ":
                    if eixo in args:
                        pos[eixo] = pos[eixo] + args[eixo] if xyz_relativo else args[eixo]

                moveu = any(eixo in args for eixo in "XYZ")
                # Só o destino importa: a primeira viagem da camada 1 parte da purga, em X=-2,4.
                if moveu:
                    x_min = limites["x_min_bloco_inicial"] if antes_da_primeira_camada else limites["x_min"]
                    if not x_min <= pos["X"] <= limites["x_max"]:
                        registrar("FORA_DA_MESA_X", BLOQUEIO, f"Movimento até X={pos['X']:g} mm, fora da mesa.", numero)
                    if not limites["y_min"] <= pos["Y"] <= limites["y_max"]:
                        registrar("FORA_DA_MESA_Y", BLOQUEIO, f"Movimento até Y={pos['Y']:g} mm, fora da mesa.", numero)
                    if pos["Z"] > limites["z_max"] or pos["Z"] < limites["z_min"]:
                        registrar("FORA_DO_VOLUME_Z", BLOQUEIO, f"Movimento até Z={pos['Z']:g} mm, fora do limite de {limites['z_max']} mm.", numero)
                    z_maximo = max(z_maximo, pos["Z"])

                if "F" in args and moveu and not excesso_velocidade:
                    limite_xy = movimento["vel_max_xy_mm_s"] * 60
                    limite_z = movimento["vel_max_z_mm_s"] * 60
                    if ("X" in args or "Y" in args) and args["F"] > limite_xy or ("Z" in args and args["F"] > limite_z):
                        excesso_velocidade = True

                if "E" in args:
                    delta = args["E"] if e_relativo else args["E"] - pos["E"]
                    pos["E"] = pos["E"] + args["E"] if e_relativo else args["E"]
                    if delta > 0:
                        if not g28_visto:
                            registrar("EXTRUSAO_ANTES_DE_G28", BLOQUEIO, "Há extrusão antes de G28.", numero)
                        extrudiu = True
                        bico_desligado_depois = False
                        mesa_desligada_depois = False
                        if moveu and not antes_da_primeira_camada:
                            if z_primeira is None:
                                z_primeira = pos["Z"]
                            for eixo in "XYZ":
                                extrusao[eixo][0] = min(extrusao[eixo][0], pos[eixo])
                                extrusao[eixo][1] = max(extrusao[eixo][1], pos[eixo])

    if not extrudiu or camadas == 0:
        registrar("SEM_EXTRUSAO", BLOQUEIO, "O arquivo não tem extrusão de peça.")
    if extrudiu and not bico_desligado_depois:
        registrar("BICO_NAO_DESLIGA", BLOQUEIO, "O arquivo termina sem desligar o bico (M104 S0).")
    if extrudiu and not mesa_desligada_depois:
        registrar("MESA_NAO_DESLIGA", BLOQUEIO, "O arquivo termina sem desligar a mesa (M140 S0).")
    if z_primeira is not None and abs(z_primeira - camada_inicial_mm) > 0.01:
        registrar("PRIMEIRA_CAMADA", ALERTA, f"A primeira camada está em Z={z_primeira:g} mm, e o esperado era {camada_inicial_mm:g} mm.")
    if tamanho_peca_xy and z_primeira is not None:
        centro = (limites["x_max"] + limites["x_min"]) / 2
        for indice, eixo in enumerate("XY"):
            metade = tamanho_peca_xy[indice] / 2 + brim_mm + 1.0
            if extrusao[eixo][0] < centro - metade or extrusao[eixo][1] > centro + metade:
                registrar("AREA_DIVERGENTE", ALERTA, f"A área impressa em {eixo} ({extrusao[eixo][0]:g} a {extrusao[eixo][1]:g} mm) difere da posição esperada da peça.")
    if excesso_velocidade:
        registrar("VELOCIDADE_ACIMA", INFO, "Há valores de F acima do limite da máquina. O firmware limita o movimento.")

    lista = list(itens.values())
    bloqueios = [i for i in lista if i["severidade"] == BLOQUEIO]
    return {
        "aprovado": not bloqueios,
        "bloqueios": bloqueios,
        "alertas": [i for i in lista if i["severidade"] == ALERTA],
        "informacoes": [i for i in lista if i["severidade"] == INFO],
        "camadas": camadas,
        "z_maximo_mm": round(z_maximo, 3),
        "bico_c": max(temperaturas_bico) if temperaturas_bico else None,
        "mesa_c": max(temperaturas_mesa) if temperaturas_mesa else None,
        **resumo,
    }
