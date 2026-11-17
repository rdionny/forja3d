"""Cores por troca manual de filamento: a impressora pausa, a pessoa troca, e a impressão segue.

A V3 SE tem um bico só. A técnica vem de um tutorial testado nesta impressora, e o que ele faz à
mão no Bloco de Notas acontece aqui sozinho: cada M600 que o fatiador deixa vira uma rotina que sobe
a cabeça, vai para o canto, pausa com M25, purga e volta. Veja "Cores" no README.

A rotina do tutorial foi pensada para uma peça de 1 mm. Aqui ela volta para a altura em que estava
antes de seguir, e só desce para purgar perto da mesa quando a peça ainda é baixa: com uma peça alta
por perto, a cabeça bateria nela.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from forja3d.fatiamento.validador_gcode import MARCA_FIM, MARCA_INICIO
from forja3d.layout_mesa import ItemMesa, LayoutMesa, rotacao_do_item

CAMADA_INICIAL_MM = 0.2  # Igual em todos os presets de qualidade da V3 SE.
TORRE_LARGURA_MM = 40.0
TORRE_PURGA_MM3 = 70
TORRE_ESCORVA_MM3 = 20
TORRE_FOLGA_MM = 8.0
ALTURA_DA_PURGA_BAIXA_MM = 2.0
FOLGA_NA_VOLTA_MM = 0.4
LIMITE_DE_TROCAS_SEM_AVISO = 12
FACE_PLANA = 0.999  # Componente Z da normal: até uns 2,5° de inclinação.
AREA_MINIMA_DA_FACE_MM2 = 1.0
ALTURAS_PLANAS_MOSTRADAS = 8
TOLERANCIA_DA_ALTURA_MM = 0.1

# Nome e cor. O nome vai para o roteiro de trocas e para o visor, então não leva acento.
PALETA = (
    ("preto", "#1A1A1A"),
    ("branco", "#F5F5F5"),
    ("vermelho", "#D32F2F"),
    ("laranja", "#F57C00"),
    ("amarelo", "#FBC02D"),
    ("verde", "#388E3C"),
    ("azul", "#1976D2"),
    ("roxo", "#7B1FA2"),
    ("rosa", "#EC407A"),
    ("marrom", "#6D4C41"),
    ("cinza", "#9E9E9E"),
    ("bege", "#D7B98E"),
)
NOMES_EM_ARQUIVOS = {
    "black": "preto", "preto": "preto", "preta": "preto",
    "white": "branco", "branco": "branco", "branca": "branco",
    "red": "vermelho", "vermelho": "vermelho", "vermelha": "vermelho",
    "orange": "laranja", "laranja": "laranja",
    "yellow": "amarelo", "gold": "amarelo", "amarelo": "amarelo", "amarela": "amarelo",
    "green": "verde", "verde": "verde",
    "blue": "azul", "azul": "azul",
    "purple": "roxo", "violet": "roxo", "roxo": "roxo", "roxa": "roxo",
    "pink": "rosa", "rosa": "rosa",
    "brown": "marrom", "marrom": "marrom",
    "gray": "cinza", "grey": "cinza", "silver": "cinza", "cinza": "cinza",
    "beige": "bege", "bege": "bege",
}
_COR = re.compile(r"#[0-9A-Fa-f]{6}")
_FERRAMENTA = re.compile(r"MANUAL_TOOL_CHANGE T(\d+)")
_Z_DA_CAMADA = re.compile(r"^;\s*Z:\s*(-?[\d.]+)")
_ALTURA_DA_CAMADA = re.compile(r"^;\s*HEIGHT:\s*([\d.]+)")
_PALAVRA_G = re.compile(r"\b([XYZF])(-?\d*\.?\d+)")
_GRAMAS = re.compile(r"^;\s*filament used \[g\]\s*=\s*(.+)$")


class ErroDeCor(Exception):
    """As cores escolhidas não têm como sair nesta impressão. A mensagem é para o usuário."""


def cor_valida(valor: Any) -> str | None:
    return valor.upper() if isinstance(valor, str) and _COR.fullmatch(valor) else None


def _componentes(cor: str) -> tuple[int, int, int]:
    return int(cor[1:3], 16), int(cor[3:5], 16), int(cor[5:7], 16)


def nome_da_cor(cor: str | None) -> str:
    """O nome da cor mais próxima na paleta. Sem cor escolhida, serve o filamento que estiver à mão."""
    if not cor:
        return "qualquer cor"
    r, g, b = _componentes(cor)
    return min(PALETA, key=lambda c: sum((a - v) ** 2 for a, v in zip((r, g, b), _componentes(c[1]))))[0]


def cor_pelo_nome_do_arquivo(texto: str) -> str | None:
    """black.stl e peca_vermelha.stl já dizem a cor: quem baixa um modelo em partes costuma receber assim."""
    paleta = dict(PALETA)
    for palavra in re.findall(r"[a-z]+", texto.lower()):
        if palavra in NOMES_EM_ARQUIVOS:
            return paleta[NOMES_EM_ARQUIVOS[palavra]]
    return None


def proxima_cor(usadas: list[str]) -> str:
    return next((c for _, c in PALETA if c not in usadas), PALETA[0][1])


# ---------- O que cada item usa ----------


def cores_do_item(item: ItemMesa, medida: dict[str, Any] | None) -> dict[str, str | None]:
    """Corpo e cor, com "" para a peça inteira. O que a pessoa mudou na mesa vale sobre o que veio na importação."""
    corpos = (medida or {}).get("corpos")
    if not corpos:
        return {"": item.cores.get("") or (medida or {}).get("cor")}
    return {c["nome"]: item.cores.get(c["nome"]) or c.get("cor") for c in corpos}


def fundo_do_item(item: ItemMesa, medida: dict[str, Any] | None) -> str | None:
    """O corpo cuja cor vai acima da primeira camada. None: todas as camadas ficam coloridas."""
    valor = item.fundo if item.fundo is not None else (medida or {}).get("fundo")
    return valor or None


@dataclass(frozen=True)
class PlanoDeCores:
    cores: tuple[str | None, ...]
    alturas: tuple[dict[str, Any], ...] = ()

    @property
    def multicor(self) -> bool:
        return len(self.cores) > 1

    @property
    def tem_trocas(self) -> bool:
        return self.multicor or bool(self.alturas)


def plano_do_arquivo(layout: LayoutMesa, numero: int, medidas: dict[str, dict[str, Any]]) -> PlanoDeCores:
    cores: list[str | None] = []
    for item in layout.itens_do_arquivo(numero):
        for cor in cores_do_item(item, medidas.get(item.parte)).values():
            if cor not in cores:
                cores.append(cor)
    alturas = tuple(layout.alturas.get(numero, []))
    if alturas and len(cores) > 1:
        raise ErroDeCor(f"O arquivo {numero} tem peças de cores diferentes e também troca por altura. Use só uma das duas formas: a troca por altura vale para a mesa inteira.")
    return PlanoDeCores(tuple(cores), alturas)


def _altura_do_item(layout: LayoutMesa, item: ItemMesa, medida: dict[str, Any] | None) -> float:
    """A altura na posição em que ele imprime: uma peça deitada troca de cor em outras alturas."""
    return layout.altura_do_item(item) if item.tombo else float((medida or {}).get("tamanho", [0, 0, 0])[2])


def estimar_trocas(layout: LayoutMesa, numero: int, medidas: dict[str, dict[str, Any]], camada_mm: float) -> int:
    """Quantas pausas o arquivo pede, antes de fatiar. Em uma peça alta, cores diferentes custam uma troca por camada."""
    plano = plano_do_arquivo(layout, numero, medidas)
    if plano.alturas:
        return len(plano.alturas)
    if not plano.multicor:
        return 0
    alturas_por_cor: dict[str | None, float] = {}
    for item in layout.itens_do_arquivo(numero):
        medida = medidas.get(item.parte) or {}
        altura = _altura_do_item(layout, item, medida)
        fundo = fundo_do_item(item, medida)
        cores = cores_do_item(item, medida)
        for corpo, cor in cores.items():
            # Só a primeira camada é colorida: as outras cores acabam nela.
            topo = altura if not fundo or corpo == fundo else CAMADA_INICIAL_MM
            alturas_por_cor[cor] = max(alturas_por_cor.get(cor, 0.0), topo)
    # A cor mais alta segue de uma camada para a outra sem troca. Cada outra cor custa uma troca por camada em que aparece.
    outras = sorted(alturas_por_cor.values())[:-1]
    return sum(1 + math.ceil(max(0.0, topo - CAMADA_INICIAL_MM) / max(camada_mm, 0.01)) for topo in outras)


# ---------- Cores pedidas ao agente ----------


def _cores_dos_itens(itens: list[ItemMesa], medidas: dict[str, dict[str, Any]]) -> set[str | None]:
    return {cor for item in itens for cor in cores_do_item(item, medidas.get(item.parte)).values()}


def _cores_novas(item: ItemMesa, medida: dict[str, Any], cor: str | None, corpos: dict[str, str] | None) -> dict[str, str]:
    nomes = [c["nome"] for c in medida.get("corpos") or []]
    if not nomes:
        if corpos:
            raise ErroDeCor(f"A parte {item.parte} não tem corpos separados. Use a cor da parte inteira.")
        return {"": cor} if cor else dict(item.cores)
    desconhecidos = set(corpos or ()) - set(nomes)
    if desconhecidos:
        raise ErroDeCor(f"A parte {item.parte} não tem o corpo {', '.join(sorted(desconhecidos))}. Os corpos dela são: {', '.join(nomes)}.")
    atuais = cores_do_item(item, medida)
    novas = {nome: (corpos or {}).get(nome) or cor or atuais[nome] for nome in nomes}
    return {nome: valor for nome, valor in novas.items() if valor}


def _conferir_trocas(trocas: list[dict[str, Any]], parte: str, altura: float) -> None:
    for troca in trocas:
        if troca["z"] >= altura:
            raise ErroDeCor(f"A parte {parte} tem {altura:g} mm de altura, então a troca em {troca['z']:g} mm nunca aconteceria.")
    for baixa, alta in pairwise(trocas):
        if alta["z"] - baixa["z"] < CAMADA_INICIAL_MM:
            raise ErroDeCor(f"As trocas em {baixa['z']:g} mm e {alta['z']:g} mm cairiam na mesma camada. Deixe só uma delas.")


def _levar_para_arquivo_igual(layout: LayoutMesa, itens: list[ItemMesa], medidas: dict[str, dict[str, Any]], trocas: list[dict[str, Any]]) -> None:
    """Junta com um arquivo que sai igual, se couber. Assim, três peças vermelhas pedidas uma a uma não viram três impressões."""
    cores = _cores_dos_itens(itens, medidas)
    iguais = [
        numero
        for numero in layout.arquivos()
        if numero != itens[0].arquivo and len(cores) == 1 and layout.alturas.get(numero, []) == trocas and _cores_dos_itens(layout.itens_do_arquivo(numero), medidas) == cores
    ]
    for item in itens:
        destino = next((numero for numero in iguais if layout.cabe_no_arquivo(item, numero)), None)
        if destino is not None:
            layout.mover_para_arquivo(item.id, destino)
            continue
        destino = layout.mover_para_arquivo(item.id)
        layout.definir_alturas(destino, trocas)
        iguais.append(destino)


def aplicar_cores(
    layout: LayoutMesa,
    parte: str,
    medidas: dict[str, dict[str, Any]],
    cor: str | None = None,
    corpos: dict[str, str] | None = None,
    trocas: list[dict[str, Any]] | None = None,
) -> list[int]:
    """Aplica o pedido em todas as cópias da parte e devolve os arquivos em que ela ficou.

    Uma troca por altura vale para o arquivo inteiro, e cores diferentes no mesmo arquivo custam uma pausa por camada.
    Por isso a parte só divide o arquivo com peças que saem iguais a ela: as outras ficam onde estão, e ela muda de arquivo.
    """
    medida = medidas[parte]
    itens = [i for i in layout.itens if i.parte == parte]
    if not itens:
        raise ErroDeCor(f"A parte {parte} foi retirada da mesa. Peça para o usuário colocá-la de volta.")
    ordenadas = None if trocas is None else sorted(trocas, key=lambda t: t["z"])
    if ordenadas:
        _conferir_trocas(ordenadas, parte, max(_altura_do_item(layout, i, medida) for i in itens))
    for item in itens:
        layout.definir_cores(item.id, _cores_novas(item, medida, cor, corpos), item.fundo)
    for numero in sorted({i.arquivo for i in itens}):
        desejadas = ordenadas if ordenadas is not None else list(layout.alturas.get(numero, []))
        no_arquivo = layout.itens_do_arquivo(numero)
        sozinha = all(i.parte == parte for i in no_arquivo)
        saem_iguais = len(_cores_dos_itens(no_arquivo, medidas)) == 1 and layout.alturas.get(numero, []) == desejadas
        if sozinha or saem_iguais:
            layout.definir_alturas(numero, desejadas)
        else:
            _levar_para_arquivo_igual(layout, [i for i in itens if i.arquivo == numero], medidas, desejadas)
    arquivos = sorted({i.arquivo for i in itens})
    for numero in arquivos:
        plano_do_arquivo(layout, numero, medidas)
    return arquivos


def alturas_planas(stl: Path, tombo: list[float] | None = None) -> list[dict[str, float]]:
    """Onde a peça tem faces planas viradas para cima, medido da mesa, da maior área para a menor.

    É nessas alturas que uma troca de cor desenha alguma coisa: letras em relevo começam no topo da placa.
    """
    import numpy as np
    import trimesh

    triangulos = trimesh.load_mesh(stl, process=False).triangles
    if tombo:
        triangulos = triangulos @ np.reshape(tombo, (3, 3)).T
    normais = np.cross(triangulos[:, 1] - triangulos[:, 0], triangulos[:, 2] - triangulos[:, 0])
    tamanhos = np.linalg.norm(normais, axis=1)
    planas = normais[:, 2] > FACE_PLANA * np.maximum(tamanhos, 1e-12)
    alturas_z = triangulos[:, :, 2]
    base, topo = alturas_z.min(), alturas_z.max()
    valores, grupos = np.unique(np.round(alturas_z[planas].mean(axis=1) - base, 2), return_inverse=True)
    areas = np.bincount(grupos, tamanhos[planas] / 2, minlength=len(valores))
    # O topo fica de fora: acima dele não há mais nada para mudar de cor.
    encontradas = [{"z_mm": float(z), "area_mm2": round(float(a), 1)} for z, a in zip(valores, areas) if a >= AREA_MINIMA_DA_FACE_MM2 and z < topo - base - 0.01]
    return sorted(encontradas, key=lambda f: -f["area_mm2"])


def situacao_das_cores(layout: LayoutMesa, parte: str, medidas: dict[str, dict[str, Any]], stl: Path, camada_mm: float) -> dict[str, Any]:
    """O que o agente precisa para conferir e explicar o resultado: ele não vê as cores nas vistas da peça."""
    medida = medidas[parte]
    itens = [i for i in layout.itens if i.parte == parte]
    # As cópias costumam estar na mesma posição: a resposta vale para a primeira.
    tombo = itens[0].tombo if itens else None
    planas = alturas_planas(stl, tombo)
    arquivos = []
    alertas: list[str] = []
    for numero in sorted({i.arquivo for i in itens}):
        plano = plano_do_arquivo(layout, numero, medidas)
        pausas = estimar_trocas(layout, numero, medidas, camada_mm)
        arquivos.append(
            {
                "numero": numero,
                "partes": sorted({i.parte for i in layout.itens_do_arquivo(numero)}),
                "cores": [nome_da_cor(c) for c in plano.cores],
                "trocas": [{"z_mm": t["z"], "cor": nome_da_cor(t["cor"])} for t in plano.alturas],
                "pausas_previstas": pausas,
            }
        )
        alertas += [
            f"Não há face plana em {t['z']:g} mm: a cor muda no meio das paredes, em uma faixa horizontal. Se a ideia era destacar um relevo, a altura está errada."
            for t in plano.alturas
            if not any(abs(p["z_mm"] - t["z"]) <= TOLERANCIA_DA_ALTURA_MM for p in planas)
        ]
        if pausas > LIMITE_DE_TROCAS_SEM_AVISO:
            alertas.append(f"O arquivo {numero} pede cerca de {pausas} pausas, com alguém ao lado da impressora em cada uma.")
    situacao: dict[str, Any] = {
        "parte": parte,
        "copias": len(itens),
        "deitada_na_mesa": bool(tombo),
        "altura_mm": _altura_do_item(layout, itens[0], medida) if itens else float(medida["tamanho"][2]),
        "alturas_planas": sorted(planas[:ALTURAS_PLANAS_MOSTRADAS], key=lambda f: f["z_mm"]),
        "arquivos": arquivos,
        "total_de_arquivos": len(layout.arquivos()),
        "alertas": list(dict.fromkeys(alertas)),
    }
    if medida.get("corpos") and itens:
        situacao["corpos"] = [{"corpo": nome, "cor": nome_da_cor(cor)} for nome, cor in cores_do_item(itens[0], medida).items()]
    return situacao


# ---------- Malhas por cor ----------


def matriz_do_item(inteira: Any, item: ItemMesa) -> Any:
    """A peça na posição da mesa: deitada, girada, centrada no item e apoiada em Z = 0.

    O corpo anda junto com a peça: a caixa que vale para centralizar é a da peça inteira.
    """
    import numpy as np
    import trimesh

    rotacao = np.eye(4)
    rotacao[:3, :3] = np.reshape(rotacao_do_item(item), (3, 3))
    pontos = trimesh.transform_points(trimesh.bounds.corners(inteira.bounds), rotacao)
    minimo, maximo = pontos.min(axis=0), pontos.max(axis=0)
    # Em um ângulo qualquer, o canto da caixa pode nem ser da peça: quem encosta na mesa é o vértice mais baixo.
    base = trimesh.transform_points(inteira.vertices, rotacao)[:, 2].min() if item.tombo else minimo[2]
    translacao = trimesh.transformations.translation_matrix((item.x - (minimo[0] + maximo[0]) / 2, item.y - (minimo[1] + maximo[1]) / 2, -base))
    return translacao @ rotacao


def _cortar_na_primeira_camada(malha: Any) -> tuple[Any, Any]:
    """A parte de baixo e a de cima. O plano fica no topo da primeira camada."""
    import manifold3d
    import numpy as np
    import trimesh

    fechada = trimesh.Trimesh(malha.vertices, malha.faces, process=True)
    solido = manifold3d.Manifold(manifold3d.Mesh(np.asarray(fechada.vertices, np.float32), np.asarray(fechada.faces, np.uint32)))
    if solido.is_empty():
        raise ErroDeCor("Para colorir só a primeira camada, cada corpo precisa ser uma malha fechada. Um dos corpos tem buracos.")
    cima, baixo = solido.split_by_plane((0.0, 0.0, 1.0), CAMADA_INICIAL_MM)

    def para_malha(parte: Any) -> Any:
        if parte.is_empty():
            return None
        dados = parte.to_mesh()
        return trimesh.Trimesh(np.asarray(dados.vert_properties)[:, :3], np.asarray(dados.tri_verts), process=False)

    return para_malha(baixo), para_malha(cima)


def montar_stls_por_cor(itens: list[ItemMesa], stls: dict[str, Path], medidas: dict[str, dict[str, Any]], pasta: Path) -> list[tuple[str | None, Path]]:
    """Um STL por cor, com tudo na posição da mesa, e a cor mais alta por último.

    A última cor da primeira camada segue para a camada de cima sem troca: é assim que quatro cores custam três trocas.
    """
    import trimesh

    pedacos: dict[str | None, list[Any]] = {}
    for item in itens:
        medida = medidas.get(item.parte) or {}
        inteira = trimesh.load_mesh(stls[item.parte], process=False)
        matriz = matriz_do_item(inteira, item)
        cores = cores_do_item(item, medida)
        if not medida.get("corpos"):
            pedacos.setdefault(cores[""], []).append(inteira.copy().apply_transform(matriz))
            continue
        fundo = fundo_do_item(item, medida)
        malhas = {c["nome"]: trimesh.load_mesh(stls[item.parte].parent / c["stl"], process=False).apply_transform(matriz) for c in medida["corpos"]}
        if fundo is None:
            for nome, malha in malhas.items():
                pedacos.setdefault(cores[nome], []).append(malha)
            continue
        if fundo not in malhas:
            raise ErroDeCor(f"O corpo {fundo}, escolhido como fundo de {item.parte}, não existe mais. Escolha outro em Cor.")
        # A malha da cor de fundo leva tudo o que está acima da primeira camada: assim ela começa na mesa e o fatiador não a derruba.
        fundo_malhas = [malhas[fundo]]
        for nome, malha in malhas.items():
            if nome == fundo:
                continue
            baixo, cima = _cortar_na_primeira_camada(malha)
            if baixo is not None:
                pedacos.setdefault(cores[nome], []).append(baixo)
            if cima is not None:
                fundo_malhas.append(cima)
        pedacos.setdefault(cores[fundo], []).extend(fundo_malhas)

    juntas = {cor: trimesh.util.concatenate(lista) for cor, lista in pedacos.items()}
    for cor, malha in juntas.items():
        # O fatiador põe na mesa cada objeto que começa no ar, e a cor sairia na altura errada.
        if malha.bounds[0, 2] > 0.01:
            raise ErroDeCor(
                f"A cor {nome_da_cor(cor)} começa acima da mesa, e não há como imprimir assim com cores por corpo. "
                "Marque Só a primeira camada colorida, com um fundo que encoste na mesa, ou use a troca por altura."
            )
    saida = []
    for indice, (cor, malha) in enumerate(sorted(juntas.items(), key=lambda par: par[1].bounds[1, 2]), start=1):
        destino = pasta / f"cor_{indice}.stl"
        malha.export(destino)
        saida.append((cor, destino))
    return saida


def posicao_da_torre(layout: LayoutMesa, numero: int, quantidade_de_cores: int, camada_mm: float) -> tuple[float, float, float, float] | None:
    """Um canto livre da mesa para a torre de purga, com o espaço que ela ocupa: início em X e Y, largura e profundidade.

    A torre cresce em Y conforme as trocas de cada camada.
    """
    largura = TORRE_LARGURA_MM + 2 * TORRE_FOLGA_MM
    profundidade = (TORRE_PURGA_MM3 + TORRE_ESCORVA_MM3) * (quantidade_de_cores - 1) / (TORRE_LARGURA_MM * min(camada_mm, CAMADA_INICIAL_MM)) + 12.0 + 2 * TORRE_FOLGA_MM
    limite_x, limite_y = layout.largura - layout.margem, layout.profundidade - layout.margem
    if largura > limite_x - layout.margem or profundidade > limite_y - layout.margem:
        return None
    ocupados = [(i.x, i.y, *layout.tamanho_do_item(i)) for i in layout.itens_do_arquivo(numero)]

    def livre(x: float, y: float) -> bool:
        return all(abs(x + largura / 2 - ix) >= (largura + dx) / 2 or abs(y + profundidade / 2 - iy) >= (profundidade + dy) / 2 for ix, iy, dx, dy in ocupados)

    # Atrás primeiro: a cabeça vai para o canto da frente e a esquerda a cada troca.
    for x in (limite_x - largura, layout.margem, (layout.largura - largura) / 2):
        for y in (limite_y - profundidade, layout.margem, (layout.profundidade - profundidade) / 2):
            if livre(x, y):
                return round(x + TORRE_FOLGA_MM, 1), round(y + TORRE_FOLGA_MM, 1), largura - 2 * TORRE_FOLGA_MM, profundidade - 2 * TORRE_FOLGA_MM
    return None


# ---------- Pausa no G-code ----------


def bloco_de_troca(numero: int, total: int, camada: int, z_camada: float, z_atual: float, velocidade: float | None, cor: str | None, z_maximo: float, com_torre: bool) -> list[str]:
    """A rotina do tutorial, com a volta para a altura certa. Sem a torre, purga mais: é o único lugar em que a cor antiga sai."""
    subida = max(0.0, min(5.0, z_maximo - z_atual))
    baixa = z_camada <= ALTURA_DA_PURGA_BAIXA_MM
    linha_mm = 10 if com_torre else 20
    linhas = [
        f"{MARCA_INICIO} {numero}/{total} camada={camada} z={z_camada:g} para={cor or 'livre'}",
        "M400",
        "G91",
        f"G1 Z{subida:g} F300",
        "G90",
        "G1 X-2.1 Y20 F5000",
        "M400",
        f"M117 Troca {numero}/{total}: {nome_da_cor(cor).upper()}",
        "M25",
        "M83",
    ]
    if baixa:
        linhas += [
            "G1 E5 F200",
            "G1 Z0.28 F3000",
            f"G1 Y145 E{linha_mm} F1200",
            "G1 X-2.4 Y145 F5000",
            f"G1 Y20 E{linha_mm} F1200",
        ]
    else:
        # Com peça alta na mesa, descer até 0,28 mm levaria a carcaça da cabeça contra ela: a purga fica no ar, no canto.
        linhas.append(f"G1 E{5 + 2 * linha_mm} F200")
    linhas.append(f"G1 Z{z_atual:g} F3000")
    if velocidade:
        linhas.append(f"G1 F{velocidade:g}")
    linhas.append(MARCA_FIM)
    return linhas


def _estado_depois_da_linha(linha: str, estado: dict[str, Any]) -> None:
    """Acompanha a altura e a velocidade: as linhas seguintes do fatiador contam com elas."""
    codigo = linha.split(";", 1)[0].strip()
    if codigo.startswith("G90"):
        estado["relativo"] = False
    elif codigo.startswith("G91"):
        estado["relativo"] = True
    elif re.match(r"G[01]\b", codigo):
        for letra, valor in _PALAVRA_G.findall(codigo):
            if letra == "F":
                estado["f"] = float(valor)
            elif letra == "Z":
                estado["z"] = estado["z"] + float(valor) if estado["relativo"] else float(valor)


def _proximas_ferramentas(linhas: list[str]) -> tuple[int | None, list[int | None]]:
    """A ferramenta inicial e, para cada M600, a que vem depois dele. O comentário vem logo abaixo do comando."""
    inicial = None
    depois: list[int | None] = []
    for indice, linha in enumerate(linhas):
        if inicial is None and (achada := _FERRAMENTA.search(linha)):
            inicial = int(achada.group(1))
        if linha.strip() == "M600":
            depois.append(next((int(m.group(1)) for seguinte in linhas[indice + 1 : indice + 6] if (m := _FERRAMENTA.search(seguinte))), None))
    return inicial, depois


def inserir_pausas_nas_trocas(gcode: Path, cores: list[str | None], z_maximo: float) -> dict[str, Any]:
    """Faz em cada M600 o que o tutorial faz à mão. Devolve a cor inicial e o roteiro de trocas."""
    linhas = gcode.read_text(encoding="utf-8", errors="replace").splitlines()
    inicial, destinos = _proximas_ferramentas(linhas)
    if any(d is None or d >= len(cores) for d in destinos):
        raise ErroDeCor("O fatiador gerou uma troca de filamento sem dizer para qual cor. O arquivo não foi liberado.")
    atual = cores[inicial] if inicial is not None and inicial < len(cores) else cores[0]
    cor_inicial = atual
    estado: dict[str, Any] = {"z": 0.0, "f": None, "relativo": False}
    camada = 0
    z_camada = 0.0
    trocas = []
    saida: list[str] = []
    for linha in linhas:
        if linha.startswith(";LAYER_CHANGE"):
            camada += 1
        elif achado := _Z_DA_CAMADA.match(linha):
            z_camada = float(achado.group(1))
        if linha.strip() == "M600":
            numero = len(trocas) + 1
            proxima = cores[destinos[numero - 1]]
            saida += bloco_de_troca(numero, len(destinos), camada, z_camada, estado["z"], estado["f"], proxima, z_maximo, com_torre=True)
            trocas.append({"numero": numero, "camada": camada, "z": round(z_camada, 3), "de": atual, "para": proxima})
            atual = proxima
            continue
        # Sobra da descarga de um trocador automático: desliga o avanço de pressão e nada o religa depois.
        if linha.strip() == "M900 K0":
            continue
        saida.append(linha)
        _estado_depois_da_linha(linha, estado)
    gcode.write_text("\n".join(saida) + "\n", encoding="utf-8")
    return {"cor_inicial": cor_inicial, "trocas": trocas, "gramas_por_cor": _gramas_por_cor(saida, cores)}


def inserir_pausas_por_altura(gcode: Path, alturas: list[dict[str, Any]], cor_inicial: str | None, z_maximo: float) -> dict[str, Any]:
    """Uma cor a partir de cada altura. A troca entra antes da primeira camada que começa nela, ou logo acima."""
    linhas = gcode.read_text(encoding="utf-8", errors="replace").splitlines()
    pendentes = sorted(alturas, key=lambda a: a["z"])
    # Primeiro acha onde cada troca entra. O número total vai escrito em cada pausa.
    planejadas: dict[int, dict[str, Any]] = {}
    numero_da_camada = 0
    for indice, linha in enumerate(linhas):
        if not linha.startswith(";LAYER_CHANGE"):
            continue
        numero_da_camada += 1
        z_topo, altura = _dados_da_camada(linhas, indice)
        base = z_topo - altura
        escolhida = None
        while pendentes and pendentes[0]["z"] <= base + 1e-3:
            escolhida = pendentes.pop(0)
        # Antes da primeira camada não há o que trocar: começa direto na cor pedida.
        if escolhida is not None:
            if numero_da_camada == 1:
                cor_inicial = escolhida["cor"]
            else:
                planejadas[indice] = {"z": z_topo, "camada": numero_da_camada, "cor": escolhida["cor"], "altura": escolhida["z"]}
    if pendentes:
        fora = ", ".join(f"{a['z']:g} mm" for a in pendentes)
        raise ErroDeCor(f"A peça não chega à altura da troca de cor ({fora}). Tire essa troca, ou escolha uma altura menor.")

    estado: dict[str, Any] = {"z": 0.0, "f": None, "relativo": False}
    atual = cor_inicial
    trocas = []
    saida: list[str] = []
    for indice, linha in enumerate(linhas):
        if indice in planejadas:
            troca = planejadas[indice]
            numero = len(trocas) + 1
            # A camada que acabou é a referência: o bico ainda está nela. Sem a subida em espiral da torre, a folga da volta entra aqui.
            z_camada = estado["z"]
            saida += bloco_de_troca(numero, len(planejadas), troca["camada"], z_camada, min(z_camada + FOLGA_NA_VOLTA_MM, z_maximo), estado["f"], troca["cor"], z_maximo, com_torre=False)
            trocas.append({"numero": numero, "camada": troca["camada"], "z": round(troca["z"], 3), "de": atual, "para": troca["cor"]})
            atual = troca["cor"]
        saida.append(linha)
        _estado_depois_da_linha(linha, estado)
    gcode.write_text("\n".join(saida) + "\n", encoding="utf-8")
    return {"cor_inicial": cor_inicial, "trocas": trocas, "gramas_por_cor": []}


def _dados_da_camada(linhas: list[str], indice: int) -> tuple[float, float]:
    z_topo = altura = None
    for linha in linhas[indice + 1 : indice + 6]:
        if z_topo is None and (achado := _Z_DA_CAMADA.match(linha)):
            z_topo = float(achado.group(1))
        elif altura is None and (achado := _ALTURA_DA_CAMADA.match(linha)):
            altura = float(achado.group(1))
    if z_topo is None or altura is None:
        raise ErroDeCor("Não consegui ler a altura das camadas no G-code, então a troca de cor não foi colocada.")
    return z_topo, altura


def _gramas_por_cor(linhas: list[str], cores: list[str | None]) -> list[dict[str, Any]]:
    """Sem sensor de fim de filamento, é bom saber quanto de cada cor a peça pede."""
    for linha in linhas:
        if achado := _GRAMAS.match(linha):
            valores = [v.strip() for v in achado.group(1).split(",")]
            try:
                return [{"cor": cor, "g": round(float(v), 2)} for cor, v in zip(cores, valores)]
            except ValueError:
                return []
    return []


# ---------- Para a pessoa ----------


def roteiro_da_preparacao(resumo: dict[str, Any]) -> str:
    """O roteiro de todos os arquivos de uma preparação, ou vazio se não há nenhuma troca."""
    arquivos = resumo.get("arquivos") or []
    varios = len(arquivos) > 1
    textos = [roteiro_das_trocas(a.get("cores") or {}, a["numero"] if varios else None) for a in arquivos]
    return "\n\n".join(t for t in textos if t)


def roteiro_das_trocas(resumo_de_cores: dict[str, Any], numero_do_arquivo: int | None = None) -> str:
    """O passo a passo em texto, para ter à mão na hora de imprimir."""
    if not resumo_de_cores or not resumo_de_cores.get("trocas"):
        return ""
    trocas = resumo_de_cores["trocas"]
    titulo = f"Arquivo {numero_do_arquivo}: " if numero_do_arquivo else ""
    linhas = [f"{titulo}{len(trocas)} {'troca' if len(trocas) == 1 else 'trocas'} de filamento. Comece com {nome_da_cor(resumo_de_cores.get('cor_inicial'))}."]
    linhas += [f"Troca {t['numero']}, camada {t['camada']} ({t['z']:g} mm): coloque {nome_da_cor(t['para'])}." for t in trocas]
    gramas = resumo_de_cores.get("gramas_por_cor") or []
    if gramas:
        linhas.append("Filamento: " + ", ".join(f"{nome_da_cor(g['cor'])} {g['g']:g} g" for g in gramas) + ".")
    return "\n".join(linhas)


COMO_TROCAR = (
    "Em cada pausa, a cabeça sobe e vai para o canto. Aperte a alavanca do extrusor, tire o filamento e coloque o da cor seguinte até sair plástico. "
    "Depois, aperte para retomar no visor: a impressora purga sozinha e volta para a peça. "
    "O bico continua quente durante a pausa: não encoste nele."
)
