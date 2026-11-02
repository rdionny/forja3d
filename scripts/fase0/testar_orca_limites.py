"""Fase 0: descobre o maior tamanho aceito em XY, com e sem --arrange."""
import json
import sys
from pathlib import Path

import cadquery as cq

sys.path.insert(0, str(Path(__file__).parent))
from analisar_gcode import analisar  # noqa: E402
from testar_orca import MAQUINA, PRESETS, SAIDA, fatiar  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

PROCESSO = PRESETS / "processo_padrao.json"
PLA = PRESETS / "filamento_PLA.json"


def placa(lado, centro=(0, 0)):
    caminho = SAIDA / f"lim_{lado}_{centro[0]}.stl"
    corpo = cq.Workplane("XY").box(lado, lado, 2, centered=(True, True, False)).translate((centro[0], centro[1], 0))
    cq.exporters.export(corpo, str(caminho), tolerance=0.01, angularTolerance=0.1)
    return caminho


def processo_com_brim():
    dados = json.loads(PROCESSO.read_text(encoding="utf-8"))
    dados.update({"brim_type": "outer_only", "brim_width": "5"})
    caminho = SAIDA / "processo_brim.json"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return caminho


def tentar(rotulo, modelo, processo, arrange):
    r = fatiar(rotulo, modelo, [MAQUINA, processo], PLA, arrange=arrange)
    codigo = r["codigo"]
    codigo = codigo - 2**32 if isinstance(codigo, int) and codigo >= 2**31 else codigo
    if "gcode_bytes" not in r:
        print(f"{rotulo}: codigo={codigo} recusado")
        return
    a = analisar(SAIDA / rotulo / "peca.gcode")
    final = a["limites"]["final"]
    print(f"{rotulo}: codigo={codigo} extrusao X={a['limites']['extrusao_corpo']['X']} Y={a['limites']['extrusao_corpo']['Y']} final X={final['X']} Y={final['Y']}")


if __name__ == "__main__":
    brim = processo_com_brim()
    print("=== Com --arrange 1, sem brim ===")
    for lado in (211, 212, 214, 216, 217):
        tentar(f"arr_{lado}", placa(lado), PROCESSO, True)
    print("=== Com --arrange 1, com brim de 5 mm ===")
    for lado in (205, 208, 210, 211, 212):
        tentar(f"arrb_{lado}", placa(lado), brim, True)
    print("=== Sem --arrange, modelo centrado em (110, 110) ===")
    for lado in (20, 210, 216, 218, 219.8):
        tentar(f"sem_{lado}", placa(lado, (110, 110)), PROCESSO, False)
    print("=== Sem --arrange, com brim de 5 mm, centrado em (110, 110) ===")
    for lado in (210, 212):
        tentar(f"semb_{lado}", placa(lado, (110, 110)), brim, False)
    print("=== Sem --arrange, modelo na origem (metade fora da mesa) ===")
    tentar("origem_20", placa(20), PROCESSO, False)
