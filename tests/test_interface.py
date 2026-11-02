"""Fluxo da interface, com a janela real e respostas gravadas da IA."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

from forja3d.configuracao import ConfigIA, Configuracao  # noqa: E402
from forja3d.fatiamento.orca import localizar_orca  # noqa: E402
from forja3d.ia.cliente import Cancelado, ChamadaFerramenta, RespostaIA  # noqa: E402
from forja3d.interface.janela_principal import JanelaPrincipal  # noqa: E402
from forja3d.projetos.armazenamento import Projeto  # noqa: E402

DADOS = Path(__file__).parent / "dados"
ESPEC = {
    "nome": "suporte_l",
    "finalidade": "teste",
    "material": "PETG",
    "carga": "media",
    "dimensoes": {"x_mm": 40, "y_mm": 30, "z_mm": 34},
    "pecas": 1,
    "furos": [{"quantidade": 2, "diametro_nominal_mm": 4, "diametro_modelado_mm": 4, "eixo": "Z", "passante": True}],
    "orientacao_impressao": "base na mesa",
    "premissas": [],
}


def chamada(nome, argumentos=None, id_="c"):
    bruto = json.dumps(argumentos or {})
    return RespostaIA(
        "",
        [ChamadaFerramenta(id_, nome, bruto)],
        {"role": "assistant", "tool_calls": [{"id": id_, "type": "function", "function": {"name": nome, "arguments": bruto}}]},
    )


def texto(conteudo):
    return RespostaIA(conteudo, [], {"role": "assistant", "content": conteudo})


class ClienteGravado:
    respostas: list = []

    def __init__(self, _config=None):
        pass

    def verificar(self):
        return None

    def conversar(self, mensagens, ferramentas, etapa, cancelar=None):
        return type(self).respostas.pop(0)


class ClienteQueEspera(ClienteGravado):
    def conversar(self, mensagens, ferramentas, etapa, cancelar=None):
        limite = time.monotonic() + 20
        while time.monotonic() < limite:
            if cancelar is not None and cancelar.is_set():
                raise Cancelado
            time.sleep(0.02)
        return texto("tarde demais")


def _fluxo_completo():
    codigo = (DADOS / "suporte_l_corrigido.py").read_text(encoding="utf-8")
    return [
        chamada("definir_especificacao", ESPEC, "c1"),
        chamada("executar_cad", {"codigo": codigo, "resumo_alteracao": "primeira"}, "c2"),
        chamada("verificar_peca", {}, "c3"),
        texto("**Suporte L** está pronta para a sua revisão."),
    ]


@pytest.fixture
def janela(qtbot, tmp_path, perfil):
    config = Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)

    def criar(cliente=ClienteGravado):
        j = JanelaPrincipal(config, perfil, cliente, verificar_endpoint=False)
        qtbot.addWidget(j)
        return j

    janelas = []

    def fabrica(cliente=ClienteGravado):
        j = criar(cliente)
        janelas.append(j)
        return j

    yield fabrica
    for j in janelas:
        j.close()


def _enviar(qtbot, janela, pedido):
    janela.painel_chat.campo.setPlainText(pedido)
    janela.painel_chat._enviar()
    assert janela.ocupado
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=90000)


def _textos_do_chat(janela):
    from PySide6.QtWidgets import QLabel

    return [r.text() for r in janela.painel_chat.conteudo.findChildren(QLabel) if r.isVisibleTo(janela.painel_chat.conteudo)]


def test_estado_inicial(janela):
    j = janela()
    assert not j.botao_aprovar.isEnabled()
    assert not j.botao_exportar.isEnabled()
    assert j.painel_chat.botao_enviar.isEnabled()
    assert not j.ocupado
    assert j.orquestrador is None


def test_campo_vazio_nao_envia(janela):
    j = janela()
    j.painel_chat.campo.setPlainText("   ")
    j.painel_chat._enviar()
    assert not j.ocupado
    assert j.projeto is None


def test_erro_da_ia_aparece_e_libera_a_interface(qtbot, janela):
    from forja3d.ia.cliente import ErroIA

    class ClienteComErro(ClienteGravado):
        def conversar(self, *_args, **_kwargs):
            raise ErroIA("A IA recusou a chave da API.")

    j = janela(ClienteComErro)
    _enviar(qtbot, j, "oi")
    assert any("recusou a chave" in t for t in _textos_do_chat(j))
    assert j.painel_chat.botao_enviar.isEnabled()


def test_cancelar_libera_a_interface_rapido(qtbot, janela):
    j = janela(ClienteQueEspera)
    j.painel_chat.campo.setPlainText("oi")
    j.painel_chat._enviar()
    assert j.painel_chat.linha_etapa.isVisibleTo(j.painel_chat)
    assert not j.painel_chat.botao_enviar.isEnabled()
    inicio = time.monotonic()
    j.painel_chat.botao_cancelar.click()
    qtbot.waitUntil(lambda: not j.ocupado, timeout=5000)
    assert time.monotonic() - inicio < 5
    assert any("cancelada" in t.lower() for t in _textos_do_chat(j))


def test_interface_nao_trava_durante_o_trabalho(qtbot, janela):
    j = janela(ClienteQueEspera)
    j.painel_chat.campo.setPlainText("oi")
    j.painel_chat._enviar()
    contagem = []
    relogio = j.painel_chat._relogio
    relogio.timeout.connect(lambda: contagem.append(1))
    qtbot.waitUntil(lambda: len(contagem) >= 2, timeout=5000)
    j.painel_chat.botao_cancelar.click()
    qtbot.waitUntil(lambda: not j.ocupado, timeout=5000)


@pytest.mark.cad
@pytest.mark.orca
def test_fluxo_completo_ate_exportar(qtbot, janela, tmp_path, monkeypatch):
    if localizar_orca() is None:
        pytest.skip("OrcaSlicer não encontrado")
    ClienteGravado.respostas = _fluxo_completo()
    j = janela()
    _enviar(qtbot, j, "Suporte em L")

    # A especificação ajusta as opções, e a peça aprovada libera só a aprovação.
    assert j.combo_material.currentText() == "PETG"
    assert (j.spin_paredes.value(), j.spin_preenchimento.value()) == (4, 25)
    assert j.versao_vista == "v001"
    assert j.botao_aprovar.isEnabled()
    assert not j.botao_exportar.isEnabled(), "exportar exige fatiar antes"
    assert j.projeto.titulo == "Suporte L"

    j.botao_aprovar.click()
    assert j.ocupado
    qtbot.waitUntil(lambda: not j.ocupado, timeout=120000)
    assert j.botao_exportar.isEnabled()
    assert "Pronto para exportar" in j.rotulo_resumo.text()
    assert "250 °C" in j.rotulo_resumo.text()

    # Mudar uma opção invalida o G-code pronto.
    j.spin_paredes.setValue(5)
    assert not j.botao_exportar.isEnabled()
    j.botao_aprovar.click()
    qtbot.waitUntil(lambda: not j.ocupado, timeout=120000)
    assert j.botao_exportar.isEnabled()

    # A exportação pede confirmação e copia o arquivo.
    destino = tmp_path / "saida_final.gcode"
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(destino), ""))
    j.botao_exportar.click()
    assert destino.stat().st_size > 1000

    # Sem confirmação, nada é copiado.
    outro = tmp_path / "nao_deve_existir.gcode"
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(outro), ""))
    j.botao_exportar.click()
    assert not outro.exists()


@pytest.mark.cad
def test_peca_reprovada_nao_libera_aprovacao(qtbot, janela):
    errado = (DADOS / "suporte_l_modelo_errado.py").read_text(encoding="utf-8")
    ClienteGravado.respostas = [
        chamada("definir_especificacao", ESPEC, "c1"),
        chamada("executar_cad", {"codigo": errado, "resumo_alteracao": ""}, "c2"),
        chamada("verificar_peca", {}, "c3"),
        texto("Reprovada."),
    ]
    j = janela()
    _enviar(qtbot, j, "Suporte")
    assert not j.botao_aprovar.isEnabled()
    assert not j.botao_exportar.isEnabled()
    assert not j.orquestrador.pode_aprovar()


@pytest.mark.cad
def test_reabrir_peca_e_voltar_de_versao(qtbot, janela, tmp_path):
    codigo = (DADOS / "suporte_l_corrigido.py").read_text(encoding="utf-8")
    ClienteGravado.respostas = [
        chamada("definir_especificacao", ESPEC, "c1"),
        chamada("executar_cad", {"codigo": codigo, "resumo_alteracao": "um"}, "c2"),
        chamada("verificar_peca", {}, "c3"),
        chamada("executar_cad", {"codigo": codigo, "resumo_alteracao": "dois"}, "c4"),
        chamada("verificar_peca", {}, "c5"),
        texto("Duas versões."),
    ]
    j = janela()
    _enviar(qtbot, j, "Suporte")
    assert j.projeto.versoes() == ["v001", "v002"]
    assert j.painel_versoes.lista_versoes.count() == 2

    # Ver uma versão antiga não libera a aprovação dela.
    j._ver_versao("v001")
    assert not j.botao_aprovar.isEnabled()
    j._restaurar_versao("v001")
    assert j.orquestrador.versao_atual == "v001"
    assert j.botao_aprovar.isEnabled()

    # Em outra janela, a peça reabre do disco com a conversa e o estado.
    segunda = janela()
    projeto = Projeto.listar(tmp_path)[0]
    segunda.abrir_projeto(projeto)
    assert segunda.versao_vista == "v001"
    assert segunda.orquestrador.pode_aprovar()
    assert any("Duas versões" in t for t in _textos_do_chat(segunda))


def test_nova_peca_limpa_a_tela(qtbot, janela):
    ClienteGravado.respostas = [texto("Qual a medida?")]
    j = janela()
    _enviar(qtbot, j, "Quero um suporte")
    assert j.projeto is not None
    j.painel_versoes.botao_nova.click()
    assert j.projeto is None
    assert j.orquestrador is None
    assert not any("Qual a medida" in t for t in _textos_do_chat(j))


def test_janela_nao_bloqueia_thread_principal_ao_verificar_endpoint(qtbot, tmp_path, perfil):
    liberar = threading.Event()

    class ClienteLento(ClienteGravado):
        def verificar(self):
            liberar.wait(5)

    config = Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)
    inicio = time.monotonic()
    j = JanelaPrincipal(config, perfil, ClienteLento, verificar_endpoint=True)
    qtbot.addWidget(j)
    assert time.monotonic() - inicio < 3
    assert "verificando" in j.rotulo_ia.text()
    liberar.set()
    qtbot.waitUntil(lambda: "conectada" in j.rotulo_ia.text(), timeout=3000)
    j.close()
