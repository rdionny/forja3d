"""Barra das mensagens, desfazer e refazer da mesa e menu da lista de peças."""
import json
import os
import sys
import time
from typing import ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QInputDialog, QMessageBox

from apoio_conceitos import texto_ia
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.interface import janela_principal
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.interface.leitura import LeitorEmVoz, comando_de_voz, texto_para_fala
from forja3d.projetos.armazenamento import Projeto

# Nenhum teste pode falar pelos alto-falantes do usuário.
LEITOR_MUDO = (sys.executable, ["-c", "import sys; sys.stdin.buffer.read()"])


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
        ultimo = next(m["content"] for m in reversed(mensagens) if m["role"] == "user")
        return texto_ia(f"Resposta a: {ultimo}")


@pytest.fixture
def janela(qtbot, tmp_path, perfil):
    ClienteSimples.enviadas = []
    config = Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)
    j = JanelaPrincipal(config, perfil, ClienteSimples, verificar_endpoint=False)
    j.painel_chat.leitor._comando = LEITOR_MUDO
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


def enviar(qtbot, j, texto):
    j.painel_chat.campo.setPlainText(texto)
    j.painel_chat.botao_enviar.click()
    qtbot.waitUntil(lambda: not j.ocupado, timeout=5000)


def responder_perguntas(monkeypatch, resposta=QMessageBox.StandardButton.Yes):
    perguntas = []

    def perguntar(_janela, _titulo, texto, *_a, **_k):
        perguntas.append(texto)
        return resposta

    monkeypatch.setattr(QMessageBox, "question", perguntar)
    return perguntas


def textos_das_bolhas(j):
    return [b.texto for b in j.painel_chat.bolhas]


def pedidos_para_o_modelo(j):
    return [m["content"] for m in j.orquestrador.mensagens if m["role"] == "user"]


# ---------- Barra das mensagens ----------


def test_pergunta_e_resposta_tem_as_quatro_acoes(qtbot, janela):
    enviar(qtbot, janela, "oi")
    pergunta, resposta = janela.painel_chat.bolhas
    assert set(pergunta.botoes) == {"copiar", "editar", "reenviar", "ler"}
    assert set(resposta.botoes) == {"copiar", "editar", "reenviar", "ler"}


def test_erro_so_copia_e_le(qtbot, janela):
    enviar(qtbot, janela, "oi")
    janela.painel_chat.adicionar("erro", "A IA não respondeu.")
    assert set(janela.painel_chat.bolhas[-1].botoes) == {"copiar", "ler"}


def test_mensagem_de_conceito_nao_se_edita(janela, tmp_path):
    imagem = tmp_path / "conceito.png"
    imagem.write_bytes(b"")
    chat = janela.painel_chat
    chat.adicionar("usuario", "Escolhi o conceito: Tampa.", imagem)
    chat.adicionar("agente", "Certo.")
    assert "editar" not in chat.bolhas[0].botoes and "reenviar" in chat.bolhas[0].botoes
    assert "editar" not in chat.bolhas[1].botoes


def test_copiar_nao_leva_as_linhas_em_branco_da_resposta(janela):
    chat = janela.painel_chat
    chat.adicionar("agente", "\n\nazul\n")
    chat.bolhas[0].botoes["copiar"].click()
    assert QGuiApplication.clipboard().text() == "azul"


def test_copiar_leva_o_texto_com_a_marcacao_original(qtbot, janela):
    chat = janela.painel_chat
    chat.adicionar("usuario", "pergunta")
    chat.adicionar("agente", "Use **M4** aqui.")
    chat.bolhas[1].botoes["copiar"].click()
    assert QGuiApplication.clipboard().text() == "Use **M4** aqui."
    assert chat.bolhas[1].botoes["copiar"].toolTip() == "Copiado"


def test_agente_trabalhando_trava_so_o_que_mexe_na_conversa(janela):
    chat = janela.painel_chat
    chat.adicionar("usuario", "pergunta")
    chat.definir_ocupado(True)
    chat.adicionar("agente", "resposta")
    for bolha in chat.bolhas:
        assert not bolha.botoes["editar"].isEnabled() and not bolha.botoes["reenviar"].isEnabled()
        assert bolha.botoes["copiar"].isEnabled() and bolha.botoes["ler"].isEnabled()
    chat.definir_ocupado(False)
    assert chat.bolhas[1].botoes["editar"].isEnabled()


# ---------- Enviar de novo e editar ----------


def test_enviar_de_novo_refaz_a_ultima_resposta_sem_repetir_o_pedido(qtbot, janela, monkeypatch):
    perguntas = responder_perguntas(monkeypatch)
    enviar(qtbot, janela, "primeiro")
    enviar(qtbot, janela, "segundo")
    janela.painel_chat.bolhas[2].botoes["reenviar"].click()
    qtbot.waitUntil(lambda: not janela.ocupado and len(janela.painel_chat.bolhas) == 4, timeout=5000)
    assert perguntas == [], "a última mensagem não perde nada além da própria resposta"
    assert textos_das_bolhas(janela) == ["primeiro", "Resposta a: primeiro", "segundo", "Resposta a: segundo"]
    assert pedidos_para_o_modelo(janela)[-2:] == ["primeiro", "segundo"]
    assert [r["texto"] for r in janela.projeto.ler_conversa()] == textos_das_bolhas(janela)


def test_enviar_de_novo_pela_resposta_reenvia_a_pergunta_dela(qtbot, janela):
    enviar(qtbot, janela, "pergunta")
    janela.painel_chat.bolhas[1].botoes["reenviar"].click()
    qtbot.waitUntil(lambda: not janela.ocupado and len(janela.painel_chat.bolhas) == 2, timeout=5000)
    assert pedidos_para_o_modelo(janela) == ["pergunta"]
    assert len(ClienteSimples.enviadas) == 2


def test_voltar_antes_de_outros_pedidos_pede_confirmacao(qtbot, janela, monkeypatch):
    perguntas = responder_perguntas(monkeypatch, QMessageBox.StandardButton.No)
    enviar(qtbot, janela, "primeiro")
    enviar(qtbot, janela, "segundo")
    janela.painel_chat.bolhas[0].botoes["reenviar"].click()
    assert "pedido seguinte" in perguntas[0]
    assert len(janela.painel_chat.bolhas) == 4, "disse não: nada muda"
    assert pedidos_para_o_modelo(janela) == ["primeiro", "segundo"]

    responder_perguntas(monkeypatch)
    janela.painel_chat.bolhas[0].botoes["reenviar"].click()
    qtbot.waitUntil(lambda: not janela.ocupado and len(janela.painel_chat.bolhas) == 2, timeout=5000)
    assert textos_das_bolhas(janela) == ["primeiro", "Resposta a: primeiro"]
    assert pedidos_para_o_modelo(janela) == ["primeiro"]


def test_editar_troca_a_mensagem_e_a_resposta(qtbot, janela):
    enviar(qtbot, janela, "cubo de 20 mm")
    pergunta = janela.painel_chat.bolhas[0]
    pergunta.botoes["editar"].click()
    assert pergunta.campo_edicao.toPlainText() == "cubo de 20 mm"
    pergunta.campo_edicao.setPlainText("cubo de 30 mm")
    pergunta.botao_concluir.click()
    qtbot.waitUntil(lambda: not janela.ocupado and len(janela.painel_chat.bolhas) == 2, timeout=5000)
    assert textos_das_bolhas(janela) == ["cubo de 30 mm", "Resposta a: cubo de 30 mm"]
    assert pedidos_para_o_modelo(janela) == ["cubo de 30 mm"]


def test_editar_pela_resposta_abre_a_pergunta(qtbot, janela):
    enviar(qtbot, janela, "cubo")
    pergunta, resposta = janela.painel_chat.bolhas
    resposta.botoes["editar"].click()
    assert not pergunta.caixa_edicao.isHidden()
    assert resposta.caixa_edicao is None


def test_cancelar_a_edicao_nao_muda_nada(qtbot, janela):
    enviar(qtbot, janela, "cubo")
    pergunta = janela.painel_chat.bolhas[0]
    pergunta.botoes["editar"].click()
    pergunta.campo_edicao.setPlainText("outra coisa")
    pergunta.cancelar_edicao()
    assert pergunta.caixa_edicao.isHidden() and not pergunta.rotulo.isHidden()
    assert textos_das_bolhas(janela) == ["cubo", "Resposta a: cubo"]
    pergunta.botoes["editar"].click()
    assert pergunta.campo_edicao.toPlainText() == "cubo", "abrir de novo começa do texto salvo"


def test_edicao_vazia_nao_envia(qtbot, janela):
    enviar(qtbot, janela, "cubo")
    pergunta = janela.painel_chat.bolhas[0]
    pergunta.botoes["editar"].click()
    pergunta.campo_edicao.setPlainText("   ")
    pergunta.botao_concluir.click()
    assert len(ClienteSimples.enviadas) == 1


def test_referencia_marcada_nao_vaza_para_o_reenvio(qtbot, aberta, monkeypatch):
    enviar(qtbot, aberta, "cubo")
    aberta.painel_chat.definir_referencia("tampa")
    aberta.painel_chat.bolhas[0].botoes["reenviar"].click()
    qtbot.waitUntil(lambda: not aberta.ocupado and len(aberta.painel_chat.bolhas) == 2, timeout=5000)
    assert aberta.painel_chat.referencia == "tampa"
    assert textos_das_bolhas(aberta)[0] == "cubo"


def test_pedido_sobre_uma_parte_volta_a_falar_daquela_parte(qtbot, aberta, monkeypatch):
    enviados = []
    monkeypatch.setattr(aberta.orquestrador, "processar_parte", lambda texto, parte: (enviados.append((texto, parte)), "Feito.")[1])
    aberta.projeto.registrar_conversa("usuario", "Sobre a parte tampa: aumente")
    aberta.painel_chat.adicionar("usuario", "Sobre a parte tampa: aumente")
    aberta.painel_chat.bolhas[0].botoes["reenviar"].click()
    qtbot.waitUntil(lambda: not aberta.ocupado and bool(enviados), timeout=5000)
    assert enviados == [("aumente", "tampa")]
    assert textos_das_bolhas(aberta)[0] == "Sobre a parte tampa: aumente"


def test_conceito_escolhido_pode_ser_reenviado(qtbot, aberta, tmp_path, monkeypatch):
    conceito = {"imagem": "conceitos/a.png", "opcao": {"titulo": "Tampa"}}
    monkeypatch.setattr(aberta.projeto, "conceito_escolhido", lambda: conceito)
    recebidos = []
    monkeypatch.setattr(aberta.orquestrador, "processar_conceito", lambda c: (recebidos.append(c), "Certo.")[1])
    aberta.projeto.registrar_conversa("usuario", "Escolhi o conceito: Tampa.", "conceitos/a.png")
    aberta.painel_chat.adicionar("usuario", "Escolhi o conceito: Tampa.", aberta.projeto.pasta / "conceitos" / "a.png")
    aberta.painel_chat.bolhas[0].botoes["reenviar"].click()
    qtbot.waitUntil(lambda: not aberta.ocupado and bool(recebidos), timeout=5000)
    assert recebidos == [conceito]
    assert textos_das_bolhas(aberta)[0] == "Escolhi o conceito: Tampa."


def test_conceito_trocado_nao_pode_ser_reenviado(aberta, monkeypatch):
    avisos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **_k: avisos.append(a[2]))
    aberta.projeto.registrar_conversa("usuario", "Escolhi o conceito: Tampa.", "conceitos/a.png")
    aberta.painel_chat.adicionar("usuario", "Escolhi o conceito: Tampa.", aberta.projeto.pasta / "conceitos" / "a.png")
    aberta.painel_chat.bolhas[0].botoes["reenviar"].click()
    assert avisos and aberta.projeto.pedidos(), "avisou, e a conversa continua igual"
    assert not aberta.ocupado


def test_conversa_reaberta_continua_refazendo(qtbot, janela, tmp_path):
    enviar(qtbot, janela, "primeiro")
    projeto = Projeto(janela.projeto.pasta)
    janela._nova_peca()
    janela.abrir_projeto(projeto)
    assert textos_das_bolhas(janela) == ["primeiro", "Resposta a: primeiro"]
    janela.painel_chat.bolhas[1].botoes["reenviar"].click()
    qtbot.waitUntil(lambda: not janela.ocupado and len(janela.painel_chat.bolhas) == 2, timeout=5000)
    assert pedidos_para_o_modelo(janela) == ["primeiro"]


# ---------- Ler em voz alta ----------


def test_texto_para_fala_tira_as_marcas():
    texto = "## Medidas\n- **Base** de `40 mm`\n1. Parte espelho_io_traseiro\n\n```python\nx = 1\n```\n[manual](http://x)"
    fala = texto_para_fala(texto)
    for sobra in ("#", "*", "`", "_", "x = 1", "http"):
        assert sobra not in fala
    assert "Base de 40 mm" in fala and "espelho io traseiro" in fala and "manual" in fala


def test_comando_de_voz_nao_leva_o_texto_e_usa_caminho_completo():
    programa, argumentos = comando_de_voz()
    assert os.path.isabs(programa)
    assert programa.lower().endswith("powershell.exe")
    assert "-EncodedCommand" in argumentos


def test_leitor_entrega_o_texto_com_acentos(qtbot, tmp_path):
    destino = tmp_path / "lido.txt"
    leitor = LeitorEmVoz(comando=(sys.executable, ["-c", "import sys, pathlib; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())", str(destino)]))
    with qtbot.waitSignal(leitor.terminou, timeout=15000):
        leitor.ler("Peça em ação: 40 × 30 mm")
    assert destino.read_bytes().decode("utf-8") == "Peça em ação: 40 × 30 mm"
    assert not leitor.lendo()


def test_leitor_avisa_quando_a_voz_falha(qtbot):
    leitor = LeitorEmVoz(comando=(sys.executable, ["-c", "raise SystemExit(3)"]))
    with qtbot.waitSignal(leitor.falhou, timeout=15000):
        leitor.ler("oi")
    assert not leitor.lendo()


def test_leitor_avisa_quando_o_programa_nao_abre(qtbot, tmp_path):
    leitor = LeitorEmVoz(comando=(str(tmp_path / "nao_existe.exe"), []))
    with qtbot.waitSignal(leitor.falhou, timeout=5000):
        leitor.ler("oi")
    assert not leitor.lendo()


def test_parar_corta_a_fala_sem_avisar_de_erro(qtbot):
    leitor = LeitorEmVoz(comando=(sys.executable, ["-c", "import time; time.sleep(30)"]))
    falhas = []
    leitor.falhou.connect(falhas.append)
    inicio = time.monotonic()
    leitor.ler("oi")
    assert leitor.lendo()
    leitor.parar()
    qtbot.wait(100)
    assert not leitor.lendo() and not falhas
    assert time.monotonic() - inicio < 10


def test_botao_de_ler_vira_parar_e_volta(qtbot, janela):
    chat = janela.painel_chat
    chat.adicionar("agente", "Certo.")
    chat.adicionar("usuario", "ok")
    chat.leitor._comando = (sys.executable, ["-c", "import time; time.sleep(30)"])
    primeira, segunda = chat.bolhas
    primeira.botoes["ler"].click()
    assert primeira.botoes["ler"].toolTip() == "Parar de ler"
    segunda.botoes["ler"].click()
    assert primeira.botoes["ler"].toolTip() == "Ler em voz alta", "só uma mensagem é lida por vez"
    assert segunda.botoes["ler"].toolTip() == "Parar de ler"
    segunda.botoes["ler"].click()
    assert segunda.botoes["ler"].toolTip() == "Ler em voz alta" and not chat.leitor.lendo()


def test_fim_da_fala_volta_o_botao(qtbot, janela):
    chat = janela.painel_chat
    chat.adicionar("agente", "Certo.")
    chat.bolhas[0].botoes["ler"].click()
    qtbot.waitUntil(lambda: chat.bolhas[0].botoes["ler"].toolTip() == "Ler em voz alta", timeout=15000)


def test_falha_na_fala_aparece_no_chat(qtbot, janela):
    from PySide6.QtWidgets import QLabel

    chat = janela.painel_chat
    chat.leitor._comando = (sys.executable, ["-c", "raise SystemExit(3)"])
    chat.adicionar("agente", "Certo.")
    chat.bolhas[0].botoes["ler"].click()
    qtbot.waitUntil(lambda: any("voz alta" in r.text() for r in chat.conteudo.findChildren(QLabel)), timeout=15000)
    assert chat.bolhas[0].botoes["ler"].toolTip() == "Ler em voz alta"


def test_trocar_de_peca_para_a_leitura(janela):
    chat = janela.painel_chat
    chat.leitor._comando = (sys.executable, ["-c", "import time; time.sleep(30)"])
    chat.adicionar("agente", "Certo.")
    chat.bolhas[0].botoes["ler"].click()
    janela._nova_peca()
    assert not chat.leitor.lendo() and chat.bolhas == []


# ---------- Desfazer e refazer no topo ----------


def selecionar_primeiro(j):
    j.painel_3d.selecionar(j.painel_3d.mesa.itens[0].id)
    return j.painel_3d.mesa.itens[0]


def opacidade_total(icone, modo):
    imagem = icone.pixmap(24, 24, modo).toImage()
    return sum(imagem.pixelColor(x, y).alpha() for x in range(imagem.width()) for y in range(imagem.height()))


def test_icone_desligado_e_bem_mais_apagado(aberta):
    icone = aberta.botao_desfazer.icon()
    ligado, desligado = opacidade_total(icone, QIcon.Mode.Normal), opacidade_total(icone, QIcon.Mode.Disabled)
    assert 0 < desligado < ligado * 0.5


def test_icones_comecam_desligados_e_seguem_a_mesa(aberta):
    assert not aberta.botao_desfazer.isEnabled() and not aberta.botao_refazer.isEnabled()
    selecionar_primeiro(aberta)
    aberta.painel_3d.girar()
    assert aberta.botao_desfazer.isEnabled() and not aberta.botao_refazer.isEnabled()


def test_desfazer_e_refazer_pelos_icones(aberta):
    p = aberta.painel_3d
    item = selecionar_primeiro(aberta)
    p.mover_selecionado(30, 40)
    assert (item.x, item.y) == (30, 40)
    aberta.botao_desfazer.click()
    voltou = p.mesa.item(item.id)
    assert (voltou.x, voltou.y) != (30, 40)
    assert aberta.botao_refazer.isEnabled() and not aberta.botao_desfazer.isEnabled()
    aberta.botao_refazer.click()
    refeito = p.mesa.item(item.id)
    assert (refeito.x, refeito.y) == (30, 40)
    assert aberta.botao_desfazer.isEnabled() and not aberta.botao_refazer.isEnabled()


def test_refazer_grava_a_posicao_no_projeto(aberta):
    p = aberta.painel_3d
    item = selecionar_primeiro(aberta)
    p.mover_selecionado(30, 40)
    aberta.botao_desfazer.click()
    aberta.botao_refazer.click()
    dados = json.loads((aberta.projeto.pasta_versao(aberta.versao_vista) / "layout.json").read_text(encoding="utf-8"))
    guardado = next(i for i in dados["itens"] if i["id"] == item.id)
    assert (guardado["x"], guardado["y"]) == (30, 40)


def test_alteracao_nova_apaga_o_que_podia_refazer(aberta):
    p = aberta.painel_3d
    selecionar_primeiro(aberta)
    p.mover_selecionado(30, 40)
    p.desfazer()
    assert aberta.botao_refazer.isEnabled()
    selecionar_primeiro(aberta)
    p.girar()
    assert not aberta.botao_refazer.isEnabled()
    p.refazer()
    assert p.mesa.itens[0].giro == 90, "refazer sem nada para refazer não muda a mesa"


def test_varios_desfazer_e_refazer_na_ordem(aberta):
    p = aberta.painel_3d
    item = selecionar_primeiro(aberta)
    origem = (item.x, item.y)
    p.mover_selecionado(30, 40)
    p.mover_selecionado(50, 60)
    p.desfazer()
    p.desfazer()
    assert (p.mesa.item(item.id).x, p.mesa.item(item.id).y) == origem
    p.refazer()
    assert (p.mesa.item(item.id).x, p.mesa.item(item.id).y) == (30, 40)
    p.refazer()
    assert (p.mesa.item(item.id).x, p.mesa.item(item.id).y) == (50, 60)


def test_refazer_traz_de_volta_o_que_foi_deletado_e_desfeito(aberta):
    p = aberta.painel_3d
    selecionar_primeiro(aberta)
    p.deletar()
    assert len(p.mesa.itens) == 1
    p.desfazer()
    assert len(p.mesa.itens) == 2
    p.refazer()
    assert len(p.mesa.itens) == 1


def test_agente_trabalhando_desliga_os_icones(aberta):
    p = aberta.painel_3d
    selecionar_primeiro(aberta)
    p.girar()
    p.desfazer()
    p.refazer()
    aberta._definir_ocupado(True)
    assert not aberta.botao_desfazer.isEnabled() and not aberta.botao_refazer.isEnabled()
    aberta.botao_desfazer.click()
    assert p.mesa.itens[0].giro == 90, "travado, o clique não muda nada"
    aberta._definir_ocupado(False)
    assert aberta.botao_desfazer.isEnabled()


def test_menu_da_mesa_tem_refazer(aberta):
    from PySide6.QtWidgets import QMenu

    p = aberta.painel_3d
    selecionar_primeiro(aberta)
    p.girar()
    p.desfazer()
    menu = QMenu()
    p._preencher_menu(menu)
    acoes = {a.text(): a for a in menu.actions()}
    assert acoes["Refazer"].isEnabled()


def test_atalhos_de_refazer_estao_registrados(aberta):
    from PySide6.QtGui import QKeySequence, QShortcut

    teclas = {a.key().toString() for a in aberta.painel_3d.findChildren(QShortcut)}
    assert {QKeySequence("Ctrl+Y").toString(), QKeySequence("Ctrl+Shift+Z").toString()} <= teclas


# ---------- Lista de peças: armazenamento ----------


def criar_em_ordem(pasta, *titulos):
    projetos = []
    for numero, titulo in enumerate(titulos):
        projeto = Projeto.criar(pasta, titulo)
        os.utime(projeto.pasta / "projeto.json", (1000 + numero, 1000 + numero))
        projetos.append(projeto)
    return projetos


def test_fixado_vem_primeiro(tmp_path):
    velho, _medio, _novo = criar_em_ordem(tmp_path, "velho", "medio", "novo")
    velho.fixar(True)
    assert [p.titulo for p in Projeto.listar(tmp_path)] == ["velho", "novo", "medio"]
    velho.fixar(False)
    assert [p.titulo for p in Projeto.listar(tmp_path)] == ["novo", "medio", "velho"]


def test_renomear_e_fixar_nao_mudam_a_ordem(tmp_path):
    velho, medio, _novo = criar_em_ordem(tmp_path, "velho", "medio", "novo")
    medio.renomear("renomeado")
    assert [p.titulo for p in Projeto.listar(tmp_path)] == ["novo", "renomeado", "velho"]
    velho.fixar(True)
    velho.fixar(False)
    assert [p.titulo for p in Projeto.listar(tmp_path)] == ["novo", "renomeado", "velho"]


def test_varios_fixados_ficam_em_ordem_de_uso(tmp_path):
    velho, medio, _novo = criar_em_ordem(tmp_path, "velho", "medio", "novo")
    velho.fixar(True)
    medio.fixar(True)
    assert [p.titulo for p in Projeto.listar(tmp_path)] == ["medio", "velho", "novo"]


def test_nome_escolhido_vale_mais_que_o_do_agente(tmp_path):
    projeto = Projeto.criar(tmp_path, "Automático")
    projeto.titulo_automatico("Sugestão 1")
    assert projeto.titulo == "Sugestão 1"
    projeto.renomear("Meu nome")
    projeto.titulo_automatico("Sugestão 2")
    assert projeto.titulo == "Meu nome"
    assert Projeto(projeto.pasta).titulo == "Meu nome"


# ---------- Lista de peças: menu ----------


def projeto_da_lista(j, titulo):
    lista = j.painel_versoes.lista_projetos
    for indice in range(lista.count()):
        if lista.item(indice).text() == titulo:
            return lista.item(indice).data(Qt.ItemDataRole.UserRole)
    raise AssertionError(titulo)


def titulos_da_lista(j):
    lista = j.painel_versoes.lista_projetos
    return [lista.item(i).text() for i in range(lista.count())]


def acoes_do_menu(j, projeto):
    return {a.text(): a for a in j.painel_versoes._menu_do_projeto(projeto).actions() if a.text()}


def test_menu_mostra_as_acoes(aberta):
    aberta._recarregar_projetos()
    projeto = projeto_da_lista(aberta, "Peça")
    assert set(acoes_do_menu(aberta, projeto)) == {"Renomear", "Fixar no topo", "Salvar como…", "Apagar"}
    projeto.fixar(True)
    assert "Desafixar do topo" in acoes_do_menu(aberta, projeto)


def test_renomear_pelo_menu(janela, tmp_path, monkeypatch):
    Projeto.criar(tmp_path, "Antigo")
    janela._recarregar_projetos()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("  Novo nome  ", True))
    acoes_do_menu(janela, projeto_da_lista(janela, "Antigo"))["Renomear"].trigger()
    assert titulos_da_lista(janela) == ["Novo nome"]


def test_desistir_de_renomear_nao_muda_nada(janela, tmp_path, monkeypatch):
    Projeto.criar(tmp_path, "Antigo")
    janela._recarregar_projetos()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Outro", False))
    acoes_do_menu(janela, projeto_da_lista(janela, "Antigo"))["Renomear"].trigger()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("   ", True))
    acoes_do_menu(janela, projeto_da_lista(janela, "Antigo"))["Renomear"].trigger()
    assert titulos_da_lista(janela) == ["Antigo"]


def test_renomear_a_peca_aberta_sobrevive_ao_agente(aberta, monkeypatch):
    aberta._recarregar_projetos()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Mini torre", True))
    acoes_do_menu(aberta, projeto_da_lista(aberta, "Peça"))["Renomear"].trigger()
    assert aberta.rotulo_projeto.text() == "Mini torre"
    aberta.orquestrador._salvar_estado()
    aberta.projeto.titulo_automatico("Sugestão do agente")
    aberta.projeto.salvar_estado()
    assert Projeto(aberta.projeto.pasta).titulo == "Mini torre"
    assert titulos_da_lista(aberta) == ["Mini torre"]


def test_fixar_a_peca_aberta_nao_se_perde_ao_salvar(aberta):
    aberta._recarregar_projetos()
    acoes_do_menu(aberta, projeto_da_lista(aberta, "Peça"))["Fixar no topo"].trigger()
    aberta.orquestrador._salvar_estado()
    assert Projeto(aberta.projeto.pasta).fixado


def test_fixar_pelo_menu_sobe_e_marca_com_icone(janela, tmp_path):
    criar_em_ordem(tmp_path, "velho", "novo")
    janela._recarregar_projetos()
    assert titulos_da_lista(janela) == ["novo", "velho"]
    acoes_do_menu(janela, projeto_da_lista(janela, "velho"))["Fixar no topo"].trigger()
    assert titulos_da_lista(janela) == ["velho", "novo"]
    lista = janela.painel_versoes.lista_projetos
    assert not lista.item(0).icon().isNull() and lista.item(1).icon().isNull()


def test_apagar_manda_para_a_lixeira(janela, tmp_path, monkeypatch):
    responder_perguntas(monkeypatch)
    enviados = []
    monkeypatch.setattr(janela_principal, "mandar_para_a_lixeira", lambda pasta: (enviados.append(pasta), True)[1])
    projeto = Projeto.criar(tmp_path, "Para apagar")
    janela._recarregar_projetos()
    acoes_do_menu(janela, projeto_da_lista(janela, "Para apagar"))["Apagar"].trigger()
    assert enviados == [projeto.pasta]


def test_dizer_nao_nao_apaga(janela, tmp_path, monkeypatch):
    perguntas = responder_perguntas(monkeypatch, QMessageBox.StandardButton.No)
    enviados = []
    monkeypatch.setattr(janela_principal, "mandar_para_a_lixeira", lambda pasta: (enviados.append(pasta), True)[1])
    Projeto.criar(tmp_path, "Importante")
    janela._recarregar_projetos()
    acoes_do_menu(janela, projeto_da_lista(janela, "Importante"))["Apagar"].trigger()
    assert "Importante" in perguntas[0] and "Lixeira" in perguntas[0]
    assert enviados == []


def test_apagar_a_peca_aberta_volta_para_nova_peca(aberta, monkeypatch):
    responder_perguntas(monkeypatch)
    monkeypatch.setattr(janela_principal, "mandar_para_a_lixeira", lambda _pasta: True)
    aberta._recarregar_projetos()
    acoes_do_menu(aberta, projeto_da_lista(aberta, "Peça"))["Apagar"].trigger()
    assert aberta.projeto is None and aberta.orquestrador is None
    assert aberta.rotulo_projeto.text() == "Nova peça"
    assert aberta.painel_chat.bolhas == []


def test_apagar_outra_peca_mantem_a_aberta(aberta, tmp_path, monkeypatch):
    responder_perguntas(monkeypatch)
    monkeypatch.setattr(janela_principal, "mandar_para_a_lixeira", lambda _pasta: True)
    aberta_antes = aberta.projeto
    Projeto.criar(tmp_path, "Outra")
    aberta._recarregar_projetos()
    acoes_do_menu(aberta, projeto_da_lista(aberta, "Outra"))["Apagar"].trigger()
    assert aberta.projeto is aberta_antes


def test_falha_na_lixeira_avisa_e_nao_apaga_de_vez(janela, tmp_path, monkeypatch):
    responder_perguntas(monkeypatch)
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **_k: avisos.append(a[2]))
    monkeypatch.setattr(janela_principal, "mandar_para_a_lixeira", lambda _pasta: False)
    projeto = Projeto.criar(tmp_path, "Presa")
    janela._recarregar_projetos()
    acoes_do_menu(janela, projeto_da_lista(janela, "Presa"))["Apagar"].trigger()
    assert avisos and (projeto.pasta / "projeto.json").exists()
    assert titulos_da_lista(janela) == ["Presa"]


def test_nada_se_apaga_com_o_agente_trabalhando(janela, tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(janela_principal, "mandar_para_a_lixeira", lambda pasta: (enviados.append(pasta), True)[1])
    responder_perguntas(monkeypatch)
    projeto = Projeto.criar(tmp_path, "Ocupada")
    janela.ocupado = True
    janela._apagar_projeto(projeto)
    janela.ocupado = False
    assert enviados == []


def test_botao_direito_nao_troca_a_peca_marcada(qtbot, janela, tmp_path, monkeypatch):
    criar_em_ordem(tmp_path, "primeira", "segunda")
    janela._recarregar_projetos()
    lista = janela.painel_versoes.lista_projetos
    janela.show()
    qtbot.waitExposed(janela)
    lista.item(0).setSelected(True)
    centro = lista.visualItemRect(lista.item(1)).center()
    qtbot.mouseClick(lista.viewport(), Qt.MouseButton.RightButton, pos=centro)
    assert lista.item(0).isSelected() and not lista.item(1).isSelected()
    assert janela.projeto is None, "o clique direito não abre a peça"


def test_menu_nao_abre_fora_dos_itens(janela, monkeypatch):
    abertos = []
    monkeypatch.setattr(janela.painel_versoes, "_menu_do_projeto", lambda p: abertos.append(p))
    janela.painel_versoes._abrir_menu_do_projeto(QPoint(5, 5000))
    assert abertos == []
