"""Especificação estruturada da peça, registrada pelo agente antes de modelar."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Dimensoes(BaseModel):
    x_mm: float = Field(gt=0)
    y_mm: float = Field(gt=0)
    z_mm: float = Field(gt=0)
    tolerancia_mm: float = Field(default=0.2, gt=0)


class Furo(BaseModel):
    quantidade: int = Field(ge=1)
    diametro_nominal_mm: float = Field(gt=0)
    diametro_modelado_mm: float = Field(gt=0)
    eixo: Literal["X", "Y", "Z"]
    passante: bool
    uso: str = ""


class Especificacao(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nome: str
    finalidade: str
    material: Literal["PLA", "PETG", "TPU"]
    carga: Literal["nenhuma", "leve", "media", "alta"]
    ambiente: str = ""
    dimensoes: Dimensoes
    pecas: int = Field(ge=1)
    furos: list[Furo] = Field(default_factory=list)
    caracteristicas: list[str] = Field(default_factory=list)
    espessura_minima_mm: float | None = None
    orientacao_impressao: str
    premissas: list[str] = Field(default_factory=list)


def problemas_de_tamanho(especificacao: Especificacao, perfil: dict[str, Any]) -> list[str]:
    """Peças que não podem ser impressas são recusadas antes de gastar uma chamada de modelagem."""
    volume = perfil["volume_mm"]
    maximo_xy = volume["maximo_fatiador_xy"]
    seguro_z = volume["seguro"]["z"]
    d = especificacao.dimensoes
    problemas = []
    if d.x_mm > maximo_xy or d.y_mm > maximo_xy:
        problemas.append(
            f"A peça tem {d.x_mm:g} × {d.y_mm:g} mm, e o fatiador recusa acima de {maximo_xy} mm em X ou Y. "
            "Pergunte ao usuário se prefere dividir ou reduzir."
        )
    if d.z_mm > seguro_z:
        problemas.append(
            f"A peça tem {d.z_mm:g} mm de altura, e o limite é {seguro_z} mm. "
            "Pergunte ao usuário se prefere deitar, dividir ou reduzir."
        )
    return problemas
