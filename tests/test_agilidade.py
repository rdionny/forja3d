"""Pedidos rápidos: a resposta da IA chega aos pedaços, com andamento na tela, e mudanças pontuais não reescrevem o script."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from apoio_ia import responder
from forja3d.configuracao import ConfigIA
from forja3d.ia.cliente import ClienteIA, ErroIA, o_que_esta_fazendo
from test_chat_e_lista import criar_versao
from test_orquestrador import agente, chamada, config, resultados_das_ferramentas, texto  # noqa: F401

LOGO = "PONTOS = [" + ", ".join(f"({i}.5, {i}.25)" for i in range(1500)) + "]\n"
SCRIPT = LOGO + "import cadquery as cq\nNOME = 'Turbo Net'\nresult = cq.Workplane('XY').box(10, 10, 2)\n"


# ---------- Aos pedaços ----------


@pytest.fixture
def servidor():
    estado = {"corpo": {}, "pedidos": []}

    class Manipulador(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802 - nome definido pelo http.server.
            estado["pedidos"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            responder(self, 200, estado["corpo"], estado["pedidos"][-1])

    http = ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    estado["url"] = f"http://127.0.0.1:{http.server_port}/v1"
    yield estado
    http.shutdown()


def conclusao(mensagem):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", **mensagem}, "finish_reason": "stop"}]}


def test_chamada_de_ferramenta_chega_aos_pedacos_e_volta_inteira_para_o_historico(servidor):
    argumentos = json.dumps({"codigo": "a = 1\nb = 2\nresult = 3", "resumo_alteracao": "x"})
    assinatura = {"google": {"thought_signature": "assinatura-que-precisa-voltar"}}
    servidor["corpo"] = conclusao(
        {"reasoning_content": "Vou pensar bastante.", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "executar_cad", "arguments": argumentos}, "extra_content": assinatura}]}
    )
    cliente = ClienteIA(ConfigIA(endpoint=servidor["url"], api_key="k", modelo="m", timeout_s=5))
    andamento = []
    cliente.acompanhar = andamento.append
    resposta = cliente.conversar([{"role": "user", "content": "oi"}], [{"type": "function", "function": {"name": "executar_cad"}}], "modelagem")
    assert servidor["pedidos"][0]["stream"] is True
    assert [(c.id, c.nome, c.argumentos) for c in resposta.chamadas] == [("c1", "executar_cad", argumentos)]
    chamada_no_historico = resposta.mensagem["tool_calls"][0]
    assert chamada_no_historico["extra_content"] == assinatura, "o Gemini recusa a próxima chamada sem a assinatura"
    assert resposta.mensagem["reasoning_content"] == "Vou pensar bastante."
    # No máximo um aviso a cada meio segundo: aqui tudo chega de uma vez, e só o primeiro sai.
    assert andamento == ["pensando, 5 tokens"]


def test_texto_chega_aos_pedacos(servidor):
    servidor["corpo"] = conclusao({"content": "Pronto para revisão."})
    cliente = ClienteIA(ConfigIA(endpoint=servidor["url"], api_key="k", modelo="m", timeout_s=5))
    resposta = cliente.conversar([{"role": "user", "content": "oi"}], None, "conversa")
    assert resposta.conteudo == "Pronto para revisão." and resposta.chamadas == []
    assert resposta.mensagem == {"role": "assistant", "content": "Pronto para revisão."}


def test_resposta_sem_nada_vira_erro_claro(servidor):
    servidor["corpo"] = {"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": []}
    cliente = ClienteIA(ConfigIA(endpoint=servidor["url"], api_key="k", modelo="m", timeout_s=5))
    with pytest.raises(ErroIA, match="vazia"):
        cliente.conversar([{"role": "user", "content": "oi"}], None, "conversa")


@pytest.mark.parametrize(
    ("conteudo", "extras", "chamadas", "esperado"),
    [
        ("", {}, {}, "esperando a IA"),
        ("", {"reasoning_content": "x" * 4000}, {}, "pensando, 1.000 tokens"),
        ("Olá", {}, {}, "escrevendo a resposta"),
        ("", {}, {0: {"function": {"name": "executar_cad", "arguments": '{"codigo": "a\\nb\\nc'}}}, "escrevendo o script, 3 linhas"),
        ("", {}, {0: {"function": {"name": "editar_cad", "arguments": '{"trocas": ['}}}, "escrevendo a alteração, 1 linhas"),
        ("", {}, {0: {"function": {"name": "verificar_peca", "arguments": ""}}}, "preparando verificar peca"),
    ],
)
def test_andamento_diz_o_que_a_ia_esta_fazendo(conteudo, extras, chamadas, esperado):
    assert o_que_esta_fazendo(conteudo, extras, chamadas) == esperado


# ---------- Editar em vez de reescrever ----------


@pytest.fixture
def com_logo(config, perfil, monkeypatch):  # noqa: F811
    a, cliente = agente(config, perfil, [])
    versao = criar_versao(a.projeto, partes=("modelo",))
    (a.projeto.pasta_versao(versao) / "modelo.py").write_text(SCRIPT, encoding="utf-8")
    a.versao_atual = versao
    executados = []
    monkeypatch.setattr(a, "_executar_cad", lambda argumentos: (executados.append(argumentos), ({"ok": True, "versao": "v002"}, []))[1])
    return a, executados


def test_editar_troca_so_o_trecho_e_mantem_o_contorno(com_logo):
    a, executados = com_logo
    resultado, _ = a._editar_cad({"trocas": [{"de": "NOME = 'Turbo Net'", "para": "NOME = 'Kalyne Patrocínio'"}], "resumo_alteracao": "Troca o nome"})
    assert resultado == {"ok": True, "versao": "v002"}
    assert executados[0]["codigo"] == SCRIPT.replace("Turbo Net", "Kalyne Patrocínio")
    assert executados[0]["resumo_alteracao"] == "Troca o nome"
    assert len(json.dumps({"trocas": [{"de": "NOME = 'Turbo Net'", "para": "NOME = 'Kalyne Patrocínio'"}]})) < len(SCRIPT) / 100


def test_trecho_que_nao_existe_volta_como_erro_e_nao_executa(com_logo):
    a, executados = com_logo
    resultado, _ = a._editar_cad({"trocas": [{"de": "NOME = 'Outro'", "para": "x"}], "resumo_alteracao": ""})
    assert not resultado["ok"] and "0 vezes" in resultado["erro"] and executados == []


def test_trecho_repetido_volta_como_erro(com_logo):
    a, executados = com_logo
    resultado, _ = a._editar_cad({"trocas": [{"de": ".5, ", "para": "x"}], "resumo_alteracao": ""})
    assert not resultado["ok"] and "1500 vezes" in resultado["erro"] and executados == []


def test_sem_versao_pede_o_script_completo(config, perfil):  # noqa: F811
    a, _ = agente(config, perfil, [])
    resultado, _ = a._editar_cad({"trocas": [{"de": "a", "para": "b"}], "resumo_alteracao": ""})
    assert not resultado["ok"] and "executar_cad" in resultado["erro"]


def test_agente_usa_editar_pelo_loop(com_logo):
    a, executados = com_logo
    a.cliente.respostas = [chamada("editar_cad", {"trocas": [{"de": "NOME = 'Turbo Net'", "para": "NOME = 'Kalyne'"}], "resumo_alteracao": "Nome"}), texto("Pronto.")]
    assert a.processar("Troca o nome para Kalyne") == "Pronto."
    assert "Kalyne" in executados[0]["codigo"] and LOGO in executados[0]["codigo"]
    assert resultados_das_ferramentas(a.cliente.chamadas[-1]["mensagens"])[0] == {"ok": True, "versao": "v002"}


def test_andamento_vai_para_a_linha_de_etapa(config, perfil):  # noqa: F811
    etapas = []
    a, cliente = agente(config, perfil, [])
    a.ouvinte.etapa = etapas.append
    a._proxima = "modelagem"
    cliente.acompanhar("escrevendo o script, 40 linhas")
    assert etapas == ["Modelando a peça: escrevendo o script, 40 linhas"]


# ---------- Andamento na tela ----------


def test_linha_de_andamento_lista_as_etapas_feitas_e_gira(qtbot):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from forja3d.interface.painel_chat import GIRO, PainelChat

    painel = PainelChat()
    qtbot.addWidget(painel)
    painel.definir_ocupado(True)
    painel.definir_etapa("Entendendo o pedido")
    painel.definir_etapa("Entendendo o pedido: pensando, 120 tokens")
    assert not painel.rotulo_feitas.isVisibleTo(painel), "o detalhe muda, a etapa é a mesma"
    painel.definir_etapa("Modelando a peça: escrevendo o script, 40 linhas")
    assert painel.rotulo_feitas.text().startswith("✓ Entendendo o pedido · ")
    assert painel.rotulo_etapa.text().startswith("Modelando a peça: escrevendo o script, 40 linhas…")
    giro = painel.rotulo_giro.text()
    painel._atualizar_etapa()
    assert painel.rotulo_giro.text() != giro and painel.rotulo_giro.text() in GIRO
    for etapa in ("Gerando o sólido", "Verificando", "Gerando as vistas", "Conferindo as vistas", "Revisando a peça"):
        painel.definir_etapa(etapa)
    assert painel.rotulo_feitas.text().splitlines()[0] == "✓ mais 2 etapas"


def test_troca_do_arquivo_espera_quem_esta_lendo(tmp_path, monkeypatch):
    from pathlib import Path

    from forja3d.projetos import armazenamento

    tentativas = []
    original = Path.replace

    def ocupado_na_primeira(self, destino):
        tentativas.append(destino)
        if len(tentativas) == 1:
            raise PermissionError(5, "Acesso negado")
        return original(self, destino)

    monkeypatch.setattr(Path, "replace", ocupado_na_primeira)
    armazenamento.gravar_json(tmp_path / "historico.json", {"ok": True})
    assert len(tentativas) == 2 and json.loads((tmp_path / "historico.json").read_text(encoding="utf-8")) == {"ok": True}
