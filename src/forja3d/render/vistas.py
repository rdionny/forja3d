"""Renderização fora da tela, em processo separado.

Uma única isométrica pode esconder detalhes (na Fase 0, escondeu 1 dos 2 furos de um
suporte em L). Por isso as vistas incluem duas isométricas de cantos opostos.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from forja3d.cad.executor import SEM_JANELA, aguardar, ambiente_limpo, comando_auxiliar

VISTAS = {
    "isometrica_1": ((1.0, -1.0, 0.8), (0, 0, 1), False, "Isometrica, canto +X -Y"),
    "isometrica_2": ((-1.0, 1.0, 0.8), (0, 0, 1), False, "Isometrica, canto -X +Y"),
    "frontal": ((0.0, -1.0, 0.0), (0, 0, 1), True, "Frontal, olhando de -Y"),
    "lateral": ((1.0, 0.0, 0.0), (0, 0, 1), True, "Lateral, olhando de +X"),
    "superior": ((0.0, 0.0, 1.0), (0, 1, 0), True, "Superior, olhando de +Z"),
    "inferior": ((0.0, 0.0, -1.0), (0, 1, 0), True, "Inferior, olhando de -Z"),
}


def principal(pasta_texto: str, *stls: str) -> int:
    """Executado no processo auxiliar."""
    import pyvista as pv

    pasta = Path(pasta_texto)
    pasta.mkdir(parents=True, exist_ok=True)
    malhas = []
    deslocamento = 0.0
    for caminho in stls:
        malha = pv.read(caminho)
        x0, x1, y0, y1, z0, _ = malha.bounds
        malhas.append(malha.translate((deslocamento - x0, -(y0 + y1) / 2, -z0)))
        deslocamento += (x1 - x0) + 10.0
    conjunto = malhas[0] if len(malhas) == 1 else malhas[0].merge(malhas[1:])
    x0, x1, y0, y1, z0, z1 = conjunto.bounds
    conjunto = conjunto.translate((-(x0 + x1) / 2, -(y0 + y1) / 2, 0))
    x0, x1, y0, y1, z0, z1 = conjunto.bounds
    centro = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    raio = max(x1 - x0, y1 - y0, z1 - z0, 1.0)
    medida = f"{x1 - x0:.1f} x {y1 - y0:.1f} x {z1 - z0:.1f} mm (X, Y, Z)"

    for nome, (direcao, para_cima, paralela, rotulo) in VISTAS.items():
        cena = pv.Plotter(off_screen=True, window_size=(1024, 1024))
        cena.set_background("#d9dde3")
        cena.add_mesh(conjunto, color="#d9822b", smooth_shading=False, show_edges=False, specular=0.2)
        cena.add_mesh(conjunto.extract_feature_edges(feature_angle=30, boundary_edges=False, non_manifold_edges=False, manifold_edges=False), color="#3b2a18", line_width=2)
        cena.add_axes(color="black")
        cena.add_text(f"{rotulo}\n{medida}", position="upper_left", font_size=12, color="black")
        if paralela:
            cena.enable_parallel_projection()
        posicao = tuple(centro[i] + direcao[i] * raio * 3 for i in range(3))
        cena.camera_position = [posicao, centro, para_cima]
        cena.reset_camera()
        cena.screenshot(str(pasta / f"{nome}.png"))
        cena.close()
    return 0


def renderizar(stls: list[Path], pasta: Path, timeout: float = 120.0, cancelar: threading.Event | None = None) -> list[Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    with open(pasta / "render_erros.txt", "wb") as erros:
        processo = subprocess.Popen(
            comando_auxiliar("--worker-render", str(pasta), *(str(s) for s in stls)),
            cwd=pasta,
            env=ambiente_limpo(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=erros,
            creationflags=SEM_JANELA,
        )
        estado = aguardar(processo, timeout, cancelar)
    imagens = [pasta / f"{nome}.png" for nome in VISTAS]
    faltando = [i.name for i in imagens if not i.exists()]
    if estado == "timeout" or faltando:
        raise RuntimeError(f"Não consegui gerar as vistas ({estado}, faltando {faltando}).")
    return imagens
