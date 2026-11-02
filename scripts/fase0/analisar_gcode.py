"""Fase 0: analisa um G-code real para orientar o validador da Fase 4."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

MARCA_CAMADA = re.compile(r"^;\s*(CHANGE_LAYER|LAYER_CHANGE)")
PALAVRA = re.compile(r"([A-Za-z])(-?\d*\.?\d+)")


def analisar(caminho):
    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0, "E": 0.0}
    relativo_xyz = False
    relativo_e = False
    fase = "inicio"
    comandos = Counter()
    limites = {
        "inicio": {"X": [1e9, -1e9], "Y": [1e9, -1e9], "Z": [1e9, -1e9]},
        "corpo": {"X": [1e9, -1e9], "Y": [1e9, -1e9], "Z": [1e9, -1e9]},
        "extrusao_corpo": {"X": [1e9, -1e9], "Y": [1e9, -1e9], "Z": [1e9, -1e9]},
        "final": {"X": [1e9, -1e9], "Y": [1e9, -1e9], "Z": [1e9, -1e9]},
    }
    temperaturas = {"M104": Counter(), "M109": Counter(), "M140": Counter(), "M190": Counter(), "M106": Counter()}
    primeiros = []
    modos = []
    camadas = 0
    z_primeira = None
    g28_em = None
    primeira_extrusao_em = None
    velocidade_max = {"XY": 0.0, "Z": 0.0, "E": 0.0}
    comentarios = {}
    linhas = Path(caminho).read_text(encoding="utf-8", errors="replace").splitlines()

    for numero, bruta in enumerate(linhas, 1):
        linha = bruta.strip()
        if linha.startswith(";"):
            if MARCA_CAMADA.match(linha):
                camadas += 1
                if fase == "inicio":
                    fase = "corpo"
            baixo = linha.lower()
            if "end gcode" in baixo or "machine end" in baixo or "machine_end_gcode start" in baixo:
                fase = "final"
            for chave in ("estimated printing time", "total estimated time", "filament used", "total filament", "total layer number", "max_z_height", "HEADER_BLOCK", "FLAVOR", "TIME:", "layer height"):
                if chave.lower() in baixo and chave not in comentarios:
                    comentarios[chave] = linha[:110]
            continue
        codigo = linha.split(";")[0].strip()
        if not codigo:
            continue
        partes = codigo.split()
        comando = partes[0].upper()
        comandos[comando] += 1
        if len(primeiros) < 28:
            primeiros.append(linha[:70])
        args = {m[0].upper(): float(m[1]) for m in PALAVRA.findall(" ".join(partes[1:]))}

        if comando == "G90":
            relativo_xyz = False
            modos.append((numero, "G90"))
        elif comando == "G91":
            relativo_xyz = True
            modos.append((numero, "G91"))
        elif comando == "M82":
            relativo_e = False
            modos.append((numero, "M82"))
        elif comando == "M83":
            relativo_e = True
            modos.append((numero, "M83"))
        elif comando == "G92":
            for eixo, valor in args.items():
                if eixo in pos:
                    pos[eixo] = valor
        elif comando == "G28":
            g28_em = g28_em or numero
            pos.update({"X": 0.0, "Y": 0.0, "Z": 0.0})
        elif comando in temperaturas:
            temperaturas[comando][args.get("S", -1)] += 1
        elif comando in ("G0", "G1", "G2", "G3"):
            anterior = dict(pos)
            for eixo in "XYZ":
                if eixo in args:
                    pos[eixo] = pos[eixo] + args[eixo] if relativo_xyz else args[eixo]
            extrudiu = False
            if "E" in args:
                delta = args["E"] if relativo_e else args["E"] - pos["E"]
                pos["E"] = pos["E"] + args["E"] if relativo_e else args["E"]
                extrudiu = delta > 0
                if extrudiu and primeira_extrusao_em is None:
                    primeira_extrusao_em = numero
            moveu = any(eixo in args for eixo in "XYZ")
            if "F" in args and moveu:
                mm_s = args["F"] / 60
                if "X" in args or "Y" in args:
                    velocidade_max["XY"] = max(velocidade_max["XY"], mm_s)
                if "Z" in args:
                    velocidade_max["Z"] = max(velocidade_max["Z"], mm_s)
            if "F" in args and "E" in args and not moveu:
                velocidade_max["E"] = max(velocidade_max["E"], args["F"] / 60)
            if moveu:
                alvos = [fase]
                if fase == "corpo" and extrudiu:
                    alvos.append("extrusao_corpo")
                    if z_primeira is None:
                        z_primeira = pos["Z"]
                for alvo in alvos:
                    for eixo in "XYZ":
                        limites[alvo][eixo][0] = min(limites[alvo][eixo][0], anterior[eixo], pos[eixo]) if alvo != "extrusao_corpo" else min(limites[alvo][eixo][0], pos[eixo])
                        limites[alvo][eixo][1] = max(limites[alvo][eixo][1], anterior[eixo], pos[eixo]) if alvo != "extrusao_corpo" else max(limites[alvo][eixo][1], pos[eixo])

    return {
        "arquivo": str(caminho),
        "linhas": len(linhas),
        "camadas": camadas,
        "comandos": dict(sorted(comandos.items())),
        "limites": {k: {e: [round(v[0], 3), round(v[1], 3)] for e, v in d.items()} for k, d in limites.items()},
        "z_primeira_extrusao": z_primeira,
        "g28_linha": g28_em,
        "primeira_extrusao_linha": primeira_extrusao_em,
        "modos": modos[:12],
        "temperaturas": {k: {str(t): n for t, n in v.items()} for k, v in temperaturas.items()},
        "velocidade_max_mm_s": {k: round(v, 1) for k, v in velocidade_max.items()},
        "comentarios": comentarios,
        "primeiras_linhas": primeiros,
    }


if __name__ == "__main__":
    for caminho in sys.argv[1:]:
        print(json.dumps(analisar(caminho), ensure_ascii=False, indent=1))
