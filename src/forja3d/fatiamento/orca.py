"""Fatiamento pela CLI do OrcaSlicer. Veja a seção 9.7 do prompt de desenvolvimento."""
from __future__ import annotations

import json
import math
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
from forja3d.fatiamento.tempo import formatar_duracao, segundos_do_tempo
from forja3d.fatiamento.validador_gcode import validar_gcode
from forja3d.layout_mesa import ItemMesa, LayoutMesa

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


def _conferir_pedido(parametros: ParametrosFatiamento, perfil: dict[str, Any], caminho_orca: Path | None) -> Path:
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
    return orca


def _rodar_orca(
    orca: Path,
    stls: list[Path],
    parametros: ParametrosFatiamento,
    brim_mm: int,
    trabalho: Path,
    destino: Path,
    cancelar: threading.Event | None,
    organizar: bool,
) -> None:
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    processo_json = _processo_derivado(parametros, trabalho / "processo.json", brim_mm)
    comando = [
        str(orca),
        "--datadir", str(PASTA_DADOS),
        "--load-settings", f"{PASTA_CONFIG / 'orca' / 'maquina.json'};{processo_json}",
        "--load-filaments", str(PASTA_CONFIG / "orca" / f"filamento_{parametros.material}.json"),
        "--arrange", "1" if organizar else "0",
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
    shutil.copyfile(gerado, destino)


def montar_stl_da_mesa(itens: list[ItemMesa], stls: dict[str, Path], destino: Path) -> None:
    """Junta os itens de um arquivo em um STL só, cada um na sua posição da mesa."""
    import trimesh

    originais = {parte: trimesh.load_mesh(stls[parte], process=False) for parte in {i.parte for i in itens}}
    pecas = []
    for item in itens:
        malha = originais[item.parte].copy()
        if item.giro:
            malha.apply_transform(trimesh.transformations.rotation_matrix(math.radians(item.giro), (0, 0, 1)))
        (x0, y0, z0), (x1, y1, _) = malha.bounds
        malha.apply_translation((item.x - (x0 + x1) / 2, item.y - (y0 + y1) / 2, -z0))
        pecas.append(malha)
    trimesh.util.concatenate(pecas).export(destino)


def _resumir_arquivos(arquivos: list[dict[str, Any]]) -> dict[str, Any]:
    """A validação geral: aprova só se todos aprovarem, e os problemas dizem de qual arquivo são."""
    if len(arquivos) == 1:
        return {**arquivos[0]["validacao"], "tempo_s": segundos_do_tempo(arquivos[0]["validacao"].get("tempo"))}

    def juntar(campo: str) -> list[dict[str, Any]]:
        return [{**item, "mensagem": f"Arquivo {a['numero']}: {item['mensagem']}"} for a in arquivos for item in a["validacao"][campo]]

    validacoes = [a["validacao"] for a in arquivos]
    informacoes = {i["codigo"]: i for v in validacoes for i in v["informacoes"]}
    tempos = [segundos_do_tempo(v.get("tempo")) for v in validacoes]
    total = sum(tempos) if all(tempos) else None
    geral: dict[str, Any] = {
        "aprovado": all(v["aprovado"] for v in validacoes),
        "bloqueios": juntar("bloqueios"),
        "alertas": juntar("alertas"),
        "informacoes": list(informacoes.values()),
        "camadas": sum(v["camadas"] for v in validacoes),
        "z_maximo_mm": max(v["z_maximo_mm"] for v in validacoes),
        "bico_c": max((v["bico_c"] for v in validacoes if v["bico_c"] is not None), default=None),
        "mesa_c": max((v["mesa_c"] for v in validacoes if v["mesa_c"] is not None), default=None),
        "tempo_s": total,
    }
    if total:
        geral["tempo"] = formatar_duracao(total)
    for campo in ("filamento_g", "filamento_m"):
        if all(campo in v for v in validacoes):
            geral[campo] = round(sum(v[campo] for v in validacoes), 2)
    return geral


def fatiar_layout(
    layout: LayoutMesa,
    stls: dict[str, Path],
    parametros: ParametrosFatiamento,
    perfil: dict[str, Any],
    pasta_saida: Path,
    caminho_orca: Path | None = None,
    cancelar: threading.Event | None = None,
) -> dict[str, Any]:
    """Gera um G-code por arquivo da mesa, cada um com os itens nas posições escolhidas."""
    orca = _conferir_pedido(parametros, perfil, caminho_orca)
    numeros = layout.arquivos()
    if not numeros:
        raise ErroFatiamento("A mesa está vazia. Adicione ao menos um item para preparar a impressão.")

    brim_mm = perfil["processo_padrao"]["brim_mm"]
    pasta_saida.mkdir(parents=True, exist_ok=True)
    # Uma preparação anterior pode ter tido mais arquivos do que esta.
    for antigo in pasta_saida.glob("*.gcode"):
        antigo.unlink()
    arquivos = []
    for numero in numeros:
        itens = layout.itens_do_arquivo(numero)
        destino = pasta_saida / ("peca.gcode" if len(numeros) == 1 else f"arquivo_{numero}.gcode")
        with tempfile.TemporaryDirectory(prefix="forja3d_orca_") as temporaria:
            trabalho = Path(temporaria)
            stl = trabalho / "mesa.stl"
            montar_stl_da_mesa(itens, stls, stl)
            _rodar_orca(orca, [stl], parametros, brim_mm, trabalho, destino, cancelar, organizar=False)
        validacao = validar_gcode(
            destino, perfil, parametros.material, CAMADA_INICIAL_MM, None, brim_mm if parametros.brim else 0.0, layout.area_do_arquivo(numero)
        )
        arquivos.append({"numero": numero, "gcode": destino.name, "itens": [i.parte for i in itens], "validacao": validacao})

    resumo = {
        "parametros": parametros.__dict__,
        "gcode": arquivos[0]["gcode"],
        "orca": perfil["orca"]["versao"],
        "layout": layout.assinatura(),
        "arquivos": arquivos,
        "validacao": _resumir_arquivos(arquivos),
    }
    (pasta_saida / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    return resumo


def fatiar(
    stls: list[Path],
    parametros: ParametrosFatiamento,
    perfil: dict[str, Any],
    pasta_saida: Path,
    caminho_orca: Path | None = None,
    cancelar: threading.Event | None = None,
    tamanho_peca_xy: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Fatia, deixando o OrcaSlicer organizar a mesa, e grava `peca.gcode` e `resumo.json`."""
    orca = _conferir_pedido(parametros, perfil, caminho_orca)
    brim_mm = perfil["processo_padrao"]["brim_mm"]
    pasta_saida.mkdir(parents=True, exist_ok=True)
    destino = pasta_saida / "peca.gcode"
    with tempfile.TemporaryDirectory(prefix="forja3d_orca_") as temporaria:
        _rodar_orca(orca, stls, parametros, brim_mm, Path(temporaria), destino, cancelar, organizar=True)

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
