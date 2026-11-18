"""A janela da impressão pela USB: andamento, pausa, troca de cor e cancelamento."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from forja3d.fatiamento import usb
from forja3d.fatiamento.tempo import AVISO_DA_ESTIMATIVA, formatar_duracao, previsao_de_termino

ENCERRAM = ("cancelado", "concluido", "erro")
ESPERAM = ("pausado", "troca")
ESPERA_AO_FECHAR_S = 5


def aviso_de_energia() -> str:
    base = "Até o fim, o computador não pode dormir nem desligar, e o Forja3D precisa ficar aberto: a impressora depende dele a cada linha."
    if usb.impede_o_descanso():
        return f"{base} O app pede ao sistema para não dormir sozinho, mas fechar a tampa do notebook ou mandar dormir para a impressão no meio."
    return f"{base} Desative a suspensão automática nas configurações de energia antes de começar."


class DialogoUsb(QDialog):
    terminou = Signal()
    _mudou = Signal(str, str, float, object)

    def __init__(self, gcode: Path, tempo_estimado_s: float | None, z_maximo: float, dispositivo: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Imprimindo pela USB ({dispositivo})")
        self.setMinimumWidth(480)
        # Fechar no meio deixaria a impressora sem ninguém mandando as linhas: só o Cancelar encerra.
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.ativa = True
        self.estado = "ligando"
        self._pausa_pedida = False
        self.impressao = usb.ImpressaoUsb(gcode, tempo_estimado_s, z_maximo, self._mudou.emit)
        self._linha = threading.Thread(target=usb.imprimir, args=(self.impressao, dispositivo), daemon=True)

        coluna = QVBoxLayout(self)
        coluna.setSpacing(10)
        self.rotulo_aviso = QLabel(aviso_de_energia(), objectName="aviso", wordWrap=True)
        coluna.addWidget(self.rotulo_aviso)
        self.barra = QProgressBar(maximum=1000, format="%p%")
        coluna.addWidget(self.barra)
        self.rotulo_tempo = QLabel("", objectName="tempo", wordWrap=True, toolTip=AVISO_DA_ESTIMATIVA)
        coluna.addWidget(self.rotulo_tempo)
        self.rotulo_estado = QLabel("Ligando à impressora…", wordWrap=True)
        self.rotulo_estado.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        coluna.addWidget(self.rotulo_estado)
        coluna.addStretch(1)
        botoes = QHBoxLayout()
        botoes.addStretch(1)
        self.botao_pausar = QPushButton("Pausar", enabled=False)
        self.botao_pausar.clicked.connect(self._pausar_ou_retomar)
        self.botao_cancelar = QPushButton("Cancelar impressão")
        self.botao_cancelar.clicked.connect(self._cancelar)
        self.botao_fechar = QPushButton("Fechar", objectName="primario")
        self.botao_fechar.clicked.connect(self.accept)
        self.botao_fechar.hide()
        for botao in (self.botao_pausar, self.botao_cancelar, self.botao_fechar):
            botoes.addWidget(botao)
        coluna.addLayout(botoes)
        self._mudou.connect(self._mostrar)

    def iniciar(self) -> None:
        self.show()
        self._linha.start()

    def encerrar(self) -> None:
        """O app vai fechar: cancela e espera as linhas que desligam o bico e a mesa saírem."""
        self.ativa = False
        self.impressao.cancelar()
        self._linha.join(ESPERA_AO_FECHAR_S)

    def _mostrar(self, estado: str, texto: str, fracao: float, restante: int | None) -> None:
        self.estado = estado
        if estado != "imprimindo":
            self._pausa_pedida = False
        self.barra.setValue(round(fracao * 1000))
        if estado == "imprimindo":
            texto = ". ".join(p for p in ("Imprimindo", texto) if p) + "."
            if self._pausa_pedida:
                texto = "A pausa entra depois do comando atual. Se a impressora estiver aquecendo, ela termina de aquecer antes."
        self.rotulo_estado.setText(texto)
        mostra_tempo = restante is not None and estado in ("imprimindo", "pausando", *ESPERAM)
        termino = f". Se nada parar, fica pronta {previsao_de_termino(restante, datetime.now())}" if mostra_tempo and estado == "imprimindo" else ""
        self.rotulo_tempo.setText(f"Faltam {formatar_duracao(restante)}{termino}." if mostra_tempo else "")
        esperando = estado in ESPERAM
        self.botao_pausar.setText("Retomar" if esperando else "Pausando…" if estado == "pausando" or self._pausa_pedida else "Pausar")
        self.botao_pausar.setEnabled(esperando or (estado == "imprimindo" and not self._pausa_pedida))
        self.botao_pausar.setObjectName("primario" if esperando else "")
        self.botao_pausar.style().unpolish(self.botao_pausar)
        self.botao_pausar.style().polish(self.botao_pausar)
        self.botao_cancelar.setEnabled(estado not in ("cancelando", *ENCERRAM))
        if estado == "troca":
            # A pessoa pode estar longe do computador: a barra de tarefas pisca.
            QApplication.alert(self)
            self.raise_()
            self.activateWindow()
        if estado in ENCERRAM and self.ativa:
            self.ativa = False
            self.setWindowTitle("Impressão pela USB")
            self.botao_pausar.hide()
            self.botao_cancelar.hide()
            self.botao_fechar.show()
            self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
            self.show()
            self.terminou.emit()

    def _pausar_ou_retomar(self) -> None:
        if self.estado in ESPERAM:
            self.impressao.retomar()
            self.botao_pausar.setEnabled(False)
            return
        self.impressao.pausar()
        self._pausa_pedida = True
        self._mostrar(self.estado, "", self.barra.value() / 1000, None)

    def _cancelar(self) -> None:
        resposta = QMessageBox.question(self, "Cancelar a impressão", "O bico e a mesa desligam, a cabeça sobe e a peça fica pela metade. Cancelar mesmo?")
        if resposta == QMessageBox.StandardButton.Yes:
            self.impressao.cancelar()
            self.botao_cancelar.setEnabled(False)
            self.botao_pausar.setEnabled(False)

    def reject(self) -> None:
        if not self.ativa:
            super().reject()

    def closeEvent(self, evento) -> None:
        if self.ativa:
            evento.ignore()
            return
        super().closeEvent(evento)
