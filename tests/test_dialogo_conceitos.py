"""Diálogo de conceitos e sua ligação com a janela, com a tela de verdade e sem rede."""
import os
import threading
from pathlib import Path
from typing import ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QMessageBox

from apoio_conceitos import ANALISE, REPROVADA, ClienteFalso, imagem_png, texto_ia
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.ia.cliente import Cancelado, ErroIA, ModeloIndisponivel
from forja3d.interface import janela_principal
from forja3d.interface.dialogo_conceitos import DialogoConceitos
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.interface.painel_chat import imagem_do_mime
from forja3d.projetos.armazenamento import Projeto

REFERENCIA = imagem_png("blue", (200, 100))


@pytest.fixture
def abrir(qtbot, tmp_path):
    projeto = Projeto.criar(tmp_path)
    abertos = []

    def _abrir(cliente=None):
        cliente = cliente or ClienteFalso()
        dialogo = DialogoConceitos(REFERENCIA, cliente, lambda: projeto)
        qtbot.addWidget(dialogo)
        dialogo.show()
        abertos.append(dialogo)
        return dialogo, cliente

    yield _abrir
    for dialogo in abertos:
        dialogo._cancelar.set()
        for linha in dialogo._linhas:
            linha.join(5)


def gerar(qtbot, dialogo):
    dialogo.botao_gerar.click()
    qtbot.waitUntil(lambda: dialogo._ativas == 0, timeout=10000)


def textos(widget):
    return [r.text() for r in widget.findChildren(QLabel)]


def test_nada_acontece_antes_do_clique_em_gerar(abrir):
    chamadas = []
    dialogo = DialogoConceitos(REFERENCIA, ClienteFalso(), lambda: chamadas.append(1))
    assert chamadas == [], "abrir o diálogo não cria peça nenhuma"
    dialogo.close()
    d, cliente = abrir()
    assert d._cartoes == []
    assert cliente.pedidos_de_analise == []
    assert not d.botao_mais.isEnabled()


def test_entrega_duas_opcoes_prontas_para_escolher(qtbot, abrir):
    d, cliente = abrir()
    d.campo_pedido.setPlainText("Caixa para carregador")
    gerar(qtbot, d)
    assert len(d._cartoes) == 2
    assert "Caixa para carregador" in cliente.pedidos_de_analise[0][0]["content"][0]["text"]
    for cartao, opcao in zip(d._cartoes, ANALISE["opcoes"], strict=True):
        assert cartao.pronta
        assert not cartao.imagem.pixmap().isNull()
        assert cartao.botao_escolher.isEnabled()
        assert cartao.botao_de_novo.isEnabled()
        rotulos = textos(cartao)
        assert "Montada" in rotulos
        assert "Vista explodida" in rotulos
        assert opcao["titulo"] in rotulos
    assert "Parafuso M3 (não impresso)" in " ".join(textos(d._cartoes[1]))
    assert d.botao_gerar.text() == "Refazer com este pedido"
    assert d.botao_mais.isEnabled()
    assert d.rotulo_erro.isHidden()


def test_escolha_fica_bloqueada_enquanto_gera(qtbot, abrir):
    liberar = threading.Event()
    d, _ = abrir(ClienteFalso(ao_gerar=lambda _p, _c: liberar.wait(10)))
    d.botao_gerar.click()
    qtbot.waitUntil(lambda: len(d._cartoes) == 2, timeout=5000)
    assert not d.botao_gerar.isEnabled()
    assert all(not c.botao_escolher.isEnabled() for c in d._cartoes)
    qtbot.waitUntil(lambda: "Gerando" in d._cartoes[0].imagem.text(), timeout=5000)
    assert "Gerando as opções" in d.rotulo_estado.text()
    liberar.set()
    qtbot.waitUntil(lambda: d._ativas == 0, timeout=10000)
    assert all(c.botao_escolher.isEnabled() for c in d._cartoes)
    assert d.rotulo_estado.text() == ""


def test_com_menos_de_duas_imagens_a_escolha_fica_bloqueada(qtbot, abrir):
    def falha_na_segunda_opcao(prompt, _cancelar):
        if "Tampa parafusada" in prompt and not sucesso.is_set():
            raise ErroIA("O modelo não devolveu imagem.")

    sucesso = threading.Event()
    d, _ = abrir(ClienteFalso(ao_gerar=falha_na_segunda_opcao))
    gerar(qtbot, d)

    boa, ruim = d._cartoes
    assert boa.pronta and not ruim.pronta
    assert not boa.botao_escolher.isEnabled(), "uma opção só não é escolha"
    assert not ruim.botao_escolher.isEnabled()
    assert ruim.botao_de_novo.isEnabled()
    assert "O modelo não devolveu imagem" in ruim.rotulo_aviso.text()
    assert ruim.imagem.text() == "Sem imagem"
    assert "pelo menos 2 imagens" in d.rotulo_erro.text()

    d._escolher(boa)
    assert d.conceito is None, "nem pelo método direto"

    sucesso.set()
    ruim.botao_de_novo.click()
    qtbot.waitUntil(lambda: d._ativas == 0, timeout=10000)
    assert ruim.pronta
    assert boa.botao_escolher.isEnabled() and ruim.botao_escolher.isEnabled()
    assert ruim.rotulo_aviso.isHidden()
    assert d.rotulo_erro.isHidden()


def test_imagem_reprovada_tres_vezes_aparece_com_aviso_e_pode_ser_escolhida(qtbot, abrir):
    d, _ = abrir(ClienteFalso(conferencias=[REPROVADA] * 6))
    gerar(qtbot, d)
    for cartao in d._cartoes:
        assert cartao.pronta
        assert "podem não bater" in cartao.rotulo_aviso.text()
        assert "4 parafusos" in cartao.rotulo_aviso.text()
        assert cartao.botao_escolher.isEnabled()


def test_modelo_indisponivel_leva_as_configuracoes(qtbot, abrir):
    d, _ = abrir(ClienteFalso(imagens=[ModeloIndisponivel("O modelo escolhido não está disponível no servidor agora.")] * 4))
    gerar(qtbot, d)
    assert "não está disponível" in d.rotulo_erro.text()
    assert d.botao_config.isVisibleTo(d)
    assert all(c.imagem.text() == "Sem imagem" for c in d._cartoes)
    assert all(not c.botao_escolher.isEnabled() for c in d._cartoes)
    d.botao_config.click()
    assert d.quer_configuracoes
    assert d.result() == QDialog.DialogCode.Rejected


def test_erro_comum_nao_oferece_configuracoes(qtbot, abrir):
    d, _ = abrir(ClienteFalso(analises=[ErroIA("A IA demorou demais para responder.")]))
    gerar(qtbot, d)
    assert "demorou demais" in d.rotulo_erro.text()
    assert not d.botao_config.isVisibleTo(d)
    assert d._cartoes == []
    assert d.botao_gerar.isEnabled()


def test_recusa_mostra_o_motivo_e_nao_gera_nada(qtbot, abrir):
    recusa = {"objeto": "arma", "recusa": "Não projeto armas.", "medidas_criticas": [], "opcoes": []}
    d, cliente = abrir(ClienteFalso(analises=[recusa]))
    gerar(qtbot, d)
    assert "Não projeto armas." in d.rotulo_erro.text()
    assert d._cartoes == []
    assert cliente.prompts_de_imagem == []


def test_mais_opcoes_acrescenta_sem_perder_as_anteriores(qtbot, abrir):
    d, cliente = abrir()
    gerar(qtbot, d)
    primeiras = list(d._cartoes)
    d.botao_mais.click()
    qtbot.waitUntil(lambda: d._ativas == 0, timeout=10000)
    assert len(d._cartoes) == 4
    assert d._cartoes[:2] == primeiras
    assert "Tampa sobreposta; Tampa parafusada" in cliente.pedidos_de_analise[1][0]["content"][0]["text"]
    assert d._cartoes[0].rodada.pasta.name == "rodada_01"
    assert d._cartoes[2].rodada.pasta.name == "rodada_02"


def test_gerar_de_novo_nao_troca_de_opcao(qtbot, abrir):
    d, _ = abrir()
    gerar(qtbot, d)
    primeira = d._cartoes[0].resultado.imagem
    segunda = d._cartoes[1].resultado.imagem
    d._cartoes[0].botao_de_novo.click()
    qtbot.waitUntil(lambda: d._ativas == 0, timeout=10000)
    assert d._cartoes[0].resultado.imagem != primeira
    assert d._cartoes[0].resultado.imagem.name == "opcao_1_02.png"
    assert d._cartoes[1].resultado.imagem == segunda


def test_escolher_grava_e_fecha(qtbot, abrir):
    d, _ = abrir()
    gerar(qtbot, d)
    d._cartoes[1].botao_escolher.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert d.conceito["opcao"]["titulo"] == "Tampa parafusada"
    assert d.conceito["imagem"].endswith("opcao_2_01.png")


def test_cancelar_interrompe_rapido_e_nao_escolhe_nada(qtbot, abrir):
    def espera_cancelar(_prompt, cancelar):
        while not cancelar.is_set():
            threading.Event().wait(0.01)
        raise Cancelado

    d, _ = abrir(ClienteFalso(ao_gerar=espera_cancelar))
    d.botao_gerar.click()
    qtbot.waitUntil(lambda: len(d._cartoes) == 2, timeout=5000)
    d.reject()
    assert d.result() == QDialog.DialogCode.Rejected
    assert d.conceito is None
    assert d._cancelar.is_set()
    for linha in d._linhas:
        assert not linha.is_alive()


def test_texto_da_ia_nao_e_interpretado_como_html(qtbot, abrir):
    malicioso = {**ANALISE, "opcoes": [{**ANALISE["opcoes"][0], "titulo": "<img src='x'><b>Tampa</b>"}, ANALISE["opcoes"][1]]}
    d, _ = abrir(ClienteFalso(analises=[malicioso]))
    gerar(qtbot, d)
    rotulo = next(r for r in d._cartoes[0].findChildren(QLabel) if "<img" in r.text())
    assert rotulo.textFormat() == Qt.TextFormat.PlainText


# ---------- Colar e arrastar ----------


def test_mime_com_arquivo_de_imagem_devolve_o_caminho(tmp_path):
    arquivo = tmp_path / "foto.JPG"
    arquivo.write_bytes(b"x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(arquivo))])
    assert imagem_do_mime(mime) == arquivo


def test_mime_com_imagem_copiada_devolve_um_png():
    mime = QMimeData()
    imagem = QImage(4, 4, QImage.Format.Format_RGB32)
    imagem.fill(0xff0000)
    mime.setImageData(imagem)
    assert imagem_do_mime(mime).startswith(b"\x89PNG")


def test_mime_sem_imagem_nao_conta():
    mime = QMimeData()
    mime.setText("só texto")
    assert imagem_do_mime(mime) is None


def test_mime_nunca_busca_imagem_na_internet():
    mime = QMimeData()
    mime.setUrls([QUrl("https://exemplo.invalido/foto.png")])
    assert imagem_do_mime(mime) is None


def test_mime_com_arquivo_que_nao_e_imagem_nao_conta(tmp_path):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "planilha.xlsx"))])
    assert imagem_do_mime(mime) is None


# ---------- Janela ----------


class ClienteJanela(ClienteFalso):
    """Fala como agente quando recebe a etapa normal, e como fluxo de conceitos nas outras."""

    enviadas: ClassVar[list] = []

    def __init__(self, _config=None):
        super().__init__()

    def verificar(self):
        return None

    def conversar(self, mensagens, ferramentas, etapa, cancelar=None, temperatura=None):
        if etapa.startswith("conceito_"):
            return super().conversar(mensagens, ferramentas, etapa, cancelar, temperatura)
        type(self).enviadas.append(list(mensagens))
        return texto_ia("Qual o tamanho do carregador?")


@pytest.fixture
def janela(qtbot, tmp_path, perfil):
    ClienteJanela.enviadas = []
    config = Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)
    j = JanelaPrincipal(config, perfil, ClienteJanela, verificar_endpoint=False)
    qtbot.addWidget(j)
    yield j
    j.close()


def dialogo_escolhe(numero):
    """Troca o `exec` para o diálogo gerar as opções de verdade e escolher uma, sem precisar de clique humano."""

    def executar(self):
        self.show()
        self.botao_gerar.click()
        while self._ativas:
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
        self._cartoes[numero].botao_escolher.click()
        return self.result()

    return executar


def test_botao_abre_o_seletor_de_arquivos(qtbot, janela, monkeypatch, tmp_path):
    pedidos = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **_k: (pedidos.append(a[3]), "")[1:] + ("",))
    janela.painel_chat.botao_imagem.click()
    assert "*.png" in pedidos[0] and "*.jpg" in pedidos[0] and "*.webp" in pedidos[0]
    assert janela.projeto is None, "cancelar o seletor não cria peça"


def test_imagem_invalida_avisa_e_nao_abre_o_dialogo(janela, monkeypatch):
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **_k: avisos.append(a[2]))
    monkeypatch.setattr(janela_principal, "DialogoConceitos", lambda *_a, **_k: pytest.fail("não pode abrir"))
    janela.painel_chat.imagem_recebida.emit(b"nao sou imagem")
    assert "PNG, JPG ou WEBP" in avisos[0]
    assert janela.projeto is None


def test_cancelar_o_dialogo_sem_gerar_nao_cria_peca(janela, monkeypatch):
    monkeypatch.setattr(DialogoConceitos, "exec", lambda self: self.reject() or self.result())
    janela.painel_chat.imagem_recebida.emit(REFERENCIA)
    assert janela.projeto is None
    assert not janela.ocupado


def test_fluxo_da_imagem_ate_o_agente_perguntar_as_medidas(qtbot, janela, monkeypatch, tmp_path):
    monkeypatch.setattr(DialogoConceitos, "exec", dialogo_escolhe(1))
    janela.painel_chat.imagem_recebida.emit(REFERENCIA)
    assert janela.ocupado
    assert not janela.painel_chat.botao_imagem.isEnabled(), "sem nova imagem durante o trabalho"
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=20000)

    assert janela.projeto.titulo == "Tampa parafusada"
    assert janela.rotulo_projeto.text() == "Tampa parafusada"
    assert (janela.projeto.pasta / "referencias" / "referencia_01.png").exists()
    enviadas = ClienteJanela.enviadas[-1]
    assert any(isinstance(m["content"], str) and m["content"].startswith("[CONCEITO ESCOLHIDO]") for m in enviadas)
    rotulos = textos(janela.painel_chat.conteudo)
    assert "Escolhi o conceito: Tampa parafusada." in rotulos
    assert any("tamanho do carregador" in r for r in rotulos)
    assert janela.versao_vista is None, "a imagem não vira versão nem peça pronta"
    assert not janela.botao_aprovar.isEnabled()
    assert not janela.botao_exportar.isEnabled()

    # Reaberta, a peça mostra a conversa com a imagem do conceito.
    outra = JanelaPrincipal(janela.config, janela.perfil, ClienteJanela, verificar_endpoint=False)
    qtbot.addWidget(outra)
    outra.abrir_projeto(Projeto(janela.projeto.pasta))
    miniaturas = [r for r in outra.painel_chat.conteudo.findChildren(QLabel) if r.pixmap() is not None and not r.pixmap().isNull()]
    assert len(miniaturas) == 1
    outra.close()


def test_pedir_configuracoes_abre_a_tela_de_configuracoes(janela, monkeypatch):
    def executar(self):
        self._abrir_configuracoes()
        return self.result()

    abertas = []
    monkeypatch.setattr(DialogoConceitos, "exec", executar)
    monkeypatch.setattr(JanelaPrincipal, "_abrir_configuracoes", lambda _s: abertas.append(1))
    janela.painel_chat.imagem_recebida.emit(REFERENCIA)
    assert abertas == [1]


def test_usa_o_modulo_de_prompts_do_pacote():
    assert (Path(janela_principal.__file__).parents[1] / "ia" / "prompts" / "conceito_imagem.md").exists()


# ---------- Material e roda do mouse ----------


@pytest.fixture
def janela_com_peca(janela, espec_suporte):
    janela._garantir_projeto("Peça")
    janela.orquestrador.especificacao = espec_suporte.model_copy(update={"material": "PETG"})
    janela.combo_material.setCurrentText("PETG")
    return janela


def _responder(monkeypatch, resposta):
    perguntas = []

    def perguntar(_janela, _titulo, texto, *_a, **_k):
        perguntas.append(texto)
        return resposta

    monkeypatch.setattr(QMessageBox, "question", perguntar)
    return perguntas


def test_material_igual_ao_especificado_nao_pergunta_nada(janela_com_peca, monkeypatch):
    perguntas = _responder(monkeypatch, QMessageBox.StandardButton.No)
    assert janela_com_peca._confirmar_material()
    assert perguntas == []


def test_material_diferente_nao_prepara_sem_confirmar(janela_com_peca, monkeypatch):
    perguntas = _responder(monkeypatch, QMessageBox.StandardButton.No)
    janela_com_peca.combo_material.setCurrentText("PLA")
    janela_com_peca._aprovar_e_fatiar()
    assert "especificada em PETG" in perguntas[0]
    assert "seletor está em PLA" in perguntas[0]
    assert not janela_com_peca.ocupado, "nada foi fatiado"
    assert janela_com_peca.combo_material.currentText() == "PETG", "o seletor volta para o especificado"


def test_material_diferente_pode_ser_confirmado(janela_com_peca, monkeypatch):
    _responder(monkeypatch, QMessageBox.StandardButton.Yes)
    janela_com_peca.combo_material.setCurrentText("PLA")
    assert janela_com_peca._confirmar_material()
    assert janela_com_peca.combo_material.currentText() == "PLA"


def _roda(valor: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, valor),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )  # fmt: skip


@pytest.mark.parametrize("campo", ["combo_material", "combo_qualidade", "spin_paredes", "spin_preenchimento"])
def test_roda_do_mouse_sem_foco_nao_muda_as_opcoes_de_impressao(janela, campo):
    alvo = getattr(janela, campo)
    assert not alvo.hasFocus()
    antes = alvo.currentText() if hasattr(alvo, "currentText") else alvo.value()
    QApplication.sendEvent(alvo, _roda(-120))
    QApplication.sendEvent(alvo, _roda(120))
    assert (alvo.currentText() if hasattr(alvo, "currentText") else alvo.value()) == antes
    assert alvo.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_filtro_deixa_a_roda_passar_quando_o_campo_tem_foco():
    class ComFoco:
        def hasFocus(self):
            return True

    assert janela_principal.SemRodaSemFoco().eventFilter(ComFoco(), _roda(-120)) is False


def test_resumo_do_fatiamento_mostra_o_material(janela):
    resumo = {"parametros": {"material": "PETG"}, "validacao": {"aprovado": True, "tempo": "1h", "filamento_g": 10.0, "camadas": 5, "bico_c": 250.0, "mesa_c": 70.0}}
    assert janela._texto_resumo(resumo).startswith("Pronto para exportar: PETG, 1h")
