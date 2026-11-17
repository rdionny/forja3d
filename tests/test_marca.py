"""A logo e o ícone: os desenhos existem e aparecem em cada tamanho."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtSvg import QSvgRenderer

from forja3d.interface import tema
from forja3d.interface.aplicativo import (
    ALTURA_DA_ABERTURA,
    LARGURA_DA_ABERTURA,
    TAMANHOS_DO_ICONE,
    _criar_abertura,
    _desenho_do_tamanho,
    criar_icone,
    criar_icone_do_app,
)

DESENHOS = ("forja3d.svg", "forja3d_medio.svg", "forja3d_pequeno.svg", "logo.svg")


@pytest.mark.parametrize("nome", DESENHOS)
def test_desenhos_validos(qapp, nome):
    assert QSvgRenderer(str(tema.PASTA_ICONES / nome)).isValid()


def test_cada_faixa_de_tamanho_tem_o_proprio_desenho():
    assert _desenho_do_tamanho(16) == _desenho_do_tamanho(32) == "forja3d_pequeno.svg"
    assert _desenho_do_tamanho(40) == _desenho_do_tamanho(48) == "forja3d_medio.svg"
    assert _desenho_do_tamanho(64) == _desenho_do_tamanho(256) == "forja3d.svg"


@pytest.mark.parametrize("lado", TAMANHOS_DO_ICONE)
def test_icone_preenche_o_quadrado_e_tem_cantos_redondos(qapp, lado):
    imagem = criar_icone(lado).toImage()
    assert (imagem.width(), imagem.height()) == (lado, lado)
    assert imagem.pixelColor(0, 0).alpha() == 0
    assert imagem.pixelColor(lado // 2, lado // 2).alpha() == 255


@pytest.mark.parametrize("lado", [16, 48, 256])
def test_a_bigorna_aparece_em_laranja(qapp, lado):
    """Um SVG quebrado desenharia só o fundo escuro."""
    imagem = criar_icone(lado).toImage()
    quentes = sum(1 for x in range(lado) for y in range(lado) if (cor := imagem.pixelColor(x, y)).alpha() == 255 and cor.red() > 200 and cor.blue() < 130)
    assert quentes > lado * lado * 0.08


def test_icone_do_app_tem_todos_os_tamanhos(qapp):
    tamanhos = criar_icone_do_app().availableSizes()
    assert {QSize(lado, lado) for lado in TAMANHOS_DO_ICONE} <= set(tamanhos)


def test_abertura_mostra_a_logo_e_a_faixa(qapp):
    tela = _criar_abertura()
    imagem = tela.pixmap().toImage()
    proporcao = tela.pixmap().devicePixelRatio()
    assert imagem.width() == round(LARGURA_DA_ABERTURA * proporcao)
    assert imagem.height() == round(ALTURA_DA_ABERTURA * proporcao)
    # A camada mais quente da bigorna, logo abaixo do bico. O desenho tem 256 unidades e ocupa 210 px na tela.
    x, y = round((26 + 210 * 150 / 256) * proporcao), round((40 + 210 * 98 / 256) * proporcao)
    assert imagem.pixelColor(x, y) == QColor("#ffd86b")
    # A faixa do quente para o frio.
    embaixo = imagem.height() - 2
    assert imagem.pixelColor(5, embaixo).red() > imagem.pixelColor(imagem.width() - 5, embaixo).red()
