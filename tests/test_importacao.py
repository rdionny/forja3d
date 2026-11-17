"""Modelos 3D baixados: leitura de cada formato, conserto da malha e a parte importada na mesa."""
import json
import os
import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np
import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox

from forja3d.agente.especificacao import Especificacao
from forja3d.agente.orquestrador import Orquestrador
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.importacao.fbx import ASSINATURA, SEM_MALHA, ler_fbx
from forja3d.importacao.leitura import ler_modelo
from forja3d.importacao.malha import MalhaLida, ModeloInvalido
from forja3d.importacao.preparo import (
    ModeloLido,
    OpcoesDoModelo,
    altura_maxima,
    altura_sugerida,
    desenhar_previa,
    gravar_parte,
    ler_em_processo,
    nome_da_parte,
    normalizar,
    principal,
    tamanho_girado,
)
from forja3d.interface.dialogo_importar import DialogoImportar
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.projetos.armazenamento import Projeto
from forja3d.projetos.compartilhar import importar_projeto, salvar_projeto
from forja3d.verificacao.verificador import verificar_peca

CUBO_V = np.array([[0, 0, 0], [10, 0, 0], [10, 20, 0], [0, 20, 0], [0, 0, 30], [10, 0, 30], [10, 20, 30], [0, 20, 30]], dtype=np.float64)
# Quatro lados por polígono, com o último índice negativo, como no FBX.
CUBO_POLIGONOS = [0, 3, 2, ~1, 4, 5, 6, ~7, 0, 1, 5, ~4, 1, 2, 6, ~5, 2, 3, 7, ~6, 3, 0, 4, ~7]


# ---------- FBX de teste ----------


def _propriedade(valor) -> bytes:
    if isinstance(valor, str):
        bruto = valor.encode()
        return b"S" + struct.pack("<I", len(bruto)) + bruto
    if isinstance(valor, bool):
        return b"C" + struct.pack("<B", valor)
    if isinstance(valor, int):
        return b"L" + struct.pack("<q", valor)
    if isinstance(valor, float):
        return b"D" + struct.pack("<d", valor)
    tipo, dados = (b"d", np.asarray(valor, "<f8").tobytes()) if np.asarray(valor).dtype.kind == "f" else (b"i", np.asarray(valor, "<i4").tobytes())
    comprimido = zlib.compress(dados)
    return tipo + struct.pack("<III", len(valor), 1, len(comprimido)) + comprimido


def _no(nome: str, propriedades=(), filhos=(), inicio: int = 0, largos: bool = True) -> bytes:
    corpo_props = b"".join(_propriedade(p) for p in propriedades)
    formato = "<QQQ" if largos else "<III"
    cabecalho = struct.calcsize(formato) + 1 + len(nome)
    posicao = inicio + cabecalho + len(corpo_props)
    corpo_filhos = b""
    for filho in filhos:
        gerado = filho(posicao, largos)
        corpo_filhos += gerado
        posicao += len(gerado)
    if filhos:
        corpo_filhos += b"\x00" * (struct.calcsize(formato) + 1)
        posicao += struct.calcsize(formato) + 1
    return struct.pack(formato, posicao, len(propriedades), len(corpo_props)) + bytes([len(nome)]) + nome.encode() + corpo_props + corpo_filhos


def _n(nome: str, propriedades=(), filhos=()):
    return lambda inicio, largos: _no(nome, propriedades, filhos, inicio, largos)


def _valor70(nome: str, *valores):
    return _n("P", [nome, "", "", "", *valores])


def criar_fbx(versao: int = 7700, translacao=(0.0, 0.0, 0.0), escala=(1.0, 1.0, 1.0), giro=(0.0, 0.0, 0.0), para_cima: int = 1, unidade: float = 1.0, com_malha: bool = True) -> bytes:
    largos = versao >= 7500
    configuracoes = _n("GlobalSettings", filhos=[_n("Properties70", filhos=[_valor70("UpAxis", para_cima), _valor70("UnitScaleFactor", unidade)])])
    objetos = [
        _n("Model", [10, "Peca", "Mesh"], [_n("Properties70", filhos=[_valor70("Lcl Translation", *translacao), _valor70("Lcl Scaling", *escala), _valor70("Lcl Rotation", *giro)])]),
    ]
    if com_malha:
        objetos.insert(0, _n("Geometry", [20, "Malha", "Mesh"], [_n("Vertices", [CUBO_V.ravel().tolist()]), _n("PolygonVertexIndex", [CUBO_POLIGONOS])]))
    conexoes = [_n("C", ["OO", 20, 10]), _n("C", ["OO", 10, 0])]
    arquivo = ASSINATURA + b"\x1a\x00" + struct.pack("<I", versao)
    for no in (configuracoes, _n("Objects", filhos=objetos), _n("Connections", filhos=conexoes)):
        arquivo += no(len(arquivo), largos)
    return arquivo + b"\x00" * (25 if largos else 13)


# ---------- Formatos ----------


@pytest.mark.parametrize("versao", [7400, 7700])
def test_fbx_le_malha_com_32_e_64_bits(versao):
    lida = ler_fbx(criar_fbx(versao))
    assert len(lida.faces) == 12
    assert np.allclose(np.ptp(lida.vertices, axis=0), (10, 20, 30))
    assert lida.mm_por_unidade == 10.0
    assert lida.para_cima == (0.0, 1.0, 0.0)


def test_fbx_aplica_as_transformacoes_do_no():
    lida = ler_fbx(criar_fbx(translacao=(100.0, 0.0, 0.0), escala=(2.0, 2.0, 2.0)))
    assert np.allclose(lida.vertices.min(axis=0), (100, 0, 0))
    assert np.allclose(np.ptp(lida.vertices, axis=0), (20, 40, 60))


def test_fbx_gira_na_ordem_certa():
    lida = ler_fbx(criar_fbx(giro=(90.0, 0.0, 0.0)))
    assert np.allclose(np.ptp(lida.vertices, axis=0), (10, 30, 20))


def test_fbx_espelhado_nao_fica_do_avesso():
    malha, _ = normalizar(ler_fbx(criar_fbx(escala=(-1.0, 1.0, 1.0))))
    assert malha.is_watertight
    assert malha.volume > 0


def test_fbx_le_a_unidade_e_o_eixo_do_arquivo():
    lida = ler_fbx(criar_fbx(para_cima=2, unidade=100.0))
    assert lida.mm_por_unidade == 1000.0
    assert lida.para_cima == (0.0, 0.0, 1.0)


def test_fbx_so_com_animacao_diz_o_que_fazer():
    with pytest.raises(ModeloInvalido, match="não tem malha"):
        ler_fbx(criar_fbx(com_malha=False))
    assert "Mixamo" in SEM_MALHA


def test_fbx_em_texto_tem_mensagem_propria():
    with pytest.raises(ModeloInvalido, match="formato de texto"):
        ler_fbx(b"; FBX 7.4.0 project file\nFBXHeaderExtension: {\n}\n")


def test_stl_obj_ply_off_e_glb(tmp_path):
    cubo = trimesh.creation.box((10, 20, 30))
    for sufixo in ("stl", "obj", "ply", "off"):
        cubo.export(tmp_path / f"cubo.{sufixo}")
    (tmp_path / "cubo.glb").write_bytes(trimesh.exchange.gltf.export_glb(trimesh.Scene(cubo)))
    for sufixo in ("stl", "ply", "off"):
        lida = ler_modelo(tmp_path / f"cubo.{sufixo}")
        assert np.allclose(np.ptp(lida.vertices, axis=0), (10, 20, 30)), sufixo
        assert lida.para_cima == (0.0, 0.0, 1.0)
    assert ler_modelo(tmp_path / "cubo.obj").para_cima == (0.0, 1.0, 0.0)
    glb = ler_modelo(tmp_path / "cubo.glb")
    assert glb.mm_por_unidade == 1000.0
    assert glb.para_cima == (0.0, 1.0, 0.0)


def test_glb_junta_os_nos_com_as_posicoes_de_cada_um(tmp_path):
    cena = trimesh.Scene()
    cena.add_geometry(trimesh.creation.box((1, 1, 1)), node_name="a")
    cena.add_geometry(trimesh.creation.box((1, 1, 1)), node_name="b", transform=trimesh.transformations.translation_matrix((10, 0, 0)))
    (tmp_path / "dois.glb").write_bytes(trimesh.exchange.gltf.export_glb(cena))
    lida = ler_modelo(tmp_path / "dois.glb")
    assert np.allclose(lida.vertices[:, 0].max() - lida.vertices[:, 0].min(), 11)


def test_glb_com_compressao_explica_o_motivo(tmp_path):
    cabecalho = json.dumps({"asset": {"version": "2.0"}, "extensionsRequired": ["KHR_draco_mesh_compression"]}).encode()
    bruto = b"glTF" + struct.pack("<II", 2, 20 + len(cabecalho)) + struct.pack("<I", len(cabecalho)) + b"JSON" + cabecalho
    (tmp_path / "x.glb").write_bytes(bruto)
    with pytest.raises(ModeloInvalido, match="Draco"):
        ler_modelo(tmp_path / "x.glb")


def _criar_3mf(caminho: Path, unidade: str = "millimeter") -> None:
    faces = trimesh.creation.box((10, 20, 30))
    pontos = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in faces.vertices)
    triangulos = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces.faces)
    principal_xml = f"""<?xml version="1.0"?><model unit="{unidade}" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
    <resources>
      <object id="1" type="model"><mesh><vertices>{pontos}</vertices><triangles>{triangulos}</triangles></mesh></object>
      <object id="2" type="model"><components><component objectid="7" p:path="/3D/Objects/outro.model" transform="1 0 0 0 1 0 0 0 1 50 0 0"/></components></object>
    </resources>
    <build><item objectid="1"/><item objectid="2"/></build></model>"""
    outro = f"""<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>
      <object id="7" type="model"><mesh><vertices>{pontos}</vertices><triangles>{triangulos}</triangles></mesh></object></resources><build/></model>"""
    with zipfile.ZipFile(caminho, "w") as pacote:
        pacote.writestr("3D/3dmodel.model", principal_xml)
        pacote.writestr("3D/Objects/outro.model", outro)


def test_3mf_le_componentes_em_outros_arquivos_e_transformacoes(tmp_path):
    _criar_3mf(tmp_path / "x.3mf")
    lida = ler_modelo(tmp_path / "x.3mf")
    assert len(lida.faces) == 24
    assert np.isclose(np.ptp(lida.vertices[:, 0]), 60)


def test_3mf_le_a_unidade(tmp_path):
    _criar_3mf(tmp_path / "x.3mf", "centimeter")
    assert ler_modelo(tmp_path / "x.3mf").mm_por_unidade == 10.0


@pytest.mark.parametrize("sufixo", [".usdz", ".usd", ".blend"])
def test_formatos_recusados_dizem_o_que_baixar(tmp_path, sufixo):
    arquivo = tmp_path / f"x{sufixo}"
    arquivo.write_bytes(b"qualquer coisa")
    with pytest.raises(ModeloInvalido, match="STL, GLB"):
        ler_modelo(arquivo)


def test_extensao_desconhecida(tmp_path):
    arquivo = tmp_path / "x.exe"
    arquivo.write_bytes(b"MZ")
    with pytest.raises(ModeloInvalido, match="não lê"):
        ler_modelo(arquivo)


# ---------- Conserto ----------


def _lida(malha: trimesh.Trimesh, **campos) -> MalhaLida:
    return MalhaLida(np.array(malha.vertices), np.array(malha.faces), **campos)


def test_y_para_cima_vira_z_em_milimetros():
    malha, _ = normalizar(_lida(trimesh.creation.box((0.01, 0.03, 0.02)), mm_por_unidade=1000.0, para_cima=(0.0, 1.0, 0.0)))
    assert np.allclose(malha.extents, (10, 20, 30))
    assert np.isclose(malha.bounds[0, 2], 0)


def test_modelo_sem_fundo_ganha_fundo():
    aberto = trimesh.creation.cylinder(10, 30, sections=48)
    aberto.update_faces(aberto.face_normals[:, 2] > -0.5)
    aberto.remove_unreferenced_vertices()
    assert not aberto.is_watertight
    malha, _ = normalizar(_lida(aberto))
    assert malha.is_watertight
    assert malha.volume > 0


def test_faces_em_sentidos_misturados_sao_acertadas():
    cubo = trimesh.creation.icosphere(subdivisions=2)
    faces = np.array(cubo.faces)
    faces[::3] = faces[::3, ::-1]
    malha, _ = normalizar(MalhaLida(np.array(cubo.vertices), faces))
    assert malha.is_winding_consistent


def test_malha_do_avesso_e_virada():
    cubo = trimesh.creation.box((10, 10, 10))
    malha, _ = normalizar(MalhaLida(np.array(cubo.vertices), np.array(cubo.faces)[:, ::-1]))
    assert malha.volume > 0


def test_malha_grande_demais_e_reduzida_e_continua_fechada(monkeypatch):
    monkeypatch.setattr("forja3d.importacao.preparo.LIMITE_DE_TRIANGULOS", 2000)
    malha, originais = normalizar(_lida(trimesh.creation.icosphere(subdivisions=5, radius=20)))
    assert originais == 20480
    assert len(malha.faces) <= 2200
    assert malha.is_watertight


def test_arquivo_sem_superficie():
    with pytest.raises(ModeloInvalido, match="nenhuma superfície"):
        normalizar(MalhaLida(np.empty((0, 3)), np.empty((0, 3), dtype=np.int64)))


def test_superficie_plana_nao_tem_volume():
    v = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0]], dtype=np.float64)
    with pytest.raises(ModeloInvalido, match="não tem volume"):
        normalizar(MalhaLida(v, np.array([[0, 1, 2]])))


def test_processo_grava_o_resultado_e_erro_vira_mensagem(tmp_path):
    trimesh.creation.box((10, 20, 30)).export(tmp_path / "bom.stl")
    (tmp_path / "ruim.fbx").write_bytes(b"lixo")
    pasta_boa, pasta_ruim = tmp_path / "a", tmp_path / "b"
    pasta_boa.mkdir()
    pasta_ruim.mkdir()
    assert principal(str(tmp_path / "bom.stl"), str(pasta_boa)) == 0
    dados = json.loads((pasta_boa / "leitura.json").read_text(encoding="utf-8"))
    assert dados["ok"] and dados["tamanho_mm"] == [10.0, 20.0, 30.0] and dados["corpos"] == 1
    assert np.load(pasta_boa / "previa.npy").shape[1] == 6
    assert principal(str(tmp_path / "ruim.fbx"), str(pasta_ruim)) == 0
    assert not json.loads((pasta_ruim / "leitura.json").read_text(encoding="utf-8"))["ok"]


def test_leitura_em_processo_separado_de_verdade(tmp_path):
    trimesh.creation.box((10, 20, 30)).export(tmp_path / "bom.stl")
    modelo = ler_em_processo(tmp_path / "bom.stl", timeout=120)
    assert modelo.tamanho_mm == (10.0, 20.0, 30.0)
    assert modelo.stl.is_file() and modelo.previa.is_file()
    (tmp_path / "ruim.glb").write_bytes(b"lixo")
    with pytest.raises(ModeloInvalido):
        ler_em_processo(tmp_path / "ruim.glb", timeout=120)


# ---------- Tamanho e nome ----------


def test_nome_da_parte():
    assert nome_da_parte("Mma Kick (final)") == "Mma_Kick_final"
    assert nome_da_parte("José") == "Jose"
    assert nome_da_parte("!!!") == "importado"
    assert nome_da_parte("modelo") == "importado"
    assert nome_da_parte("caixa", {"caixa", "caixa_2"}) == "caixa_3"


def test_tamanho_que_nao_cabe_vira_o_maximo_e_o_que_cabe_fica(perfil):
    giro = np.eye(3)
    seguro = perfil["volume_mm"]["seguro"]
    assert altura_sugerida((50.0, 50.0, 40.0), giro, perfil) == 40.0
    grande = (3600.0, 1000.0, 3700.0)
    assert altura_sugerida(grande, giro, perfil) == min(100.0, altura_maxima(grande, giro, perfil))
    maxima = altura_maxima(grande, giro, perfil)
    x, y, z = tamanho_girado(grande, giro) * (maxima / grande[2])
    assert x <= seguro["x"] + 1e-6 and y <= seguro["y"] + 1e-6 and z <= seguro["z"] + 1e-6


def test_modelo_minusculo_ganha_tamanho_visivel(perfil):
    assert altura_sugerida((0.5, 0.5, 1.0), np.eye(3), perfil) == 100.0


def test_gravar_parte_aplica_giro_e_escala_e_apoia_na_mesa(tmp_path):
    trimesh.creation.box((10, 20, 30)).export(tmp_path / "o.stl")
    giro = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    parte = gravar_parte(tmp_path / "o.stl", tmp_path, OpcoesDoModelo("peca", 2.0, giro))
    assert parte["tamanho"] == [20.0, 60.0, 40.0]
    assert parte["caixa"]["z"] == [0.0, 40.0]
    assert parte["importada"] is True
    assert parte["volume_mm3"] == pytest.approx(48000, rel=1e-3)
    assert trimesh.load_mesh(tmp_path / "peca.stl").is_watertight


def test_previa_desenha_a_peca_sem_janela_3d():
    cubo = trimesh.creation.box((10, 20, 30))
    pontos, faces = trimesh.sample.sample_surface(cubo, 5000, seed=7)
    amostra = np.hstack((pontos, cubo.face_normals[faces])).astype(np.float32)
    imagem, chao = desenhar_previa(amostra, np.eye(3), (200, 100, 50), 120)
    assert imagem.shape == (120, 120, 4)
    assert (imagem[:, :, 3] > 0).sum() > 500
    assert chao.shape == (4, 2)


def test_previa_olha_o_modelo_de_frente_e_de_cima():
    """Duas placas: a da frente da mesa, com a normal para -Y, precisa cobrir a de trás. Em cima, a da cor mais clara."""
    gerador = np.random.default_rng(7)
    pontos = gerador.uniform(-10, 10, (60000, 3))
    pontos[:30000, 1] = -5.0
    pontos[30000:, 1] = 5.0
    normais = np.zeros_like(pontos)
    normais[:30000, 1] = -1.0
    normais[30000:, 1] = 1.0
    imagem, chao = desenhar_previa(np.hstack((pontos, normais)).astype(np.float32), np.eye(3), (200, 200, 200), 120)
    assert imagem[60, 60, 0] > 100
    # Vista de cima: a borda de trás do chão fica mais alta na imagem que a da frente.
    frente = chao[:2, 1].mean()
    tras = chao[2:, 1].mean()
    assert tras < frente


# ---------- Verificação ----------


def _especificacao(**campos) -> Especificacao:
    base = {
        "nome": "suporte", "finalidade": "x", "material": "PLA", "carga": "nenhuma",
        "dimensoes": {"x_mm": 40, "y_mm": 30, "z_mm": 10}, "pecas": 1, "orientacao_impressao": "em pé",
    }
    return Especificacao.model_validate({**base, **campos})


def _medida_cad(pasta: Path, nome: str = "base", tamanho=(40, 30, 10)) -> dict:
    trimesh.creation.box(tamanho).apply_translation((0, 0, tamanho[2] / 2)).export(pasta / f"{nome}.stl")
    return {
        "nome": nome, "valido": True, "solidos": 1, "volume_mm3": float(np.prod(tamanho)), "tamanho": list(tamanho), "stl": f"{nome}.stl", "furos": [],
        "caixa": {"x": [-tamanho[0] / 2, tamanho[0] / 2], "y": [-tamanho[1] / 2, tamanho[1] / 2], "z": [0, tamanho[2]]},
    }


def _medida_importada(pasta: Path, nome: str = "figura", escala: float = 1.0) -> dict:
    trimesh.creation.box((20, 20, 40)).export(pasta / "bruto.stl")
    return gravar_parte(pasta / "bruto.stl", pasta, OpcoesDoModelo(nome, escala))


def test_verificacao_sem_especificacao_aprova_modelo_que_cabe(perfil, tmp_path):
    resultado = verificar_peca([_medida_importada(tmp_path)], None, perfil, tmp_path)
    assert resultado["aprovado"]
    assert resultado["medidas"][0]["parte"] == "figura"


def test_modelo_importado_nao_entra_na_comparacao_com_a_especificacao(perfil, tmp_path):
    partes = [_medida_cad(tmp_path), _medida_importada(tmp_path)]
    assert verificar_peca(partes, _especificacao(), perfil, tmp_path)["aprovado"]


def test_modelo_grande_demais_continua_bloqueado(perfil, tmp_path):
    resultado = verificar_peca([_medida_importada(tmp_path, escala=20.0)], None, perfil, tmp_path)
    assert not resultado["aprovado"]
    assert {b["codigo"] for b in resultado["bloqueios"]} & {"FORA_DO_VOLUME_FISICO", "ALTURA_ACIMA_DO_LIMITE"}


def test_malha_aberta_so_avisa_no_importado_e_bloqueia_no_desenhado(perfil, tmp_path):
    aberta = trimesh.creation.box((20, 20, 40))
    aberta.update_faces(np.arange(len(aberta.faces)) != 0)
    aberta.export(tmp_path / "bruto.stl")
    parte = gravar_parte(tmp_path / "bruto.stl", tmp_path, OpcoesDoModelo("buraco"))
    resultado = verificar_peca([parte], None, perfil, tmp_path)
    assert resultado["aprovado"]
    assert "MALHA_ABERTA" in {a["codigo"] for a in resultado["alertas"]}
    desenhada = {**parte, "importada": False}
    assert "MALHA_ABERTA" in {b["codigo"] for b in verificar_peca([desenhada], _especificacao(dimensoes={"x_mm": 20, "y_mm": 20, "z_mm": 40}), perfil, tmp_path)["bloqueios"]}


def test_corpos_separados_avisam(perfil, tmp_path):
    dois = trimesh.util.concatenate([trimesh.creation.box((10, 10, 10)), trimesh.creation.box((10, 10, 10)).apply_translation((30, 0, 0))])
    dois.export(tmp_path / "bruto.stl")
    resultado = verificar_peca([gravar_parte(tmp_path / "bruto.stl", tmp_path, OpcoesDoModelo("dois"))], None, perfil, tmp_path)
    assert "CORPOS_SEPARADOS" in {a["codigo"] for a in resultado["alertas"]}


# ---------- Projeto, versões e arquivo ----------


class ClienteFalso:
    modelo = "falso"

    def __init__(self, _config=None):
        pass

    def conversar(self, *_a, **_k):
        raise AssertionError("Importar um modelo não pode chamar a IA")

    def verificar(self):
        pass


def _config(tmp_path: Path) -> Configuracao:
    return Configuracao(ia=ConfigIA(endpoint="http://x/v1", api_key="chave", modelo="m"), caminho_orca=tmp_path / "orca.exe", pasta_projetos=tmp_path / "projetos")


@pytest.fixture
def stl_cubo(tmp_path) -> Path:
    caminho = tmp_path / "baixado.stl"
    trimesh.creation.box((20, 20, 40)).export(caminho)
    return caminho


def _versao_com_cad(projeto: Projeto, especificacao: Especificacao) -> str:
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(40, 30, 10)\n", encoding="utf-8")
    versao = projeto.registrar_versao(pasta, especificacao.model_dump(), [_medida_cad(pasta)], "cad")
    projeto.estado.update({"versao_atual": versao, "especificacao": especificacao.model_dump()})
    projeto.salvar_estado()
    return versao


def test_peca_nova_so_com_modelo_importado(perfil, tmp_path, stl_cubo):
    projeto = Projeto.criar(tmp_path / "projetos", "baixado")
    agente = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), projeto)
    parte = agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("figura"))
    assert agente.versao_atual == "v001"
    assert agente.especificacao is None
    assert agente.so_importadas()
    assert agente.pode_aprovar()
    assert parte["tamanho"] == [20.0, 20.0, 40.0]
    assert (projeto.pasta_versao("v001") / "modelo.py").is_file()
    assert "malha pronta" in agente.mensagens[-1]["content"]
    assert projeto.layout("v001", perfil).itens[0].parte == "figura"


def test_modelo_entra_na_peca_aberta_sem_tirar_o_que_ja_havia(perfil, tmp_path, stl_cubo):
    projeto = Projeto.criar(tmp_path / "projetos", "peca")
    _versao_com_cad(projeto, _especificacao())
    agente = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), projeto)
    agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("figura"))
    assert agente.versao_atual == "v002"
    assert list(projeto.stls_por_parte("v002")) == ["base", "figura"]
    assert all(c.is_file() for c in projeto.stls("v002"))
    assert (projeto.pasta_versao("v002") / "modelo.py").read_text(encoding="utf-8").startswith("import cadquery")
    assert agente.pode_aprovar()
    assert {i.parte for i in projeto.layout("v002", perfil).itens} == {"base", "figura"}


def test_nome_repetido_ganha_sufixo(perfil, tmp_path, stl_cubo):
    projeto = Projeto.criar(tmp_path / "projetos", "peca")
    agente = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), projeto)
    agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("figura"))
    parte = agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("figura"))
    assert parte["nome"] == "figura_2"
    assert list(projeto.stls_por_parte("v002")) == ["figura", "figura_2"]


@pytest.mark.cad
def test_modelo_importado_continua_quando_o_agente_gera_outra_versao(perfil, tmp_path, stl_cubo):
    projeto = Projeto.criar(tmp_path / "projetos", "peca")
    _versao_com_cad(projeto, _especificacao())
    agente = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), projeto)
    agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("figura"))
    resposta, _ = agente._executar_cad({"codigo": "import cadquery as cq\nresult = cq.Workplane('XY').box(40, 30, 10, centered=(True, True, False))\n"})
    assert resposta["ok"]
    assert agente.versao_atual == "v003"
    assert [p["nome"] for p in projeto.medidas("v003")] == ["modelo", "figura"]
    assert projeto.pasta_versao("v003").joinpath("figura.stl").is_file()
    resultado, _ = agente._verificar({})
    assert resultado["aprovado"], resultado["bloqueios"]


def test_voltar_para_uma_versao_so_com_importado(perfil, tmp_path, stl_cubo):
    projeto = Projeto.criar(tmp_path / "projetos", "peca")
    agente = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), projeto)
    agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("figura"))
    agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("outra"))
    agente.restaurar_versao("v001")
    assert agente.versao_atual == "v001"
    assert agente.pode_aprovar()


def test_arquivo_forja3d_leva_e_traz_o_modelo_importado(perfil, tmp_path, stl_cubo):
    projeto = Projeto.criar(tmp_path / "projetos", "peca")
    agente = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), projeto)
    agente.adicionar_modelo(stl_cubo, OpcoesDoModelo("figura"))
    arquivo = salvar_projeto(projeto, tmp_path / "x.forja3d")
    aberto = importar_projeto(arquivo, tmp_path / "outros", perfil)
    medidas = aberto.medidas("v001")
    assert medidas[0]["importada"] is True
    assert aberto.stls("v001")[0].is_file()
    novo = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), aberto)
    assert novo.so_importadas()
    assert novo.resultado_verificacao is None
    novo.verificar_versao_atual()
    assert novo.pode_aprovar()


def _esperar_fim(qtbot, janela: JanelaPrincipal) -> None:
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=120000)


@pytest.fixture
def janela(qtbot, perfil, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    janela = JanelaPrincipal(_config(tmp_path), perfil, ClienteFalso, verificar_endpoint=False)
    qtbot.addWidget(janela)
    yield janela
    janela.close()


class DialogoAceito:
    def __init__(self, resposta, nome="figura"):
        self.resposta = resposta
        self.nome = nome

    def __call__(self, modelo, *_a):
        self.modelo = modelo
        return self

    def exec(self):
        return self.resposta

    def opcoes(self):
        return OpcoesDoModelo(self.nome)

    def deleteLater(self):
        pass


def test_janela_importa_para_uma_peca_nova(qtbot, janela, stl_cubo, monkeypatch):
    dialogo = DialogoAceito(1)
    monkeypatch.setattr("forja3d.interface.janela_principal.DialogoImportar", dialogo)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_a, **_k: ([str(stl_cubo)], ""))
    janela._importar_modelo()
    assert janela.ocupado
    qtbot.waitUntil(lambda: janela.projeto is not None and janela.orquestrador is not None and janela.orquestrador.versao_atual == "v001" and not janela.ocupado, timeout=120000)
    assert janela.projeto.titulo == "baixado"
    assert janela.versao_vista == "v001"
    assert janela.painel_3d.mesa.itens[0].parte == "figura"
    assert janela.botao_aprovar.isEnabled()
    assert not list(dialogo.modelo.pasta.parent.glob(dialogo.modelo.pasta.name))


def test_desistir_na_janela_nao_cria_peca_nem_deixa_sobra(qtbot, janela, stl_cubo, monkeypatch):
    dialogo = DialogoAceito(0)
    monkeypatch.setattr("forja3d.interface.janela_principal.DialogoImportar", dialogo)
    janela._importar_modelo(stl_cubo)
    qtbot.waitUntil(lambda: hasattr(dialogo, "modelo") and not dialogo.modelo.pasta.exists(), timeout=120000)
    assert janela.projeto is None
    assert not janela.ocupado


def test_arquivo_recusado_mostra_a_mensagem(qtbot, janela, tmp_path, monkeypatch):
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, texto: avisos.append(texto))
    (tmp_path / "x.usdz").write_bytes(b"PK")
    janela._importar_modelo(tmp_path / "x.usdz")
    qtbot.waitUntil(lambda: bool(avisos), timeout=120000)
    assert "USDZ" in avisos[0]
    assert janela.projeto is None


def test_soltar_o_arquivo_na_mesa_importa(qtbot, janela, stl_cubo, monkeypatch):
    pedidos = []
    janela.painel_3d.modelo_solto.disconnect()
    janela.painel_3d.modelo_solto.connect(pedidos.append)
    dados = QMimeData()
    dados.setUrls([QUrl.fromLocalFile(str(stl_cubo))])
    evento = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, dados, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    janela.painel_3d.dropEvent(evento)
    assert pedidos == [[stl_cubo]]
    outros = QMimeData()
    outros.setUrls([QUrl.fromLocalFile(str(stl_cubo.with_suffix(".png")))])
    janela.painel_3d.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, outros, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert len(pedidos) == 1


def test_botao_de_importar_fica_travado_enquanto_o_app_trabalha(janela):
    assert janela.painel_3d.botao_importar.isEnabled()
    janela._definir_ocupado(True)
    assert not janela.painel_3d.botao_importar.isEnabled()
    assert not janela.acao_importar.isEnabled()
    janela._definir_ocupado(False)


# ---------- Janela de escolhas ----------


@pytest.fixture
def modelo_lido(tmp_path) -> ModeloLido:
    pasta = tmp_path / "lido"
    pasta.mkdir()
    cubo = trimesh.creation.box((10, 20, 30))
    cubo.export(pasta / "modelo.stl")
    pontos, faces = trimesh.sample.sample_surface(cubo, 3000, seed=7)
    np.save(pasta / "previa.npy", np.hstack((pontos, cubo.face_normals[faces])).astype(np.float32))
    return ModeloLido(tmp_path / "José e Maria.glb", pasta, (10.0, 20.0, 30.0), 12, 12, True, 1)


def test_dialogo_sugere_nome_e_altura(qtbot, modelo_lido, perfil):
    dialogo = DialogoImportar(modelo_lido, perfil, set())
    qtbot.addWidget(dialogo)
    assert dialogo.campo_nome.text() == "Jose_e_Maria"
    assert dialogo.campo_altura.value() == 30.0
    opcoes = dialogo.opcoes()
    assert opcoes.escala == pytest.approx(1.0)
    assert np.allclose(opcoes.rotacao, np.eye(3))


def test_dialogo_escala_junto_e_gira_sem_mudar_o_tamanho(qtbot, modelo_lido, perfil):
    dialogo = DialogoImportar(modelo_lido, perfil, set())
    qtbot.addWidget(dialogo)
    dialogo.campo_altura.setValue(60.0)
    assert dialogo.opcoes().escala == pytest.approx(2.0)
    assert "20,0 × 40,0 × 60,0 mm" in dialogo.rotulo_medidas.text()
    dialogo.girar("frente")
    assert dialogo.campo_altura.value() == pytest.approx(40.0)
    assert dialogo.opcoes().escala == pytest.approx(2.0)
    assert "20,0 × 60,0 × 40,0 mm" in dialogo.rotulo_medidas.text()
    girada = gravar_parte(modelo_lido.stl, modelo_lido.pasta, dialogo.opcoes())
    assert girada["tamanho"] == [20.0, 60.0, 40.0]


def test_dialogo_nao_deixa_passar_do_limite_da_mesa(qtbot, modelo_lido, perfil):
    dialogo = DialogoImportar(modelo_lido, perfil, set())
    qtbot.addWidget(dialogo)
    dialogo.campo_altura.setValue(100000.0)
    x, y, z = tamanho_girado(modelo_lido.tamanho_mm, dialogo.rotacao) * dialogo.opcoes().escala
    seguro = perfil["volume_mm"]["seguro"]
    assert x <= seguro["x"] and y <= seguro["y"] and z <= seguro["z"]


def test_dialogo_avisa_sobre_buracos_corpos_e_reducao(qtbot, modelo_lido, perfil):
    modelo_lido.fechada = False
    modelo_lido.corpos = 3
    modelo_lido.triangulos_originais = 1200000
    dialogo = DialogoImportar(modelo_lido, perfil, set())
    qtbot.addWidget(dialogo)
    textos = " ".join(a[0] for a in dialogo._avisos())
    assert "buracos" in textos and "3 corpos" in textos and "1.200.000" in textos
    assert "textura" in textos


def test_dialogo_nao_repete_o_nome_de_outra_parte(qtbot, modelo_lido, perfil):
    dialogo = DialogoImportar(modelo_lido, perfil, {"Jose_e_Maria"})
    qtbot.addWidget(dialogo)
    assert dialogo.campo_nome.text() == "Jose_e_Maria_2"
    dialogo.campo_nome.setText("Jose_e_Maria")
    assert dialogo.opcoes().nome == "Jose_e_Maria_2"
