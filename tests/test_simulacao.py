"""A simulação da impressão: lê o G-code preparado e percorre com uma barra."""
import json
import os
from pathlib import Path

import numpy as np
import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.fatiamento.orca import ParametrosFatiamento, fatiar_layout, localizar_orca
from forja3d.fatiamento.simulacao import ler_percurso
from forja3d.interface.barra_simulacao import RESOLUCAO, VELOCIDADES, BarraDeSimulacao
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.interface.painel_3d import Painel3D
from forja3d.layout_mesa import ItemMesa, LayoutMesa
from forja3d.projetos.armazenamento import Projeto

BRANCO, VERMELHO = "#F5F5F5", "#D32F2F"
GCODE = """\
G28
G90
M83
G1 X-2 Y20 Z0.28 F6000
G1 Y120 E10 F600
;LAYER_CHANGE
;Z:0.2
;HEIGHT:0.2
;TYPE:Outer wall
G1 Z.2 F600
G1 X50 Y50 F6000
G1 X110 Y50 E2 F600
G1 E-1 F1200
G1 X50 Y50 F6000
G1 E1 F1200
;TYPE:Sparse infill
G1 X50 Y110 E2 F600
; FORJA3D_TROCA_INICIO 1/1 camada=2 z=0.4 para=#D32F2F
M400
G91
G1 Z5 F300
G90
G1 X-2.1 Y20 F5000
M400
M25
M83
G1 E45 F200
G1 Z0.8 F3000
; FORJA3D_TROCA_FIM
;LAYER_CHANGE
;Z:0.4
;HEIGHT:0.2
;TYPE:Outer wall
G1 Z.4 F600
G1 X50 Y50 F6000
G1 X110 Y50 E2 F600
M104 S0
"""


def criar_gcode(pasta: Path, texto: str = GCODE) -> Path:
    caminho = pasta / "p.gcode"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


@pytest.fixture
def percurso(tmp_path):
    return ler_percurso(criar_gcode(tmp_path), BRANCO)


# ---------- Leitura ----------


def test_so_desenha_o_que_extruda(percurso):
    # A purga inicial, duas linhas na primeira camada e uma na segunda. Viagem e retração não saem plástico.
    assert len(percurso.tempo_dos_trechos) == 4
    assert percurso.pontos.shape == (8, 3)
    assert percurso.pontos[2].tolist() == pytest.approx([50.0, 50.0, 0.2], abs=1e-5)
    assert percurso.pontos[3].tolist() == pytest.approx([110.0, 50.0, 0.2], abs=1e-5)


def test_o_tempo_so_anda_para_frente(percurso):
    assert (np.diff(percurso.tempo_dos_trechos) > 0).all()
    assert (np.diff(percurso.tempo_das_posicoes) >= 0).all()
    assert percurso.tempo_total >= percurso.tempo_dos_trechos[-1]


def test_tempo_da_distancia_e_da_velocidade(tmp_path):
    # 60 mm a 600 mm/min são 6 segundos.
    percurso = ler_percurso(criar_gcode(tmp_path, ";LAYER_CHANGE\n;Z:0.2\nM83\nG1 X0 Y0 F600\nG1 X60 E1\n"))
    assert percurso.tempo_total == pytest.approx(6.0)


def test_ajusta_o_tempo_a_estimativa_do_fatiador(tmp_path):
    sem = ler_percurso(criar_gcode(tmp_path))
    com = ler_percurso(criar_gcode(tmp_path), None, 3600)
    assert com.tempo_total == pytest.approx(3600)
    assert com.pausas[0].tempo / com.tempo_total == pytest.approx(sem.pausas[0].tempo / sem.tempo_total)


def test_camadas(percurso):
    assert percurso.quantidade_de_camadas == 2
    assert percurso.camada_em(0.0) == (0, 0.0), "antes da primeira camada, é a preparação"
    assert percurso.camada_em(percurso.tempo_total) == (2, pytest.approx(0.4))


def test_barra_mostra_mais_trechos_conforme_o_tempo(percurso):
    assert percurso.trechos_ate(0.0) == 0
    assert percurso.trechos_ate(percurso.tempo_total) == 4
    quantidades = [percurso.trechos_ate(t) for t in np.linspace(0, percurso.tempo_total, 50)]
    assert quantidades == sorted(quantidades)


def test_bico_acompanha_inclusive_nas_viagens(percurso):
    assert percurso.bico_em(percurso.tempo_total) == pytest.approx((110.0, 50.0, 0.4), abs=1e-5)
    # Na pausa, a cabeça está no canto de troca, e não em cima da peça.
    assert percurso.bico_em(percurso.pausas[0].tempo)[:2] == pytest.approx((-2.1, 20.0), abs=1e-5)


def test_cor_muda_na_pausa_e_nao_antes(percurso):
    pausa = percurso.pausas[0]
    assert (pausa.numero, pausa.total, pausa.camada, pausa.cor) == (1, 1, 2, VERMELHO)
    # O que veio antes da pausa continua branco: tons diferentes por tipo, mas sempre claros.
    assert percurso.cores[0].min() > 150
    # O que veio depois é vermelho.
    r, g, b = percurso.cores[-1]
    assert r > 150
    assert g < 100
    assert b < 100


def test_filamento_preto_continua_visivel_no_fundo_escuro(tmp_path):
    percurso = ler_percurso(criar_gcode(tmp_path), "#1A1A1A")
    assert percurso.cores[0].max() >= 90


def test_filamento_preto_mantem_os_tons_de_cada_tipo(tmp_path):
    """Sem isso, as paredes de um furo ficam da cor da superfície em volta, e o furo some na imagem."""
    percurso = ler_percurso(criar_gcode(tmp_path), "#1A1A1A")
    parede, preenchimento = percurso.cores[1], percurso.cores[2]
    assert parede.max() > preenchimento.max()


def test_sem_cor_usa_a_cor_das_pecas(tmp_path):
    percurso = ler_percurso(criar_gcode(tmp_path))
    assert percurso.cores[0][0] > percurso.cores[0][2], "laranja"


def test_gcode_sem_extrusao_nao_tem_o_que_simular(tmp_path):
    with pytest.raises(ValueError, match="nenhuma extrusão"):
        ler_percurso(criar_gcode(tmp_path, "G28\nG1 X10 Y10 F600\n"))


def test_gcode_com_movimento_relativo(tmp_path):
    percurso = ler_percurso(criar_gcode(tmp_path, "M83\nG1 X10 Y10 Z1 F600\nG91\nG1 X5 E1\nG90\nM83\nG1 X30 E1\n"))
    assert percurso.pontos[1].tolist() == pytest.approx([15.0, 10.0, 1.0])
    assert percurso.pontos[3].tolist() == pytest.approx([30.0, 10.0, 1.0])


# ---------- Barra ----------


@pytest.fixture
def barra(qtbot, percurso):
    widget = BarraDeSimulacao()
    qtbot.addWidget(widget)
    widget.iniciar(percurso)
    yield widget
    widget.encerrar()


def test_barra_comeca_do_inicio(barra):
    assert barra.tempo == 0.0
    assert barra.barra.value() == 0
    assert "preparando" in barra.rotulo.text()


def test_arrastar_leva_para_o_momento_e_avisa(barra, percurso):
    recebidos = []
    barra.tempo_mudou.connect(recebidos.append)
    barra.barra.setValue(RESOLUCAO // 2)
    assert barra.tempo == pytest.approx(percurso.tempo_total / 2)
    assert recebidos == [pytest.approx(percurso.tempo_total / 2)]
    assert "50 %" in barra.rotulo.text()


def test_texto_mostra_camada_e_tempo(barra):
    barra.barra.setValue(RESOLUCAO)
    texto = barra.rotulo.text()
    assert "100 %" in texto
    assert "camada 2 de 2" in texto
    assert "0,40 mm" in texto


def test_reproduzir_anda_e_para_no_fim(qtbot, barra, percurso):
    # Sem a pausa no caminho: começa depois dela.
    barra.barra.setValue(RESOLUCAO - 10)
    barra.combo_velocidade.setCurrentIndex(len(VELOCIDADES) - 1)
    barra.reproduzir()
    assert barra.tocando
    assert barra.botao_tocar.text() == "Pausar"
    qtbot.waitUntil(lambda: not barra.tocando, timeout=5000)
    assert barra.tempo == pytest.approx(percurso.tempo_total)
    assert barra.botao_tocar.text() == "Reproduzir"


def test_reproduzir_no_fim_recomeca(barra):
    barra.barra.setValue(RESOLUCAO)
    barra.reproduzir()
    assert barra.tempo == 0.0
    barra.pausar()


def test_simulacao_para_na_troca_como_a_impressora(qtbot, barra, percurso):
    pausa = percurso.pausas[0]
    barra.barra.setValue(round((pausa.tempo * 0.999) / percurso.tempo_total * RESOLUCAO))
    barra.reproduzir()
    qtbot.waitUntil(lambda: not barra.tocando, timeout=5000)
    assert barra.tempo == pytest.approx(pausa.tempo)
    assert "Troca 1 de 1" in barra.rotulo.text()
    assert "vermelho" in barra.rotulo.text()
    # Reproduzir de novo segue dali, sem parar na mesma troca.
    barra.reproduzir()
    qtbot.waitUntil(lambda: barra.tempo > pausa.tempo, timeout=5000)
    barra.pausar()


def test_arrastar_pausa_a_reproducao(barra):
    barra.reproduzir()
    barra.barra.sliderPressed.emit()
    assert not barra.tocando


def test_proxima_troca_pula_para_a_pausa(barra, percurso):
    barra.ir_para_a_proxima_troca()
    assert barra.tempo == pytest.approx(percurso.pausas[0].tempo)
    assert "Troca 1 de 1" in barra.rotulo.text()


def test_sem_troca_o_botao_nem_aparece(qtbot, tmp_path):
    widget = BarraDeSimulacao()
    qtbot.addWidget(widget)
    widget.show()
    widget.iniciar(ler_percurso(criar_gcode(tmp_path, ";LAYER_CHANGE\n;Z:0.2\nM83\nG1 X10 Y10 F600\nG1 X20 E1\n")))
    assert not widget.botao_troca.isVisible()
    widget.encerrar()


# ---------- Na mesa ----------


@pytest.fixture
def painel(qtbot, perfil):
    painel = Painel3D(perfil)
    qtbot.addWidget(painel)
    painel.show()
    painel.mesa = LayoutMesa({"x": (30.0, 30.0)}, perfil, [ItemMesa("1", "x", 60.0, 60.0)])
    painel.editavel = True
    painel._atualizar_barra()
    yield painel
    painel.encerrar_simulacao()


def test_simular_fecha_a_mesa_e_voltar_abre(painel, percurso):
    painel.selecionar("1")
    assert painel.botoes["girar"].isEnabled()
    painel.iniciar_simulacao(percurso)
    assert painel.simulando
    assert painel.barra_simulacao.isVisible()
    assert painel.barra_simulacao.tocando, "já começa andando"
    assert not painel.editavel
    assert not painel.botoes["girar"].isEnabled()
    painel.barra_simulacao.botao_fechar.click()
    assert not painel.simulando
    assert not painel.barra_simulacao.isVisible()
    assert not painel.barra_simulacao.tocando
    assert painel.editavel


def test_pedido_de_editar_durante_a_simulacao_vale_depois(painel, percurso):
    painel.iniciar_simulacao(percurso)
    painel.definir_editavel(False)
    assert not painel.editavel
    painel.encerrar_simulacao()
    assert not painel.editavel


def test_trocar_de_peca_encerra_a_simulacao(painel, percurso):
    painel.iniciar_simulacao(percurso)
    painel.limpar()
    assert not painel.simulando
    assert not painel.barra_simulacao.isVisible()


def test_atalhos_nao_mexem_na_mesa_durante_a_simulacao(painel, percurso):
    painel.selecionar("1")
    painel.iniciar_simulacao(percurso)
    antes = painel.mesa.para_dados()
    painel.girar()
    painel.deletar()
    painel.duplicar()
    assert painel.mesa.para_dados() == antes


# ---------- Na janela ----------


class ClienteFalso:
    modelo = "falso"

    def __init__(self, _config=None):
        pass

    def verificar(self):
        pass


def _caixa(pasta: Path, nome: str) -> dict:
    malha = trimesh.creation.box(extents=(30, 30, 2))
    malha.apply_translation((0, 0, 1))
    malha.export(pasta / f"{nome}.stl")
    return {
        "nome": nome, "importada": True, "valido": True, "solidos": 1, "volume_mm3": 1800.0, "tamanho": [30.0, 30.0, 2.0], "stl": f"{nome}.stl",
        "furos": [], "caixa": {"x": [-15.0, 15.0], "y": [-15.0, 15.0], "z": [0.0, 2.0]},
    }


@pytest.fixture
def janela(qtbot, perfil, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    config = Configuracao(ia=ConfigIA(endpoint="http://x/v1", api_key="k", modelo="m"), caminho_orca=localizar_orca() or tmp_path / "orca.exe", pasta_projetos=tmp_path / "projetos")
    janela = JanelaPrincipal(config, perfil, ClienteFalso, verificar_endpoint=False)
    qtbot.addWidget(janela)
    janela.show()
    yield janela
    janela.close()


def _abrir_com_gcode(janela, tmp_path, quantidade_de_arquivos: int = 1) -> Projeto:
    """Uma peça já preparada, com o G-code de teste no lugar do fatiado."""
    projeto = Projeto.criar(tmp_path / "projetos", "peca")
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("# nada\n", encoding="utf-8")
    versao = projeto.registrar_versao(pasta, None, [_caixa(pasta, "x")], "teste")
    projeto.estado["versao_atual"] = versao
    projeto.salvar_estado()
    janela.abrir_projeto(projeto)
    return projeto


def _preparar(janela, projeto, qtbot, gcodes: dict[int, str]) -> None:
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    saida = projeto.pasta_impressao(janela.versao_vista, "PLA", "padrao")
    saida.mkdir(parents=True)
    validacao = {"aprovado": True, "bloqueios": [], "alertas": [], "informacoes": [], "camadas": 2, "z_maximo_mm": 0.4, "bico_c": 220, "mesa_c": 55, "tempo": "1h 0m 0s"}
    arquivos = []
    for numero, texto in gcodes.items():
        nome = "peca.gcode" if len(gcodes) == 1 else f"arquivo_{numero}.gcode"
        (saida / nome).write_text(texto, encoding="utf-8")
        arquivos.append({"numero": numero, "gcode": nome, "itens": ["x"], "validacao": validacao, "cores": {"cor_inicial": BRANCO, "trocas": [], "gramas_por_cor": []}})
    resumo = {"parametros": {"material": "PLA", "qualidade": "padrao"}, "gcode": arquivos[0]["gcode"], "layout": janela.painel_3d.mesa.assinatura(), "arquivos": arquivos, "validacao": validacao}
    (saida / "resumo.json").write_text(json.dumps(resumo), encoding="utf-8")
    janela.pasta_impressao = saida
    janela._atualizar_botoes()


def test_botao_de_simular_so_libera_com_gcode_preparado(qtbot, janela, tmp_path):
    projeto = _abrir_com_gcode(janela, tmp_path)
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    assert not janela.botao_simular.isEnabled()
    _preparar(janela, projeto, qtbot, {1: GCODE})
    assert janela.botao_simular.isEnabled()


def test_simular_le_o_gcode_e_mostra_a_barra(qtbot, janela, tmp_path):
    projeto = _abrir_com_gcode(janela, tmp_path)
    _preparar(janela, projeto, qtbot, {1: GCODE})
    janela.botao_simular.click()
    qtbot.waitUntil(lambda: janela.painel_3d.simulando, timeout=30000)
    assert not janela.ocupado
    percurso = janela.painel_3d._percurso
    assert percurso.tempo_total == pytest.approx(3600), "o tempo é o da estimativa"
    assert len(percurso.pausas) == 1


def test_simulacao_usa_o_arquivo_que_esta_na_tela(qtbot, janela, tmp_path):
    projeto = _abrir_com_gcode(janela, tmp_path)
    segundo = ";LAYER_CHANGE\n;Z:0.2\nM83\nG1 X10 Y10 F600\nG1 X20 E1\nG1 X30 E1\nG1 X40 E1\n"
    _preparar(janela, projeto, qtbot, {1: GCODE, 2: segundo})
    janela.painel_3d.arquivo_ativo = 2
    janela.botao_simular.click()
    qtbot.waitUntil(lambda: janela.painel_3d.simulando, timeout=30000)
    assert len(janela.painel_3d._percurso.tempo_dos_trechos) == 3


def test_app_ocupado_encerra_a_simulacao(qtbot, janela, tmp_path):
    projeto = _abrir_com_gcode(janela, tmp_path)
    _preparar(janela, projeto, qtbot, {1: GCODE})
    janela.botao_simular.click()
    qtbot.waitUntil(lambda: janela.painel_3d.simulando, timeout=30000)
    janela._definir_ocupado(True)
    assert not janela.painel_3d.simulando
    janela._definir_ocupado(False)


def test_gcode_que_sumiu_avisa(qtbot, janela, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, texto: avisos.append(texto))
    projeto = _abrir_com_gcode(janela, tmp_path)
    _preparar(janela, projeto, qtbot, {1: GCODE})
    (janela.pasta_impressao / "peca.gcode").unlink()
    janela._simular()
    qtbot.waitUntil(lambda: bool(avisos), timeout=30000)
    assert "Preparar de novo" in avisos[0]
    assert not janela.painel_3d.simulando


# ---------- O G-code de verdade ----------


@pytest.mark.orca
@pytest.mark.skipif(localizar_orca() is None, reason="OrcaSlicer não encontrado")
def test_simula_o_que_o_orcaslicer_gerou(tmp_path, perfil):
    malha = trimesh.creation.box(extents=(30, 30, 4))
    malha.apply_translation((0, 0, 2))
    malha.export(tmp_path / "a.stl")
    mesa = LayoutMesa({"a": (30.0, 30.0)}, perfil, [ItemMesa("1", "a", 110.0, 110.0, 1, 0, {"": BRANCO})])
    mesa.definir_alturas(1, [{"z": 2.0, "cor": VERMELHO}])
    parametros = ParametrosFatiamento("PLA", qualidade="rascunho", paredes=2, preenchimento_pct=10)
    resumo = fatiar_layout(mesa, {"a": tmp_path / "a.stl"}, parametros, perfil, tmp_path / "saida")
    assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
    percurso = ler_percurso(tmp_path / "saida" / "peca.gcode", BRANCO, resumo["validacao"]["tempo_s"])
    assert percurso.tempo_total == pytest.approx(resumo["validacao"]["tempo_s"])
    assert percurso.quantidade_de_camadas == resumo["validacao"]["camadas"]
    assert len(percurso.pausas) == 1
    # A peça é o que vai para a mesa: fora da purga, nada sai da posição dela.
    depois_da_purga = percurso.pontos[percurso.pontos[:, 2] > 0.3]
    assert depois_da_purga[:, 2].max() == pytest.approx(4.0, abs=0.05)
    camada = percurso.pausas[0].camada
    assert percurso.z_das_camadas[camada - 1] >= 2.0
