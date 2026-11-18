"""Bico, mesa, ventoinha e velocidade escolhidos antes de preparar o G-code."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from forja3d.fatiamento.orca import TIPOS_DE_MESA, ParametrosFatiamento, _processo_derivado, filamento_ajustado
from test_chat_e_lista import janela  # noqa: F401


def test_sem_ajuste_vale_o_padrao_do_material():
    filamento = filamento_ajustado(ParametrosFatiamento(material="PLA"))
    assert filamento["nozzle_temperature"] == ["240"] and filamento["hot_plate_temp"] == ["65"]


def test_bico_mesa_e_ventoinha_valem_tambem_na_primeira_camada_e_em_toda_mesa():
    filamento = filamento_ajustado(ParametrosFatiamento(material="PETG", bico_c=245, mesa_c=75, ventoinha_pct=60))
    assert filamento["nozzle_temperature"] == filamento["nozzle_temperature_initial_layer"] == ["245"]
    assert all(filamento[t] == filamento[t + "_initial_layer"] == ["75"] for t in TIPOS_DE_MESA)
    assert (filamento["fan_min_speed"], filamento["fan_max_speed"]) == (["40"], ["60"]), "a faixa do PETG continua"
    assert filamento_ajustado(ParametrosFatiamento(material="PETG", ventoinha_pct=30))["fan_min_speed"] == ["30"]


def test_velocidade_escala_so_as_velocidades_em_mm_s(tmp_path):
    normal = json.loads(_processo_derivado(ParametrosFatiamento(material="PLA"), tmp_path / "a.json", 5).read_text(encoding="utf-8"))
    rapido = json.loads(_processo_derivado(ParametrosFatiamento(material="PLA", velocidade_pct=150), tmp_path / "b.json", 5).read_text(encoding="utf-8"))
    assert float(rapido["outer_wall_speed"]) == float(normal["outer_wall_speed"]) * 1.5
    assert rapido["support_interface_speed"] == normal["support_interface_speed"] == "100%"
    assert rapido["sparse_infill_density"] == normal["sparse_infill_density"]


# ---------- Na barra de impressão ----------


def valores(j):
    return (j.spin_bico.value(), j.spin_mesa.value(), j.spin_ventoinha.value(), j.spin_velocidade.value())


def test_barra_vem_com_o_padrao_do_material_e_troca_com_ele(janela):  # noqa: F811
    janela.combo_material.setCurrentText("PLA")
    assert valores(janela) == (240, 65, 100, 100)
    janela.combo_material.setCurrentText("PETG")
    assert valores(janela) == (250, 70, 90, 100)
    assert (janela.spin_bico.minimum(), janela.spin_bico.maximum()) == (220, 260), "o bico fica na faixa do material"


def test_ajustes_vao_para_o_fatiamento(janela):  # noqa: F811
    janela.combo_material.setCurrentText("PLA")
    for spin, valor in ((janela.spin_bico, 245), (janela.spin_mesa, 70), (janela.spin_ventoinha, 80), (janela.spin_velocidade, 120)):
        spin.setValue(valor)
    p = janela._parametros()
    assert (p.bico_c, p.mesa_c, p.ventoinha_pct, p.velocidade_pct) == (245, 70, 80, 120)


def test_mudar_a_temperatura_pede_preparar_de_novo(janela, tmp_path):  # noqa: F811
    janela.pasta_impressao = tmp_path
    janela.spin_mesa.setValue(janela.spin_mesa.value() + 5)
    assert janela.pasta_impressao is None
    assert "As opções mudaram" in janela.rotulo_resumo.text()


def test_padrao_do_material_desfaz_os_ajustes(janela):  # noqa: F811
    janela.combo_material.setCurrentText("PLA")
    janela.spin_bico.setValue(200)
    janela.spin_velocidade.setValue(70)
    next(b for b in janela.findChildren(type(janela.botao_aprovar)) if b.text() == "Padrão do material").click()
    assert valores(janela) == (240, 65, 100, 100)
