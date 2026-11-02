"""Monta o gabinete virtualmente: confere interferências, encaixes e acesso aos conectores.

Uso: conferir_gabinete.py [saida.png] [--traseira]
"""
import runpy
import sys
import tempfile
from itertools import combinations
from pathlib import Path

import cadquery as cq

sys.stdout.reconfigure(encoding="utf-8")
RAIZ = Path(__file__).resolve().parents[1]
MODELO = RAIZ / "modelos" / "gabinete_tvbox" / "gabinete_tvbox.py"


def carregar(traseira=False):
    if not traseira:
        return runpy.run_path(str(MODELO))
    codigo = MODELO.read_text(encoding="utf-8").replace('POSICAO_COOLER = "topo"', 'POSICAO_COOLER = "traseira"')
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "modelo.py"
        caminho.write_text(codigo, encoding="utf-8")
        return runpy.run_path(str(caminho))


def volume_comum(a, b):
    try:
        return a.intersect(b).val().Volume()
    except Exception:  # noqa: BLE001 - a interseção vazia pode não gerar sólido.
        return 0.0


def conferir(m):
    """Devolve a lista de problemas. Lista vazia significa que tudo encaixa."""
    pecas = m["montar"]()
    itens = m["itens_de_referencia"]()
    problemas = []
    for (na, a), (nb, b) in combinations({**pecas, **itens}.items(), 2):
        comum = volume_comum(a, b)
        if comum > 0.01:
            problemas.append(f"{na} e {nb} se sobrepõem em {comum:.2f} mm³")

    caixa = m["caixa"]
    largura, profundidade = m["LARGURA"], m["PROFUNDIDADE"]

    # Os conectores precisam sair pela janela: de cima da placa até a frente do gabinete.
    ox, oy, zp = m["PLACA_ORIGEM_X"], m["PLACA_ORIGEM_Y"], m["Z_PLACA"]
    espessura = m["PLACA_ESPESSURA"]
    meio = m["JANELA_LARGURA"] / 2.0 - 1.0
    cx = largura / 2.0
    acesso = caixa(cx - meio, cx + meio, -2, oy, zp + espessura, zp + espessura + 16)
    comum = volume_comum(pecas["frente"], acesso)
    if comum > 0.01:
        problemas.append(f"a frente bloqueia os conectores em {comum:.2f} mm³")

    # A porca do cooler precisa caber por dentro.
    d = m["COOLER_ENTRE_FUROS"] / 2.0
    if m["COOLER_NO_TOPO"]:
        cy = profundidade / 2.0
        z = m["ALTURA_BASE"]
        porcas = [m["cilindro"](cx + sx * d, cy + sy * d, z - 3.6, z, 8.0) for sx in (-1, 1) for sy in (-1, 1)]
        for indice, porca in enumerate(porcas):
            for nome, peca in pecas.items():
                comum = volume_comum(peca, porca)
                if comum > 0.01:
                    problemas.append(f"a porca {indice} do cooler bate em {nome} ({comum:.2f} mm³)")

    # Cada peça precisa caber na impressora.
    for nome, peca in m["result"].items():
        b = peca.val().BoundingBox()
        if max(b.xlen, b.ylen) > 210 or b.zlen > 239:
            problemas.append(f"{nome} não cabe no volume seguro: {b.xlen:.1f} × {b.ylen:.1f} × {b.zlen:.1f} mm")
    return problemas


def renderizar(m, destino):
    """Vista explodida e vista montada, lado a lado."""
    import pyvista as pv

    pecas = m["montar"]()
    itens = m["itens_de_referencia"]()
    cores = {
        "base": "#e8873a", "tampa": "#e8a03a", "frente": "#d97a30", "traseira": "#d97a30",
        "lateral_esquerda": "#c96f2b", "lateral_direita": "#c96f2b", "placa": "#2f8f5b", "cooler": "#4a5568",
    }
    afastamento = {
        "tampa": (0, 0, 70), "cooler": (0, 0, 110), "frente": (0, -70, 0), "traseira": (0, 70, 0),
        "lateral_esquerda": (-70, 0, 0), "lateral_direita": (70, 0, 0), "base": (0, 0, 0), "placa": (0, 0, 25),
    }
    with tempfile.TemporaryDirectory() as pasta:
        malhas = {}
        for nome, peca in {**pecas, **itens}.items():
            caminho = Path(pasta) / f"{nome}.stl"
            cq.exporters.export(peca, str(caminho), tolerance=0.05, angularTolerance=0.2)
            malhas[nome] = pv.read(str(caminho))

    cena = pv.Plotter(off_screen=True, shape=(1, 2), window_size=(1800, 900), border=False)
    for coluna, explodida in ((0, False), (1, True)):
        cena.subplot(0, coluna)
        cena.set_background("#d9dde3")
        for nome, malha in malhas.items():
            mover = afastamento[nome] if explodida else (0, 0, 0)
            cena.add_mesh(malha.translate(mover), color=cores[nome], smooth_shading=False)
        cena.add_text("Vista explodida" if explodida else "Montado", font_size=14, color="black")
        cena.camera_position = [(1, -1.15, 0.85), (0, 0, 0), (0, 0, 1)]
        cena.reset_camera()
    Path(destino).parent.mkdir(parents=True, exist_ok=True)
    cena.screenshot(str(destino))
    cena.close()


if __name__ == "__main__":
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    modelo = carregar("--traseira" in sys.argv)
    print(
        f"Gabinete: {modelo['LARGURA']:g} × {modelo['PROFUNDIDADE']:g} × {modelo['ALTURA_BASE'] + modelo['TAMPA']:g} mm, "
        f"cooler: {modelo['POSICAO_COOLER']}"
    )
    for nome, peca in modelo["result"].items():
        b = peca.val().BoundingBox()
        print(f"  {nome}: {b.xlen:.1f} × {b.ylen:.1f} × {b.zlen:.1f} mm, {peca.val().Volume() / 1000:.1f} cm³, sólidos: {len(peca.solids().vals())}")
    encontrados = conferir(modelo)
    print("Encaixe: OK, sem interferências" if not encontrados else "PROBLEMAS:\n  " + "\n  ".join(encontrados))
    if argumentos:
        renderizar(modelo, argumentos[0])
        print("Imagem:", argumentos[0])
    sys.exit(1 if encontrados else 0)
