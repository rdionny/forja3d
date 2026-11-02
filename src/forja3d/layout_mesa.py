"""Como as partes ficam na mesa: posição, giro, cópias e em qual arquivo cada item sai.

A posição de um item é o centro da caixa envolvente dele, em coordenadas da mesa. O fatiador
respeita essas coordenadas com `--arrange 0`, então o que aparece na tela é o que vai imprimir.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass
from typing import Any

ESPACO_ENTRE_ITENS_MM = 6.0
FOLGA_MINIMA_MM = 2.0
PASSO_DE_BUSCA_MM = 5.0
INCLINACAO_MINIMA_DO_RAIO = 0.02


@dataclass
class ItemMesa:
    id: str
    parte: str
    x: float
    y: float
    arquivo: int = 1
    giro: int = 0


def novo_id() -> str:
    return uuid.uuid4().hex[:8]


def ponto_no_plano(inicio: tuple[float, ...], fim: tuple[float, ...], z: float) -> tuple[float, float] | None:
    """Onde o raio do mouse cruza a altura `z`. Quase paralelo ao plano, não há posição confiável."""
    delta = (fim[0] - inicio[0], fim[1] - inicio[1], fim[2] - inicio[2])
    comprimento = math.sqrt(sum(d * d for d in delta))
    if comprimento == 0 or abs(delta[2]) / comprimento < INCLINACAO_MINIMA_DO_RAIO:
        return None
    t = (z - inicio[2]) / delta[2]
    if t < 0:
        return None
    return (inicio[0] + t * delta[0], inicio[1] + t * delta[1])


class LayoutMesa:
    def __init__(self, tamanhos: dict[str, tuple[float, float]], perfil: dict[str, Any], itens: list[ItemMesa] | None = None) -> None:
        volume = perfil["volume_mm"]
        self.tamanhos = dict(tamanhos)
        self.largura = float(volume["fisico"]["x"])
        self.profundidade = float(volume["fisico"]["y"])
        self.margem = float(volume["margem_lateral"])
        self.itens: list[ItemMesa] = list(itens or [])

    @classmethod
    def novo(cls, tamanhos: dict[str, tuple[float, float]], perfil: dict[str, Any]) -> LayoutMesa:
        """Um item de cada parte, organizados."""
        layout = cls(tamanhos, perfil, [ItemMesa(novo_id(), parte, 0.0, 0.0) for parte in tamanhos])
        layout.organizar()
        return layout

    @classmethod
    def de_dados(cls, dados: dict[str, Any], tamanhos: dict[str, tuple[float, float]], perfil: dict[str, Any]) -> LayoutMesa:
        """Lê o que foi salvo e adapta ao modelo atual.

        Parte que o usuário deletou continua fora. Só entra sozinha uma parte que o layout salvo
        ainda não conhecia, como uma que o agente acabou de criar.
        """
        layout = cls(tamanhos, perfil)
        layout.restaurar(dados)
        for parte in tamanhos:
            if parte not in dados.get("partes", []):
                layout.adicionar(parte)
        return layout

    def para_dados(self) -> dict[str, Any]:
        return {"partes": list(self.tamanhos), "itens": [asdict(i) for i in self.itens]}

    def restaurar(self, dados: dict[str, Any]) -> None:
        self.itens = [
            ItemMesa(str(i["id"]), str(i["parte"]), float(i["x"]), float(i["y"]), int(i.get("arquivo", 1)), int(i.get("giro", 0)) % 360)
            for i in dados.get("itens", [])
            if i.get("parte") in self.tamanhos
        ]
        for item in self.itens:
            item.x, item.y = self.limitar(item, item.x, item.y)
        self._compactar()

    def assinatura(self) -> str:
        """Muda quando o que vai para a impressora muda. O G-code preparado guarda esse valor."""
        conteudo = sorted((i.arquivo, i.parte, i.x, i.y, i.giro) for i in self.itens)
        return hashlib.sha1(json.dumps(conteudo).encode()).hexdigest()

    # ---------- Consultas ----------

    def item(self, identificador: str | None) -> ItemMesa | None:
        return next((i for i in self.itens if i.id == identificador), None)

    def tamanho_do_item(self, item: ItemMesa) -> tuple[float, float]:
        dx, dy = self.tamanhos[item.parte]
        return (dy, dx) if item.giro % 180 else (dx, dy)

    def arquivos(self) -> list[int]:
        return sorted({i.arquivo for i in self.itens})

    def itens_do_arquivo(self, numero: int) -> list[ItemMesa]:
        return [i for i in self.itens if i.arquivo == numero]

    def area_do_arquivo(self, numero: int) -> tuple[float, float, float, float]:
        limites = [(i.x, i.y, *self.tamanho_do_item(i)) for i in self.itens_do_arquivo(numero)]
        return (
            min(x - dx / 2 for x, _, dx, _ in limites),
            max(x + dx / 2 for x, _, dx, _ in limites),
            min(y - dy / 2 for _, y, _, dy in limites),
            max(y + dy / 2 for _, y, _, dy in limites),
        )

    def limitar(self, item: ItemMesa, x: float, y: float) -> tuple[float, float]:
        """Mantém o item na área segura. O que for maior que ela fica centralizado."""
        dx, dy = self.tamanho_do_item(item)
        return (self._limitar_eixo(x, dx, self.largura), self._limitar_eixo(y, dy, self.profundidade))

    def _limitar_eixo(self, valor: float, tamanho: float, comprimento: float) -> float:
        minimo = self.margem + tamanho / 2
        maximo = comprimento - self.margem - tamanho / 2
        if minimo > maximo:
            return round(comprimento / 2, 1)
        return round(float(min(max(valor, minimo), maximo)), 1)

    def _colidem(self, a: ItemMesa, b: ItemMesa, folga: float) -> bool:
        adx, ady = self.tamanho_do_item(a)
        bdx, bdy = self.tamanho_do_item(b)
        return abs(a.x - b.x) < (adx + bdx) / 2 + folga and abs(a.y - b.y) < (ady + bdy) / 2 + folga

    def conflitos(self, brim_mm: float = 0.0) -> set[str]:
        """Itens do mesmo arquivo perto demais, pela caixa envolvente. O brim precisa de espaço dos dois lados."""
        folga = FOLGA_MINIMA_MM + 2 * brim_mm
        encontrados: set[str] = set()
        for numero in self.arquivos():
            itens = self.itens_do_arquivo(numero)
            for indice, a in enumerate(itens):
                for b in itens[indice + 1 :]:
                    if self._colidem(a, b, folga):
                        encontrados.update((a.id, b.id))
        return encontrados

    # ---------- Alterações ----------

    def mover(self, identificador: str, x: float, y: float) -> tuple[float, float]:
        item = self.item(identificador)
        item.x, item.y = self.limitar(item, x, y)
        return item.x, item.y

    def girar(self, identificador: str) -> None:
        item = self.item(identificador)
        item.giro = (item.giro + 90) % 360
        item.x, item.y = self.limitar(item, item.x, item.y)

    def adicionar(self, parte: str, arquivo: int | None = None, perto: tuple[float, float] | None = None, giro: int = 0) -> ItemMesa:
        """Coloca uma cópia em um lugar livre. Sem espaço no arquivo, ela vai para outro."""
        existentes = self.arquivos()
        pedido = arquivo if arquivo is not None else (existentes[0] if existentes else 1)
        modelo = ItemMesa(novo_id(), parte, 0.0, 0.0, pedido, giro)
        for numero in [pedido, *(n for n in existentes if n != pedido)]:
            modelo.arquivo = numero
            posicao = self._posicao_livre(modelo, perto)
            if posicao is not None:
                modelo.x, modelo.y = posicao
                self.itens.append(modelo)
                return modelo
        modelo.arquivo = max(existentes, default=0) + 1
        modelo.x, modelo.y = self.limitar(modelo, self.largura / 2, self.profundidade / 2)
        self.itens.append(modelo)
        return modelo

    def duplicar(self, identificador: str) -> ItemMesa:
        original = self.item(identificador)
        dx, _ = self.tamanho_do_item(original)
        return self.adicionar(original.parte, original.arquivo, (original.x + dx + ESPACO_ENTRE_ITENS_MM, original.y), original.giro)

    def remover(self, identificador: str) -> None:
        self.itens = [i for i in self.itens if i.id != identificador]
        self._compactar()

    def mover_para_arquivo(self, identificador: str, numero: int | None = None) -> int:
        """Sem número, cria um arquivo novo. Devolve o arquivo em que o item ficou."""
        item = self.item(identificador)
        destino = max(self.arquivos(), default=0) + 1 if numero is None else numero
        if destino != item.arquivo:
            item.arquivo = destino
            if any(self._colidem(item, o, FOLGA_MINIMA_MM) for o in self.itens_do_arquivo(destino) if o is not item):
                livre = self._posicao_livre(item, (item.x, item.y))
                if livre is not None:
                    item.x, item.y = livre
        self._compactar()
        return item.arquivo

    def organizar(self) -> None:
        """Arruma cada arquivo em fileiras, girando o que precisar. O que não couber vai para um arquivo novo."""
        for item in self.itens:
            item.giro = 0
        proximo = max(self.arquivos(), default=0) + 1
        for numero in self.arquivos():
            restantes = sorted(self.itens_do_arquivo(numero), key=lambda i: (-self.tamanhos[i.parte][1], -self.tamanhos[i.parte][0]))
            atual = numero
            while restantes:
                restantes = self._empacotar(restantes, atual)
                if restantes:
                    atual = proximo
                    proximo += 1
        self._compactar()

    def _empacotar(self, itens: list[ItemMesa], numero: int) -> list[ItemMesa]:
        """Coloca o que couber em um arquivo e devolve o que sobrou."""
        largura_util = self.largura - 2 * self.margem
        profundidade_util = self.profundidade - 2 * self.margem
        colocados: list[tuple[ItemMesa, float, float]] = []
        sobra: list[ItemMesa] = []
        x = y = altura_fileira = 0.0
        for item in itens:
            dx, dy = self.tamanho_do_item(item)
            if x > 0 and x + dx > largura_util:
                x = 0.0
                y += altura_fileira + ESPACO_ENTRE_ITENS_MM
                altura_fileira = 0.0
            if colocados and (y + dy > profundidade_util or x + dx > largura_util):
                if y + dx > profundidade_util or x + dy > largura_util:
                    sobra.append(item)
                    continue
                item.giro = (item.giro + 90) % 360
                dx, dy = dy, dx
            colocados.append((item, x + dx / 2, y + dy / 2))
            x += dx + ESPACO_ENTRE_ITENS_MM
            altura_fileira = max(altura_fileira, dy)

        extremos = [(cx, cy, *self.tamanho_do_item(i)) for i, cx, cy in colocados]
        x0 = min(cx - dx / 2 for cx, _, dx, _ in extremos)
        x1 = max(cx + dx / 2 for cx, _, dx, _ in extremos)
        y0 = min(cy - dy / 2 for _, cy, _, dy in extremos)
        y1 = max(cy + dy / 2 for _, cy, _, dy in extremos)
        ajuste_x = self.largura / 2 - (x0 + x1) / 2
        ajuste_y = self.profundidade / 2 - (y0 + y1) / 2
        for item, cx, cy in colocados:
            item.arquivo = numero
            item.x, item.y = self.limitar(item, cx + ajuste_x, cy + ajuste_y)
        return sobra

    def _posicao_livre(self, modelo: ItemMesa, perto: tuple[float, float] | None) -> tuple[float, float] | None:
        dx, dy = self.tamanho_do_item(modelo)
        alvo = perto or (self.largura / 2, self.profundidade / 2)
        ocupados = [i for i in self.itens if i.arquivo == modelo.arquivo and i is not modelo]
        teste = ItemMesa("", modelo.parte, 0.0, 0.0, modelo.arquivo, modelo.giro)
        posicoes = {self.limitar(teste, *alvo)}
        passos_x = int((self.largura - dx) / PASSO_DE_BUSCA_MM) + 1
        passos_y = int((self.profundidade - dy) / PASSO_DE_BUSCA_MM) + 1
        for ix in range(passos_x + 1):
            for iy in range(passos_y + 1):
                posicoes.add(self.limitar(teste, dx / 2 + ix * PASSO_DE_BUSCA_MM, dy / 2 + iy * PASSO_DE_BUSCA_MM))
        for x, y in sorted(posicoes, key=lambda p: (p[0] - alvo[0]) ** 2 + (p[1] - alvo[1]) ** 2):
            teste.x, teste.y = x, y
            if not any(self._colidem(teste, o, ESPACO_ENTRE_ITENS_MM) for o in ocupados):
                return x, y
        return None

    def _compactar(self) -> None:
        """Os arquivos ficam sempre numerados de 1 em diante, sem buracos."""
        novos = {numero: indice for indice, numero in enumerate(self.arquivos(), start=1)}
        for item in self.itens:
            item.arquivo = novos[item.arquivo]
