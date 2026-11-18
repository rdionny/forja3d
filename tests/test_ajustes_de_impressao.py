"""Bico, mesa, ventoinha e velocidade escolhidos antes de preparar o G-code."""
import json

from forja3d.fatiamento.orca import TIPOS_DE_MESA, ParametrosFatiamento, _processo_derivado, filamento_ajustado


def test_sem_ajuste_vale_o_padrao_do_material():
    filamento = filamento_ajustado(ParametrosFatiamento(material="PLA"))
    assert filamento["nozzle_temperature"] == ["240"] and filamento["hot_plate_temp"] == ["65"]


def test_bico_mesa_e_ventoinha_valem_tambem_na_primeira_camada_e_em_toda_mesa():
    filamento = filamento_ajustado(ParametrosFatiamento(material="PETG", bico_c=245, mesa_c=75, ventoinha_pct=60))
    assert filamento["nozzle_temperature"] == filamento["nozzle_temperature_initial_layer"] == ["245"]
    assert all(filamento[t] == filamento[t + "_initial_layer"] == ["75"] for t in TIPOS_DE_MESA)
    assert filamento["fan_min_speed"] == filamento["fan_max_speed"] == ["60"]


def test_velocidade_escala_so_as_velocidades_em_mm_s(tmp_path):
    normal = json.loads(_processo_derivado(ParametrosFatiamento(material="PLA"), tmp_path / "a.json", 5).read_text(encoding="utf-8"))
    rapido = json.loads(_processo_derivado(ParametrosFatiamento(material="PLA", velocidade_pct=150), tmp_path / "b.json", 5).read_text(encoding="utf-8"))
    assert float(rapido["outer_wall_speed"]) == float(normal["outer_wall_speed"]) * 1.5
    assert rapido["support_interface_speed"] == normal["support_interface_speed"] == "100%"
    assert rapido["sparse_infill_density"] == normal["sparse_infill_density"]
