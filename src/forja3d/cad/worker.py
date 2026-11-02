"""Processo separado que executa o script da IA.

Lê `script.py` da pasta recebida e grava `resultado.json`. Roda à parte porque o código
não é confiável e porque uma falha do kernel CAD não pode derrubar o aplicativo.
"""
from __future__ import annotations

import ctypes
import io
import json
import math
import os
import re
import sys
import traceback
from ctypes import wintypes
from pathlib import Path
from typing import Any

from forja3d.cad.seguranca_ast import BUILTINS_PERMITIDOS, CodigoRecusado, validar_codigo

LIMITE_MEMORIA = 4 * 1024**3
_trabalho_memoria: list[int] = []


def limitar_memoria(limite: int = LIMITE_MEMORIA) -> bool:
    """Coloca este processo em um Job Object do Windows com limite de memória."""
    if os.name != "nt":
        return False

    class Basico(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ContadoresIO(ctypes.Structure):
        _fields_ = [(nome, ctypes.c_uint64) for nome in ("a", "b", "c", "d", "e", "f")]

    class Estendido(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basico),
            ("IoInfo", ContadoresIO),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    trabalho = kernel32.CreateJobObjectW(None, None)
    if not trabalho:
        return False
    info = Estendido()
    limite_memoria_processo = 0x00000100
    info.BasicLimitInformation.LimitFlags = limite_memoria_processo
    info.ProcessMemoryLimit = limite
    informacao_estendida = 9
    ok = kernel32.SetInformationJobObject(trabalho, informacao_estendida, ctypes.byref(info), ctypes.sizeof(info))
    if not ok or not kernel32.AssignProcessToJobObject(trabalho, kernel32.GetCurrentProcess()):
        return False
    _trabalho_memoria.append(trabalho)  # O identificador precisa viver até o fim do processo.
    return True


def classificar_cilindros(solido: Any) -> dict[str, list[dict[str, Any]]]:
    """Agrupa faces cilíndricas por eixo e raio.

    O OCCT pode dividir um furo em várias faces, e um filete côncavo também é um cilindro cuja
    normal aponta para o eixo. Só é furo o grupo côncavo que fecha 360°.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopoDS import TopoDS

    grupos: dict[tuple, dict[str, Any]] = {}
    for face in solido.Faces():
        adaptador = BRepAdaptor_Surface(TopoDS.Face_s(face.wrapped))
        if adaptador.GetType() != GeomAbs_Cylinder:
            continue
        cilindro = adaptador.Cylinder()
        eixo = cilindro.Axis()
        d, o = eixo.Direction(), eixo.Location()
        direcao = (d.X(), d.Y(), d.Z())
        # Normaliza o sentido para que eixos opostos caiam no mesmo grupo.
        if max(direcao, key=abs) < 0:
            direcao = tuple(-c for c in direcao)
        escalar = o.X() * direcao[0] + o.Y() * direcao[1] + o.Z() * direcao[2]
        ponto = tuple(c - escalar * direcao[i] for i, c in enumerate((o.X(), o.Y(), o.Z())))
        concavo = cilindro.Direct() == (face.wrapped.Orientation() == TopAbs_REVERSED)
        chave = (
            tuple(round(c, 3) + 0.0 for c in direcao),
            tuple(round(c, 3) + 0.0 for c in ponto),
            round(cilindro.Radius(), 4),
            concavo,
        )
        grupo = grupos.setdefault(chave, {"arco": 0.0, "v": []})
        grupo["arco"] += adaptador.LastUParameter() - adaptador.FirstUParameter()
        v0, v1 = adaptador.FirstVParameter(), adaptador.LastVParameter()
        grupo["v"] += [v0, v1]

    saida: dict[str, list[dict[str, Any]]] = {"furos": [], "pinos": [], "arcos_concavos": [], "arcos_convexos": []}
    for (direcao, ponto, raio, concavo), grupo in grupos.items():
        completo = grupo["arco"] >= 2 * math.pi - 0.01
        item = {
            "diametro": round(raio * 2, 3),
            "eixo": list(direcao),
            "ponto_no_eixo": list(ponto),
            "comprimento": round(max(grupo["v"]) - min(grupo["v"]), 3),
            "arco_graus": round(math.degrees(grupo["arco"]), 1),
        }
        if concavo:
            saida["furos" if completo else "arcos_concavos"].append(item)
        else:
            saida["pinos" if completo else "arcos_convexos"].append(item)
    return saida


def _solidos(objeto: Any, cq: Any) -> list[Any]:
    if isinstance(objeto, cq.Workplane):
        valores = objeto.vals()
    elif isinstance(objeto, cq.Shape):
        valores = [objeto]
    else:
        raise ValueError(f"result precisa ser um Workplane ou um dicionário de Workplanes, não {type(objeto).__name__}.")
    solidos = [s for v in valores if isinstance(v, cq.Shape) for s in v.Solids()]
    if not solidos:
        raise ValueError(
            "result não contém nenhum sólido. Termine o script com o corpo da peça, não com uma seleção de faces ou arestas."
        )
    return solidos


def _nome_seguro(nome: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", str(nome))[:40] or "parte"


def medir_e_exportar(result: Any, pasta: Path, cq: Any) -> list[dict[str, Any]]:
    partes = result if isinstance(result, dict) else {"modelo": result}
    if not partes:
        raise ValueError("result é um dicionário vazio.")
    medidas = []
    for nome, objeto in partes.items():
        nome_arquivo = _nome_seguro(nome)
        solidos = _solidos(objeto, cq)
        composto = cq.Compound.makeCompound(solidos)
        caixa = composto.BoundingBox()
        cilindros: dict[str, list[dict[str, Any]]] = {"furos": [], "pinos": [], "arcos_concavos": [], "arcos_convexos": []}
        for solido in solidos:
            for tipo, itens in classificar_cilindros(solido).items():
                cilindros[tipo] += itens
        forma = cq.Workplane("XY").newObject([composto])
        stl, step = f"{nome_arquivo}.stl", f"{nome_arquivo}.step"
        cq.exporters.export(forma, str(pasta / stl), tolerance=0.01, angularTolerance=0.1)
        cq.exporters.export(forma, str(pasta / step))
        medidas.append(
            {
                "nome": nome_arquivo,
                "valido": all(s.isValid() for s in solidos),
                "solidos": len(solidos),
                "volume_mm3": round(sum(s.Volume() for s in solidos), 3),
                "caixa": {
                    "x": [round(caixa.xmin, 3) + 0.0, round(caixa.xmax, 3) + 0.0],
                    "y": [round(caixa.ymin, 3) + 0.0, round(caixa.ymax, 3) + 0.0],
                    "z": [round(caixa.zmin, 3) + 0.0, round(caixa.zmax, 3) + 0.0],
                },
                "tamanho": [round(caixa.xlen, 3), round(caixa.ylen, 3), round(caixa.zlen, 3)],
                "stl": stl,
                "step": step,
                **cilindros,
            }
        )
    return medidas


def _linha_do_erro(erro: BaseException) -> int | None:
    linha = None
    for quadro in traceback.extract_tb(erro.__traceback__):
        if Path(quadro.filename).name == "script.py":
            linha = quadro.lineno
    if isinstance(erro, SyntaxError) and linha is None:
        linha = erro.lineno
    return linha


def executar(pasta: Path) -> dict[str, Any]:
    codigo = (pasta / "script.py").read_text(encoding="utf-8")
    try:
        validar_codigo(codigo)
    except CodigoRecusado as erro:
        return {"ok": False, "tipo": "CodigoRecusado", "erro": erro.motivo, "linha": erro.linha}

    limitar_memoria()
    try:
        import cadquery as cq

        espaco: dict[str, Any] = {"__builtins__": BUILTINS_PERMITIDOS, "__name__": "__script__"}
        saida_original = sys.stdout
        sys.stdout = io.StringIO()
        try:
            exec(compile(codigo, "script.py", "exec"), espaco)  # noqa: S102 - o script passou pela análise AST.
        finally:
            sys.stdout = saida_original
        if "result" not in espaco:
            return {"ok": False, "tipo": "ValueError", "erro": "O script não definiu a variável result.", "linha": None}
        return {"ok": True, "partes": medir_e_exportar(espaco["result"], pasta, cq)}
    except BaseException as erro:  # noqa: BLE001 - qualquer falha precisa voltar para o loop de reparo.
        return {"ok": False, "tipo": type(erro).__name__, "erro": str(erro)[:800], "linha": _linha_do_erro(erro)}


def principal(pasta_texto: str) -> int:
    pasta = Path(pasta_texto)
    os.chdir(pasta)
    resultado = executar(pasta)
    temporario = pasta / "resultado.tmp"
    temporario.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    temporario.replace(pasta / "resultado.json")
    return 0
