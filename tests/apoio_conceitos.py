"""Cliente de IA falso para os testes da imagem de referência: analisa, gera e confere sem rede."""
from __future__ import annotations

import io
import json
import threading

from PIL import Image

from forja3d.ia.cliente import ImagemGerada, RespostaIA

ANALISE = {
    "objeto": "caixa com tampa",
    "recusa": None,
    "medidas_criticas": ["tamanho do carregador"],
    "opcoes": [
        {
            "titulo": "Tampa sobreposta",
            "descricao": "Caixa simples com tampa por atrito.",
            "componentes": [
                {"nome": "Base", "quantidade": 1, "impresso": True, "detalhes": "caixa oca"},
                {"nome": "Tampa", "quantidade": 1, "impresso": True, "detalhes": "com degrau"},
            ],
        },
        {
            "titulo": "Tampa parafusada",
            "descricao": "Caixa robusta com 4 parafusos.",
            "componentes": [
                {"nome": "Base", "quantidade": 1, "impresso": True, "detalhes": "com colunas"},
                {"nome": "Tampa", "quantidade": 1, "impresso": True, "detalhes": "com furos"},
                {"nome": "Parafuso M3", "quantidade": 4, "impresso": False, "detalhes": "cabeça sextavada"},
            ],
        },
    ],
}
APROVADA = {
    "dois_paineis": True,
    "esquerda_montada": True,
    "direita_explodida": True,
    "mesmos_componentes": True,
    "tem_texto": False,
    "observacao": "",
}
REPROVADA = {**APROVADA, "mesmos_componentes": False, "observacao": "Há 4 parafusos à esquerda e 6 à direita."}


def imagem_png(cor: str = "red", tamanho: tuple[int, int] = (64, 36)) -> bytes:
    saida = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(saida, format="PNG")
    return saida.getvalue()


def texto_ia(conteudo: str) -> RespostaIA:
    return RespostaIA(conteudo, [], {"role": "assistant", "content": conteudo})


def cercado(dados: dict) -> str:
    return "```json\n" + json.dumps(dados, ensure_ascii=False) + "\n```"


class ClienteFalso:
    """`imagens` e `conferencias` são filas: cada item pode ser um valor ou uma exceção a levantar."""

    def __init__(self, analises=None, imagens=None, conferencias=None, ao_gerar=None):
        self.analises = list(analises) if analises is not None else [ANALISE]
        self.imagens = list(imagens) if imagens is not None else None
        self.conferencias = list(conferencias) if conferencias is not None else None
        self.ao_gerar = ao_gerar
        self.pedidos_de_analise = []
        self.prompts_de_imagem = []
        self.temperaturas = {}
        self._trava = threading.Lock()

    def conversar(self, mensagens, ferramentas, etapa, cancelar=None, temperatura=None):
        assert ferramentas is None
        self.temperaturas[etapa] = temperatura
        if etapa == "conceito_analise":
            self.pedidos_de_analise.append(mensagens)
            item = self.analises.pop(0) if len(self.analises) > 1 else self.analises[0]
            if isinstance(item, Exception):
                raise item
            return texto_ia(item if isinstance(item, str) else cercado(item))
        with self._trava:
            item = self.conferencias.pop(0) if self.conferencias else APROVADA
        if isinstance(item, Exception):
            raise item
        return texto_ia(item if isinstance(item, str) else cercado(item))

    def gerar_imagem(self, prompt, referencia, cancelar=None):
        with self._trava:
            self.prompts_de_imagem.append(prompt)
            item = self.imagens.pop(0) if self.imagens else imagem_png()
        if self.ao_gerar is not None:
            self.ao_gerar(prompt, cancelar)
        if isinstance(item, Exception):
            raise item
        return ImagemGerada(item, "png")
