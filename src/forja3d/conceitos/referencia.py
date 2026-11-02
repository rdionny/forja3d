"""Prepara a imagem de referência antes de ela sair do computador."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

TAMANHO_MAX_BYTES = 10 * 1024 * 1024
LADO_MAX_PX = 1536
FORMATOS = {"PNG", "JPEG", "WEBP"}
EXTENSOES = {".png", ".jpg", ".jpeg", ".webp"}
MENSAGEM_FORMATO = "Use uma imagem PNG, JPG ou WEBP."
MENSAGEM_TAMANHO = "A imagem passa de 10 MB. Use uma imagem menor."


class ReferenciaInvalida(Exception):
    """A imagem não pode ser usada, com uma mensagem pronta para o usuário."""


def _ler(origem: bytes | Path) -> bytes:
    if not isinstance(origem, Path):
        return origem
    try:
        if origem.stat().st_size > TAMANHO_MAX_BYTES:
            raise ReferenciaInvalida(MENSAGEM_TAMANHO)
        return origem.read_bytes()
    except OSError as erro:
        raise ReferenciaInvalida("Não consegui ler esse arquivo de imagem.") from erro


def preparar_referencia(origem: bytes | Path) -> bytes:
    """Devolve um PNG de até 1536 px, sem nenhum metadado.

    A conexão com a IA não tem criptografia, então GPS, data e câmera nunca saem daqui.
    """
    dados = _ler(origem)
    if len(dados) > TAMANHO_MAX_BYTES:
        raise ReferenciaInvalida(MENSAGEM_TAMANHO)
    try:
        with Image.open(io.BytesIO(dados)) as aberta:
            if aberta.format not in FORMATOS:
                raise ReferenciaInvalida(MENSAGEM_FORMATO)
            girada = ImageOps.exif_transpose(aberta)
            modo = "RGBA" if girada.has_transparency_data else "RGB"
            convertida = girada.convert(modo)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as erro:
        raise ReferenciaInvalida(MENSAGEM_FORMATO) from erro

    convertida.thumbnail((LADO_MAX_PX, LADO_MAX_PX), Image.Resampling.LANCZOS)
    # Uma imagem nova não herda nada de `info`: nem EXIF, nem perfil de cor, nem comentários.
    limpa = Image.new(modo, convertida.size)
    limpa.paste(convertida)
    saida = io.BytesIO()
    limpa.save(saida, format="PNG", optimize=True)
    return saida.getvalue()
