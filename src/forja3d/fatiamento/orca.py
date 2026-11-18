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
from forja3d.configuracao import EMPACOTADO, ORCA_PADRAO, PASTA_CONFIG, RAIZ, pasta_usuario
from forja3d.fatiamento.cores import (
    CAMADA_INICIAL_MM,
    TORRE_ESCORVA_MM3,
    TORRE_LARGURA_MM,
    TORRE_PURGA_MM3,
    ErroDeCor,
    inserir_pausas_nas_trocas,
    inserir_pausas_por_altura,
    matriz_do_item,
    montar_stls_por_cor,
    plano_do_arquivo,
    posicao_da_torre,
)
from forja3d.fatiamento.tempo import formatar_duracao, segundos_do_tempo
from forja3d.fatiamento.validador_gcode import validar_gcode
from forja3d.layout_mesa import ItemMesa, LayoutMesa

TIMEOUT_S = 600.0
FOLGA_DE_SUPORTE_MM = 10.0  # A base do suporte passa da caixa da peça, e o quanto depende da forma dela.
FOLGA_DA_TORRE_MM = 8.0  # A torre tem uma aba em volta, além da caixa dela.
MENSAGENS_DE_ERRO = {
    -3: "O OrcaSlicer recusou um arquivo ou parâmetro.",
    -50: "A peça não cabe na área de impressão aceita pelo fatiador.",
    -51: "O fatiador recusou a combinação de configurações.",
    -101: "O fatiador encontrou peças de cores diferentes passando uma por dentro da outra.",
    -102: "A torre de purga ou uma peça saiu da área de impressão. Afaste as peças para deixar um canto livre, ou use menos cores.",
}
# O perfil da V3 SE vem preparado para um trocador automático: cada troca empurraria e puxaria 90 mm de filamento, e a torre passaria da mesa.
MAQUINA_SEM_TROCADOR = {"parking_pos_retraction": "0", "extra_loading_move": "0", "cooling_tube_length": "0", "cooling_tube_retraction": "0"}
FILAMENTO_SEM_TROCADOR = {"filament_cooling_moves": ["0"], "enable_filament_ramming": ["0"], "filament_multitool_ramming": ["0"]}
COR_SEM_NOME = "#FFFFFF"


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


@dataclass(frozen=True)
class Multicor:
    """Uma cor para cada STL, na mesma ordem, e onde fica a torre de purga."""

    cores: list[str | None]
    torre_x: float
    torre_y: float


def pasta_dados() -> Path:
    # O Orca grava nesta pasta, e a pasta de instalação não é lugar de dados do usuário.
    return pasta_usuario() / "orca_dados" if EMPACOTADO else RAIZ / "ferramentas" / "orca_dados"


def localizar_orca(configurado: Path | None = None) -> Path | None:
    candidatos = [configurado, ORCA_PADRAO]
    if os.name == "nt":
        candidatos.append(Path(os.environ.get("PROGRAMFILES", "")) / "OrcaSlicer" / "orca-slicer.exe")
    for candidato in candidatos:
        if candidato and candidato.is_file():
            return candidato
    encontrado = shutil.which("orca-slicer")
    return Path(encontrado) if encontrado else None


def _codigo_com_sinal(codigo: int) -> int:
    # O Windows devolve o código como 32 bits sem sinal: 4294967246 é -50. O Linux, como 8 bits: 206 é -50.
    if os.name != "nt":
        return codigo - 256 if codigo > 127 else codigo
    return codigo - 2**32 if codigo >= 2**31 else codigo


def _processo_derivado(parametros: ParametrosFatiamento, destino: Path, brim_mm: int, multicor: Multicor | None = None) -> Path:
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
    if multicor is not None:
        quantidade = len(multicor.cores)
        base.update(
            {
                "enable_prime_tower": "1",
                "prime_tower_width": f"{TORRE_LARGURA_MM:g}",
                "prime_volume": str(TORRE_ESCORVA_MM3),
                "wipe_tower_x": [f"{multicor.torre_x:g}"],
                "wipe_tower_y": [f"{multicor.torre_y:g}"],
                # Sem esta tabela, o OrcaSlicer fecha sozinho ao receber o segundo filamento pela linha de comando.
                "flush_volumes_matrix": ["0" if i == j else str(TORRE_PURGA_MM3) for i in range(quantidade) for j in range(quantidade)],
                # Sem esta ordem, ele começa pela cor de maior área. Os STL vêm com a cor mais alta por último:
                # assim a primeira camada termina na cor que segue para cima, e a volta para ela não custa outra troca.
                "first_layer_print_sequence": [str(i) for i in range(1, quantidade + 1)],
                "flush_volumes_vector": [str(TORRE_PURGA_MM3)] * (2 * quantidade),
                "flush_multiplier": "1",
            }
        )
    destino.write_text(json.dumps(base), encoding="utf-8")
    return destino


def _configuracoes_de_cor(multicor: Multicor, material: str, trabalho: Path) -> tuple[Path, list[Path]]:
    """A máquina e um filamento por cor. Todos do mesmo material: a temperatura não muda no meio da peça."""
    cores = [cor or COR_SEM_NOME for cor in multicor.cores]
    maquina = json.loads((PASTA_CONFIG / "orca" / "maquina.json").read_text(encoding="utf-8"))
    maquina.update({"extruder_colour": cores, **MAQUINA_SEM_TROCADOR})
    caminho_maquina = trabalho / "maquina.json"
    caminho_maquina.write_text(json.dumps(maquina), encoding="utf-8")
    filamento = json.loads((PASTA_CONFIG / "orca" / f"filamento_{material}.json").read_text(encoding="utf-8"))
    filamentos = []
    for indice, cor in enumerate(cores, start=1):
        caminho = trabalho / f"filamento_{indice}.json"
        caminho.write_text(json.dumps({**filamento, "filament_colour": [cor], **FILAMENTO_SEM_TROCADOR}), encoding="utf-8")
        filamentos.append(caminho)
    return caminho_maquina, filamentos


def _conferir_pedido(parametros: ParametrosFatiamento, perfil: dict[str, Any], caminho_orca: Path | None) -> Path:
    orca = localizar_orca(caminho_orca)
    if orca is None:
        raise ErroFatiamento(
            "Não encontrei o OrcaSlicer. Indique o caminho do executável dele em Configurações. "
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
    multicor: Multicor | None = None,
) -> None:
    dados_orca = pasta_dados()
    dados_orca.mkdir(parents=True, exist_ok=True)
    processo_json = _processo_derivado(parametros, trabalho / "processo.json", brim_mm, multicor)
    maquina = PASTA_CONFIG / "orca" / "maquina.json"
    filamentos = str(PASTA_CONFIG / "orca" / f"filamento_{parametros.material}.json")
    por_cor: list[str] = []
    if multicor is not None:
        maquina, lista = _configuracoes_de_cor(multicor, parametros.material, trabalho)
        filamentos = ";".join(str(f) for f in lista)
        por_cor = ["--load-filament-ids", ",".join(str(i) for i in range(1, len(multicor.cores) + 1))]
    comando = [
        str(orca),
        "--datadir", str(dados_orca),
        "--load-settings", f"{maquina};{processo_json}",
        "--load-filaments", filamentos,
        *por_cor,
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
        malha.apply_transform(matriz_do_item(malha, item))
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
        "trocas_de_filamento": sum(v.get("trocas_de_filamento", 0) for v in validacoes),
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


def _fatiar_em_cores(
    layout: LayoutMesa,
    numero: int,
    itens: list[ItemMesa],
    stls: dict[str, Path],
    medidas: dict[str, dict[str, Any]],
    cores: tuple[str | None, ...],
    parametros: ParametrosFatiamento,
    perfil: dict[str, Any],
    brim_mm: int,
    trabalho: Path,
    destino: Path,
    orca: Path,
    cancelar: threading.Event | None,
    area: tuple[float, float, float, float],
) -> tuple[dict[str, Any], tuple[float, float, float, float]]:
    """Fatia com um filamento por cor e a torre de purga. Devolve o roteiro de trocas e a área que a impressão ocupa, com a torre."""
    camada = float(perfil["qualidades"][parametros.qualidade]["camada_mm"])
    torre = posicao_da_torre(layout, numero, len(cores), camada)
    if torre is None:
        raise ErroFatiamento(f"O arquivo {numero} não deixou espaço na mesa para a torre de purga, que limpa o bico a cada troca. Afaste as peças de um canto, ou use menos cores.")
    por_cor = montar_stls_por_cor(itens, stls, medidas, trabalho)
    ordem = [cor for cor, _ in por_cor]
    _rodar_orca(orca, [caminho for _, caminho in por_cor], parametros, brim_mm, trabalho, destino, cancelar, False, Multicor(ordem, torre[0], torre[1]))
    x, y, largura, profundidade = torre
    area_com_torre = (
        min(area[0], x - FOLGA_DA_TORRE_MM),
        max(area[1], x + largura + FOLGA_DA_TORRE_MM),
        min(area[2], y - FOLGA_DA_TORRE_MM),
        max(area[3], y + profundidade + FOLGA_DA_TORRE_MM),
    )
    return inserir_pausas_nas_trocas(destino, ordem, perfil["validacao_gcode"]["z_max"]), area_com_torre


def fatiar_layout(
    layout: LayoutMesa,
    stls: dict[str, Path],
    parametros: ParametrosFatiamento,
    perfil: dict[str, Any],
    pasta_saida: Path,
    caminho_orca: Path | None = None,
    cancelar: threading.Event | None = None,
    medidas: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Gera um G-code por arquivo da mesa, cada um com os itens nas posições escolhidas.

    Com cores, `medidas` diz os corpos de cada parte, e o arquivo sai pronto para as trocas manuais.
    """
    orca = _conferir_pedido(parametros, perfil, caminho_orca)
    medidas = medidas or {}
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
        area = layout.area_do_arquivo(numero)
        try:
            plano = plano_do_arquivo(layout, numero, medidas)
            with tempfile.TemporaryDirectory(prefix="forja3d_orca_") as temporaria:
                trabalho = Path(temporaria)
                if plano.multicor:
                    cores_do_arquivo, area = _fatiar_em_cores(layout, numero, itens, stls, medidas, plano.cores, parametros, perfil, brim_mm, trabalho, destino, orca, cancelar, area)
                else:
                    stl = trabalho / "mesa.stl"
                    montar_stl_da_mesa(itens, stls, stl)
                    _rodar_orca(orca, [stl], parametros, brim_mm, trabalho, destino, cancelar, organizar=False)
                    cor = plano.cores[0] if plano.cores else None
                    cores_do_arquivo = {"cor_inicial": cor, "trocas": [], "gramas_por_cor": []}
                    if plano.alturas:
                        cores_do_arquivo = inserir_pausas_por_altura(destino, list(plano.alturas), cor, perfil["validacao_gcode"]["z_max"])
        except ErroDeCor as erro:
            raise ErroFatiamento(str(erro)) from erro
        validacao = validar_gcode(
            destino,
            perfil,
            parametros.material,
            CAMADA_INICIAL_MM,
            None,
            brim_mm if parametros.brim else 0.0,
            area,
            FOLGA_DE_SUPORTE_MM if parametros.suporte else 0.0,
            len(cores_do_arquivo["trocas"]),
        )
        arquivos.append({"numero": numero, "gcode": destino.name, "itens": [i.parte for i in itens], "validacao": validacao, "cores": cores_do_arquivo})

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
