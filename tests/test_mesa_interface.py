"""A mesa ligada à janela e ao agente, com a tela de verdade e sem o visualizador 3D."""
import json
import os
from pathlib import Path
from typing import ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QMessageBox

from apoio_conceitos import imagem_png, texto_ia
from forja3d.agente.orquestrador import Orquestrador
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.fatiamento.exportacao import destinos_dos_arquivos, exportar_arquivos, nome_sugerido
from forja3d.ia.cliente import ChamadaFerramenta, ErroIA, RespostaIA
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.layout_mesa import LayoutMesa
from forja3d.projetos.armazenamento import Projeto

TAMANHOS = {"caixa": (40.0, 30.0), "tampa": (40.0, 30.0)}


def criar_versao(projeto, partes=("caixa", "tampa")):
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("x", encoding="utf-8")
    medidas = []
    for nome in partes:
        (pasta / f"{nome}.stl").write_bytes(b"")
        medidas.append({"nome": nome, "stl": f"{nome}.stl", "tamanho": [40.0, 30.0, 10.0], "furos": [], "volume_mm3": 1000.0, "solidos": 1, "valido": True})
    return projeto.registrar_versao(pasta, None, medidas, "teste")


class ClienteSimples:
    enviadas: ClassVar[list] = []

    def __init__(self, _config=None):
        pass

    def conversar(self, mensagens, ferramentas, etapa, cancelar=None):
        type(self).enviadas.append(list(mensagens))
        return texto_ia("Certo.")


@pytest.fixture
def janela(qtbot, tmp_path, perfil):
    ClienteSimples.enviadas = []
    config = Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)
    j = JanelaPrincipal(config, perfil, ClienteSimples, verificar_endpoint=False)
    qtbot.addWidget(j)
    yield j
    j.close()


@pytest.fixture
def aberta(janela, tmp_path):
    projeto = Projeto.criar(tmp_path, "Peça")
    versao = criar_versao(projeto)
    agente = janela.trabalhador.abrir(projeto)
    agente.versao_atual = versao
    agente.resultado_verificacao = {"aprovado": True, "bloqueios": [], "alertas": [], "medidas": []}
    janela.projeto = projeto
    janela.versao_vista = versao
    janela._ver_versao(versao)
    return janela


# ---------- Armazenamento ----------


def test_projeto_sem_layout_salvo_cria_um_organizado(tmp_path, perfil):
    projeto = Projeto.criar(tmp_path)
    versao = criar_versao(projeto)
    assert not projeto.tem_layout(versao)
    assert len(projeto.layout(versao, perfil).itens) == 2
    assert not projeto.tem_layout(versao), "só olhar não grava nada"


def test_layout_salvo_volta_e_a_versao_nova_herda(tmp_path, perfil):
    projeto = Projeto.criar(tmp_path)
    v1 = criar_versao(projeto)
    layout = projeto.layout(v1, perfil)
    layout.mover(layout.itens[0].id, 30, 40)
    projeto.salvar_layout(v1, layout)
    assert projeto.layout(v1, perfil).assinatura() == layout.assinatura()

    v2 = criar_versao(projeto, ("caixa", "tampa", "pino"))
    herdado = projeto.layout(v2, perfil)
    assert next(i for i in herdado.itens if i.id == layout.itens[0].id).x == 30
    assert sorted(i.parte for i in herdado.itens) == ["caixa", "pino", "tampa"]
    assert projeto.layout(v1, perfil).assinatura() == layout.assinatura(), "a versão antiga não muda"


def test_stls_por_parte(tmp_path):
    projeto = Projeto.criar(tmp_path)
    versao = criar_versao(projeto)
    assert projeto.stls_por_parte(versao)["tampa"] == projeto.pasta_versao(versao) / "tampa.stl"


# ---------- Ações na mesa ----------


def selecionar_primeiro(j):
    j.painel_3d.selecionar(j.painel_3d.mesa.itens[0].id)
    return j.painel_3d.mesa.itens[0]


def test_sem_selecao_os_botoes_ficam_desligados(aberta):
    p = aberta.painel_3d
    for chave in ("copiar", "duplicar", "girar", "deletar"):
        assert not p.botoes[chave].isEnabled()
    assert not p.botoes["colar"].isEnabled()
    assert not p.botao_ia.isEnabled()
    selecionar_primeiro(aberta)
    for chave in ("copiar", "duplicar", "girar", "deletar"):
        assert p.botoes[chave].isEnabled()
    assert p.botao_ia.isEnabled()


def test_copiar_e_colar(aberta):
    p = aberta.painel_3d
    item = selecionar_primeiro(aberta)
    p.botoes["copiar"].click()
    assert p.botoes["colar"].isEnabled()
    p.botoes["colar"].click()
    assert len(p.mesa.itens) == 3
    assert p.mesa.item(p.selecionado).parte == item.parte
    assert p.selecionado != item.id
    assert p.mesa.conflitos() == set()


def test_duplicar_e_deletar_ficam_salvos(aberta):
    p = aberta.painel_3d
    selecionar_primeiro(aberta)
    p.botoes["duplicar"].click()
    assert len(aberta.projeto.layout(aberta.versao_vista, aberta.perfil).itens) == 3
    p.botoes["deletar"].click()
    assert p.selecionado is None
    assert len(aberta.projeto.layout(aberta.versao_vista, aberta.perfil).itens) == 2


def test_desfazer_traz_o_item_deletado_de_volta(aberta):
    p = aberta.painel_3d
    item = selecionar_primeiro(aberta)
    p.deletar()
    p.desfazer()
    assert p.mesa.item(item.id) is not None
    assert len(aberta.projeto.layout(aberta.versao_vista, aberta.perfil).itens) == 2


def test_deletar_tudo_impede_preparar_e_avisa(aberta):
    p = aberta.painel_3d
    assert aberta.botao_aprovar.isEnabled()
    for item in list(p.mesa.itens):
        p.selecionar(item.id)
        p.deletar()
    assert p.mesa.itens == []
    assert not aberta.botao_aprovar.isEnabled()
    assert "vazia" in p.rotulo_aviso.text()
    p.adicionar_parte("tampa")
    assert aberta.botao_aprovar.isEnabled()
    assert p.rotulo_aviso.text() == ""


def test_item_sobreposto_avisa(aberta):
    p = aberta.painel_3d
    a = selecionar_primeiro(aberta)
    b = p.mesa.itens[1]
    p.selecionar(b.id)
    p.mover_selecionado(a.x, a.y)
    assert "sobrepostos" in p.rotulo_aviso.text()
    p.organizar()
    assert p.rotulo_aviso.text() == ""


def test_faixa_de_aviso_tem_altura_fixa_para_a_cena_nao_mudar_no_meio_do_arrasto(aberta):
    p = aberta.painel_3d
    assert p.rotulo_aviso.minimumHeight() == p.rotulo_aviso.maximumHeight()
    assert not p.rotulo_aviso.isHidden()


def test_brim_ligado_pode_criar_conflito(aberta):
    p = aberta.painel_3d
    a, b = p.mesa.itens
    p.mesa.mover(a.id, 50, 100)
    p.mesa.mover(b.id, 94, 100)
    p.definir_brim(0.0)
    assert p.rotulo_aviso.text() == ""
    aberta.caixa_brim.setChecked(True)
    assert "brim" in p.rotulo_aviso.text()


def test_mover_para_arquivo_novo_troca_a_tela_e_o_combo(aberta):
    p = aberta.painel_3d
    item = selecionar_primeiro(aberta)
    assert not p.combo_arquivo.isEnabled()
    p.mover_para_arquivo(None)
    assert item.arquivo == 2
    assert p.arquivo_ativo == 2
    assert p.combo_arquivo.count() == 2
    assert p.combo_arquivo.currentText() == "Arquivo 2 de 2"
    assert p.combo_arquivo.isEnabled()
    p.combo_arquivo.setCurrentIndex(0)
    assert p.arquivo_ativo == 1
    assert p.selecionado is None


def test_menu_da_mesa_lista_partes_e_destinos(aberta):
    from PySide6.QtWidgets import QMenu

    p = aberta.painel_3d
    selecionar_primeiro(aberta)
    menu = QMenu()
    p._preencher_menu(menu)
    textos = [a.text() for a in menu.actions()]
    assert "Adicionar parte" in textos
    assert "Mover para o arquivo" in textos
    assert "Organizar a mesa" in textos
    partes = next(a.menu() for a in menu.actions() if a.text() == "Adicionar parte")
    assert sorted(a.text() for a in partes.actions()) == ["caixa", "tampa"]


def test_versao_antiga_e_so_para_ver(aberta):
    atual = aberta.versao_vista
    nova = criar_versao(aberta.projeto)
    aberta.orquestrador.versao_atual = nova
    aberta._ver_versao(atual)
    p = aberta.painel_3d
    assert not p.editavel
    selecionar_primeiro(aberta)
    assert not p.botoes["deletar"].isEnabled()
    p.deletar()
    assert len(p.mesa.itens) == 2


def test_mesa_fica_travada_enquanto_o_agente_trabalha(aberta):
    p = aberta.painel_3d
    selecionar_primeiro(aberta)
    aberta._definir_ocupado(True)
    assert not p.editavel
    assert not p.botoes["duplicar"].isEnabled()
    aberta._definir_ocupado(False)
    assert p.editavel


# ---------- G-code preparado x mesa ----------


def preparar_falso(janela, assinatura=None, arquivos=None):
    """Simula um fatiamento pronto na pasta da versão."""
    projeto, versao = janela.projeto, janela.versao_vista
    pasta = projeto.pasta_impressao(versao, "PLA", "padrao")
    pasta.mkdir(parents=True, exist_ok=True)
    validacao = {"aprovado": True, "bloqueios": [], "alertas": [], "informacoes": [], "camadas": 10, "z_maximo_mm": 10.0, "bico_c": 220.0, "mesa_c": 55.0, "tempo": "2h 0m 0s", "tempo_s": 7200, "filamento_g": 5.0}
    arquivos = arquivos if arquivos is not None else [{"numero": 1, "gcode": "peca.gcode", "itens": ["caixa"], "validacao": validacao}]
    for a in arquivos:
        (pasta / a["gcode"]).write_text("G-code", encoding="utf-8")
    resumo = {"parametros": {"material": "PLA", "qualidade": "padrao"}, "gcode": arquivos[0]["gcode"], "layout": assinatura or janela.painel_3d.mesa.assinatura(), "arquivos": arquivos, "validacao": validacao}
    (pasta / "resumo.json").write_text(json.dumps(resumo), encoding="utf-8")
    return resumo, pasta


def test_mover_um_item_invalida_o_gcode_e_o_tempo(aberta):
    resumo, pasta = preparar_falso(aberta)
    aberta.pasta_impressao = pasta
    aberta._ao_fatiar(resumo)
    assert aberta.botao_exportar.isEnabled()
    assert aberta.rotulo_tempo.isVisibleTo(aberta)
    assert "Impressão: 2h." in aberta.rotulo_tempo.text()
    assert "fica pronta" in aberta.rotulo_tempo.text()

    selecionar_primeiro(aberta)
    aberta.painel_3d.mover_selecionado(30, 30)
    assert not aberta.botao_exportar.isEnabled()
    assert "A mesa mudou" in aberta.rotulo_resumo.text()
    assert not aberta.rotulo_tempo.isVisibleTo(aberta)


def test_gcode_de_outra_mesa_nao_e_liberado_ao_reabrir(aberta):
    aberta.projeto.salvar_layout(aberta.versao_vista, aberta.painel_3d.mesa)
    preparar_falso(aberta, assinatura="de-outra-mesa")
    aberta._ver_versao(aberta.versao_vista)
    assert not aberta.botao_exportar.isEnabled()
    assert aberta.pasta_impressao is None


def test_gcode_da_mesma_mesa_continua_valendo_ao_reabrir(aberta):
    aberta.projeto.salvar_layout(aberta.versao_vista, aberta.painel_3d.mesa)
    preparar_falso(aberta)
    aberta._ver_versao(aberta.versao_vista)
    assert aberta.botao_exportar.isEnabled()
    assert "fica pronta" in aberta.rotulo_tempo.text()


def test_gcode_antigo_sem_assinatura_vale_se_a_mesa_nunca_mudou(aberta):
    _, pasta = preparar_falso(aberta)
    resumo = json.loads((pasta / "resumo.json").read_text(encoding="utf-8"))
    del resumo["layout"]
    (pasta / "resumo.json").write_text(json.dumps(resumo), encoding="utf-8")
    aberta._ver_versao(aberta.versao_vista)
    assert aberta.botao_exportar.isEnabled()
    selecionar_primeiro(aberta)
    aberta.painel_3d.mover_selecionado(30, 30)
    assert not aberta.botao_exportar.isEnabled()


def test_sobreposicao_pede_confirmacao_antes_de_preparar(aberta, monkeypatch):
    p = aberta.painel_3d
    a = selecionar_primeiro(aberta)
    p.selecionar(p.mesa.itens[1].id)
    p.mover_selecionado(a.x, a.y)
    perguntas = []
    monkeypatch.setattr(QMessageBox, "question", lambda *x, **_k: (perguntas.append(x[2]), QMessageBox.StandardButton.No)[1])
    aberta._aprovar_e_fatiar()
    assert "sobrepostos" in perguntas[0]
    assert not aberta.ocupado


# ---------- Exportar vários arquivos ----------


def _dois_arquivos():
    v = {"aprovado": True, "bloqueios": [], "alertas": [], "informacoes": [], "camadas": 1, "z_maximo_mm": 1.0, "bico_c": 220.0, "mesa_c": 55.0, "tempo": "1h 0m 0s"}
    return [{"numero": 1, "gcode": "arquivo_1.gcode", "itens": ["caixa"], "validacao": v}, {"numero": 2, "gcode": "arquivo_2.gcode", "itens": ["tampa"], "validacao": v}]


@pytest.mark.parametrize("nome", ["mini_torre_modular_espelho_io", "a"])
def test_nome_com_numero_nunca_perde_o_numero(nome):
    gerado = nome_sugerido(nome, "PLA", 2)
    assert gerado.endswith("_2_pla.gcode")
    assert len(gerado) <= 26


def test_exportar_varios_copia_todos(aberta, tmp_path):
    _, pasta = preparar_falso(aberta, arquivos=_dois_arquivos())
    destino = tmp_path / "sd"
    destino.mkdir()
    salvos = exportar_arquivos(pasta, destino, "Mini torre", "PLA")
    assert [s.name for s in salvos] == ["mini_torre_1_pla.gcode", "mini_torre_2_pla.gcode"]
    assert all(s.exists() for s in salvos)


def test_exportar_varios_nao_copia_nada_se_um_foi_reprovado(aberta, tmp_path):
    from forja3d.fatiamento.exportacao import ExportacaoBloqueada

    _, pasta = preparar_falso(aberta, arquivos=_dois_arquivos())
    resumo = json.loads((pasta / "resumo.json").read_text(encoding="utf-8"))
    resumo["validacao"]["aprovado"] = False
    (pasta / "resumo.json").write_text(json.dumps(resumo), encoding="utf-8")
    destino = tmp_path / "sd"
    destino.mkdir()
    with pytest.raises(ExportacaoBloqueada):
        exportar_arquivos(pasta, destino, "Mini torre", "PLA")
    assert list(destino.iterdir()) == []


def test_botao_exportar_com_varios_arquivos_escolhe_uma_pasta(aberta, tmp_path, monkeypatch):
    resumo, pasta = preparar_falso(aberta, arquivos=_dois_arquivos())
    aberta.pasta_impressao = pasta
    destino = tmp_path / "sd"
    destino.mkdir()
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    mensagens = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **_k: mensagens.append(a[2]))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(destino))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: pytest.fail("com vários arquivos, pede a pasta"))
    aberta._ao_fatiar(resumo)
    aberta.botao_exportar.click()
    assert sorted(p.name for p in destino.iterdir()) == ["peca_1_pla.gcode", "peca_2_pla.gcode"]
    assert "Arquivos salvos" in mensagens[0]


def test_destinos_avisam_quais_arquivos_serao_criados(aberta, tmp_path):
    _, pasta = preparar_falso(aberta, arquivos=_dois_arquivos())
    destinos = destinos_dos_arquivos(pasta, tmp_path, "Peça", "PETG")
    assert destinos["arquivo_2.gcode"] == tmp_path / "peca_2_petg.gcode"


# ---------- Referência para a IA ----------


def test_enviar_a_ia_coloca_a_parte_no_chat(aberta):
    p, chat = aberta.painel_3d, aberta.painel_chat
    item = selecionar_primeiro(aberta)
    p.botao_ia.click()
    assert chat.referencia == item.parte
    assert chat.chip_referencia.isVisibleTo(chat)
    assert item.parte in chat.rotulo_referencia.text()


def test_tirar_a_referencia(aberta):
    aberta.painel_chat.definir_referencia("tampa")
    aberta.painel_chat.limpar_referencia()
    assert aberta.painel_chat.referencia is None
    assert not aberta.painel_chat.chip_referencia.isVisibleTo(aberta.painel_chat)


def test_nova_peca_tira_a_referencia(aberta):
    aberta.painel_chat.definir_referencia("tampa")
    aberta._nova_peca()
    assert aberta.painel_chat.referencia is None


def test_mensagem_com_referencia_vai_como_parte_e_limpa_o_chip(qtbot, aberta, monkeypatch):
    enviados = []
    monkeypatch.setattr(aberta.orquestrador, "processar_parte", lambda texto, parte: (enviados.append((texto, parte)), "Feito.")[1])
    aberta.painel_chat.definir_referencia("tampa")
    aberta.painel_chat.campo.setPlainText("Aumente os nomes")
    aberta.painel_chat._enviar()
    qtbot.waitUntil(lambda: not aberta.ocupado, timeout=5000)
    assert enviados == [("Aumente os nomes", "tampa")]
    assert aberta.painel_chat.referencia is None
    textos = [r.text() for r in aberta.painel_chat.conteudo.findChildren(QLabel)]
    assert "Sobre a parte tampa: Aumente os nomes" in textos


class ClienteAgente:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.mensagens = []

    def conversar(self, mensagens, ferramentas, etapa, cancelar=None):
        self.mensagens.append(list(mensagens))
        return self.respostas.pop(0)


@pytest.fixture
def config(tmp_path):
    return Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)


def agente_com_versao(config, perfil, cliente):
    projeto = Projeto.criar(config.pasta_projetos)
    versao = criar_versao(projeto)
    agente = Orquestrador(config, perfil, cliente, projeto)
    agente.versao_atual = versao
    return agente, projeto, versao


def test_agente_recebe_a_parte_as_medidas_e_as_vistas(config, perfil, monkeypatch, tmp_path):
    vista = tmp_path / "frontal.png"
    vista.write_bytes(imagem_png())
    chamadas = []

    def renderizar_falso(stls, pasta, cancelar=None):
        chamadas.append((stls, pasta))
        return [vista]

    monkeypatch.setattr("forja3d.agente.orquestrador.renderizar", renderizar_falso)
    cliente = ClienteAgente(texto_ia("Vou ajustar."))
    agente, projeto, versao = agente_com_versao(config, perfil, cliente)
    assert agente.processar_parte("Aumente os nomes", "tampa") == "Vou ajustar."

    assert chamadas[0][0] == [projeto.pasta_versao(versao) / "tampa.stl"], "só a parte selecionada"
    enviadas = cliente.mensagens[0]
    texto = next(m["content"] for m in enviadas if isinstance(m["content"], str) and m["content"].startswith("[PARTE SELECIONADA]"))
    assert "Parte: tampa" in texto
    assert "Tamanho: 40 x 30 x 10 mm" in texto
    assert "Outras partes, que devem continuar iguais: caixa" in texto
    assert "Pedido: Aumente os nomes" in texto
    imagens = [m for m in enviadas if isinstance(m["content"], list)]
    assert len(imagens) == 1
    assert sum(p["type"] == "image_url" for p in imagens[0]["content"]) == 1
    assert projeto.ler_conversa()[0]["texto"] == "Sobre a parte tampa: Aumente os nomes"
    assert "base64" not in (projeto.pasta / "historico.json").read_text(encoding="utf-8")


def test_sem_conseguir_renderizar_o_pedido_segue_sem_imagem(config, perfil, monkeypatch):
    def falha(*_a, **_k):
        raise RuntimeError("sem render")

    monkeypatch.setattr("forja3d.agente.orquestrador.renderizar", falha)
    cliente = ClienteAgente(texto_ia("Ok."))
    agente, _, _ = agente_com_versao(config, perfil, cliente)
    assert agente.processar_parte("Ajuste", "tampa") == "Ok."
    assert not any(isinstance(m["content"], list) for m in cliente.mensagens[0])


def test_parte_que_nao_existe_mais_vira_pedido_comum(config, perfil):
    cliente = ClienteAgente(texto_ia("Ok."))
    agente, _, _ = agente_com_versao(config, perfil, cliente)
    agente.processar_parte("Ajuste", "sumiu")
    assert not any("[PARTE SELECIONADA]" in str(m["content"]) for m in cliente.mensagens[0] if m["role"] == "user")


# ---------- O agente não apresenta peça sem verificar ----------


def _chamada_cad(codigo):
    argumentos = json.dumps({"codigo": codigo, "resumo_alteracao": "um"})
    return RespostaIA("", [ChamadaFerramenta("c1", "executar_cad", argumentos)], {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "executar_cad", "arguments": argumentos}}]})


@pytest.mark.cad
def test_peca_apresentada_sem_verificar_e_verificada_pelo_codigo(config, perfil):
    codigo = (Path(__file__).parent / "dados" / "suporte_l_corrigido.py").read_text(encoding="utf-8")
    especificacao = {"nome": "suporte_l", "finalidade": "t", "material": "PLA", "carga": "leve", "dimensoes": {"x_mm": 40, "y_mm": 30, "z_mm": 34}, "pecas": 1, "furos": [{"quantidade": 2, "diametro_nominal_mm": 4, "diametro_modelado_mm": 4, "eixo": "Z", "passante": True}], "orientacao_impressao": "base na mesa", "premissas": []}
    bruto = json.dumps(especificacao)
    definir = RespostaIA("", [ChamadaFerramenta("c0", "definir_especificacao", bruto)], {"role": "assistant", "tool_calls": [{"id": "c0", "type": "function", "function": {"name": "definir_especificacao", "arguments": bruto}}]})
    cliente = ClienteAgente(definir, _chamada_cad(codigo), texto_ia("Está pronta."))
    agente = Orquestrador(config, perfil, cliente, Projeto.criar(config.pasta_projetos))
    assert agente.processar("Suporte") == "Está pronta."
    assert agente.resultado_verificacao is not None, "sem isto, o botão de aprovar nunca liberaria"
    assert agente.pode_aprovar()


@pytest.mark.cad
def test_reprovada_na_verificacao_automatica_volta_para_o_modelo(config, perfil):
    errado = (Path(__file__).parent / "dados" / "suporte_l_modelo_errado.py").read_text(encoding="utf-8")
    especificacao = {"nome": "suporte_l", "finalidade": "t", "material": "PLA", "carga": "leve", "dimensoes": {"x_mm": 40, "y_mm": 30, "z_mm": 34}, "pecas": 1, "furos": [{"quantidade": 2, "diametro_nominal_mm": 4, "diametro_modelado_mm": 4, "eixo": "Z", "passante": True}], "orientacao_impressao": "base na mesa", "premissas": []}
    bruto = json.dumps(especificacao)
    definir = RespostaIA("", [ChamadaFerramenta("c0", "definir_especificacao", bruto)], {"role": "assistant", "tool_calls": [{"id": "c0", "type": "function", "function": {"name": "definir_especificacao", "arguments": bruto}}]})
    cliente = ClienteAgente(definir, _chamada_cad(errado), texto_ia("Está pronta."), texto_ia("Tem um problema nos furos."))
    agente = Orquestrador(config, perfil, cliente, Projeto.criar(config.pasta_projetos))
    assert agente.processar("Suporte") == "Tem um problema nos furos."
    assert any("reprovou a peça" in str(m.get("content")) for m in cliente.mensagens[-1])
    assert not agente.pode_aprovar()


def test_layout_da_interface_e_o_mesmo_tipo_do_armazenamento(aberta):
    assert isinstance(aberta.painel_3d.mesa, LayoutMesa)


# ---------- Erro da IA ----------


class ClienteQueFalha(ClienteAgente):
    def conversar(self, mensagens, ferramentas, etapa, cancelar=None):
        self.mensagens.append(list(mensagens))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def test_reenviar_depois_de_um_erro_nao_repete_o_pedido(config, perfil):
    cliente = ClienteQueFalha(ErroIA("O endpoint respondeu com erro 503."), texto_ia("Certo."))
    agente = Orquestrador(config, perfil, cliente, Projeto.criar(config.pasta_projetos))
    with pytest.raises(ErroIA):
        agente.processar("Tampe a frente")
    assert agente.processar("Tampe a frente") == "Certo."
    pedidos = [m for m in cliente.mensagens[-1] if m.get("content") == "Tampe a frente"]
    assert len(pedidos) == 1


def test_erro_fica_registrado_na_conversa_e_aparece_ao_reabrir(qtbot, janela, config, perfil):
    cliente = ClienteQueFalha(ErroIA("O endpoint respondeu com erro 503."))
    projeto = Projeto.criar(janela.config.pasta_projetos, "Peça")
    agente = Orquestrador(janela.config, perfil, cliente, projeto)
    with pytest.raises(ErroIA):
        agente.processar("Tampe a frente")
    assert [r["papel"] for r in projeto.ler_conversa()] == ["usuario", "erro"]
    janela.abrir_projeto(Projeto(projeto.pasta))
    assert any(b.objectName() == "bolhaErro" for b in janela.painel_chat.conteudo.findChildren(QFrame))


def test_erro_depois_de_uma_resposta_mantem_o_historico_valido(config, perfil):
    cliente = ClienteQueFalha(_chamada_invalida(), ErroIA("Caiu."))
    agente = Orquestrador(config, perfil, cliente, Projeto.criar(config.pasta_projetos))
    with pytest.raises(ErroIA):
        agente.processar("Pedido")
    assert any(m.get("role") == "tool" for m in agente.mensagens), "a chamada e a resposta dela continuam juntas"


def _chamada_invalida():
    return RespostaIA("", [ChamadaFerramenta("c9", "nao_existe", "{}")], {"role": "assistant", "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "nao_existe", "arguments": "{}"}}]})
