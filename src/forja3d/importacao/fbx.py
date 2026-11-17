"""FBX binário: só a geometria e as transformações, que é o que a impressão usa.

O formato é fechado, mas a estrutura é conhecida: uma árvore de nós com propriedades tipadas.
A malha é lida na pose de repouso. Esqueleto, animação, cor e textura ficam de fora.
"""
from __future__ import annotations

import math
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from forja3d.importacao.malha import MalhaLida, ModeloInvalido

ASSINATURA = b"Kaydara FBX Binary  \x00"
INICIO_DOS_NOS = 27
PRIMEIRA_VERSAO_DE_64_BITS = 7500
PROFUNDIDADE_MAXIMA = 256
LIMITE_DA_LISTA_BYTES = 2 * 1024**3
CM_EM_MM = 10.0
PROPRIEDADES_SIMPLES = {b"Y": "<h", b"C": "<B", b"I": "<i", b"F": "<f", b"D": "<d", b"L": "<q"}
LISTAS = {b"f": "<f4", b"d": "<f8", b"l": "<i8", b"i": "<i4", b"b": "u1", b"c": "u1"}
ORDENS_DE_GIRO = ("XYZ", "XZY", "YZX", "YXZ", "ZXY", "ZYX", "XYZ")
EM_TEXTO = "Este FBX está em formato de texto, e o app só lê FBX binário. Baixe o modelo em STL, GLB ou OBJ."
SEM_MALHA = (
    "Este FBX não tem malha: só esqueleto ou animação, como os da Mixamo baixados sem a pele. "
    "Baixe o modelo com a malha, ou em STL, GLB ou OBJ."
)


@dataclass
class No:
    nome: str
    propriedades: list[Any] = field(default_factory=list)
    filhos: list[No] = field(default_factory=list)

    def filho(self, nome: str) -> No | None:
        return next((f for f in self.filhos if f.nome == nome), None)


def _ler_lista(dados: bytes, posicao: int, tipo: bytes) -> tuple[np.ndarray, int]:
    quantidade, codificacao, tamanho = struct.unpack_from("<III", dados, posicao)
    posicao += 12
    bruto = dados[posicao : posicao + tamanho]
    if codificacao == 1:
        # Com limite: um arquivo pequeno não pode se expandir até acabar com a memória.
        bruto = zlib.decompressobj().decompress(bruto, LIMITE_DA_LISTA_BYTES)
    return np.frombuffer(bruto, dtype=LISTAS[tipo], count=quantidade), posicao + tamanho


def _ler_propriedade(dados: bytes, posicao: int) -> tuple[Any, int]:
    tipo = dados[posicao:posicao + 1]
    posicao += 1
    if tipo in PROPRIEDADES_SIMPLES:
        formato = PROPRIEDADES_SIMPLES[tipo]
        return struct.unpack_from(formato, dados, posicao)[0], posicao + struct.calcsize(formato)
    if tipo in LISTAS:
        return _ler_lista(dados, posicao, tipo)
    if tipo in (b"S", b"R"):
        tamanho = struct.unpack_from("<I", dados, posicao)[0]
        posicao += 4
        bruto = dados[posicao : posicao + tamanho]
        return (bruto.decode("utf-8", "replace") if tipo == b"S" else bruto), posicao + tamanho
    raise ValueError(f"Propriedade desconhecida: {tipo!r}")


def _ler_no(dados: bytes, posicao: int, largos: bool, profundidade: int) -> tuple[No | None, int]:
    if profundidade > PROFUNDIDADE_MAXIMA:
        raise ValueError("Árvore funda demais.")
    formato = "<QQQ" if largos else "<III"
    fim, propriedades, _ = struct.unpack_from(formato, dados, posicao)
    posicao += struct.calcsize(formato)
    tamanho_do_nome = dados[posicao]
    posicao += 1
    if fim == 0:
        return None, posicao
    if fim <= posicao or fim > len(dados):
        raise ValueError("O nó aponta para fora do arquivo.")
    no = No(dados[posicao : posicao + tamanho_do_nome].decode("ascii", "replace"))
    posicao += tamanho_do_nome
    for _ in range(propriedades):
        valor, posicao = _ler_propriedade(dados, posicao)
        no.propriedades.append(valor)
    while posicao < fim:
        filho, posicao = _ler_no(dados, posicao, largos, profundidade + 1)
        if filho is None:
            break
        no.filhos.append(filho)
    return no, fim


def ler_arvore(dados: bytes) -> No:
    largos = struct.unpack_from("<I", dados, 23)[0] >= PRIMEIRA_VERSAO_DE_64_BITS
    raiz = No("")
    posicao = INICIO_DOS_NOS
    while posicao < len(dados):
        no, posicao = _ler_no(dados, posicao, largos, 0)
        if no is None:
            break
        raiz.filhos.append(no)
    return raiz


def _valores(no: No | None) -> dict[str, list[Any]]:
    """Propriedades de um `Properties70`. Só o que difere do padrão vem no arquivo."""
    grupo = no.filho("Properties70") if no else None
    if grupo is None:
        return {}
    return {p.propriedades[0]: p.propriedades[4:] for p in grupo.filhos if p.nome == "P" and len(p.propriedades) >= 4}


def _translacao(vetor: list[Any]) -> np.ndarray:
    matriz = np.eye(4)
    matriz[:3, 3] = vetor[:3]
    return matriz


def _escala(vetor: list[Any]) -> np.ndarray:
    return np.diag([*vetor[:3], 1.0])


def _giro(graus: list[Any], ordem: str = "XYZ") -> np.ndarray:
    eixos = {}
    for eixo, angulo in zip("XYZ", graus[:3]):
        c, s = math.cos(math.radians(angulo)), math.sin(math.radians(angulo))
        i, j = {"X": (1, 2), "Y": (2, 0), "Z": (0, 1)}[eixo]
        matriz = np.eye(4)
        matriz[i, i], matriz[i, j], matriz[j, i], matriz[j, j] = c, -s, s, c
        eixos[eixo] = matriz
    # A ordem XYZ do FBX aplica X primeiro.
    return eixos[ordem[2]] @ eixos[ordem[1]] @ eixos[ordem[0]]


def _matriz_local(modelo: No) -> np.ndarray:
    v = _valores(modelo)
    zero = [0.0, 0.0, 0.0]
    ordem = int(v.get("RotationOrder", [0])[0])
    ordem = ORDENS_DE_GIRO[ordem] if 0 <= ordem < len(ORDENS_DE_GIRO) else "XYZ"
    pivo_giro = _translacao(v.get("RotationPivot", zero))
    pivo_escala = _translacao(v.get("ScalingPivot", zero))
    return (
        _translacao(v.get("Lcl Translation", zero))
        @ _translacao(v.get("RotationOffset", zero))
        @ pivo_giro
        @ _giro(v.get("PreRotation", zero))
        @ _giro(v.get("Lcl Rotation", zero), ordem)
        @ np.linalg.inv(_giro(v.get("PostRotation", zero)))
        @ np.linalg.inv(pivo_giro)
        @ _translacao(v.get("ScalingOffset", zero))
        @ pivo_escala
        @ _escala(v.get("Lcl Scaling", [1.0, 1.0, 1.0]))
        @ np.linalg.inv(pivo_escala)
    )


def _matriz_da_geometria(modelo: No) -> np.ndarray:
    """Transformação que só vale para a malha do nó, e não passa para os filhos dele."""
    v = _valores(modelo)
    zero = [0.0, 0.0, 0.0]
    return _translacao(v.get("GeometricTranslation", zero)) @ _giro(v.get("GeometricRotation", zero)) @ _escala(v.get("GeometricScaling", [1.0, 1.0, 1.0]))


def _triangular(indices: np.ndarray) -> np.ndarray:
    """Um índice negativo fecha o polígono. Cada polígono vira um leque de triângulos."""
    fins = np.nonzero(indices < 0)[0]
    if not len(fins):
        return np.empty((0, 3), dtype=np.int64)
    inicios = np.r_[0, fins[:-1] + 1]
    positivos = np.where(indices < 0, ~indices, indices).astype(np.int64)
    meio = np.ones(int(fins[-1]) + 1, dtype=bool)
    meio[inicios] = False
    meio[fins] = False
    posicoes = np.nonzero(meio)[0]
    centro = np.repeat(inicios, np.maximum(fins - inicios - 1, 0))
    return np.column_stack((positivos[centro], positivos[posicoes], positivos[posicoes + 1]))


def _mundo(identificador: int, modelos: dict[int, No], pais: dict[int, list[int]], profundidade: int = 0) -> np.ndarray:
    if profundidade > PROFUNDIDADE_MAXIMA:
        raise ValueError("Os nós formam um ciclo.")
    local = _matriz_local(modelos[identificador])
    pai = next((p for p in pais.get(identificador, []) if p in modelos), None)
    return local if pai is None else _mundo(pai, modelos, pais, profundidade + 1) @ local


def _para_cima(configuracoes: dict[str, list[Any]]) -> tuple[float, float, float]:
    eixo = int(configuracoes.get("UpAxis", [1])[0])
    sinal = float(configuracoes.get("UpAxisSign", [1])[0]) or 1.0
    vetor = [0.0, 0.0, 0.0]
    vetor[eixo if eixo in (0, 1, 2) else 1] = 1.0 if sinal > 0 else -1.0
    return (vetor[0], vetor[1], vetor[2])


def ler_fbx(dados: bytes) -> MalhaLida:
    if not dados.startswith(ASSINATURA):
        if b"FBXHeaderExtension" in dados[:8192]:
            raise ModeloInvalido(EM_TEXTO)
        raise ModeloInvalido("Este arquivo não é um FBX, ou está corrompido.")
    raiz = ler_arvore(dados)
    objetos = raiz.filho("Objects")
    if objetos is None:
        raise ModeloInvalido(SEM_MALHA)

    geometrias = {
        g.propriedades[0]: g
        for g in objetos.filhos
        if g.nome == "Geometry" and g.filho("Vertices") is not None and g.filho("PolygonVertexIndex") is not None
    }
    if not geometrias:
        raise ModeloInvalido(SEM_MALHA)
    modelos = {m.propriedades[0]: m for m in objetos.filhos if m.nome == "Model"}
    pais: dict[int, list[int]] = defaultdict(list)
    conexoes = raiz.filho("Connections")
    for conexao in conexoes.filhos if conexoes else []:
        if conexao.nome == "C" and conexao.propriedades[0] == "OO":
            pais[conexao.propriedades[1]].append(conexao.propriedades[2])

    blocos_v, blocos_f = [], []
    total = 0
    for identificador, geometria in geometrias.items():
        pontos = np.asarray(geometria.filho("Vertices").propriedades[0], dtype=np.float64).reshape(-1, 3)
        triangulos = _triangular(np.asarray(geometria.filho("PolygonVertexIndex").propriedades[0]))
        if not len(triangulos):
            continue
        donos = [p for p in pais.get(identificador, []) if p in modelos] or [None]
        for dono in donos:
            matriz = np.eye(4) if dono is None else _mundo(dono, modelos, pais) @ _matriz_da_geometria(modelos[dono])
            blocos_v.append(pontos @ matriz[:3, :3].T + matriz[:3, 3])
            # Um espelhamento vira a malha do avesso.
            blocos_f.append((triangulos[:, ::-1] if np.linalg.det(matriz[:3, :3]) < 0 else triangulos) + total)
            total += len(pontos)
    if not blocos_f:
        raise ModeloInvalido(SEM_MALHA)

    configuracoes = _valores(raiz.filho("GlobalSettings"))
    unidade = float(configuracoes.get("UnitScaleFactor", [1.0])[0]) or 1.0
    return MalhaLida(np.concatenate(blocos_v), np.concatenate(blocos_f), unidade * CM_EM_MM, _para_cima(configuracoes))
