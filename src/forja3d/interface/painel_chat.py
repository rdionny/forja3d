"""Conversa com o agente."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

SUGESTOES = (
    "Suporte em L para parede, em PLA: base de 40 × 30 × 4 mm, parede de 30 mm de altura e 2 furos para parafuso M4 na base",
    "Caixa de 80 × 60 × 30 mm com tampa de encaixe, paredes de 2 mm, em PLA",
    "Gancho de parede para toalha, com 2 furos para parafuso M4, em PETG",
)


class Bolha(QFrame):
    def __init__(self, papel: str, texto: str) -> None:
        nomes = {"usuario": "bolhaUsuario", "agente": "bolhaAgente", "erro": "bolhaErro"}
        super().__init__(objectName=nomes[papel])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        self.rotulo = QLabel(texto, wordWrap=True)
        self.rotulo.setTextFormat(Qt.TextFormat.PlainText if papel == "usuario" else Qt.TextFormat.MarkdownText)
        self.rotulo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rotulo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.rotulo)


class CartaoSugestao(QFrame):
    """Um QPushButton não quebra linha, então o cartão é um quadro clicável."""

    clicado = Signal(str)

    def __init__(self, texto: str) -> None:
        super().__init__(objectName="sugestao")
        self.texto = texto
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.addWidget(QLabel(texto, wordWrap=True))

    def mousePressEvent(self, evento) -> None:  # noqa: ANN001, N802 - nome definido pelo Qt.
        self.clicado.emit(self.texto)
        super().mousePressEvent(evento)


class CampoTexto(QPlainTextEdit):
    enviar = Signal()

    def keyPressEvent(self, evento) -> None:  # noqa: ANN001, N802 - nome definido pelo Qt.
        enter = evento.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not evento.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.enviar.emit()
            return
        super().keyPressEvent(evento)


class PainelChat(QWidget):
    enviado = Signal(str)
    cancelar = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="painel")
        self._ocupado = False
        self._seguindo_o_fim = False
        self._inicio_etapa = 0.0
        self._texto_etapa = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(QLabel("CONVERSA", objectName="titulo"))

        self.rolagem = QScrollArea(widgetResizable=True)
        self.rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.conteudo = QWidget()
        self.lista = QVBoxLayout(self.conteudo)
        self.lista.setContentsMargins(0, 0, 4, 0)
        self.lista.setSpacing(8)
        self.lista.addStretch(1)
        self.rolagem.setWidget(self.conteudo)
        self.rolagem.verticalScrollBar().rangeChanged.connect(self._ao_mudar_faixa)
        layout.addWidget(self.rolagem, 1)

        self.vazio = self._criar_inicio()
        self.lista.insertWidget(0, self.vazio)

        self.linha_etapa = QFrame(objectName="linhaEtapa")
        linha = QHBoxLayout(self.linha_etapa)
        linha.setContentsMargins(10, 6, 6, 6)
        self.rotulo_etapa = QLabel("", objectName="etapa")
        self.botao_cancelar = QPushButton("Cancelar", objectName="plano")
        self.botao_cancelar.clicked.connect(self.cancelar)
        linha.addWidget(self.rotulo_etapa, 1)
        linha.addWidget(self.botao_cancelar)
        self.linha_etapa.hide()
        layout.addWidget(self.linha_etapa)

        entrada = QHBoxLayout()
        entrada.setSpacing(8)
        self.campo = CampoTexto(placeholderText="Descreva a peça ou peça um ajuste…")
        self.campo.setFixedHeight(66)
        self.campo.enviar.connect(self._enviar)
        self.botao_enviar = QPushButton("Enviar", objectName="primario")
        self.botao_enviar.setFixedHeight(66)
        self.botao_enviar.clicked.connect(self._enviar)
        entrada.addWidget(self.campo, 1)
        entrada.addWidget(self.botao_enviar)
        layout.addLayout(entrada)

        self._relogio = QTimer(self, interval=1000)
        self._relogio.timeout.connect(self._atualizar_etapa)

    def _criar_inicio(self) -> QWidget:
        caixa = QWidget()
        layout = QVBoxLayout(caixa)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Descreva a peça que você precisa, com as medidas que tiver. Por exemplo:", wordWrap=True, objectName="suave"))
        for texto in SUGESTOES:
            cartao = CartaoSugestao(texto)
            cartao.clicado.connect(self._usar_sugestao)
            layout.addWidget(cartao)
        return caixa

    def _usar_sugestao(self, texto: str) -> None:
        self.campo.setPlainText(texto)
        self.campo.setFocus()

    def _enviar(self) -> None:
        texto = self.campo.toPlainText().strip()
        if texto and not self._ocupado:
            self.campo.clear()
            self.enviado.emit(texto)

    def adicionar(self, papel: str, texto: str) -> None:
        self.vazio.hide()
        bolha = Bolha(papel, texto)
        linha = QHBoxLayout()
        linha.setContentsMargins(0, 0, 0, 0)
        if papel == "usuario":
            linha.addSpacing(36)
            linha.addWidget(bolha, 1)
        else:
            linha.addWidget(bolha, 1)
            linha.addSpacing(12)
        self.lista.insertLayout(self.lista.count() - 1, linha)
        self._rolar_para_o_fim()

    def adicionar_evento(self, texto: str) -> None:
        self.vazio.hide()
        rotulo = QLabel(texto, objectName="suave", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.lista.insertWidget(self.lista.count() - 1, rotulo)
        self._rolar_para_o_fim()

    def _rolar_para_o_fim(self) -> None:
        # A altura das bolhas só é conhecida depois do layout: acompanha a barra por um instante.
        self._seguindo_o_fim = True
        # Com contexto: se o painel for destruído antes, o Qt cancela o disparo.
        QTimer.singleShot(400, self, self._parar_de_seguir)
        self._ir_para_o_fim()

    def _parar_de_seguir(self) -> None:
        self._seguindo_o_fim = False

    def _ao_mudar_faixa(self) -> None:
        if self._seguindo_o_fim:
            self._ir_para_o_fim()

    def _ir_para_o_fim(self) -> None:
        barra = self.rolagem.verticalScrollBar()
        barra.setValue(barra.maximum())

    def limpar(self) -> None:
        while self.lista.count() > 1:
            item = self.lista.takeAt(0)
            if item.widget() is not None and item.widget() is not self.vazio:
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    interno = item.layout().takeAt(0)
                    if interno.widget() is not None:
                        interno.widget().hide()
                        interno.widget().deleteLater()
                item.layout().deleteLater()
        self.lista.insertWidget(0, self.vazio)
        self.vazio.show()

    def definir_ocupado(self, ocupado: bool) -> None:
        self._ocupado = ocupado
        self.botao_enviar.setEnabled(not ocupado)
        self.linha_etapa.setVisible(ocupado)
        self.botao_cancelar.setEnabled(True)
        if ocupado:
            self._inicio_etapa = time.monotonic()
            self._texto_etapa = "Trabalhando"
            self._atualizar_etapa()
            self._relogio.start()
        else:
            self._relogio.stop()

    def definir_etapa(self, texto: str) -> None:
        self._texto_etapa = texto
        self._atualizar_etapa()

    def indicar_cancelando(self) -> None:
        self._texto_etapa = "Cancelando"
        self.botao_cancelar.setEnabled(False)
        self._atualizar_etapa()

    def _atualizar_etapa(self) -> None:
        self.rotulo_etapa.setText(f"{self._texto_etapa}… {int(time.monotonic() - self._inicio_etapa)} s")
