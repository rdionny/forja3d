"""O agente aplica cor na mesa: a tampa preta com os nomes em branco, sem mexer na geometria."""
import json
import os
from typing import ClassVar

import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from forja3d.agente.orquestrador import Orquestrador, Ouvinte
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.fatiamento.cores import ErroDeCor, alturas_planas, aplicar_cores, plano_do_arquivo, situacao_das_cores
from forja3d.fatiamento.orca import ParametrosFatiamento, localizar_orca
from forja3d.ia.cliente import ChamadaFerramenta, RespostaIA
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.layout_mesa import ItemMesa, LayoutMesa
from forja3d.projetos.armazenamento import Projeto

PRETO, BRANCO, VERMELHO, VERDE = "#1A1A1A", "#F5F5F5", "#D32F2F", "#388E3C"
TAMPA = {"parte": "tampa", "cor": "preto", "trocas": [{"z_mm": 2.6, "cor": "branco"}]}


def caixa(extensao, centro) -> trimesh.Trimesh:
    malha = trimesh.creation.box(extents=extensao)
    malha.apply_translation(centro)
    return malha


def malha_da_tampa() -> trimesh.Trimesh:
    """Placa de 2,6 mm com um nome em relevo até 4 mm."""
    return trimesh.util.concatenate([caixa((60, 40, 2.6), (0, 0, 1.3)), caixa((40, 8, 1.4), (0, 0, 3.3))])


def medida(nome, altura=4.0, **extras) -> dict:
    return {"nome": nome, "stl": f"{nome}.stl", "tamanho": [60.0, 40.0, altura], "furos": [], "solidos": 1, "volume_mm3": 1.0, "valido": True, **extras}


def mesa(perfil, *itens) -> LayoutMesa:
    return LayoutMesa({i.parte: (60.0, 40.0) for i in itens}, perfil, list(itens))


# ---------- A mesa ----------


def test_tampa_vai_para_um_arquivo_proprio_e_o_chassi_nao_muda(perfil):
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40), ItemMesa("2", "chassi", 120, 40))
    medidas = {"tampa": medida("tampa"), "chassi": medida("chassi", 100.0)}
    assert aplicar_cores(layout, "tampa", medidas, PRETO, None, [{"z": 2.6, "cor": BRANCO}]) == [2]
    assert layout.item("1").arquivo == 2
    assert layout.item("2").arquivo == 1
    assert layout.item("2").cores == {}
    assert 1 not in layout.alturas
    plano = plano_do_arquivo(layout, 2, medidas)
    assert plano.cores == (PRETO,)
    assert plano.alturas == ({"z": 2.6, "cor": BRANCO},)


def test_parte_sozinha_fica_no_mesmo_arquivo(perfil):
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40))
    assert aplicar_cores(layout, "tampa", {"tampa": medida("tampa")}, PRETO, None, [{"z": 2.6, "cor": BRANCO}]) == [1]
    assert layout.alturas == {1: [{"z": 2.6, "cor": BRANCO}]}


def test_copias_vao_juntas(perfil):
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40), ItemMesa("2", "tampa", 40, 100), ItemMesa("3", "chassi", 150, 40))
    medidas = {"tampa": medida("tampa"), "chassi": medida("chassi")}
    assert aplicar_cores(layout, "tampa", medidas, PRETO) == [2]
    assert layout.item("1").arquivo == layout.item("2").arquivo == 2
    assert layout.item("1").cores == layout.item("2").cores == {"": PRETO}


def test_pedidos_um_a_um_juntam_as_partes_da_mesma_cor(perfil):
    layout = mesa(perfil, ItemMesa("a", "a", 40, 40), ItemMesa("b", "b", 40, 100), ItemMesa("c", "c", 150, 40))
    medidas = {n: medida(n) for n in "abc"}
    aplicar_cores(layout, "a", medidas, VERMELHO)
    aplicar_cores(layout, "b", medidas, VERMELHO)
    aplicar_cores(layout, "c", medidas, VERDE)
    assert layout.item("a").arquivo == layout.item("b").arquivo
    assert layout.item("c").arquivo != layout.item("a").arquivo
    assert layout.arquivos() == [1, 2]
    assert all(not plano_do_arquivo(layout, n, medidas).tem_trocas for n in layout.arquivos())


def test_nao_junta_em_arquivo_que_nao_tem_espaco(perfil):
    grande = {"tampa": (60.0, 40.0), "cheio": (200.0, 200.0), "chassi": (60.0, 40.0)}
    layout = LayoutMesa(grande, perfil, [ItemMesa("1", "tampa", 40, 40), ItemMesa("2", "chassi", 150, 40), ItemMesa("3", "cheio", 110, 110, 2, 0, {"": PRETO})])
    medidas = {n: medida(n) for n in grande}
    assert aplicar_cores(layout, "tampa", medidas, PRETO) == [3]


def test_mesma_cor_no_mesmo_arquivo_nao_separa(perfil):
    layout = mesa(perfil, ItemMesa("1", "a", 40, 40, 1, 0, {"": PRETO}), ItemMesa("2", "b", 120, 40, 1, 0, {"": BRANCO}))
    medidas = {"a": medida("a"), "b": medida("b")}
    aplicar_cores(layout, "b", medidas, PRETO)
    assert layout.arquivos() == [1]


def test_troca_que_nunca_aconteceria_e_recusada(perfil):
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40))
    with pytest.raises(ErroDeCor, match="4 mm de altura"):
        aplicar_cores(layout, "tampa", {"tampa": medida("tampa")}, PRETO, None, [{"z": 4.0, "cor": BRANCO}])


def test_duas_trocas_na_mesma_camada_sao_recusadas(perfil):
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40))
    with pytest.raises(ErroDeCor, match="mesma camada"):
        aplicar_cores(layout, "tampa", {"tampa": medida("tampa")}, PRETO, None, [{"z": 2.7, "cor": BRANCO}, {"z": 2.6, "cor": VERDE}])


def test_lista_vazia_tira_as_trocas_e_sem_a_lista_elas_ficam(perfil):
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40))
    medidas = {"tampa": medida("tampa")}
    aplicar_cores(layout, "tampa", medidas, PRETO, None, [{"z": 2.6, "cor": BRANCO}])
    aplicar_cores(layout, "tampa", medidas, VERMELHO)
    assert layout.item("1").cores == {"": VERMELHO}
    assert layout.alturas[1] == [{"z": 2.6, "cor": BRANCO}]
    aplicar_cores(layout, "tampa", medidas, None, None, [])
    assert layout.alturas == {}


def test_trocas_acompanham_a_parte_quando_ela_muda_de_arquivo(perfil):
    layout = mesa(perfil, ItemMesa("1", "a", 40, 40, 1, 0, {"": PRETO}), ItemMesa("2", "b", 120, 40, 1, 0, {"": PRETO}))
    layout.alturas = {1: [{"z": 2.6, "cor": BRANCO}]}
    medidas = {"a": medida("a"), "b": medida("b")}
    assert aplicar_cores(layout, "a", medidas, VERMELHO) == [2]
    assert layout.alturas[2] == [{"z": 2.6, "cor": BRANCO}]
    assert layout.alturas[1] == [{"z": 2.6, "cor": BRANCO}]


def test_cor_em_cada_corpo_de_um_modelo_importado(perfil):
    corpos = [{"nome": "letras", "stl": "l.stl", "cor": PRETO}, {"nome": "fundo", "stl": "f.stl", "cor": BRANCO}]
    layout = mesa(perfil, ItemMesa("1", "logo", 40, 40, 1, 0, {}, "fundo"))
    medidas = {"logo": medida("logo", corpos=corpos)}
    aplicar_cores(layout, "logo", medidas, None, {"letras": VERMELHO})
    assert layout.item("1").cores == {"letras": VERMELHO, "fundo": BRANCO}
    assert layout.item("1").fundo == "fundo"
    aplicar_cores(layout, "logo", medidas, VERDE)
    assert layout.item("1").cores == {"letras": VERDE, "fundo": VERDE}


def test_corpo_que_nao_existe_e_recusado(perfil):
    layout = mesa(perfil, ItemMesa("1", "logo", 40, 40), ItemMesa("2", "placa", 120, 40))
    medidas = {"logo": medida("logo", corpos=[{"nome": "letras", "stl": "l.stl"}]), "placa": medida("placa")}
    with pytest.raises(ErroDeCor, match="Os corpos dela são: letras"):
        aplicar_cores(layout, "logo", medidas, None, {"olhos": VERMELHO})
    with pytest.raises(ErroDeCor, match="não tem corpos"):
        aplicar_cores(layout, "placa", medidas, None, {"olhos": VERMELHO})


def test_corpos_de_cores_diferentes_nao_combinam_com_troca_por_altura(perfil):
    corpos = [{"nome": "a", "stl": "a.stl"}, {"nome": "b", "stl": "b.stl"}]
    layout = mesa(perfil, ItemMesa("1", "logo", 40, 40))
    with pytest.raises(ErroDeCor, match="troca por altura"):
        aplicar_cores(layout, "logo", {"logo": medida("logo", corpos=corpos)}, None, {"a": PRETO, "b": BRANCO}, [{"z": 2.6, "cor": VERDE}])


def test_parte_retirada_da_mesa(perfil):
    layout = mesa(perfil, ItemMesa("1", "a", 40, 40))
    with pytest.raises(ErroDeCor, match="retirada da mesa"):
        aplicar_cores(layout, "b", {"a": medida("a"), "b": medida("b")}, PRETO)


# ---------- O que o agente recebe de volta ----------


def test_alturas_planas_acham_o_topo_da_placa(tmp_path):
    stl = tmp_path / "tampa.stl"
    malha_da_tampa().export(stl)
    assert [f["z_mm"] for f in alturas_planas(stl)] == [2.6]


def test_alturas_planas_medem_da_base_da_peca(tmp_path):
    stl = tmp_path / "tampa.stl"
    malha_da_tampa().apply_translation((0, 0, 50)).export(stl)
    assert [f["z_mm"] for f in alturas_planas(stl)] == [2.6]


def test_alturas_planas_em_peca_sem_degraus(tmp_path):
    stl = tmp_path / "cubo.stl"
    caixa((20, 20, 20), (0, 0, 10)).export(stl)
    assert alturas_planas(stl) == []


def test_situacao_sem_alerta_na_altura_certa(perfil, tmp_path):
    stl = tmp_path / "tampa.stl"
    malha_da_tampa().export(stl)
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40))
    medidas = {"tampa": medida("tampa")}
    aplicar_cores(layout, "tampa", medidas, PRETO, None, [{"z": 2.6, "cor": BRANCO}])
    situacao = situacao_das_cores(layout, "tampa", medidas, stl, 0.2)
    assert situacao["alertas"] == []
    assert situacao["arquivos"] == [{"numero": 1, "partes": ["tampa"], "cores": ["preto"], "trocas": [{"z_mm": 2.6, "cor": "branco"}], "pausas_previstas": 1}]


def test_situacao_avisa_da_troca_no_meio_da_parede(perfil, tmp_path):
    stl = tmp_path / "tampa.stl"
    malha_da_tampa().export(stl)
    layout = mesa(perfil, ItemMesa("1", "tampa", 40, 40))
    medidas = {"tampa": medida("tampa")}
    aplicar_cores(layout, "tampa", medidas, PRETO, None, [{"z": 1.0, "cor": BRANCO}])
    assert "no meio das paredes" in situacao_das_cores(layout, "tampa", medidas, stl, 0.2)["alertas"][0]


def test_situacao_avisa_de_muitas_pausas(perfil, tmp_path):
    stl = tmp_path / "a.stl"
    caixa((20, 20, 40), (0, 0, 20)).export(stl)
    corpos = [{"nome": "a", "stl": "a.stl"}, {"nome": "b", "stl": "b.stl"}]
    layout = mesa(perfil, ItemMesa("1", "logo", 40, 40))
    medidas = {"logo": medida("logo", 40.0, corpos=corpos)}
    aplicar_cores(layout, "logo", medidas, None, {"a": PRETO, "b": BRANCO})
    situacao = situacao_das_cores(layout, "logo", medidas, stl, 0.2)
    assert "pausas" in situacao["alertas"][0]
    assert situacao["corpos"] == [{"corpo": "a", "cor": "preto"}, {"corpo": "b", "cor": "branco"}]


# ---------- O agente ----------


def chamada(nome, argumentos=None, id_="c1"):
    bruto = json.dumps(argumentos or {})
    return RespostaIA("", [ChamadaFerramenta(id_, nome, bruto)], {"role": "assistant", "tool_calls": [{"id": id_, "type": "function", "function": {"name": nome, "arguments": bruto}}]})


def texto(conteudo):
    return RespostaIA(conteudo, [], {"role": "assistant", "content": conteudo})


class ClienteGravado:
    respostas: ClassVar[list] = []

    def __init__(self, _config=None):
        pass

    def verificar(self):
        return None

    def conversar(self, mensagens, ferramentas, etapa, cancelar=None):
        return type(self).respostas.pop(0)


class OuvinteDeCores(Ouvinte):
    def __init__(self):
        self.avisos = []

    def cores(self, versao, arquivo):
        self.avisos.append((versao, arquivo))


@pytest.fixture
def config(tmp_path):
    return Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path / "projetos")


def projeto_com_tampa(config, com_chassi=True) -> Projeto:
    projeto = Projeto.criar(config.pasta_projetos)
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("# teste\n", encoding="utf-8")
    malha_da_tampa().apply_translation((0, 0, 0)).export(pasta / "tampa.stl")
    partes = [medida("tampa")]
    if com_chassi:
        caixa((60, 40, 30), (0, 0, 15)).export(pasta / "chassi.stl")
        partes.append(medida("chassi", 30.0))
    projeto.estado["versao_atual"] = projeto.registrar_versao(pasta, None, partes, "teste")
    projeto.salvar_estado()
    return projeto


def agente(config, perfil, respostas, projeto=None):
    cliente = ClienteGravado()
    type(cliente).respostas = list(respostas)
    ouvinte = OuvinteDeCores()
    return Orquestrador(config, perfil, cliente, projeto or projeto_com_tampa(config), ouvinte), ouvinte


def resultado(orquestrador, indice=0):
    return [json.loads(m["content"]) for m in orquestrador.mensagens if m.get("role") == "tool"][indice]


def test_agente_aplica_sem_gerar_versao(config, perfil):
    a, ouvinte = agente(config, perfil, [chamada("definir_cores", TAMPA), texto("Pronto.")])
    a.processar("Deixa a tampa preta e os nomes em branco")
    r = resultado(a)
    assert r["ok"]
    assert r["mudou_de_arquivo"]
    assert r["arquivos"][0]["trocas"] == [{"z_mm": 2.6, "cor": "branco"}]
    assert r["arquivos"][0]["pausas_previstas"] == 1
    assert r["alertas"] == []
    assert a.projeto.versoes() == ["v001"]
    layout = a.projeto.layout("v001", perfil)
    numero = next(i.arquivo for i in layout.itens if i.parte == "tampa")
    assert layout.alturas[numero] == [{"z": 2.6, "cor": BRANCO}]
    assert ouvinte.avisos == [("v001", numero)]


def test_so_com_a_parte_nao_muda_nada(config, perfil):
    a, ouvinte = agente(config, perfil, [chamada("definir_cores", {"parte": "tampa"}), texto("ok")])
    a.projeto.estado["versao_aprovada"] = "v001"
    a.versao_aprovada = "v001"
    a.processar("Em que altura os nomes começam?")
    r = resultado(a)
    assert r["ok"]
    assert r["alturas_planas"][0]["z_mm"] == 2.6
    assert "mudou_de_arquivo" not in r
    assert not a.projeto.tem_layout("v001")
    assert a.versao_aprovada == "v001"
    assert ouvinte.avisos == []


def test_cor_fora_da_paleta_volta_para_o_modelo(config, perfil):
    a, _ = agente(config, perfil, [chamada("definir_cores", {"parte": "tampa", "cor": "dourado"}), texto("ok")])
    a.processar("Dourado")
    assert "preto, branco" in resultado(a)["erro"]
    assert not a.projeto.tem_layout("v001")


def test_nome_da_cor_aceita_maiuscula(config, perfil):
    a, _ = agente(config, perfil, [chamada("definir_cores", {"parte": "tampa", "cor": "Preto"}), texto("ok")])
    a.processar("Preta")
    assert resultado(a)["arquivos"][0]["cores"] == ["preto"]


def test_parte_desconhecida_lista_as_que_existem(config, perfil):
    a, _ = agente(config, perfil, [chamada("definir_cores", {"parte": "porta", "cor": "preto"}), texto("ok")])
    a.processar("Porta preta")
    assert "tampa, chassi" in resultado(a)["erro"]


def test_sem_peca_nao_ha_o_que_colorir(config, perfil):
    projeto = Projeto.criar(config.pasta_projetos)
    a, _ = agente(config, perfil, [chamada("definir_cores", {"parte": "tampa", "cor": "preto"}), texto("ok")], projeto)
    a.processar("Preta")
    assert "executar_cad" in resultado(a)["erro"]


def test_erro_de_cor_nao_grava_nada(config, perfil):
    a, ouvinte = agente(config, perfil, [chamada("definir_cores", {"parte": "tampa", "cor": "preto", "trocas": [{"z_mm": 9, "cor": "branco"}]}), texto("ok")])
    a.versao_aprovada = "v001"
    a.processar("Troca em 9 mm")
    assert "nunca aconteceria" in resultado(a)["erro"]
    assert not a.projeto.tem_layout("v001")
    assert a.versao_aprovada == "v001"
    assert ouvinte.avisos == []


def test_mudar_a_cor_exige_aprovar_de_novo(config, perfil):
    fatiar = {"material": "PLA", "qualidade": "padrao", "paredes": 3, "preenchimento_pct": 15, "suporte": False, "brim": False}
    a, _ = agente(config, perfil, [chamada("definir_cores", TAMPA, "c1"), chamada("fatiar_peca", fatiar, "c2"), texto("ok")])
    a.versao_aprovada = "v001"
    a.processar("Preta com nomes brancos")
    assert a.versao_aprovada is None
    assert "aprovou" in resultado(a, 1)["erro"]


def test_cores_acompanham_a_versao_nova(config, perfil):
    a, _ = agente(config, perfil, [chamada("definir_cores", TAMPA), texto("ok")])
    a.processar("Preta com nomes brancos")
    pasta = a.projeto.pasta_temporaria()
    (pasta / "script.py").write_text("# outro\n", encoding="utf-8")
    malha_da_tampa().export(pasta / "tampa.stl")
    caixa((60, 40, 30), (0, 0, 15)).export(pasta / "chassi.stl")
    versao = a.projeto.registrar_versao(pasta, None, [medida("tampa"), medida("chassi", 30.0)], "ajuste")
    layout = a.projeto.layout(versao, perfil)
    assert next(i for i in layout.itens if i.parte == "tampa").cores == {"": PRETO}
    assert layout.alturas


# ---------- Na janela ----------


@pytest.fixture
def janela(qtbot, perfil, config, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    janela = JanelaPrincipal(config, perfil, ClienteGravado, verificar_endpoint=False)
    qtbot.addWidget(janela)
    yield janela
    janela.close()


def test_a_mesa_da_janela_mostra_o_que_o_agente_fez(qtbot, janela, config):
    projeto = projeto_com_tampa(config)
    ClienteGravado.respostas = [chamada("definir_cores", TAMPA), texto("Pronto.")]
    janela.abrir_projeto(projeto)
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    janela.painel_chat.campo.setPlainText("Deixa a tampa preta e os nomes em branco")
    janela.painel_chat._enviar()
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)

    painel = janela.painel_3d
    tampa = next(i for i in painel.mesa.itens if i.parte == "tampa")
    assert tampa.cores == {"": PRETO}
    assert painel.arquivo_ativo == tampa.arquivo == 2
    assert painel.mesa.alturas[2] == [{"z": 2.6, "cor": BRANCO}]
    assert painel.editavel
    # O que a janela salva depois não pode desfazer o que o agente gravou.
    painel.mover_selecionado(0, 0)
    janela._ao_mudar_mesa()
    assert projeto.layout("v001", janela.perfil).alturas == {2: [{"z": 2.6, "cor": BRANCO}]}


def test_gcode_antigo_deixa_de_valer_depois_da_cor(qtbot, janela, config):
    projeto = projeto_com_tampa(config, com_chassi=False)
    ClienteGravado.respostas = [chamada("definir_cores", TAMPA), texto("Pronto.")]
    janela.abrir_projeto(projeto)
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    antiga = janela.painel_3d.mesa.assinatura()
    assert janela._fatiamento_vale({"layout": antiga})
    janela.painel_chat.campo.setPlainText("Preta com nomes brancos")
    janela.painel_chat._enviar()
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    assert not janela._fatiamento_vale({"layout": antiga})
    assert janela.pasta_impressao is None


# ---------- No OrcaSlicer ----------


@pytest.mark.orca
@pytest.mark.skipif(localizar_orca() is None, reason="OrcaSlicer não encontrado")
def test_pedido_ao_agente_vira_uma_pausa_no_gcode(config, perfil):
    a, _ = agente(config, perfil, [chamada("definir_cores", TAMPA), texto("Pronto.")], projeto_com_tampa(config, com_chassi=False))
    a.processar("Preta com nomes brancos")
    a.resultado_verificacao = {"aprovado": True, "bloqueios": [], "alertas": []}
    a.aprovar()
    resumo = a.fatiar(ParametrosFatiamento(material="PLA", qualidade="rascunho", paredes=2, preenchimento_pct=10))
    assert resumo["validacao"]["aprovado"]
    assert resumo["validacao"]["trocas_de_filamento"] == 1
    cores = resumo["arquivos"][0]["cores"]
    assert cores["cor_inicial"] == PRETO
    assert cores["trocas"][0]["para"] == BRANCO
    assert "Roteiro de cores" in a.mensagens[-1]["content"]
