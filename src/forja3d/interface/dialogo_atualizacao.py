"""Avisa que há uma versão nova na página de downloads e atualiza pelo próprio diálogo."""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from forja3d import atualizacao
from forja3d.atualizacao import ErroDeAtualizacao, Novidade
from forja3d.versao import VERSAO

log = logging.getLogger(__name__)
TITULO = "Atualização do Forja3D"


class ProcuraDeAtualizacao(QObject):
    """Procura em outra thread: fora de casa, a página não responde, e a janela não pode esperar por ela."""

    encontrada = Signal(object)

    def iniciar(self, endereco: str) -> None:
        def executar() -> None:
            # O pacote da atualização anterior fica na pasta até a abertura seguinte, já com a versão nova.
            shutil.rmtree(atualizacao.pasta_de_download(), ignore_errors=True)
            novidade = atualizacao.procurar(endereco, VERSAO)
            if novidade is None:
                return
            try:
                self.encontrada.emit(novidade)
            except RuntimeError:
                log.debug("Atualização ignorada: a janela já foi fechada")

        threading.Thread(target=executar, daemon=True).start()


class DialogoAtualizacao(QDialog):
    def __init__(self, novidade: Novidade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITULO)
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>Forja3D {novidade.versao}</b> está disponível. Você está com a {VERSAO}.", wordWrap=True))
        if novidade.notas:
            notas = QLabel(novidade.notas, wordWrap=True)
            notas.setTextFormat(Qt.TextFormat.MarkdownText)
            layout.addWidget(notas)
        layout.addWidget(QLabel("O app baixa a versão nova, confere o arquivo, instala e abre de novo. As suas peças e configurações continuam.", objectName="suave", wordWrap=True))
        botoes = QDialogButtonBox()
        self.botao_atualizar = botoes.addButton("Atualizar agora", QDialogButtonBox.ButtonRole.AcceptRole)
        self.botao_atualizar.setObjectName("primario")
        botoes.addButton("Depois", QDialogButtonBox.ButtonRole.RejectRole)
        botoes.accepted.connect(self.accept)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)


class _Download(QObject):
    progresso = Signal(int, int)
    terminou = Signal(object, str)


def instalar_e_fechar(janela: QWidget, pacote: Path) -> None:
    # A janela fecha antes: ela pode recusar, como durante uma impressão pela USB, e aí nada é instalado.
    if not janela.close():
        QMessageBox.information(None, TITULO, "A atualização fica para a próxima vez que o Forja3D abrir.")
        return
    try:
        atualizacao.instalar(pacote)
    except (ErroDeAtualizacao, OSError) as falha:
        log.exception("Falha ao instalar a atualização")
        QMessageBox.warning(None, TITULO, str(falha) if isinstance(falha, ErroDeAtualizacao) else "Não consegui começar a instalação.")
    QApplication.quit()


def atualizar(janela: QWidget, novidade: Novidade, endereco: str) -> None:
    """Baixa com uma barra de progresso. Se o arquivo conferir, instala e fecha o app."""
    barra = QProgressDialog("Baixando a atualização…", "Cancelar", 0, 100, janela)
    barra.setWindowTitle(TITULO)
    barra.setWindowModality(Qt.WindowModality.WindowModal)
    barra.setMinimumDuration(0)
    barra.setAutoClose(False)
    barra.setValue(0)
    cancelar = threading.Event()
    barra.canceled.connect(cancelar.set)
    sinais = _Download(barra)
    sinais.progresso.connect(lambda feito, total: barra.setValue(min(99, feito * 100 // total)) if total else None)

    def terminou(pacote: Path | None, erro: str) -> None:
        barra.close()
        if erro:
            QMessageBox.warning(janela, TITULO, erro)
            return
        instalar_e_fechar(janela, pacote)

    sinais.terminou.connect(terminou)

    def executar() -> None:
        pacote, erro = None, ""
        try:
            pacote = atualizacao.baixar(endereco, novidade, atualizacao.pasta_de_download(), sinais.progresso.emit, cancelar)
        except ErroDeAtualizacao as falha:
            erro = str(falha)
        except Exception:
            log.exception("Falha ao baixar a atualização")
            erro = "Não consegui baixar a atualização. Os detalhes foram para o log."
        try:
            sinais.terminou.emit(pacote, erro)
        except RuntimeError:
            log.debug("Download terminado com a janela já fechada")

    threading.Thread(target=executar, daemon=True).start()


def oferecer(janela: QWidget, novidade: Novidade, endereco: str) -> None:
    if DialogoAtualizacao(novidade, janela).exec():
        atualizar(janela, novidade, endereco)
