"""Resultado da verificação da peça e da validação do G-code."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from forja3d.interface import tema

SIMBOLOS = {"ok": ("✓", tema.OK), "alerta": ("!", tema.ALERTA), "bloqueio": ("✕", tema.ERRO), "info": ("i", tema.SUAVE)}


class ItemCheck(QFrame):
    def __init__(self, tipo: str, texto: str) -> None:
        super().__init__(objectName="itemCheck")
        simbolo, cor = SIMBOLOS[tipo]
        linha = QHBoxLayout(self)
        linha.setContentsMargins(8, 6, 8, 6)
        linha.setSpacing(8)
        marca = QLabel(simbolo, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        marca.setStyleSheet(f"color: {cor}; font-weight: 700;")
        marca.setFixedWidth(14)
        rotulo = QLabel(texto, wordWrap=True)
        rotulo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        linha.addWidget(marca)
        linha.addWidget(rotulo, 1)


class PainelChecks(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="painel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(QLabel("VERIFICAÇÃO", objectName="titulo"))
        rolagem = QScrollArea(widgetResizable=True)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        conteudo = QWidget()
        self.lista = QVBoxLayout(conteudo)
        self.lista.setContentsMargins(0, 0, 4, 0)
        self.lista.setSpacing(4)
        self.lista.addStretch(1)
        rolagem.setWidget(conteudo)
        layout.addWidget(rolagem, 1)
        self.limpar()

    def limpar(self) -> None:
        while self.lista.count() > 1:
            item = self.lista.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self._vazio = QLabel("A verificação aparece aqui depois que a peça for gerada.", objectName="suave", wordWrap=True)
        self.lista.insertWidget(0, self._vazio)

    def _adicionar(self, tipo: str, texto: str) -> None:
        if self._vazio is not None:
            self._vazio.hide()
            self._vazio.deleteLater()
            self._vazio = None
        self.lista.insertWidget(self.lista.count() - 1, ItemCheck(tipo, texto))

    def mostrar_verificacao(self, resultado: dict[str, Any] | None) -> None:
        self.limpar()
        if not resultado:
            return
        for item in resultado["bloqueios"]:
            self._adicionar("bloqueio", item["mensagem"])
        for item in resultado["alertas"]:
            self._adicionar("alerta", item["mensagem"])
        if resultado["aprovado"]:
            self._adicionar("ok", "Medidas, furos e regras de impressão conferem com a especificação.")
        for medida in resultado.get("medidas", []):
            x, y, z = medida["tamanho"]
            furos = len(medida["furos"])
            texto = (
                f"{tema.numero(x)} × {tema.numero(y)} × {tema.numero(z)} mm, {tema.numero(medida['volume_mm3'] / 1000)} cm³, "
                f"{furos} furo{'s' if furos != 1 else ''}, base de {tema.numero(medida['area_base_mm2'], 0)} mm²"
            )
            self._adicionar("info", texto)

    def mostrar_gcode(self, validacao: dict[str, Any]) -> None:
        for item in validacao["bloqueios"]:
            self._adicionar("bloqueio", f"G-code: {item['mensagem']}")
        for item in validacao["alertas"]:
            self._adicionar("alerta", f"G-code: {item['mensagem']}")
        if validacao["aprovado"]:
            self._adicionar("ok", "G-code aprovado: movimentos na mesa, temperaturas e comandos conferem.")

    def mostrar_roteiro(self, roteiro: str) -> None:
        """As trocas de filamento, na ordem em que vão acontecer."""
        for bloco in roteiro.split("\n\n"):
            if bloco:
                self._adicionar("info", bloco)
