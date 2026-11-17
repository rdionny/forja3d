"""Deita a peça em um ângulo escolhido. Os 90° mais comuns ficam direto no menu."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

# Para onde vai o topo da peça, e a rotação que leva até lá.
DIRECOES = (("para a frente", "x", 1), ("para trás", "x", -1), ("para a direita", "y", 1), ("para a esquerda", "y", -1))
ANGULO_INICIAL = 120


class DialogoTombar(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tombar em outro ângulo")
        self.setMinimumWidth(380)
        coluna = QVBoxLayout(self)
        coluna.setSpacing(10)
        coluna.addWidget(QLabel("A 90° e a 180°, uma peça de faces retas fica apoiada em uma face. Em outro ângulo, ela encosta em uma aresta e costuma precisar de suporte.", objectName="suave", wordWrap=True))
        campos = QFormLayout()
        self.combo_direcao = QComboBox()
        for texto, _, _ in DIRECOES:
            self.combo_direcao.addItem(texto.capitalize())
        campos.addRow("Direção", self.combo_direcao)
        self.campo_angulo = QSpinBox(minimum=1, maximum=359, singleStep=15, suffix="°", value=ANGULO_INICIAL)
        campos.addRow("Ângulo", self.campo_angulo)
        coluna.addLayout(campos)
        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botoes.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar")
        botoes.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primario")
        botoes.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        botoes.accepted.connect(self.accept)
        botoes.rejected.connect(self.reject)
        coluna.addWidget(botoes)

    def escolha(self) -> tuple[str, float]:
        _, eixo, sinal = DIRECOES[self.combo_direcao.currentIndex()]
        return eixo, float(sinal * self.campo_angulo.value())
