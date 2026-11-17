"""O percurso da impressão, lido do G-code preparado: é o que a simulação mostra na mesa.

Lê o mesmo arquivo que vai para o cartão SD, então o que aparece é o que a impressora vai fazer, e não uma ideia da peça.
"""
from __future__ import annotations

import math
import re
from array import array
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from forja3d.fatiamento.validador_gcode import MARCA_INICIO

COR_PADRAO = "#E8873A"
VELOCIDADE_INICIAL_MM_MIN = 1500.0
CLARIDADE_MINIMA = 150
# Tipos de extrusão em tons diferentes: com uma cor só por igual, as paredes de um furo somem na superfície em volta.
CLARIDADE_POR_TIPO = {
    "Outer wall": 1.0,
    "Overhang wall": 1.0,
    "Bridge": 0.9,
    "Inner wall": 0.8,
    "Top surface": 0.7,
    "Bottom surface": 0.66,
    "Internal solid infill": 0.62,
    "Sparse infill": 0.55,
    "Prime tower": 0.5,
    "Support": 0.45,
    "Support interface": 0.45,
}
CLARIDADE_PADRAO = 0.75
_PALAVRA = re.compile(r"([XYZEFSP])\s*(-?\d*\.?\d+)")
_TROCA = re.compile(r"(\d+)/(\d+) camada=(\d+) z=([\d.]+) para=(\S+)")
_Z_DA_CAMADA = re.compile(r"^;\s*Z:\s*(-?[\d.]+)")


@dataclass(frozen=True)
class Pausa:
    tempo: float
    numero: int
    total: int
    camada: int
    z: float
    cor: str | None


@dataclass
class Percurso:
    pontos: np.ndarray  # Início e fim de cada trecho extrudado: duas linhas por trecho.
    cores: np.ndarray  # RGB de cada trecho.
    tempo_dos_trechos: np.ndarray  # Quando cada trecho termina, em segundos.
    posicoes: np.ndarray  # Onde o bico está depois de cada movimento, inclusive as viagens.
    tempo_das_posicoes: np.ndarray
    inicio_das_camadas: np.ndarray
    z_das_camadas: np.ndarray
    tempo_total: float
    pausas: list[Pausa] = field(default_factory=list)

    @property
    def quantidade_de_camadas(self) -> int:
        return len(self.inicio_das_camadas)

    def trechos_ate(self, tempo: float) -> int:
        return int(np.searchsorted(self.tempo_dos_trechos, tempo, side="right"))

    def bico_em(self, tempo: float) -> tuple[float, float, float]:
        indice = max(int(np.searchsorted(self.tempo_das_posicoes, tempo, side="right")) - 1, 0)
        x, y, z = self.posicoes[indice]
        return float(x), float(y), float(z)

    def camada_em(self, tempo: float) -> tuple[int, float]:
        """O número da camada e a altura dela. Zero é a preparação, antes da primeira camada."""
        numero = int(np.searchsorted(self.inicio_das_camadas, tempo, side="right"))
        return numero, float(self.z_das_camadas[numero - 1]) if numero else 0.0

    def pausa_entre(self, inicio: float, fim: float) -> Pausa | None:
        return next((p for p in self.pausas if inicio < p.tempo <= fim), None)

    def proxima_pausa(self, tempo: float) -> Pausa | None:
        return next((p for p in self.pausas if p.tempo > tempo), None)


def _rgb(cor: str | None, claridade: float) -> tuple[int, int, int]:
    """Um filamento preto sumiria no fundo escuro da mesa: a base clareia antes, e o tom de cada tipo continua valendo."""
    texto = cor or COR_PADRAO
    r, g, b = (int(texto[i : i + 2], 16) for i in (1, 3, 5))
    falta = max(CLARIDADE_MINIMA - max(r, g, b), 0)
    return int((r + falta) * claridade), int((g + falta) * claridade), int((b + falta) * claridade)


def ler_percurso(gcode: Path, cor_inicial: str | None = None, tempo_estimado_s: float | None = None) -> Percurso:
    """Os tempos saem da distância e da velocidade de cada movimento, e são ajustados à estimativa do fatiador, que conta as acelerações."""
    pontos, cores = array("f"), array("B")
    tempos_trechos, posicoes, tempos_posicoes = array("d"), array("f"), array("d")
    inicios, alturas = array("d"), array("f")
    pausas: list[tuple[float, int, int, int, float, str | None]] = []
    x = y = z = e = 0.0
    velocidade = VELOCIDADE_INICIAL_MM_MIN
    relativo = e_relativo = False
    tempo = 0.0
    cor = cor_inicial
    claridade = CLARIDADE_PADRAO
    rgb = _rgb(cor, claridade)
    pendente: tuple[int, int, int, float, str | None] | None = None
    aguardando_z = False

    with open(gcode, encoding="utf-8", errors="replace") as arquivo:
        for bruta in arquivo:
            linha = bruta.strip()
            if not linha:
                continue
            if linha[0] == ";":
                if linha.startswith(";LAYER_CHANGE"):
                    aguardando_z = True
                elif aguardando_z and (achado := _Z_DA_CAMADA.match(linha)):
                    inicios.append(tempo)
                    alturas.append(float(achado.group(1)))
                    aguardando_z = False
                elif linha.startswith(";TYPE:"):
                    claridade = CLARIDADE_POR_TIPO.get(linha[6:].strip(), CLARIDADE_PADRAO)
                    rgb = _rgb(cor, claridade)
                elif linha.startswith(MARCA_INICIO) and (achado := _TROCA.search(linha)):
                    numero, total, camada, altura, para = achado.groups()
                    pendente = (int(numero), int(total), int(camada), float(altura), para if para.startswith("#") else None)
                continue

            codigo = linha.split(";", 1)[0].strip()
            comando = codigo.split(None, 1)[0].upper() if codigo else ""
            if comando in ("G0", "G1", "G2", "G3"):
                novo_x, novo_y, novo_z = x, y, z
                delta_e = 0.0
                for letra, texto in _PALAVRA.findall(codigo):
                    valor = float(texto)
                    if letra == "X":
                        novo_x = x + valor if relativo else valor
                    elif letra == "Y":
                        novo_y = y + valor if relativo else valor
                    elif letra == "Z":
                        novo_z = z + valor if relativo else valor
                    elif letra == "E":
                        delta_e = valor if e_relativo else valor - e
                        e = e + valor if e_relativo else valor
                    elif letra == "F" and valor > 0:
                        velocidade = valor
                distancia = math.sqrt((novo_x - x) ** 2 + (novo_y - y) ** 2 + (novo_z - z) ** 2)
                tempo += (distancia or abs(delta_e)) * 60.0 / velocidade
                if distancia:
                    if delta_e > 0:
                        pontos.extend((x, y, z, novo_x, novo_y, novo_z))
                        cores.extend(rgb)
                        tempos_trechos.append(tempo)
                    posicoes.extend((novo_x, novo_y, novo_z))
                    tempos_posicoes.append(tempo)
                x, y, z = novo_x, novo_y, novo_z
            elif comando == "G4":
                for letra, texto in _PALAVRA.findall(codigo):
                    tempo += float(texto) if letra == "S" else float(texto) / 1000.0 if letra == "P" else 0.0
            elif comando == "G90":
                # No Marlin, G90 também devolve o eixo E ao modo absoluto.
                relativo = e_relativo = False
            elif comando == "G91":
                relativo = e_relativo = True
            elif comando == "M82":
                e_relativo = False
            elif comando == "M83":
                e_relativo = True
            elif comando == "G92":
                for letra, texto in _PALAVRA.findall(codigo):
                    if letra == "E":
                        e = float(texto)
            elif comando == "M25" and pendente is not None:
                numero, total, camada, altura, cor = pendente
                pausas.append((tempo, numero, total, camada, altura, cor))
                pendente = None
                rgb = _rgb(cor, claridade)

    if not tempos_trechos:
        raise ValueError(f"{gcode.name} não tem nenhuma extrusão para simular.")
    # O fatiador conta acelerações e curvas, então a estimativa dele é a que aparece para a pessoa.
    escala = tempo_estimado_s / tempo if tempo_estimado_s and tempo > 0 else 1.0
    return Percurso(
        pontos=np.frombuffer(pontos, dtype=np.float32).reshape(-1, 3),
        cores=np.frombuffer(cores, dtype=np.uint8).reshape(-1, 3),
        tempo_dos_trechos=np.frombuffer(tempos_trechos, dtype=np.float64) * escala,
        posicoes=np.frombuffer(posicoes, dtype=np.float32).reshape(-1, 3),
        tempo_das_posicoes=np.frombuffer(tempos_posicoes, dtype=np.float64) * escala,
        inicio_das_camadas=np.frombuffer(inicios, dtype=np.float64) * escala,
        z_das_camadas=np.frombuffer(alturas, dtype=np.float32),
        tempo_total=tempo * escala,
        pausas=[Pausa(t * escala, n, total, c, a, cor_da_troca) for t, n, total, c, a, cor_da_troca in pausas],
    )
