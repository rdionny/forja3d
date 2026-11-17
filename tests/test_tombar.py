"""Deitar a peça na mesa: o que aparece na tela, o que vai para o fatiador e o que o app avisa."""
import json
import math
import os
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtWidgets import QMessageBox

from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.fatiamento.cores import (
    ErroDeCor,
    alturas_planas,
    aplicar_cores,
    estimar_trocas,
    matriz_do_item,
    situacao_das_cores,
)
from forja3d.fatiamento.orca import ParametrosFatiamento, fatiar_layout, localizar_orca, montar_stl_da_mesa
from forja3d.interface.dialogo_tombar import DialogoTombar
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.layout_mesa import ItemMesa, LayoutMesa, rotacao_do_item, tombo_valido
from forja3d.projetos.armazenamento import Projeto
from forja3d.projetos.compartilhar import _item_de_mesa_valido
from forja3d.verificacao.verificador import avisos_da_posicao

CAIXA = (30.0, 40.0, 100.0)
PRETO, BRANCO = "#1A1A1A", "#F5F5F5"
ROTACAO_X_90 = [1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0]


def caixa_em_pe(pasta: Path, nome="torre") -> Path:
    malha = trimesh.creation.box(extents=CAIXA)
    malha.apply_translation((0, 0, CAIXA[2] / 2))
    caminho = pasta / f"{nome}.stl"
    malha.export(caminho)
    return caminho


def mesa(perfil, **dados) -> LayoutMesa:
    return LayoutMesa({"torre": CAIXA}, perfil, [ItemMesa("1", "torre", 110.0, 110.0, **dados)])


# ---------- A mesa ----------


def test_peca_nova_nao_esta_deitada_e_o_arquivo_nao_muda(perfil):
    layout = mesa(perfil)
    assert layout.tamanho_do_item(layout.item("1")) == (30.0, 40.0)
    assert layout.altura_do_item(layout.item("1")) == 100.0
    assert "tombo" not in layout.para_dados()["itens"][0]


@pytest.mark.parametrize(
    ("eixo", "graus", "tamanho", "altura"),
    [
        ("x", 90, (30.0, 100.0), 40.0),
        ("x", -90, (30.0, 100.0), 40.0),
        ("y", 90, (100.0, 40.0), 30.0),
        ("y", -90, (100.0, 40.0), 30.0),
        ("x", 180, (30.0, 40.0), 100.0),
    ],
)
def test_deitar_a_90_e_180_troca_as_medidas(perfil, eixo, graus, tamanho, altura):
    layout = mesa(perfil)
    assert layout.tombar("1", eixo, graus)
    assert layout.tamanho_do_item(layout.item("1")) == tamanho
    assert layout.altura_do_item(layout.item("1")) == altura


def test_120_graus_usa_a_caixa_em_volta_da_peca_girada(perfil):
    layout = mesa(perfil)
    assert layout.tombar("1", "x", 120)
    dx, dy = layout.tamanho_do_item(layout.item("1"))
    seno, cosseno = math.sin(math.radians(120)), abs(math.cos(math.radians(120)))
    assert dx == pytest.approx(30.0)
    assert dy == pytest.approx(40.0 * cosseno + 100.0 * seno)
    assert layout.altura_do_item(layout.item("1")) == pytest.approx(40.0 * seno + 100.0 * cosseno)


def test_quatro_vezes_90_volta_ao_comeco(perfil):
    layout = mesa(perfil)
    for _ in range(4):
        assert layout.tombar("1", "x", 90)
    assert layout.item("1").tombo is None


def test_direcoes_seguem_a_mesa_mesmo_com_a_peca_girada(perfil):
    """Depois de girar 90°, "para a frente" continua sendo para a frente da mesa, e não para onde a peça aponta."""
    layout = mesa(perfil)
    layout.girar("1")
    assert layout.tombar("1", "x", 90)
    item = layout.item("1")
    # Rotação completa: o topo da peça (0, 0, 1) vai para -Y, a frente da mesa.
    r = np.reshape(rotacao_do_item(item), (3, 3))
    assert r @ np.array([0, 0, 1.0]) == pytest.approx([0, -1.0, 0])


def test_girar_uma_peca_deitada_troca_a_largura_e_a_profundidade(perfil):
    layout = mesa(perfil)
    layout.tombar("1", "x", 90)
    layout.girar("1")
    assert layout.tamanho_do_item(layout.item("1")) == (100.0, 30.0)
    assert layout.altura_do_item(layout.item("1")) == 40.0


def test_o_que_nao_cabe_e_recusado_sem_mudar_nada(perfil):
    layout = LayoutMesa({"longa": (20.0, 20.0, 230.0)}, perfil, [ItemMesa("1", "longa", 110.0, 110.0)])
    assert not layout.tombar("1", "x", 90)
    assert layout.item("1").tombo is None


def test_deitar_traz_a_peca_de_volta_para_dentro_da_mesa(perfil):
    layout = LayoutMesa({"torre": CAIXA}, perfil, [ItemMesa("1", "torre", 20.0, 25.0)])
    layout.tombar("1", "x", 90)
    assert layout.item("1").y == pytest.approx(5.0 + 50.0)


def test_salvar_e_abrir(perfil):
    layout = mesa(perfil)
    layout.tombar("1", "x", 120)
    dados = json.loads(json.dumps(layout.para_dados()))
    reaberta = LayoutMesa.de_dados(dados, {"torre": CAIXA}, perfil)
    assert reaberta.item("1").tombo == layout.item("1").tombo
    assert reaberta.assinatura() == layout.assinatura()


def test_assinatura_muda_e_volta(perfil):
    layout = mesa(perfil)
    antes = layout.assinatura()
    layout.tombar("1", "x", 90)
    assert layout.assinatura() != antes
    layout.endireitar("1")
    assert layout.assinatura() == antes


def test_assinatura_nao_quebra_com_itens_no_mesmo_lugar(perfil):
    layout = LayoutMesa({"torre": CAIXA}, perfil, [ItemMesa("1", "torre", 110.0, 110.0, 1, 0, {"": PRETO}), ItemMesa("2", "torre", 110.0, 110.0, 1, 0, {}, None, ROTACAO_X_90)])
    assert layout.assinatura()


def test_duplicar_leva_a_posicao(perfil):
    layout = mesa(perfil)
    layout.tombar("1", "x", 90)
    copia = layout.duplicar("1")
    assert copia.tombo == layout.item("1").tombo
    assert copia.tombo is not layout.item("1").tombo


def test_organizar_mantem_deitada_e_usa_o_tamanho_real(perfil):
    layout = LayoutMesa({"torre": CAIXA, "outra": (50.0, 50.0, 10.0)}, perfil, [ItemMesa("1", "torre", 60.0, 60.0), ItemMesa("2", "outra", 150.0, 150.0)])
    layout.tombar("1", "x", 90)
    layout.organizar()
    assert layout.item("1").tombo == ROTACAO_X_90
    assert not layout.conflitos()


def test_posicao_livre_considera_o_tamanho_deitado(perfil):
    layout = LayoutMesa({"torre": CAIXA}, perfil, [ItemMesa("1", "torre", 110.0, 110.0, 1, 0, {}, None, ROTACAO_X_90)])
    copia = layout.duplicar("1")
    assert copia.arquivo == 1
    assert not layout.conflitos()


@pytest.mark.parametrize(
    "dados",
    [
        [1.0] * 9,
        [2.0, 0, 0, 0, 2.0, 0, 0, 0, 2.0],
        [-1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0],
        [1.0, 0, 0, 0, 1.0, 0, 0, 0],
        "x",
        [True] * 9,
        [float("nan")] * 9,
    ],
)
def test_matriz_que_nao_e_rotacao_nao_entra(perfil, dados):
    assert tombo_valido(dados) is None
    layout = LayoutMesa.de_dados({"partes": ["torre"], "itens": [{"id": "1", "parte": "torre", "x": 100, "y": 100, "tombo": dados}]}, {"torre": CAIXA}, perfil)
    assert layout.item("1").tombo is None


def test_arquivo_de_outra_pessoa_com_matriz_ruim_e_recusado():
    base = {"id": "1", "parte": "a", "x": 1.0, "y": 1.0}
    assert _item_de_mesa_valido(base)
    assert _item_de_mesa_valido({**base, "tombo": ROTACAO_X_90})
    assert not _item_de_mesa_valido({**base, "tombo": [2.0, 0, 0, 0, 2.0, 0, 0, 0, 2.0]})


# ---------- O que vai para o fatiador ----------


@pytest.mark.parametrize(("eixo", "graus"), [("x", 90), ("y", -90), ("x", 120), ("y", 37)])
def test_stl_da_mesa_fica_apoiado_e_dentro_do_espaco_reservado(perfil, tmp_path, eixo, graus):
    stl = caixa_em_pe(tmp_path)
    layout = mesa(perfil)
    layout.tombar("1", eixo, graus)
    item = layout.item("1")
    destino = tmp_path / "mesa.stl"
    montar_stl_da_mesa([item], {"torre": stl}, destino)
    (x0, y0, z0), (x1, y1, z1) = trimesh.load_mesh(destino).bounds
    dx, dy = layout.tamanho_do_item(item)
    assert z0 == pytest.approx(0.0, abs=1e-5)
    assert z1 == pytest.approx(layout.altura_do_item(item), abs=1e-3)
    assert x0 >= item.x - dx / 2 - 1e-3 and x1 <= item.x + dx / 2 + 1e-3
    assert y0 >= item.y - dy / 2 - 1e-3 and y1 <= item.y + dy / 2 + 1e-3


def test_sem_tombo_o_stl_da_mesa_e_o_mesmo_de_antes(perfil, tmp_path):
    stl = caixa_em_pe(tmp_path)
    item = ItemMesa("1", "torre", 110.0, 100.0, 1, 90)
    destino = tmp_path / "mesa.stl"
    montar_stl_da_mesa([item], {"torre": stl}, destino)
    (x0, y0, z0), (x1, y1, z1) = trimesh.load_mesh(destino).bounds
    assert (x0, x1, y0, y1, z0, z1) == pytest.approx((90.0, 130.0, 85.0, 115.0, 0.0, 100.0), abs=1e-4)


def test_o_topo_vai_para_onde_o_menu_diz(perfil, tmp_path):
    """Um furo no topo mostra para que lado a peça caiu."""
    layout = mesa(perfil)
    layout.tombar("1", "x", 90)
    inteira = trimesh.creation.box(extents=CAIXA)
    inteira.apply_translation((0, 0, CAIXA[2] / 2))
    matriz = matriz_do_item(inteira, layout.item("1"))
    base, topo = trimesh.transform_points([[0.0, 0.0, 0.0], [0.0, 0.0, 100.0]], matriz)
    assert topo[1] < base[1]
    assert topo[2] == pytest.approx(base[2])


# ---------- Cores por altura ----------


def _placa_com_relevo(pasta: Path) -> Path:
    """Em pé, ela tem um degrau em Z = 80. Deitada, o degrau fica em outra altura."""
    baixo = trimesh.creation.box(extents=(30, 40, 80))
    baixo.apply_translation((0, 0, 40))
    alto = trimesh.creation.box(extents=(30, 20, 20))
    alto.apply_translation((0, 0, 90))
    caminho = pasta / "degrau.stl"
    trimesh.util.concatenate([baixo, alto]).export(caminho)
    return caminho


def test_troca_de_cor_vale_para_a_altura_deitada(perfil):
    layout = mesa(perfil)
    medidas = {"torre": {"tamanho": list(CAIXA)}}
    layout.tombar("1", "x", 90)
    with pytest.raises(ErroDeCor, match="40 mm de altura"):
        aplicar_cores(layout, "torre", medidas, PRETO, None, [{"z": 80.0, "cor": BRANCO}])
    assert aplicar_cores(layout, "torre", medidas, PRETO, None, [{"z": 20.0, "cor": BRANCO}]) == [1]
    assert estimar_trocas(layout, 1, medidas, 0.2) == 1


def test_alturas_planas_seguem_a_posicao(tmp_path):
    stl = _placa_com_relevo(tmp_path)
    assert [f["z_mm"] for f in alturas_planas(stl)] == [80.0]
    # Deitada para a frente, as faces do degrau viram paredes, e o topo plano passa a ser o maior.
    assert 80.0 not in [f["z_mm"] for f in alturas_planas(stl, ROTACAO_X_90)]


def test_agente_sabe_que_a_peca_esta_deitada(perfil, tmp_path):
    stl = caixa_em_pe(tmp_path)
    layout = mesa(perfil)
    layout.tombar("1", "x", 90)
    situacao = situacao_das_cores(layout, "torre", {"torre": {"tamanho": list(CAIXA)}}, stl, 0.2)
    assert situacao["deitada_na_mesa"]
    assert situacao["altura_mm"] == 40.0


# ---------- O que o app avisa ----------


def test_deitada_a_90_fica_apoiada_em_uma_face(perfil, tmp_path):
    assert avisos_da_posicao(caixa_em_pe(tmp_path), ROTACAO_X_90, "torre", perfil) == []


def test_a_120_graus_encosta_em_uma_aresta(perfil, tmp_path):
    layout = mesa(perfil)
    layout.tombar("1", "x", 120)
    codigos = {a["codigo"] for a in avisos_da_posicao(caixa_em_pe(tmp_path), layout.item("1").tombo, "torre", perfil)}
    assert "SEM_APOIO" in codigos
    assert "BALANCO" in codigos


# ---------- Na janela ----------


class ClienteFalso:
    def __init__(self, _config=None):
        pass

    def verificar(self):
        return None


@pytest.fixture
def janela(qtbot, perfil, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    config = Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path / "projetos")
    janela = JanelaPrincipal(config, perfil, ClienteFalso, verificar_endpoint=False)
    qtbot.addWidget(janela)
    projeto = Projeto.criar(tmp_path / "projetos")
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("# nada\n", encoding="utf-8")
    caixa_em_pe(pasta)
    medida = {
        "nome": "torre", "importada": True, "valido": True, "solidos": 1, "volume_mm3": 30.0 * 40.0 * 100.0, "tamanho": list(CAIXA), "stl": "torre.stl",
        "furos": [], "caixa": {"x": [-15.0, 15.0], "y": [-20.0, 20.0], "z": [0.0, 100.0]},
    }
    projeto.estado["versao_atual"] = projeto.registrar_versao(pasta, None, [medida], "teste")
    projeto.salvar_estado()
    janela.abrir_projeto(projeto)
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    yield janela
    janela.close()


def _selecionar(janela):
    painel = janela.painel_3d
    painel.selecionar(painel.mesa.itens[0].id)
    return painel, painel.mesa.itens[0]


def test_botao_so_liga_com_um_item_selecionado(janela):
    painel = janela.painel_3d
    painel.selecionar(None)
    assert not painel.botoes["tombar"].isEnabled()
    _selecionar(janela)
    assert painel.botoes["tombar"].isEnabled()


def test_menu_tem_as_quatro_direcoes_e_outro_angulo(janela):
    painel, _ = _selecionar(janela)
    painel._preencher_menu_tombar(painel.menu_tombar)
    textos = [a.text() for a in painel.menu_tombar.actions() if a.text()]
    assert textos == ["Deitar para a frente (90°)", "Deitar para trás (90°)", "Deitar para a direita (90°)", "Deitar para a esquerda (90°)", "Outro ângulo…", "Voltar à posição original"]


def test_tombar_salva_e_invalida_o_gcode_e_desfaz(janela):
    painel, _ = _selecionar(janela)
    antiga = painel.mesa.assinatura()
    painel.tombar("x", 90.0)
    assert janela.projeto.layout("v001", janela.perfil).itens[0].tombo == ROTACAO_X_90
    assert not janela._fatiamento_vale({"layout": antiga})
    assert "30,0 × 100,0 mm na mesa, 40,0 mm de altura" in painel.rotulo_medida.text()
    painel.desfazer()
    assert painel.mesa.itens[0].tombo is None
    painel.refazer()
    assert painel.mesa.itens[0].tombo == ROTACAO_X_90


def test_voltar_a_posicao_original(janela):
    painel, _ = _selecionar(janela)
    painel.tombar("x", 90.0)
    painel.tombar("y", 90.0)
    painel.endireitar()
    assert painel.mesa.itens[0].tombo is None
    painel._preencher_menu_tombar(painel.menu_tombar)
    assert not painel.menu_tombar.actions()[-1].isEnabled()


def test_copiar_e_colar_leva_a_posicao(janela):
    painel, _ = _selecionar(janela)
    painel.tombar("x", 90.0)
    painel.copiar()
    painel.colar()
    assert all(i.tombo == ROTACAO_X_90 for i in painel.mesa.itens)
    assert len(painel.mesa.itens) == 2


def test_o_que_nao_cabe_avisa_e_nao_entra_no_desfazer(janela, monkeypatch):
    painel, _ = _selecionar(janela)
    avisos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: avisos.append(a[2]))
    painel.mesa.tamanhos["torre"] = (30.0, 40.0, 300.0)
    tamanho_do_historico = len(painel._historico)
    painel.tombar("x", 90.0)
    assert "não cabe" in avisos[0]
    assert painel.mesa.itens[0].tombo is None
    assert len(painel._historico) == tamanho_do_historico


def test_dialogo_comeca_em_120_e_devolve_a_direcao(qtbot):
    dialogo = DialogoTombar()
    qtbot.addWidget(dialogo)
    assert dialogo.campo_angulo.value() == 120
    assert dialogo.escolha() == ("x", 120.0)
    dialogo.combo_direcao.setCurrentIndex(3)
    dialogo.campo_angulo.setValue(45)
    assert dialogo.escolha() == ("y", -45.0)


def test_aviso_aparece_na_mesa_e_some_ao_voltar(janela):
    painel, _ = _selecionar(janela)
    painel.tombar("x", 120.0)
    assert "aresta ou ponta" in painel.rotulo_aviso.text()
    painel.endireitar()
    assert painel.rotulo_aviso.text() == ""


def test_preparar_sem_suporte_pede_confirmacao_so_quando_precisa(janela, monkeypatch):
    painel, _ = _selecionar(janela)
    perguntas = []

    def responder(_janela, titulo, texto, *_resto):
        perguntas.append(texto)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", responder)
    janela.caixa_suporte.setChecked(False)
    assert janela._confirmar_posicao()
    painel.tombar("x", 90.0)
    assert janela._confirmar_posicao()
    assert not perguntas
    painel.tombar("x", 30.0)
    assert not janela._confirmar_posicao()
    assert "aresta ou ponta" in perguntas[0]
    janela.caixa_suporte.setChecked(True)
    assert janela._confirmar_posicao()


# ---------- No OrcaSlicer ----------


@pytest.mark.orca
@pytest.mark.skipif(localizar_orca() is None, reason="OrcaSlicer não encontrado")
class TestNoOrcaSlicer:
    LEVE: ClassVar[dict] = {"qualidade": "rascunho", "paredes": 2, "preenchimento_pct": 10}

    def test_deitada_a_90_sai_mais_baixa_e_no_lugar_certo(self, perfil, tmp_path):
        stl = caixa_em_pe(tmp_path)
        layout = mesa(perfil)
        layout.tombar("1", "x", 90)
        resumo = fatiar_layout(layout, {"torre": stl}, ParametrosFatiamento(material="PLA", **self.LEVE), perfil, tmp_path / "saida")
        v = resumo["validacao"]
        assert v["aprovado"], v["bloqueios"]
        assert v["z_maximo_mm"] == pytest.approx(40.0, abs=11)

    def test_a_120_graus_sai_com_suporte(self, perfil, tmp_path):
        stl = caixa_em_pe(tmp_path)
        layout = mesa(perfil)
        layout.tombar("1", "x", 120)
        resumo = fatiar_layout(layout, {"torre": stl}, ParametrosFatiamento(material="PLA", suporte=True, **self.LEVE), perfil, tmp_path / "saida")
        assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]

    def test_deitada_com_troca_de_cor_pausa_na_altura_deitada(self, perfil, tmp_path):
        stl = caixa_em_pe(tmp_path)
        layout = mesa(perfil)
        layout.tombar("1", "x", 90)
        aplicar_cores(layout, "torre", {"torre": {"tamanho": list(CAIXA)}}, PRETO, None, [{"z": 20.0, "cor": BRANCO}])
        resumo = fatiar_layout(layout, {"torre": stl}, ParametrosFatiamento(material="PLA", **self.LEVE), perfil, tmp_path / "saida", None, None, {"torre": {"tamanho": list(CAIXA)}})
        assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
        assert resumo["validacao"]["trocas_de_filamento"] == 1
        assert resumo["arquivos"][0]["cores"]["trocas"][0]["z"] == pytest.approx(20.0, abs=0.5)
