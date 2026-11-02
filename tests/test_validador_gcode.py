"""O G-code real da Fase 0 precisa passar, e toda adulteração precisa ser reprovada."""
from pathlib import Path

import pytest

from forja3d.fatiamento.exportacao import ExportacaoBloqueada, exportar_gcode, nome_sugerido
from forja3d.fatiamento.validador_gcode import validar_gcode

REAL = Path(__file__).parent / "dados" / "cubo20_pla.gcode"
PRIMEIRO_MOVIMENTO_DA_PECA = "G1 X100"


def _codigos(resultado):
    return {i["codigo"] for i in resultado["bloqueios"]}


def _adulterar(tmp_path, trocar=None, inserir_depois=None, remover=None):
    linhas = REAL.read_text(encoding="utf-8").splitlines()
    saida = []
    for linha in linhas:
        if remover and linha.startswith(remover):
            continue
        saida.append(trocar(linha) if trocar else linha)
        if inserir_depois and linha.startswith(inserir_depois[0]):
            saida.append(inserir_depois[1])
            inserir_depois = None
    caminho = tmp_path / "adulterado.gcode"
    caminho.write_text("\n".join(saida), encoding="utf-8")
    return caminho


def test_arquivo_real_passa_sem_falso_alarme(perfil):
    r = validar_gcode(REAL, perfil, "PLA", 0.2, (20, 20))
    assert r["aprovado"], r["bloqueios"]
    assert not r["alertas"], r["alertas"]
    assert r["camadas"] == 100
    assert r["bico_c"] == 220
    assert r["mesa_c"] == 55
    assert r["z_maximo_mm"] == pytest.approx(30.2)
    assert r["filamento_g"] == 3.69
    assert r["filamento_m"] == 1.24
    assert "16m 33s" in r["tempo"]


@pytest.mark.parametrize(
    ("linha", "codigo"),
    [
        ("G1 X230 Y100", "FORA_DA_MESA_X"),
        ("G1 X-2.4 Y100", "FORA_DA_MESA_X"),
        ("G1 X100 Y-5", "FORA_DA_MESA_Y"),
        ("G1 X100 Y300", "FORA_DA_MESA_Y"),
        ("G1 Z260", "FORA_DO_VOLUME_Z"),
        ("M104 S300", "BICO_ACIMA_DO_LIMITE"),
        ("M109 S270", "BICO_ACIMA_DO_LIMITE"),
        ("M104 S250", "BICO_FORA_DA_FAIXA"),
        ("M140 S120", "MESA_ACIMA_DO_LIMITE"),
        ("M190 S105", "MESA_ACIMA_DO_LIMITE"),
        ("M500", "COMANDO_PROIBIDO"),
        ("M502", "COMANDO_PROIBIDO"),
        ("M303 E0 S200", "COMANDO_PROIBIDO"),
        ("M92 E400", "COMANDO_PROIBIDO"),
        ("M851 Z-5", "COMANDO_PROIBIDO"),
        ("M997", "COMANDO_PROIBIDO"),
        ("T1", "COMANDO_PROIBIDO"),
    ],
)
def test_adulteracao_no_corpo_e_reprovada(tmp_path, perfil, linha, codigo):
    caminho = _adulterar(tmp_path, inserir_depois=(";Z:1", linha))
    r = validar_gcode(caminho, perfil, "PLA")
    assert not r["aprovado"]
    assert codigo in _codigos(r)


def test_sem_g28_e_reprovado(tmp_path, perfil):
    r = validar_gcode(_adulterar(tmp_path, remover="G28"), perfil, "PLA")
    assert "EXTRUSAO_ANTES_DE_G28" in _codigos(r)


def test_nao_desligar_bico_e_mesa_e_reprovado(tmp_path, perfil):
    r = validar_gcode(_adulterar(tmp_path, remover="M104 S0"), perfil, "PLA")
    assert "BICO_NAO_DESLIGA" in _codigos(r)
    r = validar_gcode(_adulterar(tmp_path, remover="M140 S0"), perfil, "PLA")
    assert "MESA_NAO_DESLIGA" in _codigos(r)


def test_arquivo_vazio_e_reprovado(tmp_path, perfil):
    caminho = tmp_path / "vazio.gcode"
    caminho.write_text("; nada\n", encoding="utf-8")
    assert "SEM_EXTRUSAO" in _codigos(validar_gcode(caminho, perfil, "PLA"))


def test_elevacao_final_acima_de_250_e_reprovada(tmp_path, perfil):
    # Reproduz a coluna de 245 mm: o fatiador aceita, e o movimento relativo final chega a 255,2 mm.
    caminho = _adulterar(tmp_path, trocar=lambda linha: "G1 Z245\nG91" if linha.startswith("G91") else linha)
    r = validar_gcode(caminho, perfil, "PLA")
    assert "FORA_DO_VOLUME_Z" in _codigos(r)
    assert r["z_maximo_mm"] == pytest.approx(255.2)


def test_velocidade_acima_do_limite_e_so_informativa(perfil):
    # O bloco inicial real tem F5000 com Z: o M203 limita o movimento.
    r = validar_gcode(REAL, perfil, "PLA")
    assert r["aprovado"]
    assert {i["codigo"] for i in r["informacoes"]} == {"VELOCIDADE_ACIMA"}


def test_peca_fora_da_posicao_gera_alerta(perfil):
    r = validar_gcode(REAL, perfil, "PLA", 0.2, (5, 5))
    assert "AREA_DIVERGENTE" in {i["codigo"] for i in r["alertas"]}


def test_exportacao_recusa_sem_validacao(tmp_path):
    pasta = tmp_path / "impressao"
    pasta.mkdir()
    (pasta / "peca.gcode").write_text("G28", encoding="utf-8")
    with pytest.raises(ExportacaoBloqueada):
        exportar_gcode(pasta, tmp_path / "saida.gcode")
    (pasta / "resumo.json").write_text('{"validacao": {"aprovado": false}}', encoding="utf-8")
    with pytest.raises(ExportacaoBloqueada):
        exportar_gcode(pasta, tmp_path / "saida.gcode")
    assert not (tmp_path / "saida.gcode").exists()


def test_exportacao_copia_quando_aprovado(tmp_path):
    pasta = tmp_path / "impressao"
    pasta.mkdir()
    (pasta / "peca.gcode").write_text("G28", encoding="utf-8")
    (pasta / "resumo.json").write_text('{"validacao": {"aprovado": true}}', encoding="utf-8")
    destino = exportar_gcode(pasta, tmp_path / "saida.gcode")
    assert destino.read_text(encoding="utf-8") == "G28"


@pytest.mark.parametrize(
    ("nome", "material", "esperado"),
    [
        ("Suporte em L", "PLA", "suporte_em_l_pla.gcode"),
        ("Coração açaí", "PETG", "coracao_acai_petg.gcode"),
        ("???", "TPU", "peca_tpu.gcode"),
    ],
)
def test_nome_sugerido(nome, material, esperado):
    assert nome_sugerido(nome, material) == esperado


def test_nome_sugerido_respeita_o_limite():
    assert len(nome_sugerido("x" * 80, "PETG")) <= 26
