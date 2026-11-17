"""A mesa de verdade: o OrcaSlicer precisa imprimir cada item onde o usuário o colocou."""
import re
from pathlib import Path

import pytest
import trimesh

from forja3d.fatiamento.orca import (
    FOLGA_DE_SUPORTE_MM,
    ErroFatiamento,
    ParametrosFatiamento,
    fatiar_layout,
    localizar_orca,
    montar_stl_da_mesa,
)
from forja3d.fatiamento.validador_gcode import validar_gcode
from forja3d.layout_mesa import ItemMesa, LayoutMesa

pytestmark = pytest.mark.orca
if localizar_orca() is None:
    pytest.skip("OrcaSlicer não encontrado", allow_module_level=True)

PLA = ParametrosFatiamento(material="PLA", qualidade="rascunho", paredes=2, preenchimento_pct=10)


@pytest.fixture
def stls(tmp_path):
    caminhos = {}
    for nome, medidas in {"longa": (40.0, 20.0, 5.0), "baixa": (20.0, 20.0, 3.0)}.items():
        malha = trimesh.creation.box(extents=medidas)
        # Fora do centro de propósito: a posição na mesa não pode depender da origem do arquivo.
        malha.apply_translation((7.0, -3.0, medidas[2] / 2 + 4.0))
        caminhos[nome] = tmp_path / f"{nome}.stl"
        malha.export(caminhos[nome])
    return caminhos


TAMANHOS = {"longa": (40.0, 20.0), "baixa": (20.0, 20.0)}


def extrusao_xy(gcode: Path) -> tuple[float, float, float, float]:
    """Área impressa, lida do arquivo, sem depender do validador."""
    xs, ys = [], []
    x = y = 0.0
    camada = False
    relativo = False
    for linha in gcode.read_text(encoding="utf-8", errors="replace").splitlines():
        if linha.startswith(";LAYER_CHANGE"):
            camada = True
        codigo = linha.split(";", 1)[0].strip()
        if codigo.startswith("G91"):
            relativo = True
        elif codigo.startswith("G90"):
            relativo = False
        if not re.match(r"G[01]\b", codigo) or relativo:
            continue
        m = re.search(r"\bX(-?[\d.]+)", codigo)
        if m:
            x = float(m.group(1))
        m = re.search(r"\bY(-?[\d.]+)", codigo)
        if m:
            y = float(m.group(1))
        if camada and re.search(r"\bE\d", codigo):
            xs.append(x)
            ys.append(y)
    return min(xs), max(xs), min(ys), max(ys)


def test_stl_da_mesa_coloca_cada_item_na_posicao(tmp_path, stls):
    destino = tmp_path / "mesa.stl"
    itens = [ItemMesa("1", "longa", 50.0, 40.0), ItemMesa("2", "baixa", 150.0, 170.0)]
    montar_stl_da_mesa(itens, stls, destino)
    malha = trimesh.load_mesh(destino)
    assert malha.bounds[0].tolist() == pytest.approx([30.0, 30.0, 0.0])
    assert malha.bounds[1].tolist() == pytest.approx([160.0, 180.0, 5.0])


def test_stl_da_mesa_aplica_o_giro(tmp_path, stls):
    destino = tmp_path / "mesa.stl"
    montar_stl_da_mesa([ItemMesa("1", "longa", 100.0, 100.0, 1, 90)], stls, destino)
    malha = trimesh.load_mesh(destino)
    assert malha.bounds[0].tolist() == pytest.approx([90.0, 80.0, 0.0])
    assert malha.bounds[1].tolist() == pytest.approx([110.0, 120.0, 5.0])


def test_gcode_sai_onde_o_usuario_colocou(tmp_path, stls, perfil):
    layout = LayoutMesa(TAMANHOS, perfil, [ItemMesa("1", "longa", 40.0, 30.0), ItemMesa("2", "baixa", 180.0, 190.0)])
    resumo = fatiar_layout(layout, stls, PLA, perfil, tmp_path / "saida")
    assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
    assert resumo["gcode"] == "peca.gcode"
    assert resumo["layout"] == layout.assinatura()
    x0, x1, y0, y1 = extrusao_xy(tmp_path / "saida" / "peca.gcode")
    assert x0 == pytest.approx(20.0, abs=1.0)
    assert x1 == pytest.approx(190.0, abs=1.0)
    assert y0 == pytest.approx(20.0, abs=1.0)
    assert y1 == pytest.approx(200.0, abs=1.0)


def test_item_girado_sai_girado(tmp_path, stls, perfil):
    layout = LayoutMesa(TAMANHOS, perfil, [ItemMesa("1", "longa", 100.0, 100.0, 1, 90)])
    fatiar_layout(layout, stls, PLA, perfil, tmp_path / "saida")
    x0, x1, y0, y1 = extrusao_xy(tmp_path / "saida" / "peca.gcode")
    assert x1 - x0 == pytest.approx(20.0, abs=0.8)
    assert y1 - y0 == pytest.approx(40.0, abs=0.8)


def test_copias_saem_todas(tmp_path, stls, perfil):
    uma = LayoutMesa(TAMANHOS, perfil, [ItemMesa("1", "baixa", 60.0, 110.0)])
    duas = LayoutMesa(TAMANHOS, perfil, [ItemMesa("1", "baixa", 60.0, 110.0), ItemMesa("2", "baixa", 160.0, 110.0)])
    so_uma = fatiar_layout(uma, stls, PLA, perfil, tmp_path / "uma")
    com_duas = fatiar_layout(duas, stls, PLA, perfil, tmp_path / "duas")
    assert com_duas["validacao"]["filamento_g"] == pytest.approx(so_uma["validacao"]["filamento_g"] * 2, rel=0.05)


def test_um_gcode_por_arquivo_com_tempo_somado(tmp_path, stls, perfil):
    layout = LayoutMesa(TAMANHOS, perfil, [ItemMesa("1", "longa", 100.0, 100.0, 1), ItemMesa("2", "baixa", 100.0, 100.0, 2)])
    resumo = fatiar_layout(layout, stls, PLA, perfil, tmp_path / "saida")
    assert [a["gcode"] for a in resumo["arquivos"]] == ["arquivo_1.gcode", "arquivo_2.gcode"]
    assert [a["itens"] for a in resumo["arquivos"]] == [["longa"], ["baixa"]]
    assert (tmp_path / "saida" / "arquivo_1.gcode").exists()
    assert (tmp_path / "saida" / "arquivo_2.gcode").exists()
    assert not (tmp_path / "saida" / "peca.gcode").exists()
    assert resumo["validacao"]["aprovado"]
    assert resumo["validacao"]["z_maximo_mm"] >= 15.0
    assert resumo["validacao"]["tempo_s"] > 0

    # Voltar para um arquivo só não pode deixar G-code velho na pasta.
    layout.itens = layout.itens[:1]
    fatiar_layout(layout, stls, PLA, perfil, tmp_path / "saida")
    assert sorted(p.name for p in (tmp_path / "saida").glob("*.gcode")) == ["peca.gcode"]


def test_parte_de_217_mm_centralizada_ainda_e_aceita(tmp_path, perfil):
    caminho = tmp_path / "placa.stl"
    trimesh.creation.box(extents=(217.0, 30.0, 2.0)).export(caminho)
    layout = LayoutMesa.novo({"placa": (217.0, 30.0)}, perfil)
    resumo = fatiar_layout(layout, {"placa": caminho}, PLA, perfil, tmp_path / "saida")
    assert resumo["gcode"] == "peca.gcode"


def test_mesa_vazia_tem_mensagem_clara(tmp_path, stls, perfil):
    with pytest.raises(ErroFatiamento, match="vazia"):
        fatiar_layout(LayoutMesa(TAMANHOS, perfil), stls, PLA, perfil, tmp_path / "saida")


def test_validador_reprova_gcode_fora_da_posicao_da_mesa(tmp_path, stls, perfil):
    layout = LayoutMesa(TAMANHOS, perfil, [ItemMesa("1", "baixa", 60.0, 60.0)])
    fatiar_layout(layout, stls, PLA, perfil, tmp_path / "saida")
    gcode = tmp_path / "saida" / "peca.gcode"
    certo = validar_gcode(gcode, perfil, "PLA", 0.2, None, 0.0, layout.area_do_arquivo(1))
    assert certo["aprovado"]
    errado = validar_gcode(gcode, perfil, "PLA", 0.2, None, 0.0, (150.0, 170.0, 150.0, 170.0))
    assert not errado["aprovado"]
    assert errado["bloqueios"][0]["codigo"] == "POSICAO_DIVERGENTE"


def test_base_do_suporte_pode_passar_da_caixa_do_item(tmp_path, stls, perfil):
    layout = LayoutMesa(TAMANHOS, perfil, [ItemMesa("1", "longa", 100.0, 100.0)])
    fatiar_layout(layout, stls, PLA, perfil, tmp_path / "saida")
    gcode = tmp_path / "saida" / "peca.gcode"
    x0, x1, y0, y1 = extrusao_xy(gcode)
    # Um item menor do que o que saiu: é o que acontece quando o suporte alarga a base.
    caixa_do_item = (x0 + 3.0, x1 - 3.0, y0 + 3.0, y1 - 3.0)
    sem_folga = validar_gcode(gcode, perfil, "PLA", 0.2, None, 0.0, caixa_do_item)
    assert "POSICAO_DIVERGENTE" in {b["codigo"] for b in sem_folga["bloqueios"]}
    com_folga = validar_gcode(gcode, perfil, "PLA", 0.2, None, 0.0, caixa_do_item, FOLGA_DE_SUPORTE_MM)
    assert "POSICAO_DIVERGENTE" not in {b["codigo"] for b in com_folga["bloqueios"]}


def test_peca_com_balanco_e_suporte_sai_aprovada(tmp_path, perfil):
    haste = trimesh.creation.box(extents=(8.0, 8.0, 30.0))
    haste.apply_translation((0.0, 0.0, 15.0))
    braco = trimesh.creation.box(extents=(70.0, 8.0, 4.0))
    braco.apply_translation((0.0, 0.0, 32.0))
    caminho = tmp_path / "t.stl"
    trimesh.util.concatenate([haste, braco]).export(caminho)
    layout = LayoutMesa({"t": (70.0, 8.0)}, perfil, [ItemMesa("1", "t", 110.0, 110.0)])
    parametros = ParametrosFatiamento(material="PLA", qualidade="rascunho", paredes=2, preenchimento_pct=10, suporte=True)
    resumo = fatiar_layout(layout, {"t": caminho}, parametros, perfil, tmp_path / "saida")
    assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
