"""Cores por troca manual de filamento: a técnica do tutorial, sem abrir o G-code no Bloco de Notas."""
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtWidgets import QMessageBox

from apoio_conceitos import texto_ia
from forja3d.agente.orquestrador import Orquestrador
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.fatiamento.cores import (
    CAMADA_INICIAL_MM,
    ErroDeCor,
    bloco_de_troca,
    cor_pelo_nome_do_arquivo,
    cor_valida,
    cores_do_item,
    estimar_trocas,
    fundo_do_item,
    inserir_pausas_nas_trocas,
    inserir_pausas_por_altura,
    montar_stls_por_cor,
    nome_da_cor,
    plano_do_arquivo,
    posicao_da_torre,
    proxima_cor,
    roteiro_da_preparacao,
    roteiro_das_trocas,
)
from forja3d.fatiamento.orca import ErroFatiamento, ParametrosFatiamento, fatiar_layout, localizar_orca
from forja3d.fatiamento.validador_gcode import MARCA_FIM, MARCA_INICIO, validar_gcode
from forja3d.importacao.malha import ModeloInvalido
from forja3d.importacao.preparo import ModeloLido, OpcoesDoModelo, gravar_parte, ler_em_processo, principal
from forja3d.interface.dialogo_cores import BotaoDeCor, DialogoAlturas, EditorDeCores
from forja3d.interface.dialogo_importar import DialogoImportar
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.layout_mesa import ItemMesa, LayoutMesa
from forja3d.projetos.armazenamento import Projeto
from forja3d.projetos.compartilhar import ArquivoInvalido, importar_projeto, salvar_projeto
from forja3d.verificacao.verificador import verificar_peca

PRETO, BRANCO, VERMELHO, AMARELO = "#1A1A1A", "#F5F5F5", "#D32F2F", "#FBC02D"
TEM_ORCA = localizar_orca() is not None
LEVE = {"qualidade": "rascunho", "paredes": 2, "preenchimento_pct": 10}


def caixa(extensao, centro) -> trimesh.Trimesh:
    malha = trimesh.creation.box(extents=extensao)
    malha.apply_translation(centro)
    return malha


def layout(perfil, itens, tamanhos=None) -> LayoutMesa:
    return LayoutMesa(tamanhos or {i.parte: (30.0, 30.0) for i in itens}, perfil, itens)


# ---------- Paleta ----------


def test_cor_valida():
    assert cor_valida("#d32f2f") == "#D32F2F"
    assert cor_valida("vermelho") is None
    assert cor_valida("#12") is None
    assert cor_valida(None) is None


@pytest.mark.parametrize(("cor", "nome"), [(PRETO, "preto"), ("#000000", "preto"), ("#FFFFFF", "branco"), ("#FF0000", "vermelho"), (None, "qualquer cor")])
def test_nome_da_cor_e_o_da_mais_proxima(cor, nome):
    assert nome_da_cor(cor) == nome


@pytest.mark.parametrize(("arquivo", "nome"), [("black", "preto"), ("logo_RED", "vermelho"), ("peca-amarela", "amarelo"), ("white.stl_A", "branco")])
def test_cor_pelo_nome_do_arquivo(arquivo, nome):
    assert nome_da_cor(cor_pelo_nome_do_arquivo(arquivo)) == nome


def test_nome_sem_cor():
    assert cor_pelo_nome_do_arquivo("cranio_final") is None


def test_proxima_cor_nao_repete():
    assert proxima_cor([PRETO, BRANCO]) not in (PRETO, BRANCO)


# ---------- Mesa ----------


def test_cor_do_item_vale_sobre_a_que_veio_na_importacao():
    medida = {"corpos": [{"nome": "a", "cor": PRETO}, {"nome": "b", "cor": BRANCO}], "fundo": "a"}
    item = ItemMesa("1", "x", 0, 0, cores={"b": VERMELHO})
    assert cores_do_item(item, medida) == {"a": PRETO, "b": VERMELHO}
    assert fundo_do_item(item, medida) == "a"
    item.fundo = ""
    assert fundo_do_item(item, medida) is None
    assert cores_do_item(ItemMesa("2", "y", 0, 0), {"cor": AMARELO}) == {"": AMARELO}
    assert cores_do_item(ItemMesa("3", "z", 0, 0), None) == {"": None}


def test_cor_faz_parte_do_que_invalida_o_gcode(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0)])
    antes = mesa.assinatura()
    mesa.definir_cores("1", {"": VERMELHO})
    com_cor = mesa.assinatura()
    assert com_cor != antes
    mesa.definir_alturas(1, [{"z": 3.0, "cor": PRETO}])
    assert mesa.assinatura() != com_cor


def test_peca_sem_cor_mantem_a_assinatura_e_os_dados_de_antes(perfil):
    """Um G-code preparado antes das cores não pode virar inválido sem ninguém mexer na mesa."""
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0)])
    assert mesa.assinatura() == hashlib.sha1(json.dumps([[1, "x", 60.0, 60.0, 0]]).encode()).hexdigest()
    assert set(mesa.para_dados()) == {"partes", "itens"}
    assert set(mesa.para_dados()["itens"][0]) == {"id", "parte", "x", "y", "arquivo", "giro"}


def test_cores_e_alturas_voltam_de_onde_foram_salvas(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0, 1, 0, {"": VERMELHO}, "corpo")])
    mesa.definir_alturas(1, [{"z": 5.0, "cor": BRANCO}, {"z": 2.0, "cor": PRETO}])
    dados = json.loads(json.dumps(mesa.para_dados()))
    outra = LayoutMesa.de_dados(dados, {"x": (30.0, 30.0)}, perfil)
    assert outra.itens[0].cores == {"": VERMELHO}
    assert outra.itens[0].fundo == "corpo"
    assert [t["z"] for t in outra.alturas[1]] == [2.0, 5.0], "da mais baixa para a mais alta"
    assert outra.assinatura() == mesa.assinatura()


def test_dados_estragados_de_cor_nao_entram(perfil):
    dados = {
        "partes": ["x"],
        "itens": [{"id": "1", "parte": "x", "x": 60, "y": 60, "cores": {"": "javascript:", "b": 3}, "fundo": 5}],
        "alturas": {"1": [{"z": "alto", "cor": PRETO}, {"z": 2, "cor": "azul"}], "lixo": [{"z": 1, "cor": PRETO}]},
    }
    mesa = LayoutMesa.de_dados(dados, {"x": (30.0, 30.0)}, perfil)
    assert mesa.itens[0].cores == {}
    assert mesa.itens[0].fundo is None
    assert mesa.alturas == {}


def test_copia_leva_a_cor_junto(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0, 1, 0, {"": VERMELHO})])
    copia = mesa.duplicar("1")
    assert copia.cores == {"": VERMELHO}
    copia.cores[""] = PRETO
    assert mesa.item("1").cores == {"": VERMELHO}, "cada cópia tem a sua"


def test_alturas_acompanham_a_numeracao_dos_arquivos(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0, 1), ItemMesa("2", "y", 120.0, 60.0, 2)])
    mesa.definir_alturas(2, [{"z": 3.0, "cor": BRANCO}])
    mesa.remover("1")
    assert mesa.alturas == {1: [{"z": 3.0, "cor": BRANCO}]}


def test_um_arquivo_por_cor(perfil):
    itens = [ItemMesa("1", "x", 60.0, 60.0, 1, 0, {"": PRETO}), ItemMesa("2", "y", 120.0, 60.0, 1, 0, {"": BRANCO}), ItemMesa("3", "z", 170.0, 60.0, 1, 0, {"": PRETO})]
    mesa = layout(perfil, itens)
    assert mesa.separar_por_cor(1, lambda i: i.cores[""]) == 1
    assert {i.id: i.arquivo for i in mesa.itens} == {"1": 1, "2": 2, "3": 1}


# ---------- Plano ----------


def test_plano_de_uma_cor_nao_tem_troca(perfil):
    plano = plano_do_arquivo(layout(perfil, [ItemMesa("1", "x", 60.0, 60.0, 1, 0, {"": PRETO})]), 1, {})
    assert not plano.tem_trocas


def test_plano_com_cores_e_altura_juntas_explica_o_problema(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0, 1, 0, {"": PRETO}), ItemMesa("2", "y", 120.0, 60.0, 1, 0, {"": BRANCO})])
    mesa.definir_alturas(1, [{"z": 3.0, "cor": VERMELHO}])
    with pytest.raises(ErroDeCor, match="Use só uma das duas"):
        plano_do_arquivo(mesa, 1, {})


def _medida_do_logo():
    corpos = [{"nome": "preto", "stl": "preto.stl", "cor": PRETO}, {"nome": "branco", "stl": "branco.stl", "cor": BRANCO}, {"nome": "amarelo", "stl": "amarelo.stl", "cor": AMARELO}, {"nome": "vermelho", "stl": "vermelho.stl", "cor": VERMELHO}]
    return {"tamanho": [80.0, 40.0, 1.0], "corpos": corpos, "fundo": "preto"}


def test_estimativa_do_tutorial_quatro_cores_tres_trocas(perfil):
    mesa = layout(perfil, [ItemMesa("1", "logo", 60.0, 60.0)])
    assert estimar_trocas(mesa, 1, {"logo": _medida_do_logo()}, 0.2) == 3


def test_estimativa_em_todas_as_camadas_e_uma_por_camada(perfil):
    mesa = layout(perfil, [ItemMesa("1", "logo", 60.0, 60.0, fundo="")])
    # 5 camadas, com 3 trocas em cada uma.
    assert estimar_trocas(mesa, 1, {"logo": _medida_do_logo()}, 0.2) == 15


def test_estimativa_de_pecas_altas_avisa_que_sao_muitas(perfil):
    mesa = layout(perfil, [ItemMesa("1", "alta", 60.0, 60.0, 1, 0, {"": PRETO}), ItemMesa("2", "baixa", 120.0, 60.0, 1, 0, {"": BRANCO})])
    medidas = {"alta": {"tamanho": [30, 30, 20]}, "baixa": {"tamanho": [30, 30, 10]}}
    assert estimar_trocas(mesa, 1, medidas, 0.2) == 50


def test_estimativa_por_altura_e_uma_por_troca(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0)])
    mesa.definir_alturas(1, [{"z": 3.0, "cor": BRANCO}, {"z": 5.0, "cor": PRETO}])
    assert estimar_trocas(mesa, 1, {}, 0.2) == 2


def test_torre_fica_longe_das_pecas(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 60.0, 60.0)], {"x": (80.0, 80.0)})
    x, y, largura, profundidade = posicao_da_torre(mesa, 1, 4, 0.2)
    assert not (x < 100 and y < 100), "a peça ocupa este canto"
    assert 0 <= x and x + largura <= mesa.largura
    assert 0 <= y and y + profundidade <= mesa.profundidade


def test_mesa_cheia_nao_tem_lugar_para_a_torre(perfil):
    mesa = layout(perfil, [ItemMesa("1", "x", 110.0, 110.0)], {"x": (210.0, 210.0)})
    assert posicao_da_torre(mesa, 1, 2, 0.2) is None


# ---------- A rotina de pausa ----------


def test_bloco_e_o_do_tutorial_em_peca_baixa():
    bloco = bloco_de_troca(1, 3, 1, 0.2, 0.6, 9000.0, VERMELHO, 250.0, com_torre=True)
    assert [b for b in bloco if not b.startswith(";")] == [
        "M400",
        "G91",
        "G1 Z5 F300",
        "G90",
        "G1 X-2.1 Y20 F5000",
        "M400",
        "M117 Troca 1/3: VERMELHO",
        "M25",
        "M83",
        "G1 E5 F200",
        "G1 Z0.28 F3000",
        "G1 Y145 E10 F1200",
        "G1 X-2.4 Y145 F5000",
        "G1 Y20 E10 F1200",
        "G1 Z0.6 F3000",
        "G1 F9000",
    ]
    assert bloco[0].startswith(MARCA_INICIO)
    assert bloco[-1] == MARCA_FIM


def test_em_peca_alta_a_cabeca_nao_desce_para_purgar():
    """Na altura de purga, a carcaça da cabeça passaria na altura da peça, e ela está logo ali."""
    bloco = bloco_de_troca(2, 2, 100, 20.0, 20.4, None, VERMELHO, 250.0, com_torre=False)
    assert "G1 Z0.28 F3000" not in bloco
    assert not any(re.match(r"G1 Y\d+ E", b) for b in bloco)
    assert "G1 E45 F200" in bloco
    assert "G1 Z20.4 F3000" in bloco


def test_sem_torre_a_purga_e_maior():
    com, sem = (bloco_de_troca(1, 1, 1, 0.2, 0.2, None, PRETO, 250.0, com_torre=c) for c in (True, False))
    assert "G1 Y145 E10 F1200" in com
    assert "G1 Y145 E20 F1200" in sem


def test_subida_nao_passa_do_limite_da_maquina():
    assert "G1 Z2 F300" in bloco_de_troca(1, 1, 1, 200.0, 248.0, None, PRETO, 250.0, com_torre=False)


def _gcode_de_duas_cores(pasta: Path) -> Path:
    linhas = [
        "; total filament used [g] = 3.0",
        "; filament used [g] = 1.50, 2.50",
        "M83",
        "G28",
        "M104 S220",
        "M140 S55",
        "; MANUAL_TOOL_CHANGE T0",
        ";LAYER_CHANGE",
        ";Z:0.2",
        ";HEIGHT:0.2",
        "G1 Z.2 F600",
        "G1 X50 Y50 F9000",
        "G1 X60 Y50 E1",
        "G1 F9000",
        "G1 X61 Y50 Z0.6",
        "M600",
        "M106 S0",
        "; MANUAL_TOOL_CHANGE T1",
        "; filament start gcode",
        "M900 K0",
        "G1 X70 Y50",
        "G1 Z.2",
        "G1 X80 Y50 E1",
        "M104 S0",
        "M140 S0",
    ]
    caminho = pasta / "p.gcode"
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def test_cada_m600_vira_a_rotina_e_a_altura_volta(tmp_path):
    gcode = _gcode_de_duas_cores(tmp_path)
    info = inserir_pausas_nas_trocas(gcode, [PRETO, BRANCO], 250.0)
    linhas = gcode.read_text(encoding="utf-8").splitlines()
    assert "M600" not in linhas
    assert "M900 K0" not in linhas
    assert linhas.count("M25") == 1
    assert info["cor_inicial"] == PRETO
    assert info["trocas"] == [{"numero": 1, "camada": 1, "z": 0.2, "de": PRETO, "para": BRANCO}]
    assert info["gramas_por_cor"] == [{"cor": PRETO, "g": 1.5}, {"cor": BRANCO, "g": 2.5}]
    # Antes de andar de volta para a peça, sobe para onde estava, e a velocidade continua a mesma.
    fim = linhas.index(MARCA_FIM)
    assert linhas[fim - 2 : fim] == ["G1 Z0.6 F3000", "G1 F9000"]


def test_troca_sem_destino_nao_passa(tmp_path):
    gcode = tmp_path / "p.gcode"
    gcode.write_text("; MANUAL_TOOL_CHANGE T0\nM600\nG1 X1\n", encoding="utf-8")
    with pytest.raises(ErroDeCor, match="sem dizer para qual cor"):
        inserir_pausas_nas_trocas(gcode, [PRETO, BRANCO], 250.0)


def _gcode_de_camadas(pasta: Path, quantidade: int = 10) -> Path:
    linhas = ["M83", "G28", "M104 S220", "M140 S55"]
    for numero in range(1, quantidade + 1):
        z = round(numero * 0.2, 2)
        linhas += [";LAYER_CHANGE", f";Z:{z:g}", ";HEIGHT:0.2", f"G1 Z{z:g} F600", "G1 X50 Y50 F9000", "G1 X60 Y50 E1"]
    linhas += ["M104 S0", "M140 S0"]
    caminho = pasta / "camadas.gcode"
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def test_troca_por_altura_entra_antes_da_camada_certa(tmp_path):
    gcode = _gcode_de_camadas(tmp_path)
    info = inserir_pausas_por_altura(gcode, [{"z": 1.0, "cor": BRANCO}], PRETO, 250.0)
    assert info["trocas"][0]["camada"] == 6, "a camada 6 é a primeira que começa em 1,0 mm"
    assert info["trocas"][0]["para"] == BRANCO
    linhas = gcode.read_text(encoding="utf-8").splitlines()
    fim = linhas.index(MARCA_FIM)
    assert linhas[fim + 1 : fim + 3] == [";LAYER_CHANGE", ";Z:1.2"]


def test_troca_em_altura_quebrada_vai_para_a_camada_seguinte(tmp_path):
    info = inserir_pausas_por_altura(_gcode_de_camadas(tmp_path), [{"z": 1.05, "cor": BRANCO}], PRETO, 250.0)
    assert info["trocas"][0]["camada"] == 7


def test_troca_acima_da_peca_explica(tmp_path):
    with pytest.raises(ErroDeCor, match="não chega à altura"):
        inserir_pausas_por_altura(_gcode_de_camadas(tmp_path), [{"z": 50.0, "cor": BRANCO}], PRETO, 250.0)


def test_troca_na_primeira_camada_so_muda_a_cor_inicial(tmp_path):
    gcode = _gcode_de_camadas(tmp_path)
    info = inserir_pausas_por_altura(gcode, [{"z": 0.0001, "cor": BRANCO}], PRETO, 250.0)
    assert info["cor_inicial"] == BRANCO
    assert info["trocas"] == []
    assert "M25" not in gcode.read_text(encoding="utf-8")


# ---------- Validador ----------


def _validar(gcode, perfil, esperadas):
    return validar_gcode(gcode, perfil, "PLA", 0.2, None, 0.0, None, 0.0, esperadas)


def _codigos(resultado):
    return {b["codigo"] for b in resultado["bloqueios"]}


def test_validador_aceita_a_rotina_e_ignora_a_purga_na_area_da_peca(tmp_path, perfil):
    gcode = _gcode_de_duas_cores(tmp_path)
    inserir_pausas_nas_trocas(gcode, [PRETO, BRANCO], 250.0)
    resultado = validar_gcode(gcode, perfil, "PLA", 0.2, None, 0.0, (40.0, 90.0, 40.0, 60.0), 0.0, 1)
    assert resultado["aprovado"], resultado["bloqueios"]
    assert resultado["trocas_de_filamento"] == 1


def test_sair_da_mesa_fora_da_rotina_continua_proibido(tmp_path, perfil):
    gcode = _gcode_de_camadas(tmp_path)
    gcode.write_text(gcode.read_text(encoding="utf-8").replace("G1 X50 Y50 F9000", "G1 X-2.1 Y50 F9000", 1), encoding="utf-8")
    assert "FORA_DA_MESA_X" in _codigos(_validar(gcode, perfil, 0))


def test_rotina_nao_autoriza_sair_alem_do_canto_da_purga(tmp_path, perfil):
    gcode = _gcode_de_duas_cores(tmp_path)
    inserir_pausas_nas_trocas(gcode, [PRETO, BRANCO], 250.0)
    gcode.write_text(gcode.read_text(encoding="utf-8").replace("G1 X-2.1 Y20 F5000", "G1 X-10 Y20 F5000"), encoding="utf-8")
    assert "FORA_DA_MESA_X" in _codigos(_validar(gcode, perfil, 1))


def test_m600_que_sobrou_bloqueia(tmp_path, perfil):
    assert "TROCA_SEM_ROTINA" in _codigos(_validar(_gcode_de_duas_cores(tmp_path), perfil, 0))


def test_pausa_fora_da_rotina_bloqueia(tmp_path, perfil):
    gcode = _gcode_de_camadas(tmp_path)
    gcode.write_text(gcode.read_text(encoding="utf-8").replace("G1 X60 Y50 E1", "M25\nG1 X60 Y50 E1", 1), encoding="utf-8")
    assert "PAUSA_FORA_DA_TROCA" in _codigos(_validar(gcode, perfil, 1))


def test_quantidade_de_pausas_precisa_bater_com_o_roteiro(tmp_path, perfil):
    gcode = _gcode_de_duas_cores(tmp_path)
    inserir_pausas_nas_trocas(gcode, [PRETO, BRANCO], 250.0)
    assert "TROCAS_DIVERGENTES" in _codigos(_validar(gcode, perfil, 2))


def test_voltar_mais_baixo_do_que_estava_bloqueia(tmp_path, perfil):
    """É o que aconteceria com a rotina do tutorial, sem ajuste, em uma peça alta."""
    gcode = _gcode_de_duas_cores(tmp_path)
    inserir_pausas_nas_trocas(gcode, [PRETO, BRANCO], 250.0)
    gcode.write_text(gcode.read_text(encoding="utf-8").replace("G1 Z0.6 F3000\n", ""), encoding="utf-8")
    assert "TROCA_SEM_VOLTAR_A_ALTURA" in _codigos(_validar(gcode, perfil, 1))


# ---------- Roteiro ----------


def test_roteiro_diz_a_ordem_das_cores():
    info = {"cor_inicial": BRANCO, "trocas": [{"numero": 1, "camada": 1, "z": 0.2, "de": BRANCO, "para": VERMELHO}], "gramas_por_cor": [{"cor": BRANCO, "g": 1.5}]}
    texto = roteiro_das_trocas(info)
    assert "1 troca de filamento. Comece com branco." in texto
    assert "camada 1 (0.2 mm): coloque vermelho" in texto
    assert "branco 1.5 g" in texto


def test_sem_troca_nao_tem_roteiro():
    assert roteiro_das_trocas({"cor_inicial": PRETO, "trocas": []}) == ""
    assert roteiro_da_preparacao({"arquivos": [{"numero": 1, "cores": {"cor_inicial": PRETO, "trocas": []}}]}) == ""
    assert roteiro_da_preparacao({}) == ""


# ---------- Malhas por cor ----------


def _logo(pasta: Path) -> tuple[dict[str, Path], dict[str, dict]]:
    """Uma placa de 1 mm com quatro corpos lado a lado, como os quatro STL do tutorial."""
    corpos = []
    inteiras = []
    for nome, cor, x in (("preto", PRETO, 20), ("branco", BRANCO, 40), ("amarelo", AMARELO, 60), ("vermelho", VERMELHO, 80)):
        malha = caixa((20, 40, 1), (x, 30, 0.5))
        malha.export(pasta / f"logo__{nome}.stl")
        corpos.append({"nome": nome, "stl": f"logo__{nome}.stl", "cor": cor})
        inteiras.append(malha)
    trimesh.util.concatenate(inteiras).export(pasta / "logo.stl")
    return {"logo": pasta / "logo.stl"}, {"logo": {"nome": "logo", "tamanho": [80.0, 40.0, 1.0], "corpos": corpos, "fundo": "preto"}}


def test_so_a_primeira_camada_colorida_corta_e_junta_na_cor_de_fundo(tmp_path):
    stls, medidas = _logo(tmp_path)
    saida = montar_stls_por_cor([ItemMesa("1", "logo", 110.0, 60.0)], stls, medidas, tmp_path)
    assert [cor for cor, _ in saida][-1] == PRETO, "a cor de fundo vem por último, para não custar outra troca"
    for cor, caminho in saida:
        malha = trimesh.load_mesh(caminho)
        assert malha.bounds[0, 2] == pytest.approx(0.0, abs=1e-6), "nenhum objeto começa no ar"
        assert malha.bounds[1, 2] == pytest.approx(1.0 if cor == PRETO else CAMADA_INICIAL_MM, abs=1e-5)
    fundo = trimesh.load_mesh(saida[-1][1])
    assert fundo.bounds[0, 0] == pytest.approx(70.0, abs=1e-5), "o fundo cobre a peça inteira acima da primeira camada"
    assert fundo.bounds[1, 0] == pytest.approx(150.0, abs=1e-5)


def test_corpos_acompanham_a_posicao_e_o_giro_do_item(tmp_path):
    stls, medidas = _logo(tmp_path)
    medidas["logo"]["fundo"] = None
    saida = dict(montar_stls_por_cor([ItemMesa("1", "logo", 100.0, 100.0, 1, 90)], stls, medidas, tmp_path))
    limites = np.vstack([trimesh.load_mesh(c).bounds for c in saida.values()])
    assert limites[:, 0].min() == pytest.approx(80.0, abs=1e-4)
    assert limites[:, 0].max() == pytest.approx(120.0, abs=1e-4)
    assert limites[:, 1].min() == pytest.approx(60.0, abs=1e-4)
    assert limites[:, 1].max() == pytest.approx(140.0, abs=1e-4)


def test_corpo_que_comeca_no_ar_explica_o_que_fazer(tmp_path):
    caixa((50, 50, 2), (0, 0, 1)).export(tmp_path / "base.stl")
    caixa((20, 20, 1), (0, 0, 2.5)).export(tmp_path / "relevo.stl")
    trimesh.util.concatenate([caixa((50, 50, 2), (0, 0, 1)), caixa((20, 20, 1), (0, 0, 2.5))]).export(tmp_path / "x.stl")
    medidas = {"x": {"corpos": [{"nome": "base", "stl": "base.stl", "cor": PRETO}, {"nome": "relevo", "stl": "relevo.stl", "cor": BRANCO}]}}
    with pytest.raises(ErroDeCor, match="começa acima da mesa"):
        montar_stls_por_cor([ItemMesa("1", "x", 100.0, 100.0)], {"x": tmp_path / "x.stl"}, medidas, tmp_path)


def test_fundo_que_nao_existe_mais_explica(tmp_path):
    stls, medidas = _logo(tmp_path)
    medidas["logo"]["fundo"] = "sumiu"
    with pytest.raises(ErroDeCor, match="não existe mais"):
        montar_stls_por_cor([ItemMesa("1", "logo", 100.0, 100.0)], stls, medidas, tmp_path)


def test_corpo_aberto_nao_tem_como_cortar(tmp_path):
    stls, medidas = _logo(tmp_path)
    aberto = trimesh.load_mesh(tmp_path / "logo__branco.stl")
    aberto.update_faces(np.arange(len(aberto.faces)) != 0)
    aberto.export(tmp_path / "logo__branco.stl")
    with pytest.raises(ErroDeCor, match="malha fechada"):
        montar_stls_por_cor([ItemMesa("1", "logo", 100.0, 100.0)], stls, medidas, tmp_path)


# ---------- OrcaSlicer de verdade ----------


@pytest.mark.orca
@pytest.mark.skipif(not TEM_ORCA, reason="OrcaSlicer não encontrado")
class TestNoOrcaSlicer:
    def test_o_caso_do_tutorial(self, tmp_path, perfil):
        """Quatro cores só na primeira camada: três trocas, todas ali, e o resto em uma cor."""
        stls, medidas = _logo(tmp_path)
        mesa = LayoutMesa({"logo": (80.0, 40.0)}, perfil, [ItemMesa("1", "logo", 110.0, 60.0)])
        resumo = fatiar_layout(mesa, stls, ParametrosFatiamento("PLA"), perfil, tmp_path / "saida", medidas=medidas)
        assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
        assert resumo["validacao"]["trocas_de_filamento"] == 3
        cores = resumo["arquivos"][0]["cores"]
        assert all(t["camada"] == 1 for t in cores["trocas"])
        assert cores["trocas"][-1]["para"] == PRETO
        assert {cores["cor_inicial"], *(t["para"] for t in cores["trocas"])} == {PRETO, BRANCO, AMARELO, VERMELHO}
        assert (tmp_path / "saida" / "peca.gcode").read_text(encoding="utf-8").splitlines().count("M25") == 3
        assert len(cores["gramas_por_cor"]) == 4

    def test_fundo_de_maior_area_nao_custa_outra_troca(self, tmp_path, perfil):
        """Sozinho, o OrcaSlicer começa pela cor de maior área, e teria de voltar a ela na segunda camada."""
        corpos = []
        inteiras = []
        for nome, cor, largura, x in (("branco", BRANCO, 60, 30), ("preto", PRETO, 10, 65), ("vermelho", VERMELHO, 10, 75)):
            malha = caixa((largura, 30, 1), (x, 15, 0.5))
            malha.export(tmp_path / f"{nome}.stl")
            corpos.append({"nome": nome, "stl": f"{nome}.stl", "cor": cor})
            inteiras.append(malha)
        trimesh.util.concatenate(inteiras).export(tmp_path / "logo.stl")
        medidas = {"logo": {"nome": "logo", "tamanho": [80.0, 30.0, 1.0], "corpos": corpos, "fundo": "branco"}}
        mesa = LayoutMesa({"logo": (80.0, 30.0)}, perfil, [ItemMesa("1", "logo", 110.0, 60.0)])
        resumo = fatiar_layout(mesa, {"logo": tmp_path / "logo.stl"}, ParametrosFatiamento("PLA"), perfil, tmp_path / "saida", medidas=medidas)
        assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
        cores = resumo["arquivos"][0]["cores"]
        assert len(cores["trocas"]) == 2
        assert cores["trocas"][-1]["para"] == BRANCO
        assert all(t["camada"] == 1 for t in cores["trocas"])

    def test_pecas_altas_de_cores_diferentes_nao_descem_para_purgar(self, tmp_path, perfil):
        caixa((30, 30, 6), (0, 0, 3)).export(tmp_path / "a.stl")
        caixa((30, 30, 4), (0, 0, 2)).export(tmp_path / "b.stl")
        itens = [ItemMesa("1", "a", 60.0, 60.0, 1, 0, {"": PRETO}), ItemMesa("2", "b", 110.0, 60.0, 1, 0, {"": VERMELHO})]
        mesa = LayoutMesa({"a": (30.0, 30.0), "b": (30.0, 30.0)}, perfil, itens)
        resumo = fatiar_layout(mesa, {"a": tmp_path / "a.stl", "b": tmp_path / "b.stl"}, ParametrosFatiamento("PLA", **LEVE), perfil, tmp_path / "saida")
        assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
        assert resumo["validacao"]["trocas_de_filamento"] > 5
        linhas = (tmp_path / "saida" / "peca.gcode").read_text(encoding="utf-8").splitlines()
        altas = [i for i, l in enumerate(linhas) if l.startswith(MARCA_INICIO) and float(re.search(r"z=([\d.]+)", l).group(1)) > 2.0]
        assert altas
        for inicio in altas:
            assert "G1 Z0.28 F3000" not in linhas[inicio : linhas.index(MARCA_FIM, inicio)]

    def test_troca_por_altura_em_uma_peca_de_uma_cor(self, tmp_path, perfil):
        caixa((30, 30, 4), (0, 0, 2)).export(tmp_path / "a.stl")
        mesa = LayoutMesa({"a": (30.0, 30.0)}, perfil, [ItemMesa("1", "a", 110.0, 110.0, 1, 0, {"": BRANCO})])
        mesa.definir_alturas(1, [{"z": 2.0, "cor": VERMELHO}])
        resumo = fatiar_layout(mesa, {"a": tmp_path / "a.stl"}, ParametrosFatiamento("PLA", **LEVE), perfil, tmp_path / "saida")
        assert resumo["validacao"]["aprovado"], resumo["validacao"]["bloqueios"]
        assert resumo["validacao"]["trocas_de_filamento"] == 1
        cores = resumo["arquivos"][0]["cores"]
        assert cores["cor_inicial"] == BRANCO
        assert cores["trocas"][0]["para"] == VERMELHO

    def test_sem_cor_o_gcode_continua_sem_torre_e_sem_pausa(self, tmp_path, perfil):
        """Quem não usa cor não pode pagar por isso."""
        caixa((30, 30, 2), (0, 0, 1)).export(tmp_path / "a.stl")
        mesa = LayoutMesa({"a": (30.0, 30.0)}, perfil, [ItemMesa("1", "a", 110.0, 110.0)])
        resumo = fatiar_layout(mesa, {"a": tmp_path / "a.stl"}, ParametrosFatiamento("PLA", **LEVE), perfil, tmp_path / "saida")
        linhas = (tmp_path / "saida" / "peca.gcode").read_text(encoding="utf-8").splitlines()
        assert "M25" not in linhas
        assert ";TYPE:Prime tower" not in linhas
        assert resumo["arquivos"][0]["cores"] == {"cor_inicial": None, "trocas": [], "gramas_por_cor": []}

    def test_mesa_sem_lugar_para_a_torre_explica(self, tmp_path, perfil):
        caixa((205, 205, 1), (0, 0, 0.5)).export(tmp_path / "a.stl")
        caixa((205, 205, 1), (0, 0, 0.5)).export(tmp_path / "b.stl")
        itens = [ItemMesa("1", "a", 110.0, 110.0, 1, 0, {"": PRETO}), ItemMesa("2", "b", 110.0, 110.0, 1, 0, {"": BRANCO})]
        mesa = LayoutMesa({"a": (205.0, 205.0), "b": (205.0, 205.0)}, perfil, itens)
        with pytest.raises(ErroFatiamento, match="torre de purga"):
            fatiar_layout(mesa, {"a": tmp_path / "a.stl", "b": tmp_path / "b.stl"}, ParametrosFatiamento("PLA"), perfil, tmp_path / "saida")


# ---------- Importar um arquivo por cor ----------


def _quatro_arquivos(pasta: Path) -> list[Path]:
    """Os STL do tutorial: fora da origem de propósito, todos no mesmo sistema de coordenadas."""
    arquivos = []
    for nome, x in (("logo_black", 20), ("logo_white", 40), ("logo_yellow", 60), ("logo_red", 80)):
        caminho = pasta / f"{nome}.stl"
        caixa((20, 40, 1), (x + 500, 30, 0.5)).export(caminho)
        arquivos.append(caminho)
    return arquivos


def test_arquivos_de_cor_ficam_alinhados_e_sugerem_a_cor(tmp_path):
    arquivos = _quatro_arquivos(tmp_path)
    saida = tmp_path / "saida"
    saida.mkdir()
    assert principal(*(str(a) for a in arquivos), str(saida)) == 0
    dados = json.loads((saida / "leitura.json").read_text(encoding="utf-8"))
    assert dados["ok"], dados
    assert dados["tamanho_mm"] == [80.0, 40.0, 1.0], "o conjunto, não uma parte"
    assert [p["nome"] for p in dados["partes_de_cor"]] == ["logo_black", "logo_white", "logo_yellow", "logo_red"]
    assert [nome_da_cor(p["cor"]) for p in dados["partes_de_cor"]] == ["preto", "branco", "amarelo", "vermelho"]
    assert dados["nome_comum"] == "logo"
    limites = [trimesh.load_mesh(saida / p["stl"]).bounds for p in dados["partes_de_cor"]]
    assert limites[0][1, 0] == pytest.approx(limites[1][0, 0], abs=1e-4), "cada parte encosta na outra, como foi desenhado"
    assert np.load(saida / "previa.npy").shape[1] == 7


def test_arquivo_com_cor_repetida_ou_sem_nome_de_cor_ganha_outra(tmp_path):
    for nome, x in (("a_black", 0), ("b_black", 30), ("cranio", 60)):
        caixa((20, 20, 2), (x, 0, 1)).export(tmp_path / f"{nome}.stl")
    saida = tmp_path / "saida"
    saida.mkdir()
    principal(str(tmp_path / "a_black.stl"), str(tmp_path / "b_black.stl"), str(tmp_path / "cranio.stl"), str(saida))
    cores = [p["cor"] for p in json.loads((saida / "leitura.json").read_text(encoding="utf-8"))["partes_de_cor"]]
    assert len(set(cores)) == 3


def test_erro_em_um_dos_arquivos_diz_qual(tmp_path):
    caixa((20, 20, 2), (0, 0, 1)).export(tmp_path / "bom.stl")
    (tmp_path / "ruim.usdz").write_bytes(b"PK")
    with pytest.raises(ModeloInvalido, match="ruim.usdz"):
        ler_em_processo([tmp_path / "bom.stl", tmp_path / "ruim.usdz"], timeout=120)


def _modelo_lido(tmp_path: Path) -> ModeloLido:
    return ler_em_processo(_quatro_arquivos(tmp_path), timeout=120)


def test_gravar_parte_com_corpos_aplica_a_mesma_transformacao_em_todos(tmp_path):
    modelo = _modelo_lido(tmp_path)
    destino = tmp_path / "versao"
    destino.mkdir()
    opcoes = OpcoesDoModelo("logo", 2.0, np.eye(3).tolist(), {"logo_white": VERMELHO}, "logo_black", modelo.partes_de_cor)
    parte = gravar_parte(modelo.stl, destino, opcoes)
    assert parte["tamanho"] == [160.0, 80.0, 2.0]
    assert parte["fundo"] == "logo_black"
    assert parte["solidos"] == 4
    assert parte["corpos_fechados"] is True
    assert [c["cor"] for c in parte["corpos"]][:2] == [PRETO, VERMELHO], "a cor escolhida vale sobre a sugerida"
    a = trimesh.load_mesh(destino / parte["corpos"][0]["stl"])
    b = trimesh.load_mesh(destino / parte["corpos"][1]["stl"])
    assert a.bounds[1, 0] == pytest.approx(b.bounds[0, 0], abs=1e-4)
    assert a.bounds[0, 0] == pytest.approx(-80.0, abs=1e-4), "o conjunto centralizado, não cada parte"


def test_corpos_que_encostam_nao_contam_como_malha_aberta(tmp_path, perfil):
    modelo = _modelo_lido(tmp_path)
    destino = tmp_path / "versao"
    destino.mkdir()
    parte = gravar_parte(modelo.stl, destino, OpcoesDoModelo("logo", 1.0, np.eye(3).tolist(), {}, "logo_black", modelo.partes_de_cor))
    codigos = {a["codigo"] for a in verificar_peca([parte], None, perfil, destino)["alertas"]}
    assert "MALHA_ABERTA" not in codigos
    assert "CORPOS_SEPARADOS" not in codigos


# ---------- Projeto e arquivo ----------


class ClienteFalso:
    modelo = "falso"

    def __init__(self, _config=None):
        pass

    def conversar(self, *_a, **_k):
        return texto_ia("ok")

    def verificar(self):
        pass


def _config(tmp_path: Path) -> Configuracao:
    return Configuracao(ia=ConfigIA(endpoint="http://x/v1", api_key="chave", modelo="m"), caminho_orca=localizar_orca() or tmp_path / "orca.exe", pasta_projetos=tmp_path / "projetos")


def _agente_com_logo(tmp_path: Path, perfil) -> tuple[Orquestrador, Projeto, ModeloLido]:
    modelo = _modelo_lido(tmp_path)
    projeto = Projeto.criar(tmp_path / "projetos", "logo")
    agente = Orquestrador(_config(tmp_path), perfil, ClienteFalso(), projeto)
    agente.adicionar_modelo(modelo.stl, OpcoesDoModelo("logo", 1.0, np.eye(3).tolist(), {}, "logo_black", modelo.partes_de_cor))
    return agente, projeto, modelo


def test_corpos_entram_na_versao_e_seguem_para_a_proxima(tmp_path, perfil):
    agente, projeto, modelo = _agente_com_logo(tmp_path, perfil)
    assert agente.versao_atual == "v001"
    assert agente.pode_aprovar()
    medida = projeto.medidas("v001")[0]
    assert (projeto.pasta_versao("v001") / medida["corpos"][0]["stl"]).is_file()
    agente.adicionar_modelo(modelo.stl, OpcoesDoModelo("outro", 1.0, np.eye(3).tolist()))
    assert all((projeto.pasta_versao("v002") / c["stl"]).is_file() for c in projeto.medidas("v002")[0]["corpos"])


def test_arquivo_forja3d_leva_corpos_e_cores(tmp_path, perfil):
    _, projeto, _ = _agente_com_logo(tmp_path, perfil)
    mesa = projeto.layout("v001", perfil)
    mesa.definir_cores(mesa.itens[0].id, {"logo_white": VERMELHO}, "logo_black")
    projeto.salvar_layout("v001", mesa)
    arquivo = salvar_projeto(projeto, tmp_path / "x.forja3d")
    aberto = importar_projeto(arquivo, tmp_path / "outros", perfil)
    medida = aberto.medidas("v001")[0]
    assert [c["nome"] for c in medida["corpos"]] == ["logo_black", "logo_white", "logo_yellow", "logo_red"]
    assert all((aberto.pasta_versao("v001") / c["stl"]).is_file() for c in medida["corpos"])
    item = aberto.layout("v001", perfil).itens[0]
    assert item.cores == {"logo_white": VERMELHO}
    assert item.fundo == "logo_black"


def _adulterar_medidas(arquivo: Path, destino: Path, mudar) -> Path:
    with zipfile.ZipFile(arquivo) as origem, zipfile.ZipFile(destino, "w") as saida:
        for nome in origem.namelist():
            dados = origem.read(nome)
            if nome == "v001/medidas.json":
                medidas = json.loads(dados)
                mudar(medidas[0])
                dados = json.dumps(medidas).encode()
            saida.writestr(nome, dados)
    return destino


@pytest.mark.parametrize("stl", ["../../fora.stl", "C:/Windows/x.stl", "sumiu.stl"])
def test_arquivo_forja3d_nao_pode_apontar_um_corpo_para_fora(tmp_path, perfil, stl):
    _, projeto, _ = _agente_com_logo(tmp_path, perfil)
    arquivo = salvar_projeto(projeto, tmp_path / "x.forja3d")
    adulterado = _adulterar_medidas(arquivo, tmp_path / "adulterado.forja3d", lambda m: m["corpos"][0].update(stl=stl))
    with pytest.raises(ArquivoInvalido, match="corpos"):
        importar_projeto(adulterado, tmp_path / "outros", perfil)


def test_arquivo_forja3d_limpa_cor_estragada(tmp_path, perfil):
    _, projeto, _ = _agente_com_logo(tmp_path, perfil)
    arquivo = salvar_projeto(projeto, tmp_path / "x.forja3d")

    def estragar(medida):
        medida["corpos"][0]["cor"] = "<script>"
        medida["fundo"] = "nao existe"

    medida = importar_projeto(_adulterar_medidas(arquivo, tmp_path / "adulterado.forja3d", estragar), tmp_path / "outros", perfil).medidas("v001")[0]
    assert medida["corpos"][0]["cor"] is None
    assert medida["fundo"] is None


# ---------- Tela ----------


def test_botao_de_cor_mostra_a_paleta_e_avisa(qtbot):
    botao = BotaoDeCor(PRETO)
    qtbot.addWidget(botao)
    assert botao.text() == "Preto"
    avisos = []
    botao.mudou.connect(lambda: avisos.append(botao.cor))
    next(a for a in botao.montar_paleta().actions() if a.text() == "Vermelho").trigger()
    assert botao.cor == VERMELHO
    assert botao.text() == "Vermelho"
    assert avisos == [VERMELHO]


def test_sem_cor_so_aparece_quando_pode(qtbot):
    com = BotaoDeCor(None, permitir_sem_cor=True)
    sem = BotaoDeCor(PRETO)
    qtbot.addWidget(com)
    qtbot.addWidget(sem)
    assert com.text() == "Sem cor definida"
    assert "Sem cor definida" in [a.text() for a in com.montar_paleta().actions()]
    assert "Sem cor definida" not in [a.text() for a in sem.montar_paleta().actions()]


def test_editor_conta_as_trocas_conforme_a_escolha(qtbot):
    editor = EditorDeCores({"a": PRETO, "b": BRANCO, "c": VERMELHO}, "a", 1.0)
    qtbot.addWidget(editor)
    assert "2 trocas" in editor.rotulo_trocas.text()
    assert editor.fundo() == "a"
    editor.caixa_fundo.setChecked(False)
    assert editor.fundo() == ""
    assert "Cerca de 10 trocas" in editor.rotulo_trocas.text()
    editor.botoes["b"].definir(PRETO)
    editor.botoes["c"].definir(PRETO)
    assert "nenhuma troca" in editor.rotulo_trocas.text()


def test_editor_de_um_corpo_nao_tem_fundo(qtbot):
    editor = EditorDeCores({"": None}, None, 20.0)
    qtbot.addWidget(editor)
    assert editor.caixa_fundo is None
    assert editor.fundo() is None
    assert editor.cores() == {}
    editor.botoes[""].definir(VERMELHO)
    assert editor.cores() == {"": VERMELHO}


def test_dialogo_de_alturas(qtbot):
    dialogo = DialogoAlturas([{"z": 2.0, "cor": PRETO}], 10.0)
    qtbot.addWidget(dialogo)
    assert dialogo.trocas() == [{"z": 2.0, "cor": PRETO}]
    dialogo.adicionar()
    assert dialogo.trocas()[1]["z"] == 3.0
    assert dialogo.trocas()[1]["cor"] != PRETO
    dialogo._remover(dialogo.linhas[0])
    assert len(dialogo.trocas()) == 1


def test_dialogo_de_importar_com_arquivos_de_cor(qtbot, tmp_path, perfil):
    modelo = _modelo_lido(tmp_path)
    dialogo = DialogoImportar(modelo, perfil, set())
    qtbot.addWidget(dialogo)
    assert dialogo.campo_nome.text() == "logo"
    assert dialogo.editor.caixa_fundo.isChecked(), "peça baixa já vem com a técnica do logo ligada"
    assert dialogo.editor.fundo() in {p["nome"] for p in modelo.partes_de_cor}
    dialogo.editor.botoes["logo_white"].definir(VERMELHO)
    opcoes = dialogo.opcoes()
    assert opcoes.cores["logo_white"] == VERMELHO
    assert opcoes.partes_de_cor == modelo.partes_de_cor


def test_peca_alta_nao_liga_a_tecnica_do_logo_sozinha(qtbot, tmp_path, perfil):
    for nome, x in (("a", 0), ("b", 30)):
        caixa((20, 20, 40), (x, 0, 20)).export(tmp_path / f"{nome}.stl")
    dialogo = DialogoImportar(ler_em_processo([tmp_path / "a.stl", tmp_path / "b.stl"], timeout=120), perfil, set())
    qtbot.addWidget(dialogo)
    assert not dialogo.editor.caixa_fundo.isChecked()


def test_fundo_so_pode_ser_quem_encosta_na_mesa(qtbot, tmp_path, perfil):
    caixa((50, 50, 2), (0, 0, 1)).export(tmp_path / "base.stl")
    caixa((20, 20, 1), (0, 0, 2.5)).export(tmp_path / "relevo.stl")
    dialogo = DialogoImportar(ler_em_processo([tmp_path / "base.stl", tmp_path / "relevo.stl"], timeout=120), perfil, set())
    qtbot.addWidget(dialogo)
    combo = dialogo.editor.combo_fundo
    assert [combo.itemText(i) for i in range(combo.count())] == ["base"]


@pytest.fixture
def janela(qtbot, perfil, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    janela = JanelaPrincipal(_config(tmp_path), perfil, ClienteFalso, verificar_endpoint=False)
    qtbot.addWidget(janela)
    yield janela
    janela.close()


def _abrir_com_duas_pecas(janela, tmp_path, alturas=(20.0, 10.0)) -> None:
    projeto = Projeto.criar(tmp_path / "projetos", "duas")
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("# nada\n", encoding="utf-8")
    medidas = []
    for nome, altura in zip(("a", "b"), alturas):
        caixa((30, 30, altura), (0, 0, altura / 2)).export(pasta / f"{nome}.stl")
        medidas.append(
            {
                "nome": nome, "importada": True, "valido": True, "solidos": 1, "volume_mm3": 30.0 * 30.0 * altura, "tamanho": [30.0, 30.0, altura], "stl": f"{nome}.stl",
                "furos": [], "caixa": {"x": [-15.0, 15.0], "y": [-15.0, 15.0], "z": [0.0, altura]},
            }
        )
    projeto.estado["versao_atual"] = projeto.registrar_versao(pasta, None, medidas, "teste")
    projeto.salvar_estado()
    janela.abrir_projeto(projeto)


def test_cor_na_mesa_muda_a_assinatura_e_pode_desfazer(qtbot, janela, tmp_path, monkeypatch):
    _abrir_com_duas_pecas(janela, tmp_path)
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    painel = janela.painel_3d
    item = painel.mesa.itens[0]
    painel.selecionar(item.id)
    assert painel.botoes["cor"].isEnabled()

    class Aceito:
        editor = type("E", (), {"cores": staticmethod(lambda: {"": VERMELHO}), "fundo": staticmethod(lambda: None)})()

        def __init__(self, *_a):
            pass

        def exec(self):
            return 1

        def deleteLater(self):
            pass

    monkeypatch.setattr("forja3d.interface.painel_3d.DialogoCores", Aceito)
    antes = painel.mesa.assinatura()
    painel.escolher_cor()
    assert painel.mesa.item(item.id).cores == {"": VERMELHO}
    assert painel.mesa.assinatura() != antes
    painel.desfazer()
    assert painel.mesa.item(item.id).cores == {}


def test_muitas_trocas_avisam_e_oferecem_separar(qtbot, janela, tmp_path, monkeypatch):
    _abrir_com_duas_pecas(janela, tmp_path)
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    painel = janela.painel_3d
    a, b = painel.mesa.itens
    painel.mesa.definir_cores(a.id, {"": PRETO})
    painel.mesa.definir_cores(b.id, {"": BRANCO})
    textos = []

    def clicar_separar(caixa_de_dialogo):
        textos.append(caixa_de_dialogo.text())
        botao = next(b for b in caixa_de_dialogo.buttons() if b.text() == "Um arquivo por cor")
        caixa_de_dialogo.clickedButton = lambda: botao
        return 0

    monkeypatch.setattr(QMessageBox, "exec", clicar_separar)
    assert janela._confirmar_trocas()
    assert "cerca de" in textos[0]
    assert {a.arquivo, b.arquivo} == {1, 2}
    assert janela._confirmar_trocas(), "agora cada cor tem o seu arquivo, sem nenhuma troca"
    assert len(textos) == 1


def test_poucas_trocas_nao_incomodam(qtbot, janela, tmp_path):
    _abrir_com_duas_pecas(janela, tmp_path, alturas=(1.0, 1.0))
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    a, b = janela.painel_3d.mesa.itens
    janela.painel_3d.mesa.definir_cores(a.id, {"": PRETO})
    janela.painel_3d.mesa.definir_cores(b.id, {"": BRANCO})
    assert janela._confirmar_trocas()


def test_texto_de_exportacao_mostra_as_cores():
    resumo = {"arquivos": [{"numero": 1, "cores": {"cor_inicial": PRETO, "trocas": [{"numero": 1, "camada": 1, "z": 0.2, "de": PRETO, "para": BRANCO}], "gramas_por_cor": []}}]}
    assert "Comece com preto" in JanelaPrincipal._texto_das_cores(resumo)
    assert JanelaPrincipal._texto_das_cores({"arquivos": [{"numero": 1, "cores": {"cor_inicial": BRANCO, "trocas": []}}]}) == "\nCor: branco"
    assert JanelaPrincipal._texto_das_cores({"arquivos": [{"numero": 1, "cores": {"cor_inicial": None, "trocas": []}}]}) == ""


def test_roteiro_vai_junto_com_o_gcode(qtbot, janela, tmp_path):
    resumo = {"arquivos": [{"numero": 1, "cores": {"cor_inicial": PRETO, "trocas": [{"numero": 1, "camada": 1, "z": 0.2, "de": PRETO, "para": BRANCO}], "gramas_por_cor": []}}]}
    destino = tmp_path / "logo.txt"
    aviso = janela._gravar_roteiro(resumo, destino)
    assert "logo.txt" in aviso
    texto = destino.read_text(encoding="utf-8")
    assert "Comece com preto" in texto
    assert "alavanca" in texto
    assert janela._gravar_roteiro({"arquivos": []}, tmp_path / "nada.txt") == ""
    assert not (tmp_path / "nada.txt").exists()
