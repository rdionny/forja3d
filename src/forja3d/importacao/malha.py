"""O que sai da leitura de um arquivo, antes de virar uma parte da mesa."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class ModeloInvalido(Exception):
    """O arquivo não pode virar parte da mesa. A mensagem é para o usuário."""


@dataclass
class MalhaLida:
    vertices: np.ndarray
    faces: np.ndarray
    # Cada formato tem a sua unidade e o seu "para cima". A mesa usa milímetros e Z.
    mm_por_unidade: float = 1.0
    para_cima: tuple[float, float, float] = (0.0, 0.0, 1.0)
