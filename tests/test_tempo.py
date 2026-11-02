"""Estimativa de quanto a impressão demora e de quando ela fica pronta."""
from datetime import datetime

import pytest

from forja3d.fatiamento.tempo import formatar_duracao, previsao_de_termino, segundos_do_tempo, texto_da_previsao

AGORA = datetime(2026, 11, 2, 10, 0)


@pytest.mark.parametrize(
    ("texto", "segundos"),
    [("16m 9s", 969), ("11h 19m 0s", 40740), ("1d 2h 3m 4s", 93784), ("45s", 45), ("2h", 7200), ("", None), (None, None), ("sem tempo", None)],
)
def test_le_o_tempo_do_orca(texto, segundos):
    assert segundos_do_tempo(texto) == segundos


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [(30, "1min"), (969, "16min"), (3600, "1h"), (40740, "11h 19min"), (93784, "1d 2h 3min"), (86400, "1d")],
)
def test_formata_a_duracao(segundos, texto):
    assert formatar_duracao(segundos) == texto


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [(600, "hoje às 10:10"), (50400, "às 00:00"), (40740, "hoje às 21:19"), (172800, "dia 04/11 às 10:00")],
)
def test_diz_quando_fica_pronta(segundos, texto):
    assert texto in previsao_de_termino(segundos, AGORA)


def test_amanha():
    assert previsao_de_termino(86400, AGORA) == "amanhã às 10:00"


def test_previsao_de_um_arquivo():
    resumo = {"validacao": {"tempo": "11h 19m 0s", "tempo_s": 40740}}
    assert texto_da_previsao(resumo, AGORA) == "Impressão: 11h 19min. Se começar agora, fica pronta hoje às 21:19."


def test_previsao_funciona_com_resumo_antigo_sem_tempo_em_segundos():
    assert "16min" in texto_da_previsao({"validacao": {"tempo": "16m 9s"}}, AGORA)


def test_previsao_de_varios_arquivos_detalha_cada_um():
    resumo = {
        "validacao": {"tempo_s": 10800},
        "arquivos": [{"numero": 1, "validacao": {"tempo": "2h"}}, {"numero": 2, "validacao": {"tempo": "1h"}}],
    }
    texto = texto_da_previsao(resumo, AGORA)
    assert "3h em 2 arquivos" in texto
    assert "arquivo 1: 2h, arquivo 2: 1h" in texto
    assert "hoje às 13:00" in texto


def test_sem_tempo_nao_inventa_previsao():
    assert texto_da_previsao({"validacao": {}}, AGORA) == ""
