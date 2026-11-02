"""Fase 0: testa o endpoint com o SDK oficial, lendo a configuração padrão."""
import base64
import json
import struct
import sys
import time
import tomllib
import zlib
from pathlib import Path

from openai import OpenAI

sys.stdout.reconfigure(encoding="utf-8")

RAIZ = Path(__file__).resolve().parents[2]
config = tomllib.loads((RAIZ / "config" / "ia_padrao.toml").read_text(encoding="utf-8"))["ia"]
cliente = OpenAI(
    base_url=config["endpoint"],
    api_key=config["api_key"],
    timeout=config["timeout_s"],
    max_retries=config["max_tentativas_rede"],
)
MODELO = config["modelo"]

FERRAMENTA = {
    "type": "function",
    "function": {
        "name": "validar_peca",
        "description": "Valida dimensões em mm contra o volume de impressão.",
        "parameters": {
            "type": "object",
            "properties": {"x_mm": {"type": "number"}, "y_mm": {"type": "number"}, "z_mm": {"type": "number"}},
            "required": ["x_mm", "y_mm", "z_mm"],
        },
    },
}


def relata(nome, ok, detalhe=""):
    print(f"{'OK   ' if ok else 'FALHA'} {nome}" + (f" | {detalhe}" if detalhe else ""))


def png():
    largura = altura = 64
    bruto = b""
    for _ in range(altura):
        bruto += b"\x00"
        for x in range(largura):
            bruto += bytes((230, 30, 30)) if x < largura / 2 else bytes((30, 60, 230))

    def bloco(tag, dados):
        return struct.pack(">I", len(dados)) + tag + dados + struct.pack(">I", zlib.crc32(tag + dados) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + bloco(b"IHDR", struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0))
        + bloco(b"IDAT", zlib.compress(bruto))
        + bloco(b"IEND", b"")
    )


modelos = [m.id for m in cliente.models.list().data]
relata("modelo configurado existe", MODELO in modelos, f"{len(modelos)} modelos disponíveis")

mensagens = [
    {"role": "system", "content": "Você é um agente de CAD. Sempre valide antes de responder."},
    {"role": "user", "content": "Valide um cubo de 300 x 20 x 20 mm e diga se cabe."},
]
inicio = time.time()
r1 = cliente.chat.completions.create(model=MODELO, messages=mensagens, tools=[FERRAMENTA], temperature=0.2, reasoning_effort="low")
m1 = r1.choices[0].message
chamada = m1.tool_calls[0] if m1.tool_calls else None
argumentos = json.loads(chamada.function.arguments) if chamada else {}
relata("chamada de ferramenta", bool(chamada) and argumentos.get("x_mm") == 300, f"{time.time() - inicio:.1f}s, content={m1.content!r}")

mensagens.append(m1.model_dump(exclude_none=True))
mensagens.append({"role": "tool", "tool_call_id": chamada.id, "content": json.dumps({"cabe": False, "motivo": "X=300 passa de 220 mm"})})
inicio = time.time()
r2 = cliente.chat.completions.create(model=MODELO, messages=mensagens, tools=[FERRAMENTA], temperature=0.2, reasoning_effort="low")
texto = r2.choices[0].message.content or ""
relata("retorno da ferramenta", "não" in texto.lower() or "nao" in texto.lower(), f"{time.time() - inicio:.1f}s: {texto[:110]!r}")
relata("campo model da resposta", True, f"pedido {MODELO}, recebido {r2.model}")

imagem = base64.b64encode(png()).decode()
inicio = time.time()
r3 = cliente.chat.completions.create(
    model=MODELO,
    temperature=0,
    reasoning_effort="low",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "Qual cor está à esquerda e qual à direita? Responda em uma frase."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{imagem}"}},
        ],
    }],
)
texto = (r3.choices[0].message.content or "").lower()
relata("imagem", "vermelh" in texto and "azul" in texto, f"{time.time() - inicio:.1f}s: {texto[:110]!r}")
