"""Servidores falsos de IA: respondem inteiro ou em pedaços SSE, conforme o pedido, como um provedor de verdade."""
import json


def pedacos(conclusao: dict) -> list[dict]:
    """A mesma resposta de chat em pedaços: o texto e os argumentos chegam divididos, e os campos extras vêm junto."""
    base = {k: conclusao.get(k) for k in ("id", "created", "model")} | {"object": "chat.completion.chunk"}
    saida = []
    for escolha in conclusao.get("choices", []):
        mensagem = dict(escolha["message"])
        conteudo = mensagem.pop("content", None)
        chamadas = mensagem.pop("tool_calls", None) or []
        extras = {k: v for k, v in mensagem.items() if k != "role"}
        saida.append(base | {"choices": [{"index": 0, "delta": {"role": "assistant", **extras}, "finish_reason": None}]})
        if conteudo:
            meio = len(conteudo) // 2
            for parte in (conteudo[:meio], conteudo[meio:]):
                saida.append(base | {"choices": [{"index": 0, "delta": {"content": parte}, "finish_reason": None}]})
        for indice, chamada in enumerate(chamadas):
            argumentos = chamada["function"]["arguments"]
            meio = len(argumentos) // 2
            primeira = {"index": indice, "id": chamada["id"], "type": "function", "function": {"name": chamada["function"]["name"], "arguments": argumentos[:meio]}}
            primeira |= {k: v for k, v in chamada.items() if k not in ("id", "type", "function")}
            saida.append(base | {"choices": [{"index": 0, "delta": {"tool_calls": [primeira]}, "finish_reason": None}]})
            saida.append(base | {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": indice, "function": {"arguments": argumentos[meio:]}}]}, "finish_reason": None}]})
        saida.append(base | {"choices": [{"index": 0, "delta": {}, "finish_reason": escolha.get("finish_reason", "stop")}]})
    return saida


def responder(manipulador, status: int, corpo: dict, pedido: dict) -> None:
    """Responde em SSE quando o pedido tem stream, e em JSON inteiro nos outros casos."""
    if status == 200 and pedido.get("stream"):
        dados = "".join(f"data: {json.dumps(p)}\n\n" for p in pedacos(corpo)).encode() + b"data: [DONE]\n\n"
        tipo = "text/event-stream"
    else:
        dados, tipo = json.dumps(corpo or {}).encode(), "application/json"
    manipulador.send_response(status)
    manipulador.send_header("Content-Type", tipo)
    manipulador.send_header("Content-Length", str(len(dados)))
    manipulador.end_headers()
    manipulador.wfile.write(dados)
