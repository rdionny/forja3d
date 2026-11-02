"""Inicia o aplicativo desktop."""
from __future__ import annotations

import logging
import os
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from forja3d import configuracao
from forja3d.interface import tema
from forja3d.registro import configurar_logs

log = logging.getLogger(__name__)


def criar_icone(lado: int = 256) -> QPixmap:
    imagem = QPixmap(lado, lado)
    imagem.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(imagem)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
    pintor.setBrush(QColor(tema.ACENTO))
    pintor.setPen(Qt.PenStyle.NoPen)
    pintor.drawRoundedRect(0, 0, lado, lado, lado * 0.2, lado * 0.2)
    pintor.setPen(QColor(tema.FUNDO))
    pintor.setFont(QFont("Segoe UI", int(lado * 0.42), QFont.Weight.Bold))
    pintor.drawText(imagem.rect(), Qt.AlignmentFlag.AlignCenter, "F3")
    pintor.end()
    return imagem


def _criar_abertura() -> QSplashScreen:
    fundo = QPixmap(420, 180)
    fundo.fill(QColor(tema.PAINEL))
    pintor = QPainter(fundo)
    pintor.setPen(QColor(tema.TEXTO))
    pintor.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
    pintor.drawText(fundo.rect().adjusted(0, -24, 0, -24), Qt.AlignmentFlag.AlignCenter, "Forja3D")
    pintor.setPen(QColor(tema.SUAVE))
    pintor.setFont(QFont("Segoe UI", 10))
    pintor.drawText(fundo.rect().adjusted(0, 36, 0, 36), Qt.AlignmentFlag.AlignCenter, "Abrindo…")
    pintor.end()
    return QSplashScreen(fundo)


def _tratar_excecao(tipo, valor, rastro) -> None:  # noqa: ANN001
    log.critical("Erro não tratado:\n%s", "".join(traceback.format_exception(tipo, valor, rastro)))
    if QApplication.instance() is not None:
        QMessageBox.critical(None, "Forja3D", "Aconteceu um erro inesperado. Os detalhes foram para o log.")


def executar() -> int:
    os.environ.setdefault("QT_API", "pyside6")
    config = configuracao.carregar()
    configurar_logs(config.ia.api_key)
    sys.excepthook = _tratar_excecao

    aplicativo = QApplication(sys.argv)
    aplicativo.setApplicationName("Forja3D")
    aplicativo.setWindowIcon(QIcon(criar_icone()))
    aplicativo.setStyleSheet(tema.ESTILO)
    abertura = _criar_abertura()
    abertura.show()
    aplicativo.processEvents()

    # O VTK demora para carregar: a importação vem depois da tela de abertura.
    from forja3d.impressora import carregar_perfil
    from forja3d.interface.janela_principal import JanelaPrincipal

    janela = JanelaPrincipal(config, carregar_perfil())
    janela.show()
    abertura.finish(janela)
    return aplicativo.exec()
