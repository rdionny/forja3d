"""Provedores de IA: configuração, cliente, tela de configurações e ícone no topo."""
import base64
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLineEdit

from apoio_conceitos import imagem_png
from forja3d import configuracao
from forja3d.configuracao import PROVEDOR_PADRAO, ConfigIA, Configuracao, Provedor
from forja3d.ia.cliente import ClienteIA, ErroIA, ModeloIndisponivel, data_uri_png, eh_modelo_de_imagem, usa_rota_de_imagens
from forja3d.interface.dialogo_config import DialogoConfig
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.registro import FiltroChave

MODELOS = ["gemini-3.8-flash-high", "gemini-3.1-flash-image", "gpt-image-2", "claude-opus-5-5"]


@pytest.fixture
def preferencias(monkeypatch, tmp_path):
    """Isola a configuração: nada de chaves reais nem do config.toml do usuário."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(configuracao, "PASTA_CONFIG", tmp_path)
    for variavel in ("FORJA3D_IA_ENDPOINT", "FORJA3D_IA_API_KEY", "FORJA3D_IA_MODELO", "FORJA3D_IA_MODELO_IMAGEM"):
        monkeypatch.delenv(variavel, raising=False)
    return tmp_path


def _salvar_dois(**opcoes):
    configuracao.salvar_preferencias(
        "http://padrao/v1", "chave-padrao", "modelo-chat", "", "", "modelo-imagem",
        provedores=[Provedor("Outro", "https://outro/v1/", "chave-outra")],
        **opcoes,
    )  # fmt: skip


# ---------- Configuração ----------


def test_sem_preferencias_existe_so_o_provedor_padrao(preferencias):
    (preferencias / "ia_padrao.example.toml").write_text('[ia]\nendpoint = "http://x/v1"\napi_key = "k"\nmodelo = "m"\n', encoding="utf-8")
    config = configuracao.carregar()
    assert [p.nome for p in config.provedores] == [PROVEDOR_PADRAO]
    assert (config.ia.provedor, config.ia.provedor_imagem) == (PROVEDOR_PADRAO, PROVEDOR_PADRAO)
    assert config.ia.endpoint == config.ia.endpoint_imagem == "http://x/v1"


def test_chat_e_imagem_podem_usar_provedores_diferentes(preferencias):
    _salvar_dois(provedor=PROVEDOR_PADRAO, provedor_imagem="Outro")
    config = configuracao.carregar()
    assert [p.nome for p in config.provedores] == [PROVEDOR_PADRAO, "Outro"]
    assert (config.ia.endpoint, config.ia.api_key) == ("http://padrao/v1", "chave-padrao")
    assert (config.ia.endpoint_imagem, config.ia.api_key_imagem) == ("https://outro/v1", "chave-outra")
    assert (config.ia.modelo, config.ia.modelo_imagem) == ("modelo-chat", "modelo-imagem")


def test_chat_pode_usar_outro_provedor(preferencias):
    _salvar_dois(provedor="Outro")
    config = configuracao.carregar()
    assert config.ia.provedor == "Outro"
    assert config.ia.endpoint == "https://outro/v1"
    assert config.ia.provedor_imagem == PROVEDOR_PADRAO


def test_provedor_que_nao_existe_mais_volta_para_o_padrao(preferencias):
    _salvar_dois(provedor="Sumiu", provedor_imagem="Sumiu")
    config = configuracao.carregar()
    assert (config.ia.provedor, config.ia.provedor_imagem) == (PROVEDOR_PADRAO, PROVEDOR_PADRAO)


def test_nomes_e_chaves_com_caracteres_especiais_sobrevivem(preferencias):
    nome, chave = 'Meu "provedor" ção', 'c\\h"a\nve'
    configuracao.salvar_preferencias("http://p/v1", "k", "m", "", "", provedores=[Provedor(nome, "http://o/v1", chave)], provedor=nome)
    config = configuracao.carregar()
    assert config.ia.provedor == nome
    assert config.ia.api_key == chave


def test_chaves_nao_aparecem_no_repr(preferencias):
    _salvar_dois(provedor_imagem="Outro")
    texto = repr(configuracao.carregar())
    assert "chave-padrao" not in texto
    assert "chave-outra" not in texto


def test_lista_de_chaves_traz_todos_os_provedores_sem_repetir(preferencias):
    _salvar_dois(provedor_imagem="Outro")
    assert set(configuracao.carregar().chaves) == {"chave-padrao", "chave-outra"}
    config = Configuracao(ia=ConfigIA(endpoint="e", api_key="k", modelo="m"))
    assert config.chaves == ("k",)
    assert [p.nome for p in config.todos_os_provedores] == [PROVEDOR_PADRAO]


def test_filtro_de_log_remove_todas_as_chaves():
    registro = logging.LogRecord("x", logging.INFO, "", 0, "a=%s b=%s", ("primeira", "segunda"), None)
    FiltroChave("primeira", "segunda", "").filter(registro)
    assert registro.getMessage() == "a=*** b=***"


# ---------- Cliente ----------


@pytest.fixture
def servidores():
    """Cria servidores falsos, cada um guardando os pedidos que recebeu."""
    criados = []

    def criar(resposta_chat=None, resposta_imagens=None, status=200):
        estado = {"pedidos": [], "url": ""}

        class Manipulador(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                tamanho = int(self.headers["Content-Length"])
                bruto = self.rfile.read(tamanho)
                estado["pedidos"].append({"rota": self.path, "chave": self.headers["Authorization"], "corpo": bruto})
                corpo = resposta_imagens if self.path.endswith("/images/edits") else resposta_chat
                dados = json.dumps(corpo or {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)

        http = ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        estado["url"] = f"http://127.0.0.1:{http.server_port}/v1"
        criados.append(http)
        return estado

    yield criar
    for http in criados:
        http.shutdown()


def _conclusao(mensagem):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", **mensagem}, "finish_reason": "stop"}]}


def test_chat_e_imagem_vao_cada_um_para_o_seu_provedor(servidores):
    chat = servidores(resposta_chat=_conclusao({"content": "ok"}))
    imagem = servidores(resposta_chat=_conclusao({"content": None, "images": [{"image_url": {"url": data_uri_png(imagem_png())}}]}))
    cliente = ClienteIA(ConfigIA(endpoint=chat["url"], api_key="chave-chat", modelo="texto", modelo_imagem="img", endpoint_imagem=imagem["url"], api_key_imagem="chave-img"))

    assert cliente.conversar([{"role": "user", "content": "oi"}], None, "conversa").conteudo == "ok"
    assert cliente.gerar_imagem("p", b"r").dados == imagem_png()

    assert len(chat["pedidos"]) == 1
    assert chat["pedidos"][0]["chave"] == "Bearer chave-chat"
    assert len(imagem["pedidos"]) == 1
    assert imagem["pedidos"][0]["chave"] == "Bearer chave-img"


def test_mesmo_provedor_reaproveita_o_cliente():
    cliente = ClienteIA(ConfigIA(endpoint="http://x/v1", api_key="k", modelo="m", endpoint_imagem="http://x/v1", api_key_imagem="k"))
    assert cliente._cliente_imagem is cliente._cliente


def test_modelo_gpt_image_usa_a_rota_de_imagens(servidores):
    dados = imagem_png()
    servidor = servidores(resposta_imagens={"created": 0, "data": [{"b64_json": base64.b64encode(dados).decode()}]})
    cliente = ClienteIA(ConfigIA(endpoint=servidor["url"], api_key="k", modelo="m", modelo_imagem="gpt-image-2"))
    imagem = cliente.gerar_imagem("Gere a caixa", b"referencia")
    assert imagem.dados == dados
    pedido = servidor["pedidos"][0]
    assert pedido["rota"].endswith("/images/edits")
    assert b"Gere a caixa" in pedido["corpo"]
    assert b"referencia" in pedido["corpo"]
    assert b"gpt-image-2" in pedido["corpo"]


def test_rota_de_imagens_sem_credencial_e_modelo_indisponivel_sem_repetir(servidores):
    servidor = servidores(resposta_imagens={"error": {"message": "auth_unavailable: no auth available"}}, status=503)
    cliente = ClienteIA(ConfigIA(endpoint=servidor["url"], api_key="k", modelo="m", modelo_imagem="gpt-image-2", max_tentativas_rede=3, timeout_s=5))
    with pytest.raises(ModeloIndisponivel):
        cliente.gerar_imagem("p", b"r")
    assert len(servidor["pedidos"]) == 1


def test_rota_de_imagens_sem_imagem_e_erro(servidores):
    servidor = servidores(resposta_imagens={"created": 0, "data": [{"url": "https://exemplo.invalido/x.png"}]})
    cliente = ClienteIA(ConfigIA(endpoint=servidor["url"], api_key="k", modelo="m", modelo_imagem="gpt-image-2", timeout_s=5))
    with pytest.raises(ErroIA, match="nenhuma imagem"):
        cliente.gerar_imagem("p", b"r")


@pytest.mark.parametrize(("modelo", "esperado"), [("gemini-3.1-flash-image", True), ("gpt-image-2", True), ("dall-e-3", True), ("flux-1", True), ("gemini-3.8-flash-high", False), ("claude-opus-5-5", False)])
def test_reconhece_modelos_de_imagem(modelo, esperado):
    assert eh_modelo_de_imagem(modelo) is esperado


def test_so_gpt_image_usa_a_rota_de_imagens():
    assert usa_rota_de_imagens("gpt-image-2.5-flare")
    assert not usa_rota_de_imagens("gemini-3.1-flash-image")


# ---------- Diálogo ----------


class ClienteDeModelos:
    """Devolve modelos diferentes conforme o endpoint, e guarda quem foi consultado."""

    consultas: list = []
    por_endpoint: dict = {}

    def __init__(self, ia):
        self.ia = ia

    def listar_modelos(self):
        type(self).consultas.append((self.ia.endpoint, self.ia.api_key))
        resposta = type(self).por_endpoint.get(self.ia.endpoint, MODELOS)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


@pytest.fixture
def abrir(qtbot, preferencias):
    ClienteDeModelos.consultas = []
    ClienteDeModelos.por_endpoint = {}

    def _abrir(config=None):
        config = config or Configuracao(ia=ConfigIA(endpoint="http://padrao/v1", api_key="chave-padrao", modelo="gemini-3.8-flash-high"))
        dialogo = DialogoConfig(config, None, ClienteDeModelos)
        qtbot.addWidget(dialogo)
        dialogo.show()
        return dialogo

    return _abrir


def itens(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def salvar(dialogo):
    dialogo._salvar()


def adicionar(dialogo, nome, endpoint, chave=""):
    dialogo.botao_adicionar.click()
    dialogo.campo_nome.setText(nome)
    dialogo.campo_nome.textEdited.emit(nome)
    dialogo.campo_endpoint.setText(endpoint)
    dialogo.campo_endpoint.textEdited.emit(endpoint)
    dialogo.campo_chave.setText(chave)
    dialogo.campo_chave.textEdited.emit(chave)


def test_abre_com_os_modelos_separados_por_funcao(qtbot, abrir):
    d = abrir()
    qtbot.waitUntil(lambda: d.seletor_imagem.combo_modelo.count() > 0, timeout=3000)
    assert itens(d.seletor_chat.combo_modelo) == ["gemini-3.8-flash-high", "claude-opus-5-5"]
    assert itens(d.seletor_imagem.combo_modelo) == ["gemini-3.1-flash-image", "gpt-image-2"]
    assert d.seletor_chat.combo_modelo.currentText() == "gemini-3.8-flash-high"
    assert d.seletor_imagem.combo_modelo.currentText() == "gemini-3.1-flash-image"
    assert len(ClienteDeModelos.consultas) == 1, "os dois usam o mesmo provedor: uma consulta basta"


def test_modelo_fora_da_lista_nao_e_trocado_sozinho(qtbot, abrir):
    d = abrir(Configuracao(ia=ConfigIA(endpoint="http://padrao/v1", api_key="k", modelo="modelo-antigo")))
    qtbot.waitUntil(lambda: d.seletor_chat.combo_modelo.count() > 0, timeout=3000)
    assert d.seletor_chat.combo_modelo.currentText() == "modelo-antigo"


def test_campo_de_chave_e_oculto(abrir):
    d = abrir()
    assert d.campo_chave.echoMode() == QLineEdit.EchoMode.Password
    assert d.campo_chave.text() == "chave-padrao"


def test_provedor_padrao_nao_pode_ser_removido_nem_renomeado(abrir):
    d = abrir()
    assert not d.botao_remover.isEnabled()
    assert d.campo_nome.isReadOnly()
    d._remover_provedor()
    assert len(d._provedores) == 1


def test_adicionar_provedor_aparece_nas_duas_escolhas(abrir):
    d = abrir()
    d.botao_adicionar.click()
    assert d.lista.count() == 2
    assert itens(d.seletor_chat.combo_provedor) == [PROVEDOR_PADRAO, "Provedor 2"]
    assert itens(d.seletor_imagem.combo_provedor) == [PROVEDOR_PADRAO, "Provedor 2"]
    assert d.lista.currentRow() == 1
    assert not d.campo_nome.isReadOnly()
    assert d.botao_remover.isEnabled()


def test_renomear_atualiza_lista_e_escolhas(abrir):
    d = abrir()
    adicionar(d, "Meu servidor", "https://meu/v1")
    assert d.lista.item(1).text() == "Meu servidor"
    assert d.seletor_chat.combo_provedor.itemText(1) == "Meu servidor"
    assert d.seletor_imagem.combo_provedor.itemText(1) == "Meu servidor"


def test_trocar_o_provedor_traz_os_modelos_dele_e_escolhe_um_valido(qtbot, abrir):
    d = abrir()
    qtbot.waitUntil(lambda: d.seletor_imagem.combo_modelo.count() > 0, timeout=3000)
    ClienteDeModelos.por_endpoint["https://outro/v1"] = ["texto-x", "image-x", "image-y"]
    adicionar(d, "Outro", "https://outro/v1", "chave-outra")
    d.seletor_imagem.combo_provedor.setCurrentIndex(1)
    qtbot.waitUntil(lambda: "image-x" in itens(d.seletor_imagem.combo_modelo), timeout=3000)
    assert itens(d.seletor_imagem.combo_modelo) == ["image-x", "image-y"]
    assert d.seletor_imagem.combo_modelo.currentText() == "image-x"
    assert ("https://outro/v1", "chave-outra") in ClienteDeModelos.consultas
    assert d.seletor_chat.combo_modelo.currentText() == "gemini-3.8-flash-high", "o chat não muda"


def test_provedor_sem_nome_de_imagem_reconhecido_mostra_a_lista_inteira(qtbot, abrir):
    d = abrir()
    ClienteDeModelos.por_endpoint["https://outro/v1"] = ["artista-1", "artista-2"]
    adicionar(d, "Outro", "https://outro/v1")
    d.seletor_imagem.combo_provedor.setCurrentIndex(1)
    qtbot.waitUntil(lambda: d.seletor_imagem.combo_modelo.count() == 2, timeout=3000)
    assert itens(d.seletor_imagem.combo_modelo) == ["artista-1", "artista-2"]


def test_remover_provedor_em_uso_volta_para_o_padrao(abrir):
    d = abrir()
    adicionar(d, "Outro", "https://outro/v1")
    d.seletor_chat.combo_provedor.setCurrentIndex(1)
    d.seletor_imagem.combo_provedor.setCurrentIndex(1)
    d.botao_remover.click()
    assert d.lista.count() == 1
    assert d.seletor_chat.combo_provedor.currentText() == PROVEDOR_PADRAO
    assert d.seletor_imagem.combo_provedor.currentText() == PROVEDOR_PADRAO
    assert d.seletor_chat.combo_provedor.count() == 1
    assert d.campo_endpoint.text() == "http://padrao/v1"


def test_testar_conexao_mostra_o_resultado(qtbot, abrir):
    d = abrir()
    d.botao_testar.click()
    qtbot.waitUntil(lambda: "Conectado" in d.rotulo_teste.text(), timeout=3000)
    assert d.rotulo_teste.text() == "Conectado: 4 modelos, 2 de imagem."
    assert d.botao_testar.isEnabled()


def test_testar_conexao_com_erro_mostra_a_mensagem(qtbot, abrir):
    d = abrir()
    qtbot.waitUntil(lambda: d.seletor_chat.combo_modelo.count() > 0, timeout=3000)
    ClienteDeModelos.por_endpoint["https://ruim/v1"] = ErroIA("A IA recusou a chave da API.")
    adicionar(d, "Ruim", "https://ruim/v1", "errada")
    d.botao_testar.click()
    qtbot.waitUntil(lambda: "recusou" in d.rotulo_teste.text(), timeout=3000)
    assert d.botao_testar.isEnabled()


def test_testar_conexao_sem_endpoint_avisa(abrir):
    d = abrir()
    d.botao_adicionar.click()
    d.botao_testar.click()
    assert "Preencha o endpoint" in d.rotulo_teste.text()
    assert d.botao_testar.isEnabled()


def test_erro_inesperado_ao_consultar_nao_derruba_o_dialogo(qtbot, abrir):
    d = abrir()
    qtbot.waitUntil(lambda: d.seletor_chat.combo_modelo.count() > 0, timeout=3000)
    ClienteDeModelos.por_endpoint["https://x/v1"] = RuntimeError("falha qualquer")
    adicionar(d, "X", "https://x/v1")
    d.botao_testar.click()
    qtbot.waitUntil(lambda: "Não consegui consultar" in d.rotulo_teste.text(), timeout=3000)


def test_aviso_de_http_acompanha_o_provedor_selecionado(abrir):
    d = abrir()
    assert d.rotulo_aviso.isVisibleTo(d)
    adicionar(d, "Seguro", "https://seguro/v1")
    assert not d.rotulo_aviso.isVisibleTo(d)
    d.lista.setCurrentRow(0)
    assert d.rotulo_aviso.isVisibleTo(d)


@pytest.mark.parametrize(
    ("nome", "endpoint", "mensagem"),
    [
        ("", "https://x/v1", "Dê um nome"),
        ("  ", "https://x/v1", "Dê um nome"),
        (PROVEDOR_PADRAO, "https://x/v1", "Já existe"),
        ("padrão", "https://x/v1", "Já existe"),
        ("Outro", "", "precisa começar com http"),
        ("Outro", "ftp://x", "precisa começar com http"),
    ],
)
def test_provedor_invalido_nao_salva(abrir, preferencias, nome, endpoint, mensagem):
    d = abrir()
    adicionar(d, nome, endpoint)
    salvar(d)
    assert mensagem in d.rotulo_erro.text()
    assert d.result() == 0
    assert d.isVisible()
    assert not (preferencias / "Forja3D" / "config.toml").exists()
    assert d.lista.currentRow() == 1, "seleciona o provedor com problema"


@pytest.mark.parametrize("seletor", ["seletor_chat", "seletor_imagem"])
def test_modelo_vazio_nao_salva(abrir, preferencias, seletor):
    d = abrir()
    getattr(d, seletor).combo_modelo.setCurrentText("  ")
    salvar(d)
    assert "Escolha o modelo" in d.rotulo_erro.text()
    assert not (preferencias / "Forja3D" / "config.toml").exists()


def test_salvar_grava_tudo_e_a_configuracao_volta_igual(abrir):
    d = abrir()
    adicionar(d, "Outro", " https://outro/v1/ ", " chave-outra ")
    d.seletor_chat.combo_modelo.setCurrentText("claude-opus-5-5")
    d.seletor_imagem.combo_provedor.setCurrentIndex(1)
    d.seletor_imagem.combo_modelo.setCurrentText("image-x")
    salvar(d)
    assert d.result() == 1

    config = configuracao.carregar()
    assert (config.ia.provedor, config.ia.modelo, config.ia.endpoint) == (PROVEDOR_PADRAO, "claude-opus-5-5", "http://padrao/v1")
    assert (config.ia.provedor_imagem, config.ia.modelo_imagem, config.ia.endpoint_imagem) == ("Outro", "image-x", "https://outro/v1")
    assert config.ia.api_key_imagem == "chave-outra"
    assert [p.nome for p in config.provedores] == [PROVEDOR_PADRAO, "Outro"]


def test_dialogo_reabre_com_o_que_foi_salvo(qtbot, abrir):
    _salvar_dois(provedor="Outro", provedor_imagem="Outro")
    d = abrir(configuracao.carregar())
    assert d.lista.count() == 2
    assert d.seletor_chat.combo_provedor.currentText() == "Outro"
    assert d.seletor_imagem.combo_provedor.currentText() == "Outro"
    assert d.seletor_chat.combo_modelo.currentText() == "modelo-chat"
    assert d.seletor_imagem.combo_modelo.currentText() == "modelo-imagem"
    d.lista.setCurrentRow(1)
    assert d.campo_endpoint.text() == "https://outro/v1"
    assert d.campo_chave.text() == "chave-outra"


def test_remover_e_salvar_apaga_o_provedor(abrir):
    _salvar_dois(provedor_imagem="Outro")
    d = abrir(configuracao.carregar())
    d.lista.setCurrentRow(1)
    d.botao_remover.click()
    salvar(d)
    config = configuracao.carregar()
    assert [p.nome for p in config.provedores] == [PROVEDOR_PADRAO]
    assert config.ia.provedor_imagem == PROVEDOR_PADRAO
    assert "chave-outra" not in (configuracao.pasta_usuario() / "config.toml").read_text(encoding="utf-8")


# ---------- Janela ----------


@pytest.fixture
def janela(qtbot, tmp_path, perfil, preferencias):
    config = Configuracao(ia=ConfigIA(endpoint="http://x/v1", api_key="k", modelo="m"), pasta_projetos=tmp_path / "pecas")
    j = JanelaPrincipal(config, perfil, ClienteDeModelos, verificar_endpoint=False)
    qtbot.addWidget(j)
    yield j
    j.close()


def test_topo_tem_o_icone_de_engrenagem(janela):
    botao = janela.botao_config
    assert botao.text() == ""
    assert botao.toolTip().startswith("Configurações")
    assert botao.accessibleName() == "Configurações"
    imagem = botao.icon().pixmap(QSize(20, 20)).toImage()
    assert not imagem.isNull()
    pintados = sum(imagem.pixelColor(x, y).alpha() > 0 for x in range(imagem.width()) for y in range(imagem.height()))
    assert pintados > 40, "o SVG precisa desenhar de verdade"


def test_clicar_no_icone_abre_as_configuracoes(janela, monkeypatch):
    abertos = []

    def executar(self):
        abertos.append(self)
        return 0

    monkeypatch.setattr(DialogoConfig, "exec", executar)
    janela.botao_config.click()
    assert len(abertos) == 1
    assert abertos[0].seletor_chat.combo_modelo.currentText() == "m"


def test_icone_fica_desativado_durante_o_trabalho(janela):
    janela._definir_ocupado(True)
    assert not janela.botao_config.isEnabled()
    janela._definir_ocupado(False)
    assert janela.botao_config.isEnabled()


def test_salvar_passa_a_valer_sem_reabrir_o_app(janela, monkeypatch, preferencias):
    def executar(self):
        adicionar(self, "Outro", "https://outro/v1", "chave-outra")
        self.seletor_chat.combo_provedor.setCurrentIndex(1)
        self.seletor_chat.combo_modelo.setCurrentText("texto-x")
        self._salvar()
        return self.result()

    monkeypatch.setattr(DialogoConfig, "exec", executar)
    monkeypatch.setattr(JanelaPrincipal, "_verificar_endpoint", lambda _s: None)
    janela.botao_config.click()
    assert janela.config.ia.provedor == "Outro"
    assert janela.config.ia.modelo == "texto-x"
    assert janela.trabalhador.config is janela.config
