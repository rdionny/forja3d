"""Lista de peças e de versões da peça aberta."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from forja3d.projetos.armazenamento import Projeto


class PainelVersoes(QWidget):
    nova_peca = Signal()
    projeto_escolhido = Signal(object)
    versao_escolhida = Signal(str)
    restaurar_pedido = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="painel")
        self._versao_atual: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        topo = QHBoxLayout()
        topo.addWidget(QLabel("PEÇAS", objectName="titulo"), 1)
        self.botao_nova = QPushButton("+ Nova", objectName="plano")
        self.botao_nova.clicked.connect(self.nova_peca)
        topo.addWidget(self.botao_nova)
        layout.addLayout(topo)

        self.lista_projetos = QListWidget()
        self.lista_projetos.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_projetos.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.lista_projetos.itemClicked.connect(self._clicou_projeto)
        layout.addWidget(self.lista_projetos, 3)

        layout.addSpacing(6)
        layout.addWidget(QLabel("VERSÕES", objectName="titulo"))
        self.lista_versoes = QListWidget()
        self.lista_versoes.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_versoes.itemClicked.connect(self._clicou_versao)
        layout.addWidget(self.lista_versoes, 2)

        self.botao_restaurar = QPushButton("Voltar para esta versão")
        self.botao_restaurar.clicked.connect(self._pedir_restauracao)
        self.botao_restaurar.hide()
        layout.addWidget(self.botao_restaurar)

    def atualizar_projetos(self, projetos: list[Projeto], atual: Projeto | None) -> None:
        self.lista_projetos.clear()
        for projeto in projetos:
            item = QListWidgetItem(projeto.titulo)
            item.setData(Qt.ItemDataRole.UserRole, projeto)
            item.setToolTip(projeto.pasta.name)
            self.lista_projetos.addItem(item)
            if atual is not None and projeto.pasta == atual.pasta:
                item.setSelected(True)

    def atualizar_versoes(self, projeto: Projeto | None, atual: str | None) -> None:
        self._versao_atual = atual
        self.lista_versoes.clear()
        self.botao_restaurar.hide()
        if projeto is None:
            return
        for versao in reversed(projeto.versoes()):
            verificacao = projeto.verificacao(versao)
            marca = "" if verificacao is None else (" ✓" if verificacao["aprovado"] else " ✕")
            sufixo = "  (atual)" if versao == atual else ""
            item = QListWidgetItem(f"{versao}{marca}{sufixo}")
            item.setData(Qt.ItemDataRole.UserRole, versao)
            resumo = projeto.info_versao(versao).get("resumo", "")
            if resumo:
                item.setToolTip(resumo)
            self.lista_versoes.addItem(item)
            if versao == atual:
                item.setSelected(True)

    def _clicou_projeto(self, item: QListWidgetItem) -> None:
        self.projeto_escolhido.emit(item.data(Qt.ItemDataRole.UserRole))

    def _clicou_versao(self, item: QListWidgetItem) -> None:
        versao = item.data(Qt.ItemDataRole.UserRole)
        self.botao_restaurar.setVisible(versao != self._versao_atual)
        self.versao_escolhida.emit(versao)

    def _pedir_restauracao(self) -> None:
        item = self.lista_versoes.currentItem()
        if item is not None:
            self.restaurar_pedido.emit(item.data(Qt.ItemDataRole.UserRole))
