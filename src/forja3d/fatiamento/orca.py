"""Fatiamento pela CLI do OrcaSlicer. Veja a seção 9.7 do prompt de desenvolvimento."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forja3d.cad.executor import SEM_JANELA, aguardar
from forja3d.configuracao import ORCA_PADRAO, PASTA_CONFIG, RAIZ
from forja3d.fatiamento.validador_gcode import validar_gcode

TIMEOUT_S = 600.0
PASTA_DADOS = RAIZ / "ferramentas" / "orca_dados"
CAMADA_INICIAL_MM = 0.2  # Igual em todos os presets de qualidade da V3 SE.
MENSAGENS_DE_ERRO = {
    -3: "O OrcaSlicer recusou um arquivo ou parâmetro.",
    -50: "A peça não cabe na área de impressão aceita pelo fatiador.",
    -51: "O fatiador recusou a combinação de configurações.",
}


class ErroFatiamento(Exception):
    """Falha ao fatiar, com mensagem pronta para o usuário."""


@dataclass(frozen=True)
class ParametrosFatiamento:
    material: str
    qualidade: str = "padrao"
    paredes: int = 3
    preenchimento_pct: int = 15
    suporte: bool = False
    brim: bool = False


def localizar_orca(configurado: Path | None = None) -> Path | None:
    candidatos = [configurado, ORCA_PADRAO, Path(os.environ.get("PROGRAMFILES", "")) / "OrcaSlicer" / "orca-slicer.exe"]
    for candidato in candidatos:
        if candidato and candidato.is_file():
            return candidato
    encontrado = shutil.which("orca-slicer")
    return Path(encontrado) if encontrado else None


def _codigo_com_sinal(codigo: int) -> int:
    # O Windows devolve o código como 32 bits sem sinal: 4294967246 é -50.
    return codigo - 2**32 if codigo >= 2**31 else codigo


def _processo_derivado(parametros: ParametrosFatiamento, destino: Path, brim_mm: int) -> Path:
    base = json.loads((PASTA_CONFIG / "orca" / f"processo_{parametros.qualidade}.json").read_text(encoding="utf-8"))
    base.update(
        {
            "wall_loops": str(parametros.paredes),
            "sparse_infill_density": f"{parametros.preenchimento_pct}%",
            "enable_support": "1" if parametros.suporte else "0",
            "brim_type": "outer_only" if parametros.brim else "no_brim",
            "brim_width": str(brim_mm),
        }
    )
    destino.write_text(json.dumps(base), encoding="utf-8")
    return destino


def fatiar(
    stls: list[Path],
    parametros: ParametrosFatiamento,
    perfil: dict[str, Any],
    pasta_saida: Path,
    caminho_orca: Path | None = None,
    cancelar: threading.Event | None = None,
    tamanho_peca_xy: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Fatia, valida e grava `peca.gcode` e `resumo.json` em `pasta_saida`."""
    orca = localizar_orca(caminho_orca)
    if orca is None:
        raise ErroFatiamento(
            "Não encontrei o OrcaSlicer. Indique o caminho de orca-slicer.exe em Configurações. "
            "A modelagem continua funcionando sem ele."
        )
    if parametros.material not in perfil["materiais"]:
        raise ErroFatiamento(f"Material não suportado: {parametros.material}.")
    if parametros.qualidade not in perfil["qualidades"]:
        raise ErroFatiamento(f"Qualidade desconhecida: {parametros.qualidade}.")

    brim_mm = perfil["processo_padrao"]["brim_mm"]
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forja3d_orca_") as temporaria:
        trabalho = Path(temporaria)
        processo_json = _processo_derivado(parametros, trabalho / "processo.json", brim_mm)
        comando = [
            str(orca),
            "--datadir", str(PASTA_DADOS),
            "--load-settings", f"{PASTA_CONFIG / 'orca' / 'maquina.json'};{processo_json}",
            "--load-filaments", str(PASTA_CONFIG / "orca" / f"filamento_{parametros.material}.json"),
            "--arrange", "1",
            "--slice", "0",
            "--export-3mf", "saida.gcode.3mf",
            "--outputdir", str(trabalho),
            *(str(s) for s in stls),
        ]
        # O Orca grava 00000.log na pasta atual, por isso o cwd temporário.
        with open(trabalho / "stderr.txt", "wb") as erros:
            processo = subprocess.Popen(
                comando, cwd=trabalho, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=erros, creationflags=SEM_JANELA
            )
            estado = aguardar(processo, TIMEOUT_S, cancelar)
        if estado == "timeout":
            raise ErroFatiamento("O fatiamento passou de 10 minutos e foi encerrado.")

        codigo = _codigo_com_sinal(processo.returncode)
        gerado = trabalho / "plate_1.gcode"
        if codigo != 0 or not gerado.exists() or gerado.stat().st_size == 0:
            detalhe = (trabalho / "stderr.txt").read_text(encoding="utf-8", errors="replace").strip()
            mensagem = MENSAGENS_DE_ERRO.get(codigo, f"O OrcaSlicer encerrou com o código {codigo}.")
            raise ErroFatiamento(f"{mensagem} {detalhe}".strip())
        destino = pasta_saida / "peca.gcode"
        shutil.copyfile(gerado, destino)

    validacao = validar_gcode(
        destino, perfil, parametros.material, CAMADA_INICIAL_MM, tamanho_peca_xy, brim_mm if parametros.brim else 0.0
    )
    resumo = {
        "parametros": parametros.__dict__,
        "gcode": destino.name,
        "orca": perfil["orca"]["versao"],
        "validacao": validacao,
    }
    (pasta_saida / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    return resumo
