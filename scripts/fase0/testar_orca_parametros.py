"""Fase 0: como aplicar parâmetros, outros materiais e o comportamento perto dos limites."""
import json
import runpy
import sys
from pathlib import Path

import cadquery as cq

sys.path.insert(0, str(Path(__file__).parent))
from analisar_gcode import analisar  # noqa: E402
from testar_orca import MAQUINA, PRESETS, RAIZ, SAIDA, fatiar  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

PROCESSO = PRESETS / "processo_padrao.json"
PLA = PRESETS / "filamento_PLA.json"
cubo = RAIZ / "saida" / "fase0" / "cubo_calibracao_20mm.stl"
suporte = RAIZ / "saida" / "fase0" / "suporte_l_agente_valido.stl"
comandos_vistos = set()


def config_do_gcode(caminho):
    valores = {}
    for linha in Path(caminho).read_text(encoding="utf-8", errors="replace").splitlines():
        if linha.startswith("; ") and " = " in linha:
            chave, valor = linha[2:].split(" = ", 1)
            valores.setdefault(chave.strip(), valor.strip())
    return valores


def caso(nome, modelo, configs, filamento, extras=(), ver=()):
    r = fatiar(nome, modelo, configs, filamento, extras)
    codigo = r["codigo"]
    assinado = codigo - 2**32 if isinstance(codigo, int) and codigo >= 2**31 else codigo
    if "gcode_bytes" not in r:
        print(f"{nome}: codigo={assinado} SEM G-CODE")
        return
    gcode = SAIDA / nome / "peca.gcode"
    a = analisar(gcode)
    comandos_vistos.update(a["comandos"])
    c = config_do_gcode(gcode)
    temp = a["temperaturas"]
    print(
        f"{nome}: codigo={assinado} camadas={a['camadas']} "
        f"bico={list(temp['M109'])} mesa={list(temp['M190'])} "
        f"extrusao={a['limites']['extrusao_corpo']} "
        f"z_final={a['limites']['final']['Z'][1]} tempo={a['comentarios'].get('estimated printing time', '')[-10:]} "
        f"filamento={a['comentarios'].get('total filament', '')[-5:]}"
    )
    if ver:
        print("   config:", {k: c.get(k) for k in ver})


def placa(nome, x, y, z):
    caminho = SAIDA / f"{nome}.stl"
    cq.exporters.export(cq.Workplane("XY").box(x, y, z, centered=(True, True, False)), str(caminho), tolerance=0.01, angularTolerance=0.1)
    return caminho


if __name__ == "__main__":
    print("=== Parâmetros por flags ===")
    caso(
        "G_flags", cubo, [MAQUINA, PROCESSO], PLA,
        ("--wall-loops", "4", "--sparse-infill-density", "30%", "--brim-type", "outer_only", "--brim-width", "5", "--enable-support", "1"),
        ("wall_loops", "sparse_infill_density", "brim_type", "brim_width", "enable_support"),
    )

    print("=== Parâmetros por JSON derivado ===")
    derivado = json.loads(PROCESSO.read_text(encoding="utf-8"))
    derivado.update({"wall_loops": "4", "sparse_infill_density": "30%", "brim_type": "outer_only", "brim_width": "5", "enable_support": "1"})
    arquivo_derivado = SAIDA / "processo_derivado.json"
    arquivo_derivado.write_text(json.dumps(derivado), encoding="utf-8")
    caso("H_json", cubo, [MAQUINA, arquivo_derivado], PLA, (), ("wall_loops", "sparse_infill_density", "brim_type", "brim_width", "enable_support"))

    print("=== Materiais ===")
    caso("I_PETG", cubo, [MAQUINA, PROCESSO], PRESETS / "filamento_PETG.json", (), ("nozzle_temperature", "hot_plate_temp", "fan_min_speed", "filament_max_volumetric_speed"))
    caso("J_TPU", cubo, [MAQUINA, PROCESSO], PRESETS / "filamento_TPU.json", (), ("nozzle_temperature", "hot_plate_temp", "filament_retraction_length", "filament_max_volumetric_speed"))

    print("=== Qualidades ===")
    for q in ("fina", "otima", "rascunho"):
        caso(f"K_{q}", cubo, [MAQUINA, PRESETS / f"processo_{q}.json"], PLA, (), ("layer_height", "initial_layer_print_height"))

    print("=== Peça real ===")
    caso("L_suporte", suporte, [MAQUINA, PROCESSO], PLA)

    print("=== Limites do volume ===")
    brim = ("--brim-type", "outer_only", "--brim-width", "5")
    caso("M_210_com_brim", placa("p210", 210, 210, 2), [MAQUINA, PROCESSO], PLA, brim)
    caso("N_218_sem_brim", placa("p218", 218, 218, 2), [MAQUINA, PROCESSO], PLA)
    caso("O_218_com_brim", placa("p218b", 218, 218, 2), [MAQUINA, PROCESSO], PLA, brim)
    caso("P_219_8_sem_brim", placa("p2198", 219.8, 219.8, 2), [MAQUINA, PROCESSO], PLA)
    caso("Q_coluna_239", placa("c239", 25, 25, 239), [MAQUINA, PROCESSO], PLA)
    caso("R_coluna_245", placa("c245", 25, 25, 245), [MAQUINA, PROCESSO], PLA)
    caso("S_coluna_251", placa("c251", 25, 25, 251), [MAQUINA, PROCESSO], PLA)

    print("\nComandos vistos em todos os casos:", sorted(comandos_vistos))
