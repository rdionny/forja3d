"""Estimativa de quanto a impressão demora e de quando ela fica pronta."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

_PARTE_DO_TEMPO = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])")
_SEGUNDOS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
AVISO_DA_ESTIMATIVA = "Estimativa do OrcaSlicer. Não conta o aquecimento, o nivelamento nem a troca de arquivo."


def segundos_do_tempo(texto: str | None) -> int | None:
    """Lê o formato do OrcaSlicer: 1d 2h 3m 4s."""
    partes = _PARTE_DO_TEMPO.findall(texto or "")
    if not partes:
        return None
    return round(sum(float(valor) * _SEGUNDOS[unidade] for valor, unidade in partes))


def formatar_duracao(segundos: int) -> str:
    minutos = max(round(segundos / 60), 1)
    dias, minutos = divmod(minutos, 1440)
    horas, minutos = divmod(minutos, 60)
    partes = []
    if dias:
        partes.append(f"{dias}d")
    if horas:
        partes.append(f"{horas}h")
    if minutos or not partes:
        partes.append(f"{minutos}min")
    return " ".join(partes)


def previsao_de_termino(segundos: int, agora: datetime) -> str:
    fim = agora + timedelta(seconds=segundos)
    dias = (fim.date() - agora.date()).days
    if dias == 0:
        return f"hoje às {fim:%H:%M}"
    if dias == 1:
        return f"amanhã às {fim:%H:%M}"
    return f"dia {fim:%d/%m} às {fim:%H:%M}"


def texto_da_previsao(resumo: dict[str, Any], agora: datetime | None = None) -> str:
    """Frase com a duração e o horário. Vazia quando o fatiador não informou o tempo."""
    validacao = resumo["validacao"]
    total = validacao.get("tempo_s") or segundos_do_tempo(validacao.get("tempo"))
    if not total:
        return ""
    termino = previsao_de_termino(total, agora or datetime.now())
    arquivos = resumo.get("arquivos") or []
    if len(arquivos) < 2:
        return f"Impressão: {formatar_duracao(total)}. Se começar agora, fica pronta {termino}."
    tempos = [segundos_do_tempo(a["validacao"].get("tempo")) for a in arquivos]
    detalhe = ", ".join(f"arquivo {a['numero']}: {formatar_duracao(t)}" for a, t in zip(arquivos, tempos, strict=True) if t)
    return f"Impressão: {formatar_duracao(total)} em {len(arquivos)} arquivos ({detalhe}). Um depois do outro, sem pausa, fica pronta {termino}."
