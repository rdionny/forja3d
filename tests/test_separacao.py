"""Separar um modelo baixado em peças por cor: as regiões, o corte com encaixe e o caminho pela tela."""
import os

import manifold3d
import numpy as np
import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtWidgets import QLabel, QMenu, QMessageBox

from forja3d.agente.orquestrador import Orquestrador
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.importacao import separacao
from forja3d.importacao.malha import ModeloInvalido
from forja3d.importacao.preparo import OpcoesDoModelo, gravar_parte
from forja3d.importacao.separacao import (
    BASE,
    FOLGA_MM,
    PINO_COMPRIMENTO_MM,
    PROFUNDIDADE_MM,
    ErroDeSeparacao,
    ler_regioes,
    separar,
)
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.mcp.servidor import SemIA
from forja3d.projetos.armazenamento import Projeto

VERMELHO, PRETO, ROSA = "#D32F2F", "#1A1A1A", "#EC407A"
# Um ponto na superfície de cada região do modelo de teste.
CUPULA, CONE, DISCO, LADO_DO_DISCO, GRAVADO = (45, 20, 25), (32.5, 8, 27), (15, 20, 21.5), (21, 20, 20.75), (30, 33, 19)


def _exportar(solido, caminho):
    dados = solido.to_mesh()
    trimesh.Trimesh(np.asarray(dados.vert_properties)[:, :3], np.asarray(dados.tri_verts), process=False).export(caminho)
    return caminho


@pytest.fixture(scope="module")
def modelo(tmp_path_factory):
    """Um bloco com um disco em relevo, uma cúpula, um cone e um rebaixo: os casos do vídeo, com medida conhecida."""
    bloco = manifold3d.Manifold.cube((60, 40, 20))
    disco = manifold3d.Manifold.cylinder(1.5, 6, 6, 48).translate((15, 20, 20))
    cupula = manifold3d.Manifold.sphere(8, 64).translate((45, 20, 17))
    cone = manifold3d.Manifold.cylinder(14, 5, 0.01, 48).translate((30, 8, 20))
    gravado = manifold3d.Manifold.cube((10, 6, 1)).translate((25, 30, 19))
    return _exportar(bloco + disco + cupula + cone - gravado, tmp_path_factory.mktemp("modelo") / "boneco.stl")


@pytest.fixture(scope="module")
def regioes(modelo):
    return ler_regioes(modelo)


def regiao_em(regioes, ponto) -> int:
    centros = regioes.vertices[regioes.faces].mean(axis=1)
    return int(regioes.regiao_da_face[np.argmin(np.linalg.norm(centros - np.array(ponto, dtype=float), axis=1))])


# ---------- Regiões ----------


def test_acha_as_regioes_cercadas_por_quinas(regioes):
    assert regioes.areas[BASE] == 2400.0  # O fundo do bloco, a maior face.
    numeros = {regiao_em(regioes, p) for p in (CUPULA, CONE, DISCO, LADO_DO_DISCO, GRAVADO)}
    assert len(numeros) == 5 and min(numeros) > BASE
    assert regioes.areas[regiao_em(regioes, CUPULA)] == pytest.approx(2 * np.pi * 8 * 5, rel=0.01)
    assert regioes.areas[regiao_em(regioes, DISCO)] == pytest.approx(np.pi * 36, rel=0.01)
    assert regioes.separaveis == len(regioes.areas) - 1
    assert len(regioes.contornos) > 100


def test_peca_lisa_nao_tem_o_que_separar(tmp_path):
    with pytest.raises(ErroDeSeparacao, match="lisa"):
        ler_regioes(_exportar(manifold3d.Manifold.sphere(10, 64), tmp_path / "bola.stl"))


def test_malha_aberta_e_recusada(tmp_path):
    caixa = trimesh.creation.box(extents=(10, 10, 10))
    caixa.update_faces(np.arange(len(caixa.faces)) > 1)
    caixa.export(tmp_path / "aberta.stl")
    with pytest.raises(ModeloInvalido, match="buracos"):
        ler_regioes(tmp_path / "aberta.stl")


def test_corpos_um_dentro_do_outro_viram_um_solido_so(tmp_path):
    """Como chega um modelo baixado em cores: a esfera do olho atravessa a cabeça."""
    juntos = trimesh.util.concatenate([trimesh.creation.box(extents=(30, 30, 30)), trimesh.creation.icosphere(subdivisions=3, radius=6).apply_translation((0, 0, 15))])
    juntos.export(tmp_path / "juntos.stl")
    lidas = ler_regioes(tmp_path / "juntos.stl")
    calota = regiao_em(lidas, (0, 0, 21))
    assert calota > BASE
    assert lidas.areas[calota] == pytest.approx(2 * np.pi * 6 * 6, rel=0.03)


def test_peca_oca_continua_oca(tmp_path):
    """Um cofrinho: a casca de dentro vem virada, e não pode ser somada nem jogada fora como lasca."""
    oco = trimesh.creation.icosphere(subdivisions=3, radius=8)
    oco.invert()
    cofre = trimesh.util.concatenate([trimesh.creation.box(extents=(30, 30, 30)), trimesh.creation.cylinder(radius=5, height=2).apply_translation((0, 0, 16)), oco])
    cofre.export(tmp_path / "cofre.stl")
    lidas = ler_regioes(tmp_path / "cofre.stl")
    resultado = separar(lidas, {regiao_em(lidas, (0, 0, 17)): PRETO}, pino=False)
    cheio = 27000 + np.pi * 25 * 2
    assert resultado.volume_mm3 + resultado.pecas[0].volume_mm3 == pytest.approx(cheio - abs(oco.volume), rel=0.01)
    assert resultado.solidos == 1


def test_outra_peca_logo_atras_fica_inteira(tmp_path):
    """Modelo articulado: a placa de baixo fica a 0,2 mm da de cima, no alcance do corte, e não pode perder um pedaço."""
    baixo = manifold3d.Manifold.cube((30, 30, 1))
    cima = manifold3d.Manifold.cube((30, 30, 0.6)).translate((0, 0, 1.2)) + manifold3d.Manifold.cylinder(1, 5, 5, 48).translate((15, 15, 1.8))
    lidas = ler_regioes(_exportar(manifold3d.Manifold.compose([baixo, cima]), tmp_path / "duas.stl"))
    resultado = separar(lidas, {regiao_em(lidas, (15, 15, 2.8)): PRETO}, pino=False)
    assert resultado.solidos == 2
    volumes = [p.volume() for p in separacao._solido(resultado.corpo).decompose()]
    assert min(volumes, key=lambda v: abs(v - 900)) == pytest.approx(900, abs=0.01)


# ---------- O corte ----------


def test_cada_regiao_pintada_vira_uma_peca_fechada(modelo, regioes):
    original = trimesh.load_mesh(modelo)
    resultado = separar(regioes, {regiao_em(regioes, CUPULA): VERMELHO, regiao_em(regioes, DISCO): ROSA})
    assert [p.cor for p in resultado.pecas] == [VERMELHO, ROSA]
    assert resultado.corpo.is_watertight and all(p.malha.is_watertight for p in resultado.pecas)
    assert all(len(p.malha.split()) == 1 for p in resultado.pecas)
    assert resultado.corpo.volume < original.volume - 700
    # O que saiu do corpo virou peça, menos a folga do encaixe.
    assert resultado.corpo.volume + sum(p.malha.volume for p in resultado.pecas) == pytest.approx(original.volume, rel=0.01)


def test_com_pino_a_face_plana_vai_na_mesa_e_a_curva_pede_suporte(regioes):
    cupula, disco = separar(regioes, {regiao_em(regioes, CUPULA): VERMELHO, regiao_em(regioes, DISCO): ROSA}).pecas
    assert cupula.com_pino and cupula.precisa_de_suporte
    assert disco.com_pino and not disco.precisa_de_suporte
    # A profundidade conta do ponto mais baixo da região pintada, que aqui é a própria face do disco. Mais 3 mm de pino.
    assert disco.malha.extents == pytest.approx([12 - 2 * FOLGA_MM, 12 - 2 * FOLGA_MM, PROFUNDIDADE_MM + PINO_COMPRIMENTO_MM], abs=0.02)
    # De cabeça para baixo: a face do disco é o que encosta na mesa, e o pino fica no alto.
    embaixo = disco.malha.vertices[:, 2] < disco.malha.bounds[0, 2] + 0.01
    assert np.ptp(disco.malha.vertices[embaixo][:, 0]) == pytest.approx(12 - 2 * FOLGA_MM, abs=0.02)
    no_alto = disco.malha.vertices[:, 2] > disco.malha.bounds[1, 2] - 0.01
    assert np.ptp(disco.malha.vertices[no_alto][:, 0]) < 4.1


def test_sem_pino_o_fundo_plano_vai_na_mesa(regioes):
    cupula, cone = separar(regioes, {regiao_em(regioes, CUPULA): VERMELHO, regiao_em(regioes, CONE): PRETO}, pino=False).pecas
    for peca in (cupula, cone):
        assert not peca.com_pino and not peca.precisa_de_suporte
        embaixo = peca.malha.vertices[:, 2] < peca.malha.bounds[0, 2] + 0.01
        # O fundo é a peça inteira: nada mais largo acima dele.
        assert np.ptp(peca.malha.vertices[embaixo][:, 0]) == pytest.approx(peca.malha.extents[0], abs=0.02)
    assert cupula.malha.extents[2] == pytest.approx(5 + PROFUNDIDADE_MM, abs=0.02)


def test_a_peca_entra_no_corpo_sem_encostar(modelo, regioes):
    """Com a folga, a peça e o corpo não ocupam o mesmo lugar em ponto nenhum, nem no pino."""
    corpo = separacao._solido(trimesh.load_mesh(modelo))
    faces = np.nonzero(regioes.regiao_da_face == regiao_em(regioes, CUPULA))[0]
    depois, peca, com_pino, _, _ = separacao._cortar(corpo, regioes.vertices[regioes.faces[faces]], True, PROFUNDIDADE_MM)
    assert com_pino
    assert (depois ^ peca).volume() == pytest.approx(0.0, abs=1e-6)
    # O furo do pino é mais fundo que o rebaixo.
    assert depois.volume() < (corpo - peca).volume() - 5


def test_vizinhas_da_mesma_cor_saem_juntas(regioes):
    disco, lado = regiao_em(regioes, DISCO), regiao_em(regioes, LADO_DO_DISCO)
    (peca,) = separar(regioes, {disco: ROSA, lado: ROSA}, pino=False).pecas
    # Com a lateral junto, a peça leva o relevo inteiro e mais o rebaixo.
    assert peca.malha.extents[2] == pytest.approx(1.5 + PROFUNDIDADE_MM, abs=0.02)
    # Sozinha, a lateral é um anel em volta do disco.
    with pytest.raises(ErroDeSeparacao, match="dá a volta"):
        separar(regioes, {disco: ROSA, lado: PRETO})


def test_regioes_distantes_da_mesma_cor_viram_duas_pecas(regioes):
    pecas = separar(regioes, {regiao_em(regioes, CUPULA): PRETO, regiao_em(regioes, CONE): PRETO}).pecas
    assert [p.cor for p in pecas] == [PRETO, PRETO]


def test_regiao_em_rebaixo_sai_do_fundo_do_rebaixo(regioes):
    (peca,) = separar(regioes, {regiao_em(regioes, GRAVADO): PRETO}, pino=False).pecas
    # Os eixos no plano da peça são livres: vale o tamanho, em qualquer ordem.
    assert sorted(peca.malha.extents) == pytest.approx([PROFUNDIDADE_MM, 6 - 2 * FOLGA_MM, 10 - 2 * FOLGA_MM], abs=0.02)


@pytest.mark.parametrize("pintura", [{}, {BASE: PRETO}, {-1: PRETO}, {999: PRETO}])
def test_sem_regiao_valida_nao_ha_o_que_cortar(regioes, pintura):
    with pytest.raises(ErroDeSeparacao, match="ao menos uma região"):
        separar(regioes, pintura)


def test_regiao_que_da_a_volta_na_peca_e_recusada(tmp_path):
    """Uma bola presa por um pescoço fino: não há direção por onde ela entre em um rebaixo."""
    bola = manifold3d.Manifold.cube((30, 30, 10)) + manifold3d.Manifold.sphere(8, 64).translate((15, 15, 16))
    lidas = ler_regioes(_exportar(bola, tmp_path / "bola.stl"))
    with pytest.raises(ErroDeSeparacao, match="dá a volta"):
        separar(lidas, {regiao_em(lidas, (15, 15, 24)): PRETO})


# ---------- No projeto ----------


@pytest.fixture
def config(tmp_path):
    return Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path / "projetos")


def projeto_com_o_modelo(config, modelo, com_outra=False) -> Projeto:
    projeto = Projeto.criar(config.pasta_projetos)
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("# nada\n", encoding="utf-8")
    partes = [gravar_parte(modelo, pasta, OpcoesDoModelo("boneco"))]
    if com_outra:
        trimesh.creation.box(extents=(20, 20, 5)).export(pasta / "_base.stl")
        partes.append(gravar_parte(pasta / "_base.stl", pasta, OpcoesDoModelo("base")))
        (pasta / "_base.stl").unlink()
    projeto.estado["versao_atual"] = projeto.registrar_versao(pasta, None, partes, "teste")
    projeto.salvar_estado()
    return projeto


def test_separar_cria_uma_versao_com_o_corpo_e_as_pecas(config, perfil, modelo, regioes):
    projeto = projeto_com_o_modelo(config, modelo, com_outra=True)
    agente = Orquestrador(config, perfil, SemIA(), projeto)
    pintura = {regiao_em(regioes, CUPULA): VERMELHO, regiao_em(regioes, CONE): PRETO, regiao_em(regioes, DISCO): VERMELHO}
    resultado = agente.separar_cores("boneco", separar(regioes, pintura, pino=False))

    assert agente.versao_atual == "v002"
    assert [p["nome"] for p in resultado["pecas"]] == ["boneco_vermelho", "boneco_vermelho_2", "boneco_preto"]
    medidas = projeto.medidas_por_parte("v002")
    assert list(medidas) == ["boneco", "base", "boneco_vermelho", "boneco_vermelho_2", "boneco_preto"]
    assert all(m["importada"] for m in medidas.values())
    assert medidas["boneco_preto"]["cor"] == PRETO and "cor" not in medidas["boneco"]
    assert all(abs(m["caixa"]["z"][0]) < 0.01 for m in medidas.values())
    # A ponta do cone deixa triângulos sem área no STL: eles não podem contar como corpos soltos nem como buracos.
    assert all(m["solidos"] == 1 for m in medidas.values())
    assert all(trimesh.load_mesh(projeto.pasta_versao("v002") / m["stl"]).is_watertight for m in medidas.values())
    assert medidas["boneco_preto"]["corpos_fechados"] and medidas["boneco_preto"]["volume_mm3"] == pytest.approx(511.3, abs=0.5)
    assert medidas["boneco"]["volume_mm3"] < projeto.medidas_por_parte("v001")["boneco"]["volume_mm3"]
    assert (projeto.pasta_versao("v002") / "base.stl").is_file()
    # A versão antiga continua com o modelo inteiro.
    assert trimesh.load_mesh(projeto.pasta_versao("v001") / "boneco.stl").volume == pytest.approx(trimesh.load_mesh(modelo).volume, rel=1e-4)

    arquivo = {i.parte: i.arquivo for i in projeto.layout("v002", perfil).itens}
    assert arquivo["boneco"] == arquivo["base"] == 1
    assert arquivo["boneco_vermelho"] == arquivo["boneco_vermelho_2"] != 1
    assert arquivo["boneco_preto"] not in (1, arquivo["boneco_vermelho"])
    assert "separou a parte boneco" in agente.mensagens[-1]["content"]
    assert agente.resultado_verificacao is not None and not agente.resultado_verificacao["bloqueios"]


def test_a_cor_do_corpo_continua(config, perfil, modelo, regioes):
    projeto = projeto_com_o_modelo(config, modelo)
    medidas = projeto.medidas("v001")
    medidas[0]["cor"] = "#FBC02D"
    (projeto.pasta_versao("v001") / "medidas.json").write_text(__import__("json").dumps(medidas), encoding="utf-8")
    Orquestrador(config, perfil, SemIA(), projeto).separar_cores("boneco", separar(regioes, {regiao_em(regioes, CONE): PRETO}))
    assert projeto.medidas_por_parte("v002")["boneco"]["cor"] == "#FBC02D"


# ---------- Na tela ----------


class ClienteFalso:
    def __init__(self, _config=None):
        pass

    def verificar(self):
        return None


@pytest.fixture
def janela(qtbot, perfil, config, modelo, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    janela = JanelaPrincipal(config, perfil, ClienteFalso, verificar_endpoint=False)
    qtbot.addWidget(janela)
    janela.abrir_projeto(projeto_com_o_modelo(config, modelo))
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    yield janela
    janela.close()


def no_projeto(ponto):
    """Ao importar, o modelo vai para o centro: o bloco de 60 × 40 mm anda 30 e 20 mm."""
    return (ponto[0] - 30, ponto[1] - 20, ponto[2])


def abrir_a_pintura(qtbot, janela):
    painel = janela.painel_3d
    painel.selecionar(painel.mesa.itens[0].id)
    painel.pedir_separacao()
    qtbot.waitUntil(lambda: painel._separacao is not None and not janela.ocupado, timeout=60000)
    return painel, painel._separacao["regioes"]


def oferece_separar(painel) -> bool:
    menu = QMenu()
    painel._preencher_menu(menu)
    return next(a for a in menu.actions() if a.text() == "Separar em peças por cor…").isEnabled()


def test_menu_so_oferece_para_modelo_importado_de_um_corpo(janela):
    painel = janela.painel_3d
    assert not oferece_separar(painel)
    painel.selecionar(painel.mesa.itens[0].id)
    assert oferece_separar(painel)
    painel.medidas["boneco"]["importada"] = False
    assert not oferece_separar(painel)
    painel.medidas["boneco"] |= {"importada": True, "corpos": [{"nome": "a", "stl": "a.stl"}]}
    assert not oferece_separar(painel)


def test_pintar_escolher_outra_cor_e_tirar(qtbot, janela):
    painel, regioes = abrir_a_pintura(qtbot, janela)
    barra = painel.barra_separacao
    assert not barra.isHidden() and not barra.botao_separar.isEnabled()
    assert not painel.editavel and not painel.botao_mais.isEnabled()
    assert f"Achei {regioes.separaveis} regiões" in barra.rotulo.text()

    cupula = regiao_em(regioes, no_projeto(CUPULA))
    barra.botao_cor.definir(VERMELHO)
    painel.pintar_regiao(cupula)
    assert painel._separacao["pintura"] == {cupula: VERMELHO}
    assert barra.botao_separar.isEnabled()
    barra.botao_cor.definir(PRETO)
    painel.pintar_regiao(cupula)
    assert painel._separacao["pintura"] == {cupula: PRETO}
    painel.pintar_regiao(cupula)
    assert painel._separacao["pintura"] == {}
    assert not barra.botao_separar.isEnabled()


def test_a_base_e_as_regioes_pequenas_nao_pintam(qtbot, janela):
    painel, _ = abrir_a_pintura(qtbot, janela)
    painel.pintar_regiao(BASE)
    assert "corpo da peça" in painel.rotulo_aviso.text()
    painel.pintar_regiao(-1)
    assert "pequena demais" in painel.rotulo_aviso.text()
    assert painel._separacao["pintura"] == {}


def test_voltar_a_mesa_sem_cortar(qtbot, janela):
    painel, regioes = abrir_a_pintura(qtbot, janela)
    painel.pintar_regiao(regiao_em(regioes, no_projeto(CONE)))
    painel.barra_separacao.botao_fechar.click()
    assert painel._separacao is None and painel.barra_separacao.isHidden()
    assert painel.editavel
    assert janela.projeto.versoes() == ["v001"]


def test_separar_pela_tela_poe_as_pecas_na_mesa(qtbot, janela):
    painel, regioes = abrir_a_pintura(qtbot, janela)
    barra = painel.barra_separacao
    barra.botao_cor.definir(VERMELHO)
    painel.pintar_regiao(regiao_em(regioes, no_projeto(CUPULA)))
    barra.botao_cor.definir(PRETO)
    painel.pintar_regiao(regiao_em(regioes, no_projeto(CONE)))
    barra.botao_separar.click()
    qtbot.waitUntil(lambda: not janela.ocupado and janela.versao_vista == "v002", timeout=60000)

    assert painel._separacao is None and barra.isHidden()
    assert {i.parte for i in painel.mesa.itens} == {"boneco", "boneco_vermelho", "boneco_preto"}
    assert len(painel.mesa.arquivos()) == 3
    assert painel.editavel
    textos = [rotulo.text() for rotulo in janela.painel_chat.conteudo.findChildren(QLabel)]
    assert any("Cores separadas em boneco: 2 peças novas (vermelho, preto)" in t and "Marque Suporte" in t for t in textos), textos


def test_se_o_corte_falha_a_pintura_continua_aberta(qtbot, janela, monkeypatch):
    painel, regioes = abrir_a_pintura(qtbot, janela)
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: avisos.append(a[2]))

    def falhar(*_argumentos):
        raise ErroDeSeparacao("Uma das regiões dá a volta na peça.")

    monkeypatch.setattr("forja3d.interface.trabalhador.separar", falhar)
    painel.pintar_regiao(regiao_em(regioes, no_projeto(CONE)))
    painel.barra_separacao.botao_separar.click()
    qtbot.waitUntil(lambda: bool(avisos) and not janela.ocupado, timeout=60000)
    assert "dá a volta" in avisos[0]
    assert painel._separacao is not None and painel.barra_separacao.isEnabled()
    assert janela.projeto.versoes() == ["v001"]


def test_trocar_de_peca_fecha_a_pintura(qtbot, janela):
    painel, _ = abrir_a_pintura(qtbot, janela)
    janela._nova_peca()
    assert painel._separacao is None and painel.barra_separacao.isHidden()
