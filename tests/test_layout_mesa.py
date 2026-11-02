"""A mesa da impressora: posição, giro, cópias e arquivos, sem tela."""
import pytest

from forja3d.layout_mesa import ItemMesa, LayoutMesa, ponto_no_plano

# As partes reais da Mini torre do usuário.
TORRE = {
    "chassi_slim": (48.0, 140.0),
    "espelho_io_traseiro": (28.0, 112.0),
    "tira_bateria_inferior": (27.0, 20.0),
    "tira_bateria_superior": (27.0, 20.0),
    "tampa_cooler_lateral": (139.0, 132.4),
}
PEQUENAS = {"a": (20.0, 20.0), "b": (40.0, 30.0)}


def dentro_da_area_segura(layout, item):
    dx, dy = layout.tamanho_do_item(item)
    return item.x - dx / 2 >= 5 - 1e-6 and item.x + dx / 2 <= 215 + 1e-6 and item.y - dy / 2 >= 5 - 1e-6 and item.y + dy / 2 <= 215 + 1e-6


def test_novo_tem_um_item_de_cada_parte_sem_conflito(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    assert sorted(i.parte for i in layout.itens) == ["a", "b"]
    assert layout.arquivos() == [1]
    assert layout.conflitos() == set()
    assert all(dentro_da_area_segura(layout, i) for i in layout.itens)


def test_organizar_centraliza_o_conjunto(perfil):
    layout = LayoutMesa.novo({"a": (20.0, 20.0)}, perfil)
    assert (layout.itens[0].x, layout.itens[0].y) == (110.0, 110.0)


def test_as_cinco_partes_da_torre_cabem_em_um_arquivo_girando_o_espelho(perfil):
    layout = LayoutMesa.novo(TORRE, perfil)
    assert layout.arquivos() == [1]
    assert layout.conflitos() == set()
    assert all(dentro_da_area_segura(layout, i) for i in layout.itens)
    espelho = next(i for i in layout.itens if i.parte == "espelho_io_traseiro")
    assert espelho.giro == 90
    assert layout.tamanho_do_item(espelho) == (112.0, 28.0)


def test_o_que_nao_cabe_vai_para_outro_arquivo(perfil):
    layout = LayoutMesa.novo({"a": (200.0, 200.0), "b": (200.0, 200.0), "c": (200.0, 200.0)}, perfil)
    assert layout.arquivos() == [1, 2, 3]
    assert layout.conflitos() == set()


def test_parte_maior_que_a_area_segura_fica_centralizada(perfil):
    layout = LayoutMesa.novo({"grande": (217.0, 100.0)}, perfil)
    item = layout.itens[0]
    assert item.x == 110.0
    layout.mover(item.id, 10, 10)
    assert item.x == 110.0, "não dá para mover no eixo em que ela é maior que a área segura"
    assert item.y == 55.0


@pytest.mark.parametrize(("x", "y", "esperado"), [(0, 0, (15.0, 15.0)), (500, 500, (205.0, 205.0)), (100.04, 50.06, (100.0, 50.1))])
def test_mover_fica_na_area_segura_e_arredonda(perfil, x, y, esperado):
    layout = LayoutMesa.novo({"a": (20.0, 20.0)}, perfil)
    assert layout.mover(layout.itens[0].id, x, y) == esperado


def test_conflito_entre_itens_do_mesmo_arquivo(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    a, b = layout.itens
    layout.mover(b.id, a.x, a.y)
    assert layout.conflitos() == {a.id, b.id}


def test_itens_em_arquivos_diferentes_nao_conflitam(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    a, b = layout.itens
    layout.mover(b.id, a.x, a.y)
    layout.mover_para_arquivo(b.id)
    assert layout.conflitos() == set()


def test_brim_exige_mais_espaco(perfil):
    layout = LayoutMesa.novo({"a": (20.0, 20.0), "b": (20.0, 20.0)}, perfil)
    a, b = layout.itens
    layout.mover(a.id, 50, 100)
    layout.mover(b.id, 75, 100)
    assert layout.conflitos() == set()
    assert layout.conflitos(brim_mm=5) == {a.id, b.id}


def test_duplicar_cria_copia_livre_e_mantem_o_giro(perfil):
    layout = LayoutMesa.novo({"a": (30.0, 10.0)}, perfil)
    original = layout.itens[0]
    layout.girar(original.id)
    copia = layout.duplicar(original.id)
    assert copia.parte == "a"
    assert copia.id != original.id
    assert copia.giro == 90
    assert layout.conflitos() == set()


def test_copias_ate_a_mesa_lotar_passam_para_outro_arquivo(perfil):
    layout = LayoutMesa.novo({"a": (60.0, 60.0)}, perfil)
    for _ in range(12):
        layout.duplicar(layout.itens[0].id)
    assert len(layout.itens) == 13
    assert layout.arquivos() == [1, 2]
    assert layout.conflitos() == set()


def test_remover_o_ultimo_item_de_um_arquivo_renumera(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    a, b = layout.itens
    layout.mover_para_arquivo(a.id)
    assert a.arquivo == 2
    layout.remover(b.id)
    assert layout.arquivos() == [1]
    assert a.arquivo == 1


def test_remover_tudo_deixa_a_mesa_vazia_e_adicionar_volta(perfil):
    layout = LayoutMesa.novo({"a": (20.0, 20.0)}, perfil)
    layout.remover(layout.itens[0].id)
    assert layout.itens == []
    assert layout.arquivos() == []
    item = layout.adicionar("a")
    assert item.arquivo == 1


def test_mover_para_arquivo_existente_procura_lugar_livre(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    a, b = layout.itens
    layout.mover_para_arquivo(b.id)
    assert b.arquivo == 2
    layout.mover(b.id, a.x, a.y)
    assert layout.mover_para_arquivo(b.id, 1) == 1
    assert layout.conflitos() == set()


def test_girar_troca_as_medidas_e_continua_na_area(perfil):
    layout = LayoutMesa.novo({"a": (100.0, 20.0)}, perfil)
    item = layout.itens[0]
    layout.mover(item.id, 100, 15)
    layout.girar(item.id)
    assert layout.tamanho_do_item(item) == (20.0, 100.0)
    assert item.y == 55.0
    layout.girar(item.id)
    assert item.giro == 180
    assert layout.tamanho_do_item(item) == (100.0, 20.0)


def test_area_do_arquivo_conta_o_giro(perfil):
    layout = LayoutMesa(PEQUENAS, perfil, [ItemMesa("1", "a", 50.0, 50.0), ItemMesa("2", "b", 150.0, 100.0, 1, 90)])
    assert layout.area_do_arquivo(1) == (40.0, 165.0, 40.0, 120.0)


def test_dados_voltam_iguais(perfil):
    layout = LayoutMesa.novo(TORRE, perfil)
    copia = LayoutMesa.de_dados(layout.para_dados(), TORRE, perfil)
    assert [(i.id, i.parte, i.x, i.y, i.arquivo, i.giro) for i in copia.itens] == [(i.id, i.parte, i.x, i.y, i.arquivo, i.giro) for i in layout.itens]
    assert copia.assinatura() == layout.assinatura()


def test_parte_deletada_nao_volta_sozinha(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    layout.remover(next(i.id for i in layout.itens if i.parte == "a"))
    copia = LayoutMesa.de_dados(layout.para_dados(), PEQUENAS, perfil)
    assert [i.parte for i in copia.itens] == ["b"]


def test_parte_nova_do_agente_entra_sem_mexer_nas_outras(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    b = next(i for i in layout.itens if i.parte == "b")
    layout.mover(b.id, 40, 40)
    novo = LayoutMesa.de_dados(layout.para_dados(), {**PEQUENAS, "c": (10.0, 10.0)}, perfil)
    assert sorted(i.parte for i in novo.itens) == ["a", "b", "c"]
    assert next(i for i in novo.itens if i.parte == "b").x == 40
    assert novo.conflitos() == set()


def test_parte_que_sumiu_do_modelo_sai_da_mesa(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    novo = LayoutMesa.de_dados(layout.para_dados(), {"a": (20.0, 20.0)}, perfil)
    assert [i.parte for i in novo.itens] == ["a"]


def test_parte_que_cresceu_continua_dentro_da_mesa(perfil):
    layout = LayoutMesa.novo({"a": (20.0, 20.0)}, perfil)
    layout.mover(layout.itens[0].id, 15, 15)
    novo = LayoutMesa.de_dados(layout.para_dados(), {"a": (100.0, 100.0)}, perfil)
    assert dentro_da_area_segura(novo, novo.itens[0])


def test_assinatura_muda_com_o_que_vai_para_a_impressora(perfil):
    layout = LayoutMesa.novo(PEQUENAS, perfil)
    inicial = layout.assinatura()
    item = layout.itens[0]
    layout.mover(item.id, item.x + 5, item.y)
    movido = layout.assinatura()
    assert movido != inicial
    layout.girar(item.id)
    girado = layout.assinatura()
    assert girado != movido
    layout.mover_para_arquivo(item.id)
    assert layout.assinatura() != girado


def test_assinatura_nao_depende_da_ordem_nem_do_id(perfil):
    a = LayoutMesa(PEQUENAS, perfil, [ItemMesa("1", "a", 50.0, 50.0), ItemMesa("2", "b", 100.0, 100.0)])
    b = LayoutMesa(PEQUENAS, perfil, [ItemMesa("x", "b", 100.0, 100.0), ItemMesa("y", "a", 50.0, 50.0)])
    assert a.assinatura() == b.assinatura()


def test_raio_do_mouse_cruza_o_plano():
    assert ponto_no_plano((0, 0, 10), (10, 20, 0), 5) == (5.0, 10.0)


@pytest.mark.parametrize(("inicio", "fim"), [((0, 0, 5), (100, 0, 5)), ((0, 0, 5), (1000, 0, 5.001)), ((0, 0, 0), (0, 0, 0)), ((0, 0, 10), (0, 0, 20))])
def test_raio_sem_posicao_confiavel(inicio, fim):
    assert ponto_no_plano(inicio, fim, 0) is None
