"""Lê um modelo 3D baixado, em qualquer dos formatos aceitos, para uma malha só."""
from __future__ import annotations

import json
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import numpy as np

from forja3d.importacao.fbx import ler_fbx
from forja3d.importacao.malha import MalhaLida, ModeloInvalido

FORMATOS_ACEITOS = (".stl", ".obj", ".glb", ".gltf", ".fbx", ".3mf", ".ply", ".off")
OUTRO_DOWNLOAD = "Baixe o mesmo modelo em STL, GLB, OBJ ou FBX: esses formatos o app lê."
FORMATOS_RECUSADOS = {
    ".usdz": f"O formato USDZ é feito para a realidade aumentada da Apple, e o app ainda não lê. {OUTRO_DOWNLOAD}",
    ".usd": f"O app ainda não lê arquivos USD. {OUTRO_DOWNLOAD}",
    ".usda": f"O app ainda não lê arquivos USD. {OUTRO_DOWNLOAD}",
    ".usdc": f"O app ainda não lê arquivos USD. {OUTRO_DOWNLOAD}",
    ".blend": f"Arquivos .blend só o Blender abre. {OUTRO_DOWNLOAD} No Blender, use Arquivo > Exportar > STL.",
}
# O glTF é sempre em metros e com Y para cima. O OBJ não diz, e quase todo programa grava assim.
PADROES = {".glb": (1000.0, (0.0, 1.0, 0.0)), ".gltf": (1000.0, (0.0, 1.0, 0.0)), ".obj": (1.0, (0.0, 1.0, 0.0))}
COMPRESSOES = {"KHR_draco_mesh_compression": "Draco", "EXT_meshopt_compression": "Meshopt", "KHR_meshopt_compression": "Meshopt"}
NUCLEO_3MF = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PRODUCAO_3MF = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
UNIDADES_3MF = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}
PROFUNDIDADE_MAXIMA_3MF = 32


def ler_modelo(caminho: Path) -> MalhaLida:
    sufixo = caminho.suffix.lower()
    if sufixo in FORMATOS_RECUSADOS:
        raise ModeloInvalido(FORMATOS_RECUSADOS[sufixo])
    if sufixo not in FORMATOS_ACEITOS:
        raise ModeloInvalido(f"O app não lê arquivos {sufixo or 'sem extensão'}. Os formatos aceitos são: {', '.join(f[1:].upper() for f in FORMATOS_ACEITOS)}.")
    if sufixo == ".fbx":
        return ler_fbx(caminho.read_bytes())
    if sufixo == ".3mf":
        return _ler_3mf(caminho)
    if sufixo == ".glb":
        _conferir_compressao_do_glb(caminho)
    if sufixo == ".gltf":
        _conferir_compressao(json.loads(caminho.read_text(encoding="utf-8")))
    return _ler_com_trimesh(caminho, sufixo)


def _ler_com_trimesh(caminho: Path, sufixo: str) -> MalhaLida:
    import trimesh

    malha = trimesh.load_scene(caminho, process=False, skip_materials=True).to_mesh()
    mm_por_unidade, para_cima = PADROES.get(sufixo, (1.0, (0.0, 0.0, 1.0)))
    return MalhaLida(np.asarray(malha.vertices, dtype=np.float64), np.asarray(malha.faces, dtype=np.int64), mm_por_unidade, para_cima)


def _conferir_compressao(cabecalho: dict) -> None:
    for extensao in cabecalho.get("extensionsRequired", []):
        if extensao in COMPRESSOES:
            raise ModeloInvalido(f"Este arquivo usa a compressão {COMPRESSOES[extensao]}, que o app não lê. {OUTRO_DOWNLOAD}")


def _conferir_compressao_do_glb(caminho: Path) -> None:
    with open(caminho, "rb") as arquivo:
        cabecalho = arquivo.read(20)
        if len(cabecalho) < 20 or cabecalho[:4] != b"glTF":
            raise ModeloInvalido("Este arquivo não é um GLB, ou está corrompido.")
        tamanho = struct.unpack_from("<I", cabecalho, 12)[0]
        _conferir_compressao(json.loads(arquivo.read(tamanho)))


# ---------- 3MF ----------


def _matriz_3mf(texto: str | None) -> np.ndarray:
    """O 3MF escreve as 12 posições por linha, com a translação por último."""
    matriz = np.eye(4)
    if texto:
        valores = [float(v) for v in texto.split()]
        if len(valores) != 12:
            raise ValueError("Transformação inválida no 3MF.")
        matriz[:3, 0], matriz[:3, 1], matriz[:3, 2], matriz[:3, 3] = valores[0:3], valores[3:6], valores[6:9], valores[9:12]
    return matriz


def _ler_3mf(caminho: Path) -> MalhaLida:
    with zipfile.ZipFile(caminho) as pacote:
        nomes = pacote.namelist()
        principal = "3D/3dmodel.model" if "3D/3dmodel.model" in nomes else next((n for n in nomes if n.lower().endswith(".model")), None)
        if principal is None:
            raise ModeloInvalido("Este arquivo não é um 3MF, ou está corrompido.")
        raiz = ET.fromstring(pacote.read(principal))
        mm_por_unidade = UNIDADES_3MF.get(raiz.get("unit", "millimeter"), 1.0)
        arquivos = {principal: raiz}

        def objetos_de(nome: str) -> dict[str, ET.Element]:
            if nome not in arquivos:
                arquivos[nome] = ET.fromstring(pacote.read(nome))
            return {o.get("id"): o for o in arquivos[nome].iter(f"{NUCLEO_3MF}object")}

        def malhas(nome: str, identificador: str, matriz: np.ndarray, profundidade: int) -> Iterator[tuple[np.ndarray, np.ndarray]]:
            if profundidade > PROFUNDIDADE_MAXIMA_3MF:
                raise ValueError("Componentes em ciclo no 3MF.")
            objeto = objetos_de(nome).get(identificador)
            if objeto is None:
                return
            malha = objeto.find(f"{NUCLEO_3MF}mesh")
            if malha is not None:
                pontos = np.array([(float(v.get("x")), float(v.get("y")), float(v.get("z"))) for v in malha.iter(f"{NUCLEO_3MF}vertex")])
                faces = np.array([(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in malha.iter(f"{NUCLEO_3MF}triangle")], dtype=np.int64)
                if len(pontos) and len(faces):
                    yield pontos @ matriz[:3, :3].T + matriz[:3, 3], faces
            for componente in objeto.iter(f"{NUCLEO_3MF}component"):
                # O slicer costuma guardar cada objeto em outro arquivo do pacote.
                outro = componente.get(f"{PRODUCAO_3MF}path")
                alvo = str(PurePosixPath(outro).relative_to("/")) if outro and outro.startswith("/") else (outro or nome)
                yield from malhas(alvo, componente.get("objectid"), matriz @ _matriz_3mf(componente.get("transform")), profundidade + 1)

        blocos_v, blocos_f = [], []
        total = 0
        for item in raiz.iter(f"{NUCLEO_3MF}item"):
            for pontos, faces in malhas(principal, item.get("objectid"), _matriz_3mf(item.get("transform")), 0):
                blocos_v.append(pontos)
                blocos_f.append(faces + total)
                total += len(pontos)
    if not blocos_f:
        raise ModeloInvalido("Este 3MF não tem nenhuma malha para imprimir.")
    return MalhaLida(np.concatenate(blocos_v), np.concatenate(blocos_f), mm_por_unidade, (0.0, 0.0, 1.0))
