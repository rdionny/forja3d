"""Separa regiões de um modelo baixado em peças próprias, uma por cor, com encaixe no corpo.

A ideia vem de um vídeo da SuaFarm3D: em vez de trocar o filamento no meio da impressão, cada cor vira
uma peça, impressa inteira em um filamento só, que encaixa no rebaixo deixado no corpo. Veja "Cores" no README.

Uma região é um pedaço da superfície cercado por quinas. Para separar, a região é projetada na direção
para onde ela olha, e esse contorno vira um prisma que corta a peça: os lados retos deixam a peça colorida
entrar no rebaixo, e o fundo plano deixa imprimi-la deitada.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from forja3d.importacao.malha import ModeloInvalido

ANGULO_DA_QUINA_GRAUS = 30.0
AREA_MINIMA_MM2 = 3.0
VOLUME_MINIMO_MM3 = 1e-3  # Um cubo de 0,1 mm: abaixo disso, nem o bico desenha.
PROFUNDIDADE_MM = 2.0
FOLGA_MM = 0.15
SOBRA_ACIMA_MM = 0.5
PINO_COMPRIMENTO_MM = 3.0
PINO_LADO_MM = (1.6, 4.0)
PINO_MARGEM_MM = 0.8
PINO_FOLGA_MM = 0.2
ANGULO_DA_FACE_PLANA_GRAUS = 10.0
BASE = 0  # A maior região: é o corpo, e não dá para separar.
PEQUENA = -1


class ErroDeSeparacao(Exception):
    """A mensagem é para o usuário."""


@dataclass
class Regioes:
    vertices: np.ndarray
    faces: np.ndarray
    # Por face: BASE, PEQUENA ou o número de uma região que dá para separar, da maior para a menor.
    regiao_da_face: np.ndarray
    areas: list[float]
    # Pares de vértices nas divisas entre regiões, para a tela desenhar o contorno de cada uma.
    contornos: np.ndarray

    @property
    def separaveis(self) -> int:
        return len(self.areas) - 1


@dataclass
class Peca:
    cor: str
    malha: Any
    com_pino: bool
    # Com o pino para baixo, a face de trás fica no ar.
    precisa_de_suporte: bool
    volume_mm3: float


@dataclass
class Separacao:
    corpo: Any
    pecas: list[Peca]
    # Do corpo, direto da conta do corte: no STL, o arredondamento dos vértices faz a malha parecer aberta e em mais pedaços.
    volume_mm3: float
    solidos: int


def _solido(malha: Any) -> Any:
    """Um sólido só. Modelo baixado costuma ter um corpo por cor, um passando por dentro do outro."""
    import manifold3d
    import trimesh

    # Sem o conserto do trimesh: malha aberta é recusada logo abaixo, e não remendada.
    grupos = trimesh.graph.connected_components(malha.face_adjacency, nodes=np.arange(len(malha.faces)), min_len=1)
    cheios, ocos = [], []
    for corpo in malha.submesh(grupos, repair=False) if len(grupos) > 1 else [malha]:
        # Casca virada para dentro é o oco de uma peça: desvirada, ela sai do sólido em vez de entrar na soma.
        virada = corpo.volume < 0
        parte = manifold3d.Manifold(manifold3d.Mesh(np.asarray(corpo.vertices, np.float32), np.asarray(corpo.faces[:, ::-1] if virada else corpo.faces, np.uint32)))
        if parte.is_empty():
            raise ModeloInvalido("A malha deste modelo tem buracos, e não dá para cortar uma malha aberta. Conserte o modelo antes, ou baixe outro arquivo.")
        (ocos if virada else cheios).append(parte)
    if not cheios:
        raise ModeloInvalido("Este modelo não tem nenhum corpo com volume para cortar.")
    solido = cheios[0] if len(cheios) == 1 else manifold3d.Manifold.batch_boolean(cheios, manifold3d.OpType.Add)
    for oco in ocos:
        solido -= oco
    return solido


def _malha(solido: Any) -> Any:
    """A malha do sólido, sem as cascas sem volume que as contas deixam: elas não imprimem e apareceriam como corpos soltos."""
    import trimesh

    dados = solido.to_mesh()
    malha = trimesh.Trimesh(np.asarray(dados.vert_properties)[:, :3], np.asarray(dados.tri_verts), process=False)
    grupos = trimesh.graph.connected_components(malha.face_adjacency, nodes=np.arange(len(malha.faces)), min_len=1)
    triangulos = malha.triangles
    volume_da_face = np.einsum("ij,ij->i", triangulos[:, 0], np.cross(triangulos[:, 1], triangulos[:, 2])) / 6
    # Em módulo: o oco de uma peça é uma casca de volume negativo, e tem de ficar.
    com_volume = [g for g in grupos if abs(volume_da_face[g].sum()) > VOLUME_MINIMO_MM3]
    if len(com_volume) < len(grupos):
        malha.update_faces(np.concatenate(com_volume))
        malha.remove_unreferenced_vertices()
    return malha


def ler_regioes(stl: Path) -> Regioes:
    """Acha as regiões cercadas por quinas, como o balde de tinta dos fatiadores.

    shortcut: roda na thread de trabalho do app, sem limite de memória; malha de milhões de triângulos pede o processo separado da importação.
    """
    import trimesh

    malha = _malha(_solido(trimesh.load_mesh(stl, force="mesh")))
    suaves = malha.face_adjacency[malha.face_adjacency_angles < math.radians(ANGULO_DA_QUINA_GRAUS)]
    grupos = trimesh.graph.connected_components(suaves, nodes=np.arange(len(malha.faces)), min_len=1)
    com_area = sorted(((float(malha.area_faces[g].sum()), g) for g in grupos), key=lambda par: -par[0])
    regiao_da_face = np.full(len(malha.faces), PEQUENA, dtype=np.int32)
    areas = []
    for area, grupo in com_area:
        if area < AREA_MINIMA_MM2 and areas:
            break
        regiao_da_face[grupo] = len(areas)
        areas.append(round(area, 2))
    if len(areas) < 2:
        raise ErroDeSeparacao("Não achei regiões cercadas por quinas nesta peça. A superfície dela é lisa de ponta a ponta, sem divisas para separar.")
    de_um_lado, do_outro = regiao_da_face[malha.face_adjacency].T
    divisas = (de_um_lado != do_outro) & ((de_um_lado > BASE) | (do_outro > BASE))
    return Regioes(np.asarray(malha.vertices, dtype=np.float64), np.asarray(malha.faces, dtype=np.int64), regiao_da_face, areas, np.asarray(malha.face_adjacency_edges[divisas]))


def _pedacos(regioes: Regioes, pintura: dict[int, str]) -> list[tuple[str, np.ndarray]]:
    """Cada grupo de faces vizinhas da mesma cor vira uma peça. As duas bochechas, longe uma da outra, viram duas."""
    import trimesh

    malha = trimesh.Trimesh(regioes.vertices, regioes.faces, process=False)
    vizinhas = malha.face_adjacency
    saida = []
    for cor in dict.fromkeys(pintura.values()):
        escolhidas = np.isin(regioes.regiao_da_face, [r for r, c in pintura.items() if c == cor])
        pares = vizinhas[escolhidas[vizinhas[:, 0]] & escolhidas[vizinhas[:, 1]]]
        saida += [(cor, np.asarray(grupo)) for grupo in trimesh.graph.connected_components(pares, nodes=np.nonzero(escolhidas)[0], min_len=1)]
    return saida


def _eixos(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Dois eixos no plano da região, com u × v = normal."""
    apoio = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(apoio, normal)
    u /= np.linalg.norm(u)
    return u, np.cross(normal, u)


def _prisma(contorno: Any, u: np.ndarray, v: np.ndarray, normal: np.ndarray, de: float, ate: float) -> Any:
    import manifold3d

    return manifold3d.Manifold.extrude(contorno, ate - de).transform(np.column_stack([u, v, normal, normal * de]))


def _cortar(corpo: Any, triangulos: np.ndarray, pino: bool, profundidade_mm: float) -> tuple[Any, Any, bool, bool, np.ndarray]:
    """Tira um pedaço do corpo. Devolve o corpo, a peça, se ela levou pino, se a face é plana e a direção para onde ela olha."""
    import manifold3d

    normais = np.cross(triangulos[:, 1] - triangulos[:, 0], triangulos[:, 2] - triangulos[:, 0])
    areas = np.linalg.norm(normais, axis=1) / 2
    soma = normais.sum(axis=0) / 2
    if np.linalg.norm(soma) < 0.2 * areas.sum():
        raise ErroDeSeparacao("Uma das regiões dá a volta na peça, e não há um lado por onde ela entre no encaixe. Tire essa região da seleção.")
    normal = soma / np.linalg.norm(soma)
    u, v = _eixos(normal)

    planos = np.stack([triangulos @ u, triangulos @ v], axis=-1)
    lado_a, lado_b = planos[:, 1] - planos[:, 0], planos[:, 2] - planos[:, 0]
    sentido = lado_a[:, 0] * lado_b[:, 1] - lado_a[:, 1] * lado_b[:, 0]
    # O que está de costas para a direção do corte entra na sombra do mesmo jeito.
    planos[sentido < 0] = planos[sentido < 0][:, ::-1]
    sombra = manifold3d.CrossSection(list(planos[np.abs(sentido) > 1e-9]), manifold3d.FillRule.NonZero)
    menor = sombra.offset(-FOLGA_MM)
    if menor.is_empty():
        raise ErroDeSeparacao("Uma das regiões é fina demais para virar uma peça. Tire essa região da seleção.")
    alturas = triangulos @ normal
    fundo, topo = float(alturas.min()) - profundidade_mm, float(alturas.max()) + SOBRA_ACIMA_MM

    # Só o pedaço ligado à região sai do corpo. O que mais estiver no caminho do prisma, como outra peça logo atrás, fica onde está.
    rebaixo = max((corpo ^ _prisma(sombra, u, v, normal, fundo, topo)).decompose(), key=lambda p: p.volume(), default=None)
    peca = max((rebaixo ^ _prisma(menor, u, v, normal, fundo, topo)).decompose(), key=lambda p: p.volume(), default=None) if rebaixo is not None else None
    if peca is None:
        raise ErroDeSeparacao("Não consegui cortar uma das regiões. Tire essa região da seleção.")
    com_pino = False
    if pino:
        lado = float(np.clip(0.35 * math.sqrt(sombra.area()), *PINO_LADO_MM))
        # Uma face da própria região, para o pino nunca nascer fora dela.
        centros = planos.mean(axis=1)
        ancora = centros[np.argmin(np.linalg.norm(centros - np.average(centros, axis=0, weights=areas), axis=1))]
        base = manifold3d.CrossSection.square((lado, lado), True).translate(tuple(ancora)) ^ menor.offset(-PINO_MARGEM_MM)
        if base.area() >= 0.6 * lado * lado:
            # Só dentro do corpo: em parede fina, o pino não pode sair do outro lado.
            haste = _prisma(base, u, v, normal, fundo - PINO_COMPRIMENTO_MM, fundo + 0.5) ^ corpo
            com_o_pino = peca + haste
            if len(com_o_pino.decompose()) == 1:
                peca, com_pino = com_o_pino, True
                corpo -= _prisma(base.offset(PINO_FOLGA_MM), u, v, normal, fundo - PINO_COMPRIMENTO_MM - 0.5, fundo + 0.5)
    corpo -= rebaixo
    # Plana é a peça que, de cabeça para baixo, encosta na mesa em quase toda a sombra: um disco em relevo sim, uma cúpula não.
    de_frente = (normais @ normal) / np.maximum(2 * areas, 1e-12) > math.cos(math.radians(ANGULO_DA_FACE_PLANA_GRAUS))
    no_topo = alturas.min(axis=1) > topo - SOBRA_ACIMA_MM - 0.3
    plana = float(areas[de_frente & no_topo].sum()) > 0.6 * sombra.area()
    return corpo, peca, com_pino, plana, normal


def _pronta_para_imprimir(peca: Any, normal: np.ndarray, face_para_baixo: bool) -> Any:
    """Gira a peça para a posição de impressão. Quem a põe na mesa, em Z = 0, é `gravar_parte`."""
    u, v = _eixos(normal)
    rotacao = np.array([u, -v, -normal]) if face_para_baixo else np.array([u, v, normal])
    malha = _malha(peca)
    malha.vertices = malha.vertices @ rotacao.T
    return malha


def separar(regioes: Regioes, pintura: dict[int, str], pino: bool = True, profundidade_mm: float = PROFUNDIDADE_MM) -> Separacao:
    """`pintura` diz a cor de cada região escolhida. O corpo fica com o rebaixo, e cada peça sai pronta para imprimir.

    Com pino e face plana, a peça imprime com a face na mesa e o pino para cima. Com face curva, o pino vai
    para baixo e pede suporte. Sem pino, o fundo plano vai na mesa e nada pede suporte.
    """
    import trimesh

    pintura = {r: c for r, c in pintura.items() if 0 < r < len(regioes.areas)}
    if not pintura:
        raise ErroDeSeparacao("Escolha ao menos uma região para separar.")
    corpo = _solido(trimesh.Trimesh(regioes.vertices, regioes.faces, process=False))
    pecas = []
    for cor, faces in _pedacos(regioes, pintura):
        corpo, peca, com_pino, plana, normal = _cortar(corpo, regioes.vertices[regioes.faces[faces]], pino, profundidade_mm)
        face_para_baixo = com_pino and plana
        pecas.append(Peca(cor, _pronta_para_imprimir(peca, normal, face_para_baixo), com_pino, com_pino and not plana, round(peca.volume(), 3)))
    if corpo.is_empty():
        raise ErroDeSeparacao("As regiões escolhidas levam a peça inteira. Deixe ao menos uma parte como corpo.")
    return Separacao(_malha(corpo), pecas, round(corpo.volume(), 3), sum(p.volume() > VOLUME_MINIMO_MM3 for p in corpo.decompose()))
