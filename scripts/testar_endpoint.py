"""Testa o endpoint de IA configurado em config/ia_padrao.toml e relata o resultado de cada item."""

import argparse
import base64
import io
import json
import sys
import time
import tomllib
from pathlib import Path

from openai import OpenAI
from PIL import Image, ImageDraw

CONFIG_PADRAO = Path(__file__).resolve().parent.parent / "config" / "ia_padrao.toml"
MODELO_IMAGEM_PADRAO = "gemini-3.1-flash-image"

FERRAMENTA_CUBO = {
    "type": "function",
    "function": {
        "name": "criar_cubo",
        "description": "Cria um cubo",
        "parameters": {
            "type": "object",
            "properties": {"lado_mm": {"type": "number"}},
            "required": ["lado_mm"],
        },
    },
}
PEDIDO_CUBO = [{"role": "user", "content": "Crie um cubo de 20 mm usando a ferramenta."}]


class FalhaDeTeste(Exception):
    pass


def mascarar(chave: str) -> str:
    return chave[:2] + "***"


def imagem_de_teste() -> str:
    """Quadrado vermelho sobre fundo branco, para a pergunta ter uma única resposta certa."""
    imagem = Image.new("RGB", (256, 256), "white")
    ImageDraw.Draw(imagem).rectangle((64, 64, 192, 192), fill="red")
    saida = io.BytesIO()
    imagem.save(saida, format="PNG")
    return "data:image/png;base64," + base64.b64encode(saida.getvalue()).decode()


def conferir_chamada_de_cubo(nome: str | None, argumentos: str) -> str:
    if nome != "criar_cubo":
        raise FalhaDeTeste(f"ferramenta inesperada: {nome}")
    try:
        lado = json.loads(argumentos)["lado_mm"]
    except (json.JSONDecodeError, KeyError) as erro:
        raise FalhaDeTeste(f"argumentos inválidos: {argumentos!r}") from erro
    if lado != 20:
        raise FalhaDeTeste(f"lado_mm esperado 20, veio {lado}")
    return f"{nome}(lado_mm={lado})"


def testar_modelos(cliente: OpenAI, config: dict) -> str:
    ids = {modelo.id for modelo in cliente.models.list().data}
    faltando = [m for m in (config["modelo"], config["modelo_imagem"]) if m not in ids]
    if faltando:
        raise FalhaDeTeste(f"fora da lista: {', '.join(faltando)}")
    return f"{len(ids)} modelos, incluindo {config['modelo']} e {config['modelo_imagem']}"


def testar_texto(cliente: OpenAI, config: dict) -> str:
    resposta = cliente.chat.completions.create(
        model=config["modelo"],
        messages=[{"role": "user", "content": "Responda apenas: ok"}],
        temperature=config["temperatura"],
        max_tokens=config["max_tokens"],
    )
    texto = (resposta.choices[0].message.content or "").strip()
    if "ok" not in texto.lower():
        raise FalhaDeTeste(f"resposta inesperada: {texto!r}")
    return f"respondeu {texto!r}"


def testar_ferramenta(cliente: OpenAI, config: dict) -> str:
    resposta = cliente.chat.completions.create(
        model=config["modelo"],
        messages=PEDIDO_CUBO,
        tools=[FERRAMENTA_CUBO],
        tool_choice="auto",
        temperature=config["temperatura"],
        max_tokens=config["max_tokens"],
    )
    chamadas = resposta.choices[0].message.tool_calls
    if not chamadas:
        raise FalhaDeTeste("o modelo não chamou a ferramenta")
    return conferir_chamada_de_cubo(chamadas[0].function.name, chamadas[0].function.arguments)


def testar_leitura_de_imagem(cliente: OpenAI, config: dict) -> str:
    resposta = cliente.chat.completions.create(
        model=config["modelo"],
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Qual a cor do quadrado? Responda só o nome da cor, em português."},
                    {"type": "image_url", "image_url": {"url": imagem_de_teste()}},
                ],
            }
        ],
        temperature=0,
        max_tokens=config["max_tokens"],
    )
    texto = (resposta.choices[0].message.content or "").strip()
    if "vermelh" not in texto.lower():
        raise FalhaDeTeste(f"cor errada: {texto!r}")
    return f"leu a cor certa: {texto!r}"


def testar_streaming_com_ferramenta(cliente: OpenAI, config: dict) -> str:
    fluxo = cliente.chat.completions.create(
        model=config["modelo"],
        messages=PEDIDO_CUBO,
        tools=[FERRAMENTA_CUBO],
        tool_choice="auto",
        temperature=config["temperatura"],
        max_tokens=config["max_tokens"],
        stream=True,
    )
    nome, argumentos, pedacos = None, "", 0
    for pedaco in fluxo:
        if not pedaco.choices:
            continue
        pedacos += 1
        for chamada in pedaco.choices[0].delta.tool_calls or []:
            if chamada.function and chamada.function.name:
                nome = chamada.function.name
            if chamada.function and chamada.function.arguments:
                argumentos += chamada.function.arguments
    if nome is None:
        raise FalhaDeTeste(f"nenhuma chamada de ferramenta em {pedacos} pedaços")
    return f"{conferir_chamada_de_cubo(nome, argumentos)}, em {pedacos} pedaços"


def testar_geracao_de_imagem(cliente: OpenAI, config: dict) -> str:
    resposta = cliente.chat.completions.create(
        model=config["modelo_imagem"],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Gere a foto de um cubo vermelho impresso em 3D, inspirada na referência.",
                    },
                    {"type": "image_url", "image_url": {"url": imagem_de_teste()}},
                ],
            }
        ],
        max_tokens=8000,
    )
    imagens = (resposta.choices[0].message.model_extra or {}).get("images") or []
    if not imagens:
        raise FalhaDeTeste("a resposta veio sem imagem")
    bruto = base64.b64decode(imagens[0]["image_url"]["url"].split("base64,", 1)[1])
    imagem = Image.open(io.BytesIO(bruto))
    imagem.load()
    return f"{imagem.format}, {imagem.width} x {imagem.height}, {len(bruto)} bytes"


ITENS = [
    ("Modelos na lista", testar_modelos),
    ("Resposta de texto", testar_texto),
    ("Chamada de ferramenta", testar_ferramenta),
    ("Leitura de imagem", testar_leitura_de_imagem),
    ("Streaming com ferramenta", testar_streaming_com_ferramenta),
    ("Geração de imagem", testar_geracao_de_imagem),
]


def ler_config(caminho: Path) -> dict:
    ia = tomllib.loads(caminho.read_text(encoding="utf-8"))["ia"]
    return {
        "base_url": ia["endpoint"],
        "api_key": ia["api_key"],
        "modelo": ia["modelo"],
        "modelo_imagem": ia.get("modelo_imagem", MODELO_IMAGEM_PADRAO),
        "temperatura": ia.get("temperatura", 0.2),
        "max_tokens": ia.get("max_tokens", 16000),
        "tempo_limite_s": ia.get("timeout_s", 240),
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    argumentos = argparse.ArgumentParser(description=__doc__)
    argumentos.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    caminho = argumentos.parse_args(argv).config
    if not caminho.is_file():
        print(f"Arquivo não encontrado: {caminho}")
        return 1
    config = ler_config(caminho)
    chave = config["api_key"]
    cliente = OpenAI(base_url=config["base_url"], api_key=chave, timeout=config["tempo_limite_s"], max_retries=0)

    print(f"Endpoint: {config['base_url']} | chave: {mascarar(chave)}")
    print(f"Modelo: {config['modelo']} | modelo de imagem: {config['modelo_imagem']}\n")
    aprovados = 0
    for nome, teste in ITENS:
        inicio = time.monotonic()
        try:
            detalhe = teste(cliente, config)
            situacao = " OK  "
            aprovados += 1
        except Exception as erro:  # noqa: BLE001 - um item com falha não pode impedir os seguintes.
            detalhe = f"{type(erro).__name__}: {erro}".replace(chave, mascarar(chave))[:300]
            situacao = "FALHA"
        print(f"[{situacao}] {nome} ({time.monotonic() - inicio:.1f} s): {detalhe}")

    print(f"\nResultado: {aprovados} de {len(ITENS)} itens aprovados.")
    return 0 if aprovados == len(ITENS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
