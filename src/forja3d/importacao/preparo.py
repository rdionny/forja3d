"""Da malha lida à parte da mesa: conserto, prévia, tamanho e orientação.

O arquivo vem da internet e pode ser enorme ou estar estragado. Por isso a leitura roda em
processo separado, com tempo e memória limitados, e uma falha lá não derruba o aplicativo.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from forja3d.cad.executor import SEM_JANELA, aguardar, ambiente_limpo, comando_auxiliar
from forja3d.fatiamento.cores import cor_pelo_nome_do_arquivo, proxima_cor
from forja3d.importacao.malha import MalhaLida, ModeloInvalido

log = logging.getLogger(__name__)

TEMPO_LIMITE_S = 600.0
LIMITE_DE_TRIANGULOS = 600_000
PONTOS_DA_PREVIA = 160_000
MAIOR_BURACO_EM_ARESTAS = 20_000
MUITO_PEQUENO_MM = 5.0
ALTURA_PADRAO_MM = 100.0
SEM_LEITURA = "Não consegui ler este arquivo. Ele pode estar corrompido, ou ter sido baixado pela metade."
GIROS_PARA_Z = {
    (0, 0, 1): ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    (0, 0, -1): ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
    (0, 1, 0): ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    (0, -1, 0): ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    (1, 0, 0): ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    (-1, 0, 0): ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
}


@dataclass
class ModeloLido:
    """O modelo já em milímetros e com Z para cima, esperando as escolhas do usuário."""

    arquivo: Path
    pasta: Path
    tamanho_mm: tuple[float, float, float]
    triangulos: int
    triangulos_originais: int
    fechada: bool
    corpos: int
    # Com vários arquivos, um para cada cor: nome, STL dentro de `pasta`, cor sugerida, volume e onde começa em Z.
    partes_de_cor: list[dict[str, Any]] = field(default_factory=list)
    # O que os arquivos têm em comum, como em logo_preto.stl e logo_branco.stl.
    nome_comum: str = ""

    @property
    def stl(self) -> Path:
        return self.pasta / "modelo.stl"

    @property
    def previa(self) -> Path:
        return self.pasta / "previa.npy"


@dataclass
class OpcoesDoModelo:
    nome: str
    escala: float = 1.0
    rotacao: list[list[float]] = field(default_factory=lambda: np.eye(3).tolist())
    # Corpo e cor, com "" para o modelo de um corpo só.
    cores: dict[str, str] = field(default_factory=dict)
    fundo: str | None = None
    partes_de_cor: list[dict[str, Any]] = field(default_factory=list)


# ---------- Conserto ----------


def _contar_corpos(malha: Any) -> int:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    pares = malha.face_adjacency
    total = len(malha.faces)
    grafo = coo_matrix((np.ones(len(pares), dtype=bool), (pares[:, 0], pares[:, 1])), shape=(total, total))
    _, grupo_da_face = connected_components(grafo, directed=False)
    # Triângulo sem área, que sobra de um corte ou do arredondamento do STL, fica solto dos vizinhos e não é um corpo.
    return len(np.unique(grupo_da_face[malha.nondegenerate_faces()]))


def _fechar_buracos(malha: Any) -> Any:
    """Tampa cada contorno aberto com um leque até o centro dele. Modelo oco e sem fundo ganha fundo assim."""
    import trimesh
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    ordenadas = np.sort(malha.edges, axis=1)
    _, indices, contagem = np.unique(ordenadas, axis=0, return_index=True, return_counts=True)
    abertas = malha.edges[indices[contagem == 1]]
    if not len(abertas) or len(abertas) > MAIOR_BURACO_EM_ARESTAS:
        return malha
    total = len(malha.vertices)
    grafo = coo_matrix((np.ones(len(abertas), dtype=bool), (abertas[:, 0], abertas[:, 1])), shape=(total, total))
    _, rotulos = connected_components(grafo, directed=False)
    vertices = [malha.vertices]
    faces = [malha.faces]
    proximo = total
    for rotulo in np.unique(rotulos[abertas[:, 0]]):
        contorno = abertas[rotulos[abertas[:, 0]] == rotulo]
        # Um contorno que se cruza em um vértice não tem tampa simples: fica como está.
        if len(contorno) < 3 or (np.bincount(contorno.ravel()) > 2).any():
            continue
        vertices.append(malha.vertices[np.unique(contorno)].mean(axis=0, keepdims=True))
        # A aresta nova anda no sentido contrário da face vizinha.
        faces.append(np.column_stack((contorno[:, 1], contorno[:, 0], np.full(len(contorno), proximo))))
        proximo += 1
    if proximo == total:
        return malha
    tampada = trimesh.Trimesh(np.concatenate(vertices), np.concatenate(faces), process=False)
    return tampada if tampada.is_watertight else malha


def _acertar_faces(malha: Any) -> Any:
    """Faces em sentidos misturados confundem o fatiador. O trimesh precisaria de outra biblioteca para isso."""
    import pyvista as pv
    import trimesh

    corrigida = pv.PolyData.from_regular_faces(malha.vertices, malha.faces).compute_normals(
        cell_normals=True, point_normals=False, consistent_normals=True, auto_orient_normals=False, split_vertices=False
    )
    return trimesh.Trimesh(corrigida.points.astype(np.float64), corrigida.regular_faces, process=False)


def _reduzir(malha: Any, limite: int) -> Any:
    """O bico de 0,4 mm não aproveita tantos triângulos, e eles pesam em toda a verificação."""
    import pyvista as pv
    import trimesh

    reduzida = pv.PolyData.from_regular_faces(malha.vertices, malha.faces).decimate_pro(1.0 - limite / len(malha.faces), preserve_topology=True, feature_angle=30.0)
    resultado = trimesh.Trimesh(reduzida.points.astype(np.float64), reduzida.regular_faces, process=True)
    return resultado if resultado.is_watertight or not malha.is_watertight else malha


def normalizar(lida: MalhaLida, centralizar: bool = True) -> tuple[Any, int]:
    """Milímetros, Z para cima, e o conserto que dá para fazer sem mudar a forma. Devolve também os triângulos originais.

    Sem centralizar, o modelo fica onde foi desenhado: as partes de cor de um logo só se encaixam na origem delas.
    """
    import trimesh

    if not len(lida.vertices) or not len(lida.faces):
        raise ModeloInvalido("Este arquivo não tem nenhuma superfície para imprimir.")
    para_cima = tuple(round(c) for c in lida.para_cima)
    giro = np.array(GIROS_PARA_Z.get(para_cima, GIROS_PARA_Z[(0, 0, 1)]), dtype=np.float64)
    malha = trimesh.Trimesh(lida.vertices @ giro.T * lida.mm_por_unidade, lida.faces, process=True)
    originais = len(lida.faces)
    malha.update_faces(malha.nondegenerate_faces())
    malha.update_faces(malha.unique_faces())
    malha.remove_unreferenced_vertices()
    if not len(malha.faces) or (malha.extents <= 0).any():
        raise ModeloInvalido("Este arquivo não tem volume: é uma superfície plana, ou está vazio.")
    if not malha.is_winding_consistent:
        malha = _acertar_faces(malha)
    if not malha.is_watertight:
        malha = _fechar_buracos(malha)
    if len(malha.faces) > LIMITE_DE_TRIANGULOS:
        malha = _reduzir(malha, LIMITE_DE_TRIANGULOS)
    if malha.is_watertight and malha.volume < 0:
        malha.invert()
    if centralizar:
        malha.apply_translation(_para_o_centro(malha))
    return malha, originais


def _para_o_centro(malha: Any) -> tuple[float, float, float]:
    limites = malha.bounds
    return (-(limites[0, 0] + limites[1, 0]) / 2, -(limites[0, 1] + limites[1, 1]) / 2, -limites[0, 2])


def _nome_comum(arquivos: list[Path]) -> str:
    return os.path.commonprefix([a.stem for a in arquivos]).strip(" _-.") or "colorido"


# ---------- Processo separado ----------


def _ler_e_gravar(entradas: list[Path], pasta: Path) -> dict[str, Any]:
    import trimesh

    from forja3d.importacao.leitura import ler_modelo

    if len(entradas) == 1:
        malha, originais = normalizar(ler_modelo(entradas[0]))
        malha.export(pasta / "modelo.stl")
        pontos, faces = trimesh.sample.sample_surface(malha, PONTOS_DA_PREVIA, seed=7)
        centro = (malha.bounds[0] + malha.bounds[1]) / 2
        np.save(pasta / "previa.npy", np.hstack((pontos - centro, malha.face_normals[faces])).astype(np.float32))
        return {
            "tamanho_mm": [round(float(v), 3) for v in malha.extents],
            "triangulos": len(malha.faces),
            "triangulos_originais": originais,
            "fechada": bool(malha.is_watertight),
            "corpos": _contar_corpos(malha),
        }

    # Um arquivo para cada cor, como quem baixa black.stl, red.stl e white.stl: todos valem na mesma origem.
    lidas = []
    for entrada in entradas:
        try:
            lidas.append(normalizar(ler_modelo(entrada), centralizar=False))
        except ModeloInvalido as erro:
            raise ModeloInvalido(f"{entrada.name}: {erro}") from erro
    juntas = trimesh.util.concatenate([malha for malha, _ in lidas])
    deslocamento = _para_o_centro(juntas)
    juntas.apply_translation(deslocamento)
    juntas.export(pasta / "modelo.stl")
    area_total = sum(float(malha.area) for malha, _ in lidas)
    amostras = []
    partes: list[dict[str, Any]] = []
    usadas: list[str] = []
    ocupados: set[str] = set()
    for indice, (entrada, (malha, _)) in enumerate(zip(entradas, lidas)):
        malha.apply_translation(deslocamento)
        arquivo = f"corpo_{indice + 1}.stl"
        malha.export(pasta / arquivo)
        quantidade = max(1000, int(PONTOS_DA_PREVIA * float(malha.area) / area_total))
        pontos, faces = trimesh.sample.sample_surface(malha, quantidade, seed=7)
        amostras.append(np.hstack((pontos, malha.face_normals[faces], np.full((quantidade, 1), indice))))
        cor = cor_pelo_nome_do_arquivo(entrada.stem)
        if cor is None or cor in usadas:
            cor = proxima_cor(usadas)
        usadas.append(cor)
        nome = nome_da_parte(entrada.stem, ocupados)
        ocupados.add(nome)
        partes.append(
            {
                "nome": nome,
                "stl": arquivo,
                "cor": cor,
                "volume_mm3": round(abs(float(malha.volume)) if malha.is_watertight else float(malha.convex_hull.volume), 3),
                "z_inicial": round(float(malha.bounds[0, 2]), 3),
            }
        )
    amostra = np.vstack(amostras)
    amostra[:, :3] -= (juntas.bounds[0] + juntas.bounds[1]) / 2
    np.save(pasta / "previa.npy", amostra.astype(np.float32))
    return {
        "tamanho_mm": [round(float(v), 3) for v in juntas.extents],
        "triangulos": len(juntas.faces),
        "triangulos_originais": sum(o for _, o in lidas),
        "fechada": all(bool(malha.is_watertight) for malha, _ in lidas),
        "corpos": len(lidas),
        "partes_de_cor": partes,
        "nome_comum": _nome_comum(entradas),
    }


def principal(*caminhos: str) -> int:
    """Executado no processo auxiliar, com um ou mais arquivos e, por último, a pasta de saída.

    Grava `modelo.stl`, `previa.npy`, `leitura.json` e, com vários arquivos, um STL por corpo.
    """
    from forja3d.cad.worker import limitar_memoria

    pasta = Path(caminhos[-1])
    limitar_memoria()
    try:
        resultado = {"ok": True, **_ler_e_gravar([Path(c) for c in caminhos[:-1]], pasta)}
    except ModeloInvalido as erro:
        resultado = {"ok": False, "erro": str(erro)}
    except BaseException as erro:  # noqa: BLE001 - o arquivo vem de fora: qualquer falha vira uma mensagem.
        resultado = {"ok": False, "erro": SEM_LEITURA, "detalhe": f"{type(erro).__name__}: {str(erro)[:400]}"}
    temporario = pasta / "leitura.tmp"
    temporario.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    temporario.replace(pasta / "leitura.json")
    return 0


def ler_em_processo(arquivos: Path | list[Path], cancelar: threading.Event | None = None, timeout: float = TEMPO_LIMITE_S) -> ModeloLido:
    """Quem chama fica com a pasta temporária do resultado, e apaga depois de usar."""
    lista = [arquivos] if isinstance(arquivos, Path) else list(arquivos)
    pasta = Path(tempfile.mkdtemp(prefix="forja3d_modelo_"))
    with open(pasta / "erros.txt", "wb") as erros:
        processo = subprocess.Popen(
            comando_auxiliar("--worker-importar", *(str(a) for a in lista), str(pasta)),
            cwd=pasta,
            env=ambiente_limpo(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=erros,
            creationflags=SEM_JANELA,
        )
        estado = aguardar(processo, timeout, cancelar)
    if estado == "timeout":
        raise ModeloInvalido(f"O arquivo passou de {timeout / 60:.0f} minutos para ser lido. Tente uma versão com menos detalhes.")
    leitura = pasta / "leitura.json"
    if not leitura.exists():
        log.warning("A leitura do modelo encerrou com o código %s", processo.returncode)
        raise ModeloInvalido(SEM_LEITURA)
    dados = json.loads(leitura.read_text(encoding="utf-8"))
    if not dados["ok"]:
        if dados.get("detalhe"):
            log.warning("Falha ao ler %s: %s", lista[0].name, dados["detalhe"])
        raise ModeloInvalido(dados["erro"])
    return ModeloLido(
        lista[0], pasta, tuple(dados["tamanho_mm"]), dados["triangulos"], dados["triangulos_originais"], dados["fechada"], dados["corpos"], dados.get("partes_de_cor", []), dados.get("nome_comum", "")
    )


# ---------- Escolhas do usuário ----------


def nome_da_parte(texto: str, ocupados: set[str] | frozenset[str] = frozenset()) -> str:
    """Só ASCII, como as partes que vêm do script, e sem repetir o nome de outra parte."""
    sem_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", sem_acentos).strip("_")[:40] or "importado"
    # "modelo" é o nome que a verificação usa para uma peça sem partes.
    base = "importado" if base.lower() == "modelo" else base
    nome = base
    contador = 1
    while nome in ocupados:
        contador += 1
        nome = f"{base}_{contador}"
    return nome


def tamanho_girado(tamanho: tuple[float, float, float], rotacao: np.ndarray) -> np.ndarray:
    return np.abs(np.asarray(rotacao)) @ np.asarray(tamanho, dtype=np.float64)


def altura_maxima(tamanho: tuple[float, float, float], rotacao: np.ndarray, perfil: dict[str, Any]) -> float:
    """A maior altura em que o modelo inteiro cabe na área segura da mesa."""
    seguro = perfil["volume_mm"]["seguro"]
    x, y, z = tamanho_girado(tamanho, rotacao)
    return float(min(seguro["x"] / x, seguro["y"] / y, seguro["z"] / z) * z)


def altura_sugerida(tamanho: tuple[float, float, float], rotacao: np.ndarray, perfil: dict[str, Any]) -> float:
    """A unidade do arquivo nem sempre é de verdade: tamanho que não cabe ou que some na mesa vira um padrão."""
    girado = tamanho_girado(tamanho, rotacao)
    maxima = altura_maxima(tamanho, rotacao, perfil)
    if girado[2] <= maxima and girado.max() >= MUITO_PEQUENO_MM:
        return float(girado[2])
    return min(ALTURA_PADRAO_MM, maxima)


def gravar_parte(origem: Path, destino: Path, opcoes: OpcoesDoModelo) -> dict[str, Any]:
    """Aplica as escolhas, grava o STL em `destino` e devolve as medidas no formato das outras partes."""
    import trimesh

    malha = trimesh.load_mesh(origem, force="mesh")
    # No STL, vértices quase iguais viram um só, e o triângulo entre eles fica sem área: com ele, uma malha fechada parece aberta.
    malha.update_faces(malha.nondegenerate_faces())
    transformacao = np.eye(4)
    transformacao[:3, :3] = np.asarray(opcoes.rotacao, dtype=np.float64) * opcoes.escala
    malha.apply_transform(transformacao)
    deslocamento = _para_o_centro(malha)
    malha.apply_translation(deslocamento)
    arquivo = f"{opcoes.nome}.stl"
    malha.export(destino / arquivo)
    caixa = malha.bounds
    fechada = bool(malha.is_watertight)
    # Em malha aberta, o volume calculado não quer dizer nada.
    volume = abs(float(malha.volume)) if fechada else float(malha.convex_hull.volume)
    solidos = None
    extras: dict[str, Any] = {}
    if opcoes.partes_de_cor:
        corpos = []
        fechados = True
        volume = 0.0
        for parte in opcoes.partes_de_cor:
            corpo = trimesh.load_mesh(origem.parent / parte["stl"], force="mesh")
            corpo.apply_transform(transformacao)
            corpo.apply_translation(deslocamento)
            nome_do_corpo = f"{opcoes.nome}__{parte['nome']}.stl"
            corpo.export(destino / nome_do_corpo)
            fechado = bool(corpo.is_watertight)
            fechados = fechados and fechado
            volume += abs(float(corpo.volume)) if fechado else float(corpo.convex_hull.volume)
            corpos.append({"nome": parte["nome"], "stl": nome_do_corpo, "cor": opcoes.cores.get(parte["nome"]) or parte.get("cor")})
        solidos = len(corpos)
        extras = {"corpos": corpos, "corpos_fechados": fechados, "fundo": opcoes.fundo or None}
    elif opcoes.cores.get(""):
        extras = {"cor": opcoes.cores[""]}
    return {
        "nome": opcoes.nome,
        "importada": True,
        "valido": True,
        "solidos": solidos if solidos is not None else _contar_corpos(malha),
        "volume_mm3": round(volume, 3),
        **extras,
        "caixa": {
            "x": [round(float(caixa[0, 0]), 3) + 0.0, round(float(caixa[1, 0]), 3) + 0.0],
            "y": [round(float(caixa[0, 1]), 3) + 0.0, round(float(caixa[1, 1]), 3) + 0.0],
            "z": [round(float(caixa[0, 2]), 3) + 0.0, round(float(caixa[1, 2]), 3) + 0.0],
        },
        "tamanho": [round(float(v), 3) for v in malha.extents],
        "stl": arquivo,
        "furos": [],
        "pinos": [],
        "arcos_concavos": [],
        "arcos_convexos": [],
    }


# ---------- Prévia ----------


def desenhar_previa(amostra: np.ndarray, rotacao: np.ndarray, cor: tuple[int, int, int] | list[tuple[int, int, int]], lado: int = 340) -> tuple[np.ndarray, np.ndarray]:
    """Imagem RGBA da peça, feita só com pontos, e os cantos do chão: não depende de placa de vídeo nem de janela 3D.

    A câmera olha de frente e de cima, como a vista inicial da mesa. Com uma lista de cores, a sétima coluna da amostra diz o corpo de cada ponto.
    """
    rotacao = np.asarray(rotacao, dtype=np.float32)
    pontos = amostra[:, :3] @ rotacao.T
    normais = amostra[:, 3:6] @ rotacao.T
    lado_da_camera, altura_da_camera = np.radians(28.0), np.radians(25.0)
    sa, ca, se, ce = np.sin(lado_da_camera), np.cos(lado_da_camera), np.sin(altura_da_camera), np.cos(altura_da_camera)
    # Linhas: X da imagem, Y da imagem (para baixo) e a direção até a câmera, que fica no lado -Y, a frente da mesa.
    vista = np.array([[ca, sa, 0.0], [sa * se, -ca * se, -ce], [sa * ce, -ca * ce, se]], dtype=np.float32)
    q = pontos @ vista.T
    n = normais @ vista.T
    chao = np.array([[x, y, pontos[:, 2].min()] for x, y in ((pontos[:, 0].min(), pontos[:, 1].min()), (pontos[:, 0].max(), pontos[:, 1].min()), (pontos[:, 0].max(), pontos[:, 1].max()), (pontos[:, 0].min(), pontos[:, 1].max()))], dtype=np.float32) @ vista.T

    extremos = np.vstack((q[:, :2], chao[:, :2]))
    minimo, maximo = extremos.min(axis=0), extremos.max(axis=0)
    escala = (lado - 24) / max(float((maximo - minimo).max()), 1e-6)
    deslocamento = (lado - (maximo - minimo) * escala) / 2 - minimo * escala
    xy = (q[:, :2] * escala + deslocamento).astype(np.int32)
    chao_xy = chao[:, :2] * escala + deslocamento

    luz = np.array([-0.35, -0.55, 0.76], dtype=np.float32)
    claridade = 0.35 + 0.65 * np.clip(n @ luz, 0.0, 1.0)
    base = np.array(cor, dtype=np.float32)[amostra[:, 6].astype(np.int32)] if isinstance(cor, list) else np.array(cor, dtype=np.float32)
    cores = (base * claridade[:, None]).astype(np.uint8)
    ordem = np.argsort(q[:, 2])
    imagem = np.zeros((lado, lado, 4), dtype=np.uint8)
    # Os mais próximos da câmera por último: em índice repetido, vale a última escrita.
    # Cada ponto ocupa 3 x 3 pixels, ou o lado de trás apareceria nos espaços entre eles.
    deslocamentos = np.array([(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)], dtype=np.int32)
    pixels = xy[ordem, None, :] + deslocamentos[None, :, :]
    x = np.clip(pixels[:, :, 0], 0, lado - 1).ravel()
    y = np.clip(pixels[:, :, 1], 0, lado - 1).ravel()
    imagem[y, x, :3] = np.repeat(cores[ordem], len(deslocamentos), axis=0)
    imagem[y, x, 3] = 255
    return imagem, chao_xy
