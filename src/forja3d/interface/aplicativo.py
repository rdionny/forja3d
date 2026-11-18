"""Inicia o aplicativo desktop."""
from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QPointF, QRectF, Qt, QTranslator
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from forja3d import configuracao
from forja3d.configuracao import Configuracao
from forja3d.interface import tema
from forja3d.interface.instancia import InstanciaUnica
from forja3d.registro import configurar_logs, proteger_chaves

log = logging.getLogger(__name__)

TAMANHOS_DO_ICONE = (16, 20, 24, 32, 40, 48, 64, 128, 256)
LARGURA_DA_ABERTURA, ALTURA_DA_ABERTURA = 560, 300


def _desenho_do_tamanho(lado: int) -> str:
    """Em tamanho pequeno, as camadas e o bico viram borrão: cada faixa de tamanho tem o próprio desenho."""
    if lado <= 32:
        return "forja3d_pequeno.svg"
    return "forja3d_medio.svg" if lado <= 48 else "forja3d.svg"


def criar_icone(lado: int = 256) -> QPixmap:
    imagem = QPixmap(lado, lado)
    imagem.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(imagem)
    QSvgRenderer(str(tema.PASTA_ICONES / _desenho_do_tamanho(lado))).render(pintor, QRectF(0, 0, lado, lado))
    pintor.end()
    return imagem


def criar_icone_do_app() -> QIcon:
    icone = QIcon()
    for lado in TAMANHOS_DO_ICONE:
        icone.addPixmap(criar_icone(lado))
    return icone


def _criar_abertura() -> QSplashScreen:
    largura, altura = LARGURA_DA_ABERTURA, ALTURA_DA_ABERTURA
    proporcao = QApplication.primaryScreen().devicePixelRatio()
    fundo = QPixmap(round(largura * proporcao), round(altura * proporcao))
    fundo.setDevicePixelRatio(proporcao)
    fundo.fill(QColor(tema.FUNDO))
    pintor = QPainter(fundo)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)

    # O calor da forja, atrás da bigorna.
    brilho = QRadialGradient(QPointF(130, 165), 240)
    cor = QColor(tema.ACENTO)
    cor.setAlpha(70)
    brilho.setColorAt(0, cor)
    cor.setAlpha(0)
    brilho.setColorAt(1, cor)
    pintor.fillRect(QRectF(0, 0, largura, altura), brilho)
    QSvgRenderer(str(tema.PASTA_ICONES / "logo.svg")).render(pintor, QRectF(26, 40, 210, 210))

    texto_x, texto_y = 256, 150
    fonte = QFont("Segoe UI", 42, QFont.Weight.Bold)
    pintor.setFont(fonte)
    pintor.setPen(QColor(tema.TEXTO))
    pintor.drawText(texto_x, texto_y, "Forja")
    pintor.setPen(QColor(tema.ACENTO))
    pintor.drawText(texto_x + QFontMetrics(fonte).horizontalAdvance("Forja"), texto_y, "3D")
    pintor.setPen(QColor(tema.SUAVE))
    pintor.setFont(QFont("Segoe UI", 12))
    pintor.drawText(texto_x + 3, texto_y + 34, "Da ideia à peça impressa")
    pintor.setFont(QFont("Segoe UI", 10))
    pintor.drawText(QRectF(0, 0, largura - 20, altura - 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "Abrindo…")

    # As cores das camadas da bigorna, do quente para o frio.
    faixa = QLinearGradient(0, 0, largura, 0)
    faixa.setColorAt(0, QColor("#ffd86b"))
    faixa.setColorAt(0.5, QColor(tema.ACENTO))
    faixa.setColorAt(1, QColor("#5b626f"))
    pintor.fillRect(QRectF(0, altura - 4, largura, 4), faixa)
    pintor.setPen(QColor(tema.BORDA))
    pintor.setBrush(Qt.BrushStyle.NoBrush)
    pintor.drawRect(QRectF(0.5, 0.5, largura - 1, altura - 1))
    pintor.end()
    return QSplashScreen(fundo)


def _tratar_excecao(tipo, valor, rastro) -> None:  # noqa: ANN001
    log.critical("Erro não tratado:\n%s", "".join(traceback.format_exception(tipo, valor, rastro)))
    if QApplication.instance() is not None:
        QMessageBox.critical(None, "Forja3D", "Aconteceu um erro inesperado. Os detalhes foram para o log.")


def instalar_traducao(aplicativo: QApplication) -> None:
    """Os botões prontos do Qt, como Yes e Cancel, em português."""
    tradutor = QTranslator(aplicativo)
    if tradutor.load("qtbase_pt_BR", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        aplicativo.installTranslator(tradutor)


def configurar_no_primeiro_uso(config: Configuracao) -> Configuracao:
    """Sem provedor de IA, a primeira tela é a de configurações. Quem cancelar entra assim mesmo, sem a IA."""
    if config.ia_configurada:
        return config
    from forja3d.interface.dialogo_config import DialogoConfig

    if not DialogoConfig(config).exec():
        return config
    novo = configuracao.carregar()
    proteger_chaves(*novo.chaves)
    return novo


def executar(arquivo: Path | None = None) -> int:
    os.environ.setdefault("QT_API", "pyside6")
    aplicativo = QApplication(sys.argv)
    instancia = InstanciaUnica(aplicativo)
    if not instancia.assumir():
        if instancia.entregar(arquivo):
            return 0
        QMessageBox.warning(None, "Forja3D", "O Forja3D já está aberto, mas não respondeu. Feche a janela dele e tente de novo.")
        return 1

    config = configuracao.carregar()
    configurar_logs(*config.chaves)
    sys.excepthook = _tratar_excecao

    instalar_traducao(aplicativo)
    aplicativo.setApplicationName("Forja3D")
    aplicativo.setWindowIcon(criar_icone_do_app())
    aplicativo.setStyleSheet(tema.ESTILO)

    # Um pedido pode chegar enquanto a janela ainda carrega: a tela de configurações e o VTK levam tempo.
    janela = None
    pedidos: list[Path | None] = [arquivo] if arquivo else []

    def receber(pedido: Path | None) -> None:
        if janela is None:
            pedidos.append(pedido)
        else:
            janela.receber_pedido(pedido)

    instancia.recebido.connect(receber)
    config = configurar_no_primeiro_uso(config)
    abertura = _criar_abertura()
    abertura.show()
    aplicativo.processEvents()

    # O VTK demora para carregar: a importação vem depois da tela de abertura.
    from forja3d.impressora import carregar_perfil
    from forja3d.interface.janela_principal import JanelaPrincipal

    janela = JanelaPrincipal(config, carregar_perfil())
    instancia.peca_mudou.connect(janela.peca_mudou_por_fora)
    janela.show()
    abertura.finish(janela)
    for pedido in pedidos:
        janela.receber_pedido(pedido)
    # Pelo código, quem atualiza é o git.
    if configuracao.EMPACOTADO and config.atualizacao:
        from forja3d.interface.dialogo_atualizacao import ProcuraDeAtualizacao, oferecer

        procura = ProcuraDeAtualizacao(janela)
        procura.encontrada.connect(lambda novidade: oferecer(janela, novidade, config.atualizacao))
        procura.iniciar(config.atualizacao)
    return aplicativo.exec()
