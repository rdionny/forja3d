"""A barra que percorre a impressão simulada: arrastar leva para qualquer momento, e reproduzir anda sozinho."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from forja3d.fatiamento.cores import nome_da_cor
from forja3d.fatiamento.simulacao import Pausa, Percurso
from forja3d.fatiamento.tempo import formatar_duracao
from forja3d.interface import tema

RESOLUCAO = 100_000
INTERVALO_MS = 40
# Quanto a impressão inteira leva na tela.
VELOCIDADES = (("Lenta", 120.0), ("Média", 40.0), ("Rápida", 15.0))
VELOCIDADE_INICIAL = 1


class BarraDeSimulacao(QWidget):
    tempo_mudou = Signal(float)
    fechar_pedido = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="painel")
        self.percurso: Percurso | None = None
        self.tempo = 0.0
        self._ajustando = False
        self._relogio_anterior = 0.0
        self._aviso_de_troca = ""

        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(12, 8, 12, 8)
        coluna.setSpacing(4)
        self.rotulo = QLabel("", objectName="medida")
        self.rotulo.setTextFormat(Qt.TextFormat.PlainText)
        coluna.addWidget(self.rotulo)

        linha = QHBoxLayout()
        linha.setSpacing(8)
        self.botao_tocar = QPushButton("Reproduzir", objectName="primario")
        self.botao_tocar.setFixedWidth(96)
        self.botao_tocar.clicked.connect(self.alternar)
        linha.addWidget(self.botao_tocar)
        self.barra = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=RESOLUCAO, accessibleName="Progresso da impressão")
        self.barra.setSingleStep(RESOLUCAO // 500)
        self.barra.setPageStep(RESOLUCAO // 20)
        self.barra.valueChanged.connect(self._ao_mover_a_barra)
        self.barra.sliderPressed.connect(self.pausar)
        linha.addWidget(self.barra, 1)
        self.botao_troca = QPushButton("Próxima troca", toolTip="Vai para a próxima pausa de troca de filamento")
        self.botao_troca.clicked.connect(self.ir_para_a_proxima_troca)
        linha.addWidget(self.botao_troca)
        self.combo_velocidade = QComboBox(toolTip="Velocidade da reprodução")
        self.combo_velocidade.addItems([nome for nome, _ in VELOCIDADES])
        self.combo_velocidade.setCurrentIndex(VELOCIDADE_INICIAL)
        linha.addWidget(self.combo_velocidade)
        self.botao_fechar = QPushButton("Voltar à mesa")
        self.botao_fechar.clicked.connect(self.fechar_pedido)
        linha.addWidget(self.botao_fechar)
        coluna.addLayout(linha)

        self._relogio = QTimer(self, interval=INTERVALO_MS)
        self._relogio.timeout.connect(self._avancar)
        self.hide()

    @property
    def tocando(self) -> bool:
        return self._relogio.isActive()

    def iniciar(self, percurso: Percurso) -> None:
        self.percurso = percurso
        self.botao_troca.setVisible(bool(percurso.pausas))
        self._ir_para(0.0)
        self.show()

    def encerrar(self) -> None:
        self.pausar()
        self.percurso = None
        self.hide()

    def alternar(self) -> None:
        if self.tocando:
            self.pausar()
        else:
            self.reproduzir()

    def reproduzir(self) -> None:
        if self.percurso is None:
            return
        # No fim, reproduzir recomeça do início.
        if self.tempo >= self.percurso.tempo_total:
            self._ir_para(0.0)
        self._relogio_anterior = time.monotonic()
        self._relogio.start()
        self.botao_tocar.setText("Pausar")

    def pausar(self) -> None:
        self._relogio.stop()
        self.botao_tocar.setText("Reproduzir")

    def ir_para_a_proxima_troca(self) -> None:
        if self.percurso is None:
            return
        self.pausar()
        pausa = self.percurso.proxima_pausa(self.tempo) or (self.percurso.pausas[0] if self.percurso.pausas else None)
        if pausa is not None:
            self._parar_na_troca(pausa)

    def _avancar(self) -> None:
        agora = time.monotonic()
        passo = (agora - self._relogio_anterior) * self.percurso.tempo_total / VELOCIDADES[self.combo_velocidade.currentIndex()][1]
        self._relogio_anterior = agora
        destino = min(self.tempo + passo, self.percurso.tempo_total)
        pausa = self.percurso.pausa_entre(self.tempo, destino)
        if pausa is not None:
            # A impressora para aqui até alguém trocar o filamento, e a simulação também.
            self.pausar()
            self._parar_na_troca(pausa)
            return
        self._ir_para(destino)
        if destino >= self.percurso.tempo_total:
            self.pausar()

    def _parar_na_troca(self, pausa: Pausa) -> None:
        self._ir_para(pausa.tempo)
        self._aviso_de_troca = f"Troca {pausa.numero} de {pausa.total}: a impressora pausa aqui para você colocar {nome_da_cor(pausa.cor)}. "
        self._atualizar_texto()

    def _ir_para(self, tempo: float) -> None:
        self.tempo = tempo
        self._aviso_de_troca = ""
        self._ajustando = True
        self.barra.setValue(round(tempo / max(self.percurso.tempo_total, 1e-9) * RESOLUCAO))
        self._ajustando = False
        self._atualizar_texto()
        self.tempo_mudou.emit(tempo)

    def _ao_mover_a_barra(self, valor: int) -> None:
        if self._ajustando or self.percurso is None:
            return
        self.tempo = valor / RESOLUCAO * self.percurso.tempo_total
        self._aviso_de_troca = ""
        self._atualizar_texto()
        self.tempo_mudou.emit(self.tempo)

    def _atualizar_texto(self) -> None:
        percurso = self.percurso
        if percurso is None:
            return
        percentual = self.tempo / max(percurso.tempo_total, 1e-9) * 100
        camada, z = percurso.camada_em(self.tempo)
        onde = f"camada {camada} de {percurso.quantidade_de_camadas}, {tema.numero(z, 2)} mm" if camada else "preparando: purga na lateral da mesa"
        tempos = f"{formatar_duracao(round(self.tempo))} de {formatar_duracao(round(percurso.tempo_total))}"
        self.rotulo.setText(f"{self._aviso_de_troca}{tema.numero(percentual, 0)} %, {onde}, {tempos}")
