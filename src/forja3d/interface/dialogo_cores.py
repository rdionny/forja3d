"""Escolha de cores: do item na mesa, dos corpos de um modelo e das trocas por altura."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from forja3d.fatiamento.cores import CAMADA_INICIAL_MM, PALETA, nome_da_cor, proxima_cor
from forja3d.interface import tema

LADO_DA_AMOSTRA = 14
ALTURA_MINIMA_DA_TROCA_MM = 0.2
FUNDO_LIGADO_ATE_MM = 10.0


def _amostra(cor: str | None) -> QIcon:
    imagem = QPixmap(LADO_DA_AMOSTRA, LADO_DA_AMOSTRA)
    imagem.fill(QColor(cor or tema.COR_PECA))
    return QIcon(imagem)


class BotaoDeCor(QPushButton):
    """Mostra a cor e abre a paleta. A pessoa pensa em filamento, então a paleta vem antes do seletor completo."""

    mudou = Signal()

    def __init__(self, cor: str | None = None, permitir_sem_cor: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cor = cor
        self.permitir_sem_cor = permitir_sem_cor
        self.clicked.connect(self._abrir_paleta)
        self._atualizar()

    def _atualizar(self) -> None:
        self.setIcon(_amostra(self.cor))
        self.setText(nome_da_cor(self.cor).capitalize() if self.cor else "Sem cor definida")

    def definir(self, cor: str | None) -> None:
        self.cor = cor
        self._atualizar()
        self.mudou.emit()

    def montar_paleta(self) -> QMenu:
        menu = QMenu(self)
        for nome, cor in PALETA:
            menu.addAction(_amostra(cor), nome.capitalize(), lambda c=cor: self.definir(c))
        menu.addSeparator()
        menu.addAction("Outra cor…", self._outra_cor)
        if self.permitir_sem_cor:
            menu.addAction("Sem cor definida", lambda: self.definir(None))
        return menu

    def _abrir_paleta(self) -> None:
        self.montar_paleta().exec(self.mapToGlobal(self.rect().bottomLeft()))

    def _outra_cor(self) -> None:
        escolhida = QColorDialog.getColor(QColor(self.cor or tema.COR_PECA), self, "Cor do filamento")
        if escolhida.isValid():
            self.definir(escolhida.name().upper())


class EditorDeCores(QWidget):
    """Uma cor para cada corpo e, com mais de um, a técnica de colorir só a primeira camada."""

    mudou = Signal()

    def __init__(self, cores: dict[str, str | None], fundo: str | None, altura_mm: float, camada_mm: float = CAMADA_INICIAL_MM, candidatos_a_fundo: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.altura_mm = altura_mm
        self.camada_mm = camada_mm
        coluna = QVBoxLayout(self)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(8)
        formulario = QFormLayout()
        formulario.setSpacing(6)
        self.botoes: dict[str, BotaoDeCor] = {}
        um_corpo = list(cores) == [""]
        for corpo, cor in cores.items():
            botao = BotaoDeCor(cor, permitir_sem_cor=um_corpo)
            botao.mudou.connect(self._ao_mudar)
            self.botoes[corpo] = botao
            formulario.addRow("Cor" if um_corpo else corpo, botao)
        coluna.addLayout(formulario)

        self.caixa_fundo = None
        self.combo_fundo = None
        self.rotulo_trocas = None
        if not um_corpo:
            self.caixa_fundo = QCheckBox(
                "Só a primeira camada colorida",
                toolTip="A técnica do logo: todas as cores na camada que encosta na mesa, e o resto da peça em uma cor só. Cada cor custa uma troca.",
            )
            self.combo_fundo = QComboBox(toolTip="A cor do resto da peça")
            self.combo_fundo.addItems(candidatos_a_fundo or list(cores))
            linha = QHBoxLayout()
            linha.addWidget(self.caixa_fundo)
            linha.addWidget(QLabel("Resto na cor de", objectName="suave"))
            linha.addWidget(self.combo_fundo, 1)
            coluna.addLayout(linha)
            self.rotulo_trocas = QLabel("", objectName="medida", wordWrap=True)
            coluna.addWidget(self.rotulo_trocas)
            self.rotulo_face = QLabel("A face colorida é a que fica para baixo. Se ela estiver para cima, tombe a peça duas vezes ao importar.", objectName="suave", wordWrap=True)
            coluna.addWidget(self.rotulo_face)
            ligado = bool(fundo) and self.combo_fundo.findText(fundo) >= 0
            if ligado:
                self.combo_fundo.setCurrentText(fundo)
            self.caixa_fundo.setChecked(ligado)
            self.caixa_fundo.toggled.connect(self._ao_mudar)
            self.combo_fundo.currentIndexChanged.connect(self._ao_mudar)
            self._atualizar_trocas()

    def _ao_mudar(self) -> None:
        self._atualizar_trocas()
        self.mudou.emit()

    def _atualizar_trocas(self) -> None:
        if self.rotulo_trocas is None:
            return
        self.combo_fundo.setEnabled(self.caixa_fundo.isChecked())
        self.rotulo_face.setVisible(self.caixa_fundo.isChecked())
        distintas = len({b.cor for b in self.botoes.values()})
        if distintas <= 1:
            self.rotulo_trocas.setText("Uma cor só: nenhuma troca de filamento.")
            return
        if self.caixa_fundo.isChecked():
            self.rotulo_trocas.setText(f"{distintas - 1} {'troca' if distintas == 2 else 'trocas'} de filamento.")
            return
        camadas = max(1, round(self.altura_mm / self.camada_mm))
        self.rotulo_trocas.setText(f"Cerca de {(distintas - 1) * camadas} trocas de filamento: {distintas - 1} em cada uma das {camadas} camadas.")

    def cores(self) -> dict[str, str]:
        return {corpo: botao.cor for corpo, botao in self.botoes.items() if botao.cor}

    def fundo(self) -> str | None:
        """O nome do corpo, "" se a pessoa desligou, e None para um modelo de um corpo só."""
        if self.caixa_fundo is None:
            return None
        return self.combo_fundo.currentText() if self.caixa_fundo.isChecked() else ""


class DialogoCores(QDialog):
    def __init__(self, nome: str, cores: dict[str, str | None], fundo: str | None, altura_mm: float, camada_mm: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cor")
        self.setMinimumWidth(420)
        coluna = QVBoxLayout(self)
        coluna.setSpacing(10)
        coluna.addWidget(QLabel(nome, objectName="marca", textFormat=Qt.TextFormat.PlainText))
        self.editor = EditorDeCores(cores, fundo, altura_mm, camada_mm)
        coluna.addWidget(self.editor)
        coluna.addWidget(_botoes_de_confirmacao(self))


class DialogoAlturas(QDialog):
    """A mesa inteira muda de cor a partir de uma altura. É a troca mais simples: uma pausa só, sem torre."""

    def __init__(self, trocas: list[dict[str, Any]], altura_maxima_mm: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.altura_maxima_mm = altura_maxima_mm
        self.setWindowTitle("Trocar a cor em uma altura")
        self.setMinimumWidth(420)
        coluna = QVBoxLayout(self)
        coluna.setSpacing(10)
        coluna.addWidget(QLabel(f"A impressão pausa na altura escolhida, para você colocar a cor seguinte. A peça mais alta deste arquivo tem {tema.numero(altura_maxima_mm)} mm.", objectName="suave", wordWrap=True))
        self.lista = QVBoxLayout()
        self.lista.setSpacing(6)
        coluna.addLayout(self.lista)
        self.linhas: list[tuple[QWidget, QDoubleSpinBox, BotaoDeCor]] = []
        self.botao_adicionar = QPushButton("Adicionar troca")
        self.botao_adicionar.clicked.connect(lambda: self.adicionar())
        coluna.addWidget(self.botao_adicionar, alignment=Qt.AlignmentFlag.AlignLeft)
        coluna.addWidget(_botoes_de_confirmacao(self))
        for troca in trocas:
            self.adicionar(troca["z"], troca["cor"])

    def adicionar(self, z: float | None = None, cor: str | None = None) -> None:
        if z is None:
            z = min(self.altura_maxima_mm / 2 if not self.linhas else self.linhas[-1][1].value() + 1.0, self.altura_maxima_mm)
        usadas = [b.cor for _, _, b in self.linhas if b.cor]
        linha = QWidget()
        arranjo = QHBoxLayout(linha)
        arranjo.setContentsMargins(0, 0, 0, 0)
        arranjo.addWidget(QLabel("A partir de", objectName="suave"))
        campo = QDoubleSpinBox(decimals=2, singleStep=0.2, suffix=" mm", minimum=ALTURA_MINIMA_DA_TROCA_MM, maximum=max(ALTURA_MINIMA_DA_TROCA_MM, self.altura_maxima_mm))
        campo.setValue(z)
        arranjo.addWidget(campo)
        botao = BotaoDeCor(cor or proxima_cor(usadas))
        arranjo.addWidget(botao, 1)
        remover = QPushButton("Tirar")
        registro = (linha, campo, botao)
        remover.clicked.connect(lambda: self._remover(registro))
        arranjo.addWidget(remover)
        self.lista.addWidget(linha)
        self.linhas.append(registro)

    def _remover(self, registro: tuple[QWidget, QDoubleSpinBox, BotaoDeCor]) -> None:
        self.linhas.remove(registro)
        registro[0].hide()
        registro[0].deleteLater()

    def trocas(self) -> list[dict[str, Any]]:
        return [{"z": campo.value(), "cor": botao.cor} for _, campo, botao in self.linhas if botao.cor]


def _botoes_de_confirmacao(dialogo: QDialog) -> QDialogButtonBox:
    botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    botoes.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar")
    botoes.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primario")
    botoes.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
    botoes.accepted.connect(dialogo.accept)
    botoes.rejected.connect(dialogo.reject)
    return botoes
