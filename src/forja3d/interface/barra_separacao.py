"""A barra de separar cores: a cor do clique, o pino de encaixe e o botão que corta a peça."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from forja3d.fatiamento.cores import PALETA
from forja3d.interface.dialogo_cores import BotaoDeCor


class BarraDeSeparacao(QWidget):
    separar_pedido = Signal()
    fechar_pedido = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="painel")
        self._regioes = 0
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(12, 8, 12, 8)
        coluna.setSpacing(4)
        self.rotulo = QLabel("", objectName="medida", wordWrap=True)
        self.rotulo.setTextFormat(Qt.TextFormat.PlainText)
        coluna.addWidget(self.rotulo)

        linha = QHBoxLayout()
        linha.setSpacing(8)
        linha.addWidget(QLabel("Cor do clique", objectName="suave"))
        self.botao_cor = BotaoDeCor(PALETA[0][1])
        self.botao_cor.setToolTip("A cor do filamento das próximas regiões em que você clicar")
        linha.addWidget(self.botao_cor)
        self.caixa_pino = QCheckBox("Pino de encaixe", checked=True)
        self.caixa_pino.setToolTip(
            "Com o pino, a peça colorida trava no corpo. Em face curva, ela imprime com o pino para baixo e pede suporte.\n"
            "Sem o pino, ela entra só no rebaixo e imprime deitada no fundo plano, sem suporte."
        )
        linha.addWidget(self.caixa_pino)
        linha.addStretch(1)
        self.botao_separar = QPushButton("Separar", objectName="primario")
        self.botao_separar.clicked.connect(self.separar_pedido)
        linha.addWidget(self.botao_separar)
        self.botao_fechar = QPushButton("Voltar à mesa")
        self.botao_fechar.clicked.connect(self.fechar_pedido)
        linha.addWidget(self.botao_fechar)
        coluna.addLayout(linha)
        self.hide()

    def iniciar(self, regioes: int) -> None:
        self._regioes = regioes
        self.mostrar_pintadas(0)
        self.setEnabled(True)
        self.show()

    def mostrar_pintadas(self, quantidade: int) -> None:
        self.botao_separar.setEnabled(quantidade > 0)
        if quantidade:
            self.rotulo.setText(f"{quantidade} de {self._regioes} regiões pintadas. Clique de novo em uma região para tirar a pintura, e em Separar para cortar.")
        else:
            self.rotulo.setText(f"Achei {self._regioes} regiões, contornadas na peça. Escolha a cor e clique nas regiões que vão sair nessa cor. Vizinhas da mesma cor saem juntas.")
