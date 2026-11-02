"""Fase 0: executa um script CadQuery e relata medidas, furos e malha."""
import json
import math
import runpy
import sys
from pathlib import Path

import cadquery as cq
import trimesh
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopoDS import TopoDS


def classificar_cilindros(solido):
    """Agrupa faces cilíndricas por eixo e raio.

    OCCT pode dividir um furo em várias faces, e um filete côncavo também é um
    cilindro cuja normal aponta para o eixo. Só é furo o grupo côncavo que fecha 360°.
    """
    grupos = {}
    for face in solido.Faces():
        adaptador = BRepAdaptor_Surface(TopoDS.Face_s(face.wrapped))
        if adaptador.GetType() != GeomAbs_Cylinder:
            continue
        cilindro = adaptador.Cylinder()
        eixo = cilindro.Axis()
        d = eixo.Direction()
        o = eixo.Location()
        direcao = (d.X(), d.Y(), d.Z())
        # Normaliza o sentido para que eixos opostos caiam no mesmo grupo.
        maior = max(direcao, key=abs)
        if maior < 0:
            direcao = tuple(-c for c in direcao)
        escalar = o.X() * direcao[0] + o.Y() * direcao[1] + o.Z() * direcao[2]
        ponto = (o.X() - escalar * direcao[0], o.Y() - escalar * direcao[1], o.Z() - escalar * direcao[2])

        u0, u1 = adaptador.FirstUParameter(), adaptador.LastUParameter()
        v0, v1 = adaptador.FirstVParameter(), adaptador.LastVParameter()

        # Regra: a normal natural aponta para fora do eixo quando o sistema é direto.
        reversa = face.wrapped.Orientation() == TopAbs_REVERSED
        concavo = cilindro.Direct() == reversa

        # Conferência independente: avalia a normal real em um ponto sobre a superfície.
        p = adaptador.Value((u0 + u1) / 2, (v0 + v1) / 2)
        normal = face.normalAt(cq.Vector(p.X(), p.Y(), p.Z()))
        radial = (p.X() - ponto[0], p.Y() - ponto[1], p.Z() - ponto[2])
        ao_longo = radial[0] * direcao[0] + radial[1] * direcao[1] + radial[2] * direcao[2]
        radial = tuple(radial[i] - ao_longo * direcao[i] for i in range(3))
        concavo_medido = radial[0] * normal.x + radial[1] * normal.y + radial[2] * normal.z < 0

        chave = (
            tuple(round(c, 3) + 0.0 for c in direcao),
            tuple(round(c, 3) + 0.0 for c in ponto),
            round(cilindro.Radius(), 4),
            concavo,
        )
        grupo = grupos.setdefault(chave, {"arco": 0.0, "v": [], "faces": 0, "conferencia_ok": True})
        grupo["arco"] += u1 - u0
        grupo["faces"] += 1
        grupo["v"] += [escalar + v0, escalar + v1]
        grupo["conferencia_ok"] = grupo["conferencia_ok"] and concavo == concavo_medido

    furos, pinos, arcos_concavos, arcos_convexos = [], [], [], []
    for (direcao, ponto, raio, concavo), g in grupos.items():
        completo = g["arco"] >= 2 * math.pi - 0.01
        item = {
            "diametro": round(raio * 2, 3),
            "eixo": list(direcao),
            "ponto_no_eixo": list(ponto),
            "comprimento": round(max(g["v"]) - min(g["v"]), 3),
            "arco_graus": round(math.degrees(g["arco"]), 1),
            "faces": g["faces"],
            "conferencia_ok": g["conferencia_ok"],
        }
        if concavo and completo:
            furos.append(item)
        elif concavo:
            arcos_concavos.append(item)
        elif completo:
            pinos.append(item)
        else:
            arcos_convexos.append(item)
    return furos, pinos, arcos_concavos, arcos_convexos


def medir(caminho, saida):
    resultado = runpy.run_path(str(caminho))["result"]
    solidos = resultado.solids().vals()
    composto = resultado.val()
    caixa = composto.BoundingBox()
    furos, pinos, concavos, convexos = [], [], [], []
    for s in solidos:
        f, p, cc, cv = classificar_cilindros(s)
        furos += f
        pinos += p
        concavos += cc
        convexos += cv

    saida.mkdir(parents=True, exist_ok=True)
    stl = saida / (caminho.stem + ".stl")
    step = saida / (caminho.stem + ".step")
    cq.exporters.export(resultado, str(stl), tolerance=0.01, angularTolerance=0.1)
    cq.exporters.export(resultado, str(step))
    malha = trimesh.load_mesh(stl)
    return {
        "arquivo": caminho.name,
        "valido": composto.isValid(),
        "solidos": len(solidos),
        "volume_mm3": round(composto.Volume(), 2),
        "caixa": {
            "x": [round(caixa.xmin, 3) + 0.0, round(caixa.xmax, 3) + 0.0],
            "y": [round(caixa.ymin, 3) + 0.0, round(caixa.ymax, 3) + 0.0],
            "z": [round(caixa.zmin, 3) + 0.0, round(caixa.zmax, 3) + 0.0],
        },
        "tamanho": [round(caixa.xlen, 3), round(caixa.ylen, 3), round(caixa.zlen, 3)],
        "furos": furos,
        "pinos": pinos,
        "arcos_concavos_filetes": concavos,
        "arcos_convexos": convexos,
        "malha_fechada": bool(malha.is_watertight),
        "malha_volume_mm3": round(float(malha.volume), 2),
        "malha_triangulos": len(malha.faces),
        "stl": str(stl),
    }


def resumir(m):
    if "erro" in m:
        return f"{m['arquivo']}: ERRO {m['erro']}"
    linhas = [
        f"{m['arquivo']}: valido={m['valido']} solidos={m['solidos']} volume={m['volume_mm3']} "
        f"malha_fechada={m['malha_fechada']} tamanho={m['tamanho']} caixa={m['caixa']}",
        f"  furos ({len(m['furos'])}): " + str([(f["diametro"], f["eixo"], f["ponto_no_eixo"], f["faces"], f["conferencia_ok"]) for f in m["furos"]]),
        f"  filetes concavos ({len(m['arcos_concavos_filetes'])}): " + str([(a["diametro"], a["ponto_no_eixo"], a["arco_graus"], a["conferencia_ok"]) for a in m["arcos_concavos_filetes"]]),
        f"  arcos convexos ({len(m['arcos_convexos'])}): " + str([(a["diametro"], a["ponto_no_eixo"], a["arco_graus"], a["conferencia_ok"]) for a in m["arcos_convexos"]]),
        f"  pinos ({len(m['pinos'])}): " + str([(p["diametro"], p["ponto_no_eixo"]) for p in m["pinos"]]),
    ]
    return "\n".join(linhas)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    pasta = Path(sys.argv[1])
    for nome in sys.argv[2:]:
        try:
            medidas = medir(Path(nome), pasta)
            (pasta / (Path(nome).stem + ".json")).write_text(json.dumps(medidas, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as erro:  # noqa: BLE001
            medidas = {"arquivo": nome, "erro": f"{type(erro).__name__}: {erro}"}
        print(resumir(medidas))
