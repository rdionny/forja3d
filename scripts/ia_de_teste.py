"""Endpoint de IA de mentira, para testar o app instalado sem chave e sem gastar nada.

Uso: ia_de_teste.py [--porta 8765] [--endereco 0.0.0.0]

Responde sempre o mesmo roteiro: especifica um cubo de 20 mm, modela, verifica, olha as vistas e encerra.
Prova o caminho inteiro dentro do app (rede, ferramentas, CAD, vistas), mas não prova que um provedor real responde.
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELO = "modelo-de-teste"
ESPECIFICACAO = {
    "nome": "cubo_20mm",
    "finalidade": "teste do instalador",
    "material": "PLA",
    "carga": "nenhuma",
    "dimensoes": {"x_mm": 20, "y_mm": 20, "z_mm": 20},
    "pecas": 1,
    "furos": [],
    "orientacao_impressao": "base na mesa",
    "premissas": [],
}
# Usa os resolvedores de esboço (nlopt) e de montagem (casadi com ipopt) de propósito:
# eles carregam bibliotecas pelo nome, e só rodando dá para saber se foram no pacote.
CODIGO = """import cadquery as cq
esboco = (
    cq.Sketch()
    .segment((0, 0), (0, 20.0), "a")
    .segment((20.0, 20.0), "b")
    .segment((20.0, 0), "c")
    .close("d")
    .constrain("a", "Fixed", None)
    .constrain("a", "b", "Coincident", None)
    .constrain("b", "c", "Coincident", None)
    .constrain("c", "d", "Coincident", None)
    .constrain("d", "a", "Coincident", None)
    .constrain("a", "b", "Angle", 90)
    .constrain("b", "c", "Angle", 90)
    .constrain("c", "d", "Angle", 90)
    .constrain("a", "Length", 20.0)
    .constrain("b", "Length", 20.0)
    .solve()
    .assemble()
)
result = cq.Workplane("XY").placeSketch(esboco).extrude(20)
montagem = cq.Assembly().add(result, name="p").add(result, name="q", loc=cq.Location((0, 0, 30)))
montagem.constrain("p@faces@>Z", "q@faces@<Z", "Plane")
montagem.solve()
"""
ROTEIRO = (
    ("definir_especificacao", ESPECIFICACAO),
    ("executar_cad", {"codigo": CODIGO, "resumo_alteracao": "primeira versão"}),
    ("verificar_peca", {}),
    ("renderizar_vistas", {}),
)
FINAL = "Cubo de 20 mm pronto e verificado. Clique em Aprovar e preparar para fatiar."


def proxima_resposta(mensagens: list[dict]) -> dict:
    """Quantas ferramentas já respondeu desde o último pedido diz em que ponto do roteiro está."""
    feitas = 0
    for mensagem in reversed(mensagens):
        if mensagem["role"] == "user" and not isinstance(mensagem.get("content"), list):
            break
        if mensagem["role"] == "tool":
            feitas += 1
    if feitas >= len(ROTEIRO):
        return {"role": "assistant", "content": FINAL}
    nome, argumentos = ROTEIRO[feitas]
    chamada = {"id": f"c{feitas}", "type": "function", "function": {"name": nome, "arguments": json.dumps(argumentos)}}
    return {"role": "assistant", "content": None, "tool_calls": [chamada]}


class Atendente(BaseHTTPRequestHandler):
    def _responder(self, corpo: dict) -> None:
        dados = json.dumps(corpo).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:  # noqa: N802 - nome definido pela biblioteca.
        self._responder({"object": "list", "data": [{"id": MODELO, "object": "model", "created": 0, "owned_by": "teste"}]})

    def do_POST(self) -> None:  # noqa: N802 - nome definido pela biblioteca.
        pedido = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mensagem = proxima_resposta(pedido["messages"])
        self._responder(
            {
                "id": "resposta",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODELO,
                "choices": [{"index": 0, "message": mensagem, "finish_reason": "tool_calls" if "tool_calls" in mensagem else "stop"}],
            }
        )

    def log_message(self, formato: str, *argumentos: object) -> None:
        print(f"{self.address_string()} {formato % argumentos}", flush=True)


if __name__ == "__main__":
    analisador = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    analisador.add_argument("--porta", type=int, default=8765)
    analisador.add_argument("--endereco", default="127.0.0.1")
    dados = analisador.parse_args()
    print(f"IA de teste em http://{dados.endereco}:{dados.porta}/v1, modelo {MODELO}", flush=True)
    ThreadingHTTPServer((dados.endereco, dados.porta), Atendente).serve_forever()
