"""Análise da malha: base, balanços, pontes e espessura."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import trimesh

TOLERANCIA_BASE = 0.01
ALTURA_ZONA_DA_BASE = 1.0
MAX_AMOSTRAS = 1500
MAX_OPERACOES = 60_000_000
AREA_MINIMA_DE_PONTE = 4.0


def _amostrar_regiao(malha: trimesh.Trimesh, faces: np.ndarray, quantidade: int = 400) -> np.ndarray:
    gerador = np.random.default_rng(7)
    areas = malha.area_faces[faces]
    escolhidas = gerador.choice(faces, size=quantidade, p=areas / areas.sum())
    a, b = gerador.random((2, quantidade, 1))
    invertidos = (a + b) > 1
    a = np.where(invertidos, 1 - a, a)
    b = np.where(invertidos, 1 - b, b)
    t = malha.triangles[escolhidas]
    return (t[:, 0] + a * (t[:, 1] - t[:, 0]) + b * (t[:, 2] - t[:, 0]))[:, :2]


def _distancia_aos_segmentos(pontos: np.ndarray, inicio: np.ndarray, fim: np.ndarray) -> np.ndarray:
    """Para cada ponto, a menor distância até algum segmento."""
    direcao = fim - inicio
    tamanho = np.maximum((direcao**2).sum(1), 1e-12)
    relativo = pontos[:, None, :] - inicio[None]
    t = np.clip((relativo * direcao[None]).sum(-1) / tamanho[None], 0.0, 1.0)
    mais_perto = inicio[None] + t[..., None] * direcao[None]
    return np.sqrt(((pontos[:, None, :] - mais_perto) ** 2).sum(-1)).min(axis=1)


def _maior_vao(malha: trimesh.Trimesh, mascara: np.ndarray) -> float:
    """Vão de cada teto sem apoio, medido entre as bordas que têm parede embaixo.

    Usar o lado menor da região erra: uma viga de 20 mm de largura entre dois pilares
    afastados 50 mm é uma ponte de 50 mm, não de 20 mm.
    """
    indices = np.nonzero(mascara)[0]
    if not len(indices):
        return 0.0
    pares = malha.face_adjacency
    arestas = malha.face_adjacency_edges
    vertice_oposto = malha.face_adjacency_unshared
    ambos = mascara[pares[:, 0]] & mascara[pares[:, 1]]
    maior = 0.0
    for grupo in trimesh.graph.connected_components(pares[ambos], nodes=indices, min_len=1):
        grupo = np.asarray(grupo)
        if malha.area_faces[grupo].sum() < AREA_MINIMA_DE_PONTE:
            continue
        no_grupo = np.zeros(len(malha.faces), dtype=bool)
        no_grupo[grupo] = True
        primeiro_dentro = no_grupo[pares[:, 0]]
        borda = primeiro_dentro != no_grupo[pares[:, 1]]
        # O vizinho de fora é um apoio quando a face dele desce a partir da aresta.
        oposto = np.where(primeiro_dentro[borda], vertice_oposto[borda, 1], vertice_oposto[borda, 0])
        arestas_borda = arestas[borda]
        z_aresta = malha.vertices[arestas_borda][:, :, 2].mean(axis=1)
        apoiada = malha.vertices[oposto, 2] < z_aresta - 1e-3

        pontos = _amostrar_regiao(malha, grupo)
        if not apoiada.any():
            # Ilha sem nenhum apoio: o vão é o maior comprimento da região.
            maior = max(maior, float(np.ptp(pontos, axis=0).max()))
            continue
        extremos = malha.vertices[arestas_borda[apoiada]][:, :, :2]
        distancia = _distancia_aos_segmentos(pontos, extremos[:, 0], extremos[:, 1])
        maior = max(maior, 2.0 * float(distancia.max()))
    return maior


def _espessuras(malha: trimesh.Trimesh) -> np.ndarray:
    """Lança raios para dentro da peça, com Möller–Trumbore vetorizado."""
    triangulos = malha.triangles
    total = len(triangulos)
    amostras = min(MAX_AMOSTRAS, max(100, MAX_OPERACOES // max(total, 1)))
    pontos, faces = trimesh.sample.sample_surface(malha, amostras, seed=7)
    direcoes = -malha.face_normals[faces]
    origens = pontos + direcoes * 1e-4

    v0 = triangulos[:, 0]
    e1 = triangulos[:, 1] - v0
    e2 = triangulos[:, 2] - v0
    resultado = np.full(len(origens), np.inf)
    bloco = max(8, min(256, 4_000_000 // max(total, 1)))
    for i in range(0, len(origens), bloco):
        o = origens[i : i + bloco, None, :]
        d = direcoes[i : i + bloco, None, :]
        p = np.cross(d, e2[None])
        det = (e1[None] * p).sum(-1)
        with np.errstate(divide="ignore", invalid="ignore"):
            inverso = 1.0 / det
            s = o - v0[None]
            u = (s * p).sum(-1) * inverso
            q = np.cross(s, e1[None])
            v = (d * q).sum(-1) * inverso
            t = (e2[None] * q).sum(-1) * inverso
            valido = (np.abs(det) > 1e-12) & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3)
        resultado[i : i + bloco] = np.where(valido, t, np.inf).min(axis=1)
    return resultado[np.isfinite(resultado)]


def _carregar(caminho: Path) -> trimesh.Trimesh:
    malha = trimesh.load_mesh(caminho, force="mesh")
    # O CAD deixa triângulos de área zero, como nos polos de uma esfera. Eles não são buraco, e contá-los fazia uma peça fechada parecer aberta.
    malha.update_faces(malha.nondegenerate_faces())
    return malha


def _regioes(malha: trimesh.Trimesh, balanco_max_graus: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """As faces da base, em balanço e de teto, na posição em que a malha está."""
    normais = malha.face_normals
    centros = malha.triangles_center
    zmin = float(malha.bounds[0, 2])
    na_base = (np.abs(centros[:, 2] - zmin) < TOLERANCIA_BASE) & (normais[:, 2] < -0.999)
    longe_da_base = centros[:, 2] > zmin + ALTURA_ZONA_DA_BASE
    # 0° é uma parede vertical e 90° é um teto.
    inclinacao = np.degrees(np.arcsin(np.clip(-normais[:, 2], -1.0, 1.0)))
    balanco = longe_da_base & (inclinacao > balanco_max_graus + 1) & (inclinacao < 85)
    teto = ~na_base & (centros[:, 2] > zmin + TOLERANCIA_BASE) & (inclinacao >= 85)
    return na_base, balanco, teto


def analisar_posicao(caminho: Path, tombo: list[float], balanco_max_graus: float) -> dict[str, float]:
    """Quanto a peça deitada encosta na mesa e quanto fica no ar. Rápida o bastante para rodar logo depois de tombar."""
    malha = _carregar(caminho)
    rotacao = np.eye(4)
    rotacao[:3, :3] = np.reshape(tombo, (3, 3))
    malha.apply_transform(rotacao)
    na_base, balanco, _ = _regioes(malha, balanco_max_graus)
    return {"area_base_mm2": round(float(malha.area_faces[na_base].sum()), 2), "area_balanco_mm2": round(float(malha.area_faces[balanco].sum()), 2)}


def analisar_malha(caminho: Path, balanco_max_graus: float) -> dict[str, Any]:
    malha = _carregar(caminho)
    areas = malha.area_faces
    na_base, balanco, teto = _regioes(malha, balanco_max_graus)

    espessuras = _espessuras(malha) if malha.is_watertight else np.array([])
    return {
        "fechada": bool(malha.is_watertight),
        "triangulos": len(malha.faces),
        "area_base_mm2": round(float(areas[na_base].sum()), 2),
        "area_balanco_mm2": round(float(areas[balanco].sum()), 2),
        "maior_vao_mm": round(_maior_vao(malha, teto), 2),
        "fracao_parede_fina": round(float((espessuras < 0.89).mean()), 4) if len(espessuras) else 0.0,
        "espessura_p5_mm": round(float(np.percentile(espessuras, 5)), 2) if len(espessuras) else None,
    }
