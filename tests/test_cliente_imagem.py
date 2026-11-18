"""Geração de imagem no cliente de IA, com o SDK de verdade e um servidor falso."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from apoio_conceitos import imagem_png
from apoio_ia import responder
from forja3d import configuracao
from forja3d.configuracao import MODELO_IMAGEM_PADRAO, ConfigIA
from forja3d.ia.cliente import ClienteIA, ErroIA, ModeloIndisponivel, data_uri_png


def conclusao(mensagem):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", **mensagem}, "finish_reason": "stop"}]}


@pytest.fixture
def servidor():
    """Responde com `estado["status"]` e `estado["corpo"]`, e guarda o que recebeu."""
    estado = {"status": 200, "corpo": {}, "pedidos": []}

    class Manipulador(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            estado["pedidos"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            responder(self, estado["status"], estado["corpo"], estado["pedidos"][-1])

    http = ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    estado["url"] = f"http://127.0.0.1:{http.server_port}/v1"
    yield estado
    http.shutdown()


def cliente(servidor, **opcoes):
    return ClienteIA(ConfigIA(endpoint=servidor["url"], api_key="k", modelo="texto", timeout_s=5, modelo_imagem="imagem", **opcoes))


def com_imagem(url):
    return conclusao({"content": None, "images": [{"type": "image_url", "image_url": {"url": url}, "index": 0}]})


def test_devolve_bytes_e_tipo_da_imagem(servidor):
    dados = imagem_png()
    servidor["corpo"] = com_imagem(data_uri_png(dados).replace("image/png", "image/jpeg"))
    imagem = cliente(servidor).gerar_imagem("prompt", b"referencia")
    assert imagem.dados == dados
    assert imagem.tipo == "jpeg"


def test_pedido_leva_o_modelo_de_imagem_e_a_referencia_sem_pedir_varias(servidor):
    servidor["corpo"] = com_imagem(data_uri_png(imagem_png()))
    cliente(servidor).gerar_imagem("Gere a caixa", b"referencia")
    pedido = servidor["pedidos"][0]
    assert pedido["model"] == "imagem"
    assert "n" not in pedido, "o modelo recusa n maior que 1"
    assert pedido["messages"][0]["content"][0] == {"type": "text", "text": "Gere a caixa"}
    assert pedido["messages"][0]["content"][1]["image_url"]["url"] == data_uri_png(b"referencia")


def test_servidor_sem_credencial_falha_na_hora_sem_repetir(servidor):
    servidor["status"] = 503
    servidor["corpo"] = {"error": {"message": "auth_unavailable: no auth available (providers=codex, model=imagem)"}}
    with pytest.raises(ModeloIndisponivel, match="Escolha outro em Configurações"):
        cliente(servidor, max_tentativas_rede=3).gerar_imagem("p", b"r")
    assert len(servidor["pedidos"]) == 1


def test_modelo_inexistente_tambem_e_indisponivel(servidor):
    servidor["status"] = 400
    servidor["corpo"] = {"error": {"message": "unknown provider for model imagem", "code": "model_not_found"}}
    with pytest.raises(ModeloIndisponivel):
        cliente(servidor).gerar_imagem("p", b"r")


def test_outro_erro_do_servidor_nao_e_indisponivel(servidor):
    servidor["status"] = 500
    servidor["corpo"] = {"error": {"message": "falha qualquer"}}
    with pytest.raises(ErroIA) as erro:
        cliente(servidor).gerar_imagem("p", b"r")
    assert not isinstance(erro.value, ModeloIndisponivel)


@pytest.mark.parametrize(
    ("corpo", "mensagem"),
    [
        (conclusao({"content": "Não consigo gerar."}), "nenhuma imagem"),
        (conclusao({"content": None, "images": []}), "nenhuma imagem"),
        (com_imagem("https://exemplo.invalido/imagem.png"), "não reconheço"),
        ({"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": []}, "vazia"),
    ],
)
def test_resposta_sem_imagem_utilizavel_e_erro(servidor, corpo, mensagem):
    servidor["corpo"] = corpo
    with pytest.raises(ErroIA, match=mensagem):
        cliente(servidor).gerar_imagem("p", b"r")


def test_conversar_usa_a_temperatura_pedida_ou_a_configurada(servidor):
    servidor["corpo"] = conclusao({"content": "ok"})
    c = cliente(servidor, temperatura=0.2)
    assert c.conversar([{"role": "user", "content": "oi"}], None, "conversa").conteudo == "ok"
    c.conversar([{"role": "user", "content": "oi"}], None, "conversa", temperatura=0)
    assert [p["temperature"] for p in servidor["pedidos"]] == [0.2, 0]
    assert all(p["model"] == "texto" for p in servidor["pedidos"])


def test_modelo_de_imagem_tem_padrao_e_pode_vir_do_ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(configuracao, "PASTA_CONFIG", tmp_path)
    monkeypatch.delenv("FORJA3D_IA_MODELO_IMAGEM", raising=False)
    assert configuracao.carregar().ia.modelo_imagem == MODELO_IMAGEM_PADRAO
    monkeypatch.setenv("FORJA3D_IA_MODELO_IMAGEM", "outro-modelo")
    assert configuracao.carregar().ia.modelo_imagem == "outro-modelo"


def test_preferencias_guardam_o_modelo_de_imagem(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(configuracao, "PASTA_CONFIG", tmp_path)
    configuracao.salvar_preferencias("http://x/v1", "k", "m", "orca.exe", "pecas", "gemini-imagem")
    assert configuracao.carregar().ia.modelo_imagem == "gemini-imagem"
