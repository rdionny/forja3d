"""Fatiamento com o OrcaSlicer real."""
import json
from pathlib import Path

import pytest

from forja3d.cad.executor import executar_script
from forja3d.configuracao import PASTA_CONFIG
from forja3d.fatiamento.orca import ErroFatiamento, ParametrosFatiamento, fatiar, localizar_orca

pytestmark = pytest.mark.orca
DADOS = Path(__file__).parent / "dados"

if localizar_orca() is None:
    pytest.skip("OrcaSlicer não encontrado", allow_module_level=True)


@pytest.fixture(scope="module")
def cubo(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("cubo")
    r = executar_script((DADOS / "cubo_calibracao_20mm.py").read_text(encoding="utf-8"), pasta)
    assert r.ok, r.erro
    return pasta / r.partes[0]["stl"]


def _placa(pasta, lado_x, lado_y, altura):
    r = executar_script(
        f"import cadquery as cq\nresult = cq.Workplane('XY').box({lado_x}, {lado_y}, {altura}, centered=(True, True, False))",
        pasta,
    )
    assert r.ok, r.erro
    return pasta / r.partes[0]["stl"]


@pytest.mark.parametrize(
    ("material", "bico", "mesa"), [("PLA", 240, 65), ("PETG", 250, 70), ("TPU", 230, 30)]
)
def test_materiais(tmp_path, perfil, cubo, material, bico, mesa):
    resumo = fatiar([cubo], ParametrosFatiamento(material=material), perfil, tmp_path, tamanho_peca_xy=(20, 20))
    v = resumo["validacao"]
    assert v["aprovado"], v["bloqueios"]
    assert v["bico_c"] == bico
    assert v["mesa_c"] == mesa
    assert (tmp_path / "peca.gcode").stat().st_size > 1000
    assert json.loads((tmp_path / "resumo.json").read_text(encoding="utf-8"))["validacao"]["aprovado"]


def test_parametros_chegam_ao_gcode(tmp_path, perfil, cubo):
    p = ParametrosFatiamento(material="PLA", qualidade="fina", paredes=4, preenchimento_pct=30, suporte=True, brim=True)
    resumo = fatiar([cubo], p, perfil, tmp_path, tamanho_peca_xy=(20, 20))
    assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
    texto = (tmp_path / "peca.gcode").read_text(encoding="utf-8", errors="replace")
    for esperado in ("; wall_loops = 4", "; sparse_infill_density = 30%", "; brim_type = outer_only", "; enable_support = 1", "; layer_height = 0.12"):
        assert esperado in texto


def test_ajustes_da_barra_chegam_ao_gcode(tmp_path, perfil, cubo):
    p = ParametrosFatiamento(material="PLA", bico_c=245, mesa_c=68, ventoinha_pct=80, velocidade_pct=120)
    resumo = fatiar([cubo], p, perfil, tmp_path, tamanho_peca_xy=(20, 20))
    v = resumo["validacao"]
    assert v["aprovado"], v["bloqueios"]
    assert (v["bico_c"], v["mesa_c"]) == (245, 68)
    texto = (tmp_path / "peca.gcode").read_text(encoding="utf-8", errors="replace")
    for esperado in ("; fan_max_speed = 80", "; outer_wall_speed = 72", "; nozzle_temperature_initial_layer = 245"):
        assert esperado in texto
    assert resumo["parametros"]["bico_c"] == 245


def test_peca_de_218_mm_e_recusada_com_mensagem_clara(tmp_path, perfil):
    stl = _placa(tmp_path, 218, 218, 2)
    with pytest.raises(ErroFatiamento, match="não cabe"):
        fatiar([stl], ParametrosFatiamento(material="PLA"), perfil, tmp_path / "s")


def test_peca_de_217_mm_e_aceita(tmp_path, perfil):
    stl = _placa(tmp_path, 217, 217, 2)
    resumo = fatiar([stl], ParametrosFatiamento(material="PLA"), perfil, tmp_path / "s", tamanho_peca_xy=(217, 217))
    assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]


def test_coluna_de_245_mm_o_fatiador_aceita_e_o_validador_reprova(tmp_path, perfil):
    stl = _placa(tmp_path, 25, 25, 245)
    resumo = fatiar([stl], ParametrosFatiamento(material="PLA", qualidade="rascunho"), perfil, tmp_path / "s")
    assert not resumo["validacao"]["aprovado"]
    assert "FORA_DO_VOLUME_Z" in {i["codigo"] for i in resumo["validacao"]["bloqueios"]}


def test_nao_escreve_log_na_pasta_do_projeto(tmp_path, perfil, cubo):
    fatiar([cubo], ParametrosFatiamento(material="PLA"), perfil, tmp_path)
    assert not (PASTA_CONFIG.parent / "00000.log").exists()


def test_orca_ausente_tem_mensagem_clara(tmp_path, perfil, cubo, monkeypatch):
    monkeypatch.setattr("forja3d.fatiamento.orca.localizar_orca", lambda _=None: None)
    with pytest.raises(ErroFatiamento, match="Não encontrei o OrcaSlicer"):
        fatiar([cubo], ParametrosFatiamento(material="PLA"), perfil, tmp_path)
