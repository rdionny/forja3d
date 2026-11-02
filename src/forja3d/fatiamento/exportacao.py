"""Exportação do G-code validado. Só a interface chama isto, depois do clique do usuário."""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import string
import unicodedata
from pathlib import Path

COMPRIMENTO_MAXIMO = 26
UNIDADE_REMOVIVEL = 2


class ExportacaoBloqueada(Exception):
    """O G-code não passou na validação."""


def nome_sugerido(nome_peca: str, material: str) -> str:
    """Nome em ASCII de até 26 caracteres, o limite seguro do visor pelo firmware."""
    sem_acentos = unicodedata.normalize("NFKD", nome_peca).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "_", sem_acentos.lower()).strip("_") or "peca"
    sufixo = f"_{material.lower()}.gcode"
    return f"{base[: COMPRIMENTO_MAXIMO - len(sufixo)].rstrip('_')}{sufixo}"


def unidades_removiveis() -> list[Path]:
    if os.name != "nt":
        return []
    unidades = []
    for letra in string.ascii_uppercase:
        raiz = f"{letra}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(raiz) == UNIDADE_REMOVIVEL:
            unidades.append(Path(raiz))
    return unidades


def exportar_gcode(pasta_impressao: Path, destino: Path) -> Path:
    """Recusa se o resumo não disser que a validação aprovou o arquivo."""
    resumo_arquivo = pasta_impressao / "resumo.json"
    origem = pasta_impressao / "peca.gcode"
    if not resumo_arquivo.exists() or not origem.exists():
        raise ExportacaoBloqueada("Esta versão ainda não foi fatiada.")
    resumo = json.loads(resumo_arquivo.read_text(encoding="utf-8"))
    if not resumo.get("validacao", {}).get("aprovado"):
        raise ExportacaoBloqueada("O G-code foi reprovado na validação e não pode ser exportado.")
    temporario = destino.with_name(destino.name + ".tmp")
    shutil.copyfile(origem, temporario)
    temporario.replace(destino)
    return destino
