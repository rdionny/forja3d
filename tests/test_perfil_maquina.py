"""Confere o perfil da máquina contra os presets oficiais do OrcaSlicer."""

import json
import math
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PERFIL = json.loads((RAIZ / "config" / "impressora_ender3_v3_se.json").read_text(encoding="utf-8"))
MATERIAIS = list(PERFIL["materiais"])
QUALIDADES = list(PERFIL["qualidades"])


def preset(nome: str) -> dict:
    return json.loads((RAIZ / "config" / "orca" / f"{nome}.json").read_text(encoding="utf-8"))


MAQUINA = preset("maquina")


def movimentos_relativos_do_fim() -> tuple[float, float]:
    """Soma o que o G-code final move em Z e em X enquanto está em modo relativo (G91)."""
    trecho = MAQUINA["machine_end_gcode"].split("G91", 1)[1].split("G90", 1)[0]
    z = sum(float(v) for v in re.findall(r"\bZ(-?[\d.]+)", trecho))
    x = sum(float(v) for v in re.findall(r"\bX(-?[\d.]+)", trecho))
    return z, x


def test_volume_fisico_igual_ao_preset():
    fisico = PERFIL["volume_mm"]["fisico"]
    assert MAQUINA["printable_area"] == ["0x0", f"{fisico['x']}x0", f"{fisico['x']}x{fisico['y']}", f"0x{fisico['y']}"]
    assert float(MAQUINA["printable_height"]) == fisico["z"]


def test_volume_seguro_vem_do_gcode_final():
    z, x = movimentos_relativos_do_fim()
    volume = PERFIL["volume_mm"]
    assert volume["elevacao_final_z"] == pytest.approx(z)
    assert volume["margem_lateral"] == pytest.approx(x)
    assert volume["seguro"]["z"] == math.floor(volume["fisico"]["z"] - z)
    assert volume["seguro"]["x"] == volume["fisico"]["x"] - 2 * x
    assert volume["seguro"]["y"] == volume["fisico"]["y"] - 2 * x


def test_x_negativo_do_bloco_inicial_vem_do_gcode_inicial():
    valores = [float(v) for v in re.findall(r"\bX(-?[\d.]+)", MAQUINA["machine_start_gcode"])]
    assert PERFIL["validacao_gcode"]["x_min_bloco_inicial"] == min(valores)


def test_limites_de_movimento_iguais_ao_preset():
    movimento = PERFIL["movimento"]
    assert float(MAQUINA["machine_max_speed_x"][0]) == movimento["vel_max_xy_mm_s"]
    assert float(MAQUINA["machine_max_speed_y"][0]) == movimento["vel_max_xy_mm_s"]
    assert float(MAQUINA["machine_max_speed_z"][0]) == movimento["vel_max_z_mm_s"]
    assert float(MAQUINA["machine_max_speed_e"][0]) == movimento["vel_max_e_mm_s"]
    assert float(MAQUINA["machine_max_acceleration_x"][0]) == movimento["acel_max_xy_mm_s2"]
    assert float(MAQUINA["machine_max_acceleration_z"][0]) == movimento["acel_max_z_mm_s2"]


def test_bico_e_camada_iguais_ao_preset():
    assert float(MAQUINA["nozzle_diameter"][0]) == PERFIL["bico"]["diametro_mm"]
    assert float(MAQUINA["max_layer_height"][0]) == PERFIL["camada_mm"]["max"]
    assert MAQUINA["name"] == PERFIL["orca"]["maquina"]


@pytest.mark.parametrize("material", MATERIAIS)
def test_material_igual_ao_preset(material):
    dados = PERFIL["materiais"][material]
    filamento = preset(f"filamento_{material}")
    assert filamento["name"] == PERFIL["orca"]["filamentos"][material]
    assert float(filamento["nozzle_temperature"][0]) == dados["bico_c"]
    assert float(filamento["nozzle_temperature_range_low"][0]) == dados["bico_min_c"]
    assert float(filamento["hot_plate_temp"][0]) == dados["mesa_c"]
    assert float(filamento["filament_max_volumetric_speed"][0]) == dados["vazao_max_mm3_s"]
    assert float(filamento["filament_flow_ratio"][0]) == dados["fluxo"]
    assert float(filamento["filament_density"][0]) == dados["densidade_g_cm3"]
    assert float(filamento["fan_min_speed"][0]) == dados["ventoinha_min_pct"]
    assert float(filamento["fan_max_speed"][0]) == dados["ventoinha_max_pct"]
    assert float(filamento["close_fan_the_first_x_layers"][0]) == dados["camadas_sem_ventoinha"]
    retracao = filamento["filament_retraction_length"][0]
    esperado = MAQUINA["retraction_length"][0] if retracao == "nil" else retracao
    assert float(esperado) == dados["retracao_mm"]


@pytest.mark.parametrize("material", MATERIAIS)
def test_material_respeita_os_limites_da_maquina(material):
    dados = PERFIL["materiais"][material]
    assert dados["bico_min_c"] <= dados["bico_c"] <= dados["bico_max_c"] <= PERFIL["bico"]["temp_max_c"]
    assert dados["mesa_c"] <= PERFIL["mesa"]["temp_max_c"]


def test_perfil_limita_o_petg_abaixo_da_faixa_do_preset():
    """O preset genérico declara até 270 °C, acima do que o bico desta máquina alcança."""
    assert float(preset("filamento_PETG")["nozzle_temperature_range_high"][0]) > PERFIL["bico"]["temp_max_c"]
    assert PERFIL["materiais"]["PETG"]["bico_max_c"] == PERFIL["bico"]["temp_max_c"]


@pytest.mark.parametrize("qualidade", QUALIDADES)
def test_qualidade_igual_ao_preset(qualidade):
    dados = PERFIL["qualidades"][qualidade]
    processo = preset(f"processo_{qualidade}")
    assert processo["name"] == dados["preset_orca"]
    assert float(processo["layer_height"]) == dados["camada_mm"]
    assert PERFIL["orca"]["maquina"] in processo["compatible_printers"]
    assert PERFIL["camada_mm"]["min"] <= dados["camada_mm"] <= PERFIL["camada_mm"]["max"]


def test_processo_padrao_igual_ao_preset():
    dados = PERFIL["processo_padrao"]
    processo = preset("processo_padrao")
    assert int(processo["wall_loops"]) == dados["paredes"]
    assert int(processo["top_shell_layers"]) == dados["camadas_topo"]
    assert int(processo["bottom_shell_layers"]) == dados["camadas_base"]
    assert processo["sparse_infill_density"] == f"{dados['preenchimento_pct']}%"
    assert processo["sparse_infill_pattern"] == dados["padrao_preenchimento"]
    assert float(processo["initial_layer_speed"]) == dados["vel_primeira_camada_mm_s"]
    assert float(processo["outer_wall_speed"]) == dados["vel_parede_externa_mm_s"]
    assert float(processo["inner_wall_speed"]) == dados["vel_parede_interna_mm_s"]
    assert float(processo["sparse_infill_speed"]) == dados["vel_preenchimento_mm_s"]
    assert float(processo["travel_speed"]) == dados["vel_viagem_mm_s"]
    assert float(processo["line_width"]) == dados["largura_linha_mm"]
    assert float(processo["outer_wall_line_width"]) == dados["largura_parede_externa_mm"]
    assert float(processo["brim_width"]) == dados["brim_mm"]
