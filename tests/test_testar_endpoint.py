"""Testa o relatório de scripts/testar_endpoint.py com um servidor falso, sem depender da rede."""

import base64
import importlib.util
import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("testar_endpoint", RAIZ / "scripts" / "testar_endpoint.py")
testar_endpoint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(testar_endpoint)

CHAVE = "chave-secreta-de-teste"
MODELO, MODELO_IMAGEM = "modelo-texto", "modelo-imagem"


def png_em_data_uri() -> str:
    saida = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(saida, format="PNG")
    return "data:image/png;base64," + base64.b64encode(saida.getvalue()).decode()


def conclusao(mensagem: dict, motivo: str = "stop") -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "outro-nome",
        "choices": [{"index": 0, "message": {"role": "assistant", **mensagem}, "finish_reason": motivo}],
    }


def chamada_de_cubo(lado: int) -> dict:
    funcao = {"name": "criar_cubo", "arguments": f'{{\n   "lado_mm": {lado}\n}}'}
    return {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": funcao}]}


class Servidor(BaseHTTPRequestHandler):
    comportamento: dict = {}

    def log_message(self, *_):
        pass

    def responder(self, codigo: int, corpo: dict) -> None:
        dados = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        self.responder(200, {"object": "list", "data": [{"id": MODELO}, {"id": MODELO_IMAGEM}, {"id": "outro"}]})

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.comportamento.get("erro_com_chave"):
            self.responder(401, {"error": {"message": f"chave recusada: {CHAVE}"}})
        elif corpo.get("stream"):
            self.responder_streaming()
        elif corpo["model"] == MODELO_IMAGEM:
            imagens = [{"type": "image_url", "image_url": {"url": png_em_data_uri()}}]
            self.responder(
                200, conclusao({"content": None, "images": [] if self.comportamento.get("sem_imagem") else imagens})
            )
        elif corpo.get("tools"):
            self.responder(200, conclusao(chamada_de_cubo(self.comportamento.get("lado", 20)), "tool_calls"))
        elif isinstance(corpo["messages"][0]["content"], list):
            self.responder(200, conclusao({"content": self.comportamento.get("cor", "Vermelho")}))
        else:
            self.responder(200, conclusao({"content": "ok"}))

    def responder_streaming(self) -> None:
        lado = self.comportamento.get("lado", 20)
        partes = [
            {"id": "c1", "index": 0, "type": "function", "function": {"name": "criar_cubo", "arguments": '{"lado_mm"'}},
            {"index": 0, "function": {"arguments": f": {lado}}}"}},
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for parte in partes:
            pedaco = {
                "id": "x",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "outro-nome",
                "choices": [{"index": 0, "delta": {"tool_calls": [parte]}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(pedaco)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture
def rodar(tmp_path, capsys):
    """Sobe o servidor falso com o comportamento pedido, roda o script e devolve (código, saída)."""
    servidores = []

    def _rodar(url: str | None = None, **comportamento) -> tuple[int, str]:
        if url is None:
            classe = type("ServidorDoTeste", (Servidor,), {"comportamento": comportamento})
            servidor = ThreadingHTTPServer(("127.0.0.1", 0), classe)
            threading.Thread(target=servidor.serve_forever, daemon=True).start()
            servidores.append(servidor)
            url = f"http://127.0.0.1:{servidor.server_port}/v1"
        config = {
            "base_url": url,
            "api_key": CHAVE,
            "modelo": MODELO,
            "modelo_imagem": MODELO_IMAGEM,
            "temperatura": 0.2,
            "max_tokens": 1000,
            "tempo_limite_s": 1,
        }
        caminho = tmp_path / "ia.json"
        caminho.write_text(json.dumps(config), encoding="utf-8")
        codigo = testar_endpoint.main(["--config", str(caminho)])
        return codigo, capsys.readouterr().out

    yield _rodar
    for servidor in servidores:
        servidor.shutdown()


def test_todos_os_itens_aprovados(rodar):
    codigo, saida = rodar()
    assert codigo == 0
    assert "6 de 6" in saida
    assert "FALHA" not in saida


def test_resposta_sem_imagem_reprova_so_esse_item(rodar):
    codigo, saida = rodar(sem_imagem=True)
    assert codigo == 1
    assert "[FALHA] Geração de imagem" in saida
    assert saida.count("FALHA") == 1
    assert "5 de 6" in saida


def test_cor_errada_reprova_a_leitura_de_imagem(rodar):
    codigo, saida = rodar(cor="Azul")
    assert codigo == 1
    assert "[FALHA] Leitura de imagem" in saida


def test_argumento_errado_reprova_ferramenta_e_streaming(rodar):
    codigo, saida = rodar(lado=30)
    assert codigo == 1
    assert "[FALHA] Chamada de ferramenta" in saida
    assert "[FALHA] Streaming com ferramenta" in saida
    assert "4 de 6" in saida


def test_servidor_fora_do_ar_relata_todos_os_itens(rodar):
    with socket.socket() as livre:
        livre.bind(("127.0.0.1", 0))
        porta = livre.getsockname()[1]
    codigo, saida = rodar(url=f"http://127.0.0.1:{porta}/v1")
    assert codigo == 1
    assert saida.count("FALHA") == 6
    assert "0 de 6" in saida


@pytest.mark.parametrize("comportamento", [{}, {"erro_com_chave": True}])
def test_chave_nunca_aparece_na_saida(rodar, comportamento):
    _, saida = rodar(**comportamento)
    assert CHAVE not in saida
    assert testar_endpoint.mascarar(CHAVE) in saida


def test_arquivo_de_configuracao_ausente(tmp_path, capsys):
    assert testar_endpoint.main(["--config", str(tmp_path / "nao_existe.json")]) == 1
    assert "não encontrado" in capsys.readouterr().out
