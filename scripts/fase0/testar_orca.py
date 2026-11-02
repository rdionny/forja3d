"""Fase 0: responde às dúvidas sobre a CLI do OrcaSlicer fatiando de verdade."""
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import cadquery as cq

sys.stdout.reconfigure(encoding="utf-8")

RAIZ = Path(__file__).resolve().parents[2]
ORCA = RAIZ / "ferramentas" / "OrcaSlicer_2.4.2" / "orca-slicer.exe"
DADOS = RAIZ / "ferramentas" / "orca_dados"
SAIDA = RAIZ / "saida" / "fase0" / "orca"
PRESETS = RAIZ / "config" / "orca"
PERFIS = RAIZ / "ferramentas" / "OrcaSlicer_2.4.2" / "resources" / "profiles" / "Creality"

MAQUINA = PRESETS / "maquina.json"
PROCESSO = PRESETS / "processo_padrao.json"
FILAMENTO = PRESETS / "filamento_PLA.json"
MAQUINA_ORIG = PERFIS / "machine" / "Creality Ender-3 V3 SE 0.4 nozzle.json"
PROCESSO_ORIG = PERFIS / "process" / "0.20mm Standard @Creality Ender3V3SE 0.4.json"
FILAMENTO_ORIG = PERFIS / "filament" / "Creality Generic PLA @Ender-3V3-all.json"


def fatiar(nome, modelo, configs, filamento, extras=(), arrange=True):
    pasta = SAIDA / nome
    if pasta.exists():
        shutil.rmtree(pasta)
    pasta.mkdir(parents=True)
    comando = [
        str(ORCA),
        "--datadir", str(DADOS),
        "--load-settings", ";".join(str(c) for c in configs),
        "--load-filaments", str(filamento),
    ]
    if arrange:
        comando += ["--arrange", "1"]
    comando += [*extras, "--slice", "0", "--export-3mf", "saida.gcode.3mf", "--outputdir", str(pasta), str(modelo)]
    inicio = time.time()
    try:
        r = subprocess.run(comando, cwd=pasta, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        codigo, saida_padrao, erro_padrao = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        codigo, saida_padrao, erro_padrao = "TIMEOUT", "", ""
    duracao = time.time() - inicio

    arquivos = sorted(p.name for p in pasta.iterdir())
    resultado = {"nome": nome, "codigo": codigo, "segundos": round(duracao, 1), "arquivos": arquivos, "stdout_bytes": len(saida_padrao), "stderr_bytes": len(erro_padrao)}
    json_resultado = pasta / "result.json"
    if json_resultado.exists():
        resultado["result_json"] = json.loads(json_resultado.read_text(encoding="utf-8", errors="replace"))
    pacote = pasta / "saida.gcode.3mf"
    if pacote.exists():
        with zipfile.ZipFile(pacote) as z:
            nomes = z.namelist()
            gcodes = [n for n in nomes if n.endswith(".gcode")]
            resultado["gcodes"] = gcodes
            if gcodes:
                destino = pasta / "peca.gcode"
                destino.write_bytes(z.read(gcodes[0]))
                resultado["gcode_bytes"] = destino.stat().st_size
    return resultado


def relatar(r):
    linha = f"{r['nome']}: codigo={r['codigo']} {r['segundos']}s gcode={r.get('gcodes', '-')} bytes={r.get('gcode_bytes', '-')}"
    print(linha)
    print("   arquivos:", r["arquivos"], "| stdout/stderr:", r["stdout_bytes"], r["stderr_bytes"])
    if "result_json" in r:
        print("   result.json:", json.dumps(r["result_json"], ensure_ascii=False)[:500])


if __name__ == "__main__":
    SAIDA.mkdir(parents=True, exist_ok=True)
    cubo_stl = RAIZ / "saida" / "fase0" / "cubo_calibracao_20mm.stl"

    # 3MF gerado pelo CadQuery, para comparar com o STL.
    import runpy

    resultado_cubo = runpy.run_path(str(RAIZ / "tests" / "dados" / "cubo_calibracao_20mm.py"))["result"]
    cubo_3mf = SAIDA / "cubo.3mf"
    cq.exporters.export(resultado_cubo, str(cubo_3mf), tolerance=0.01, angularTolerance=0.1)

    # Peça maior que a mesa, para ver como a falha aparece.
    grande = cq.Workplane("XY").box(300, 60, 40, centered=(True, True, False))
    grande_stl = SAIDA / "grande_300mm.stl"
    cq.exporters.export(grande, str(grande_stl), tolerance=0.01, angularTolerance=0.1)

    casos = [
        ("A_achatado_maquina_primeiro_stl", cubo_stl, [MAQUINA, PROCESSO], FILAMENTO),
        ("B_achatado_processo_primeiro_stl", cubo_stl, [PROCESSO, MAQUINA], FILAMENTO),
        ("C_achatado_maquina_primeiro_3mf", cubo_3mf, [MAQUINA, PROCESSO], FILAMENTO),
        ("D_original_com_heranca_stl", cubo_stl, [MAQUINA_ORIG, PROCESSO_ORIG], FILAMENTO_ORIG),
        ("E_falha_peca_grande", grande_stl, [MAQUINA, PROCESSO], FILAMENTO),
        ("F_falha_modelo_inexistente", SAIDA / "nao_existe.stl", [MAQUINA, PROCESSO], FILAMENTO),
    ]
    for nome, modelo, configs, filamento in casos:
        relatar(fatiar(nome, modelo, configs, filamento))
