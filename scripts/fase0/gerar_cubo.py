"""Fase 0: gera o G-code do cubo de calibração e confere antes de liberar para imprimir."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analisar_gcode import analisar  # noqa: E402
from testar_orca import MAQUINA, PRESETS, RAIZ, SAIDA, fatiar  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

PROCESSO = PRESETS / "processo_padrao.json"
CUBO = RAIZ / "saida" / "fase0" / "cubo_calibracao_20mm.stl"
DESTINO = RAIZ / "saida" / "impressao"
LISTA_BRANCA = set("G0 G1 G2 G3 G4 G21 G28 G90 G91 G92 M25 M73 M82 M83 M84 M104 M105 M106 M107 M109 M117 M140 M190 M201 M203 M204 M205 M220 M221 M400 M600".split())
TEMPERATURAS = {"PLA": (220.0, 55.0), "PETG": (250.0, 70.0)}


def conferir(a, material):
    bico, mesa = TEMPERATURAS[material]
    extrusao = a["limites"]["extrusao_corpo"]
    final = a["limites"]["final"]
    inicio = a["limites"]["inicio"]
    temp = a["temperaturas"]
    return [
        ("comandos na lista branca", set(a["comandos"]) <= LISTA_BRANCA),
        ("purga dentro do esperado (X >= -2,4)", inicio["X"][0] >= -2.4),
        ("cubo centrado, 19,5 a 20 mm", 99 < extrusao["X"][0] < 101 and 119 < extrusao["X"][1] < 121 and 99 < extrusao["Y"][0] < 101 and 119 < extrusao["Y"][1] < 121),
        ("altura de 20 mm", abs(extrusao["Z"][1] - 20.0) < 0.01),
        ("primeira camada em 0,20 mm", a["z_primeira_extrusao"] == 0.2),
        ("fim dentro da mesa", final["X"][0] >= 0 and final["X"][1] <= 220 and final["Y"][0] >= 0 and final["Y"][1] <= 220),
        ("Z final até 250 mm", final["Z"][1] <= 250),
        ("G28 antes de extrudar", a["g28_linha"] < a["primeira_extrusao_linha"]),
        (f"bico {bico:.0f} °C", list(temp["M109"]) == [str(bico)]),
        (f"mesa {mesa:.0f} °C", list(temp["M190"]) == [str(mesa)]),
        ("desliga bico e mesa no fim", "0.0" in temp["M104"] and "0.0" in temp["M140"]),
    ]


if __name__ == "__main__":
    DESTINO.mkdir(parents=True, exist_ok=True)
    tudo_ok = True
    for material in TEMPERATURAS:
        nome = f"cubo20_{material.lower()}"
        r = fatiar(nome, CUBO, [MAQUINA, PROCESSO], PRESETS / f"filamento_{material}.json")
        gcode = SAIDA / nome / "peca.gcode"
        a = analisar(gcode)
        print(f"=== {material}: {a['comentarios']['estimated printing time'][-10:]}, {a['comentarios']['total filament'][-5:]} g, {a['camadas']} camadas")
        for texto, ok in conferir(a, material):
            print(f"  {'ok   ' if ok else 'FALHA'} {texto}")
            tudo_ok = tudo_ok and ok
        final = DESTINO / f"{nome}.gcode"
        shutil.copyfile(gcode, final)
        print("  arquivo:", final, final.stat().st_size, "bytes")
    print("RESULTADO:", "aprovado" if tudo_ok else "REPROVADO")
