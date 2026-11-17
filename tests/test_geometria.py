"""A análise da malha precisa separar um buraco de verdade de um triângulo que não tem área."""
import numpy as np
import pytest
import trimesh

from forja3d.verificacao.geometria import analisar_malha


def _com_triangulo_de_area_zero(caminho):
    """Cada aresta de um triângulo sem área vai e volta em cima de arestas da peça, como nos polos de uma esfera do CAD."""
    cubo = trimesh.creation.box((20, 20, 20))
    faces = np.vstack((cubo.faces, [cubo.faces[0][0], cubo.faces[0][1], cubo.faces[0][0]]))
    trimesh.Trimesh(cubo.vertices, faces, process=False).export(caminho)


def test_triangulo_de_area_zero_nao_e_buraco(tmp_path):
    caminho = tmp_path / "peca.stl"
    _com_triangulo_de_area_zero(caminho)
    assert not trimesh.load_mesh(caminho).is_watertight, "sem isso o teste não mostra nada"
    analise = analisar_malha(caminho, 50)
    assert analise["fechada"]
    assert analise["espessura_p5_mm"] is not None, "peça fechada também passa pela medida de espessura"


def test_buraco_de_verdade_continua_aberto(tmp_path):
    cubo = trimesh.creation.box((20, 20, 20))
    cubo.update_faces(np.arange(len(cubo.faces)) != 0)
    caminho = tmp_path / "aberta.stl"
    cubo.export(caminho)
    assert not analisar_malha(caminho, 50)["fechada"]


def test_aresta_de_tres_faces_continua_aberta(tmp_path):
    cubo = trimesh.creation.box((20, 20, 20))
    aba = np.array([[0.0, 0.0, 0.0]])
    vertices = np.vstack((cubo.vertices, aba + [30.0, 30.0, 30.0]))
    a, b = cubo.faces[0][:2]
    faces = np.vstack((cubo.faces, [a, b, len(vertices) - 1]))
    caminho = tmp_path / "aba.stl"
    trimesh.Trimesh(vertices, faces, process=False).export(caminho)
    assert not analisar_malha(caminho, 50)["fechada"]


@pytest.mark.cad
def test_cabeca_redonda_do_cad_nao_da_malha_aberta(tmp_path):
    """O caso real: uma esfera unida a um cone fazia o agente trocar as cabeças por cones."""
    from forja3d.cad.executor import executar_script

    script = """
import cadquery as cq
corpo = cq.Workplane("XY").circle(4).workplane(offset=12).circle(2).loft(combine=True)
cabeca = cq.Workplane("XY").sphere(2.5).translate((0, 0, 13.5))
result = corpo.union(cabeca)
"""
    resultado = executar_script(script, tmp_path)
    assert resultado.ok, resultado.erro
    assert analisar_malha(tmp_path / resultado.partes[0]["stl"], 50)["fechada"]
