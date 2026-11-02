"""Tela de configurações."""
from __future__ import annotations

import threading
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from forja3d import configuracao
from forja3d.configuracao import Configuracao
from forja3d.ia.cliente import ClienteIA, ErroIA


class DialogoConfig(QDialog):
    _resultado_teste = Signal(object, str)

    def __init__(self, config: Configuracao, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        formulario = QFormLayout()
        formulario.setSpacing(10)

        self.campo_endpoint = QLineEdit(config.ia.endpoint)
        formulario.addRow("Endpoint", self.campo_endpoint)
        self.campo_chave = QLineEdit(config.ia.api_key, echoMode=QLineEdit.EchoMode.Password)
        formulario.addRow("Chave da API", self.campo_chave)

        linha_modelo = QHBoxLayout()
        self.campo_modelo = QComboBox(editable=True)
        self.campo_modelo.addItem(config.ia.modelo)
        self.botao_testar = QPushButton("Testar conexão")
        self.botao_testar.clicked.connect(self._testar)
        linha_modelo.addWidget(self.campo_modelo, 1)
        linha_modelo.addWidget(self.botao_testar)
        formulario.addRow("Modelo", linha_modelo)

        self.rotulo_teste = QLabel("", objectName="suave", wordWrap=True)
        formulario.addRow("", self.rotulo_teste)

        self.campo_orca = QLineEdit(str(config.caminho_orca))
        formulario.addRow("OrcaSlicer", self._com_procurar(self.campo_orca, self._procurar_orca))
        self.campo_projetos = QLineEdit(str(config.pasta_projetos))
        formulario.addRow("Pasta das peças", self._com_procurar(self.campo_projetos, self._procurar_pasta))
        layout.addLayout(formulario)

        if config.ia.endpoint.startswith("http://"):
            aviso = QLabel("A conexão usa HTTP, sem criptografia: a chave e os pedidos trafegam em texto aberto.", objectName="aviso", wordWrap=True)
            layout.addWidget(aviso)

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        botoes.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        botoes.button(QDialogButtonBox.StandardButton.Save).setObjectName("primario")
        botoes.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        botoes.accepted.connect(self._salvar)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)

        self._resultado_teste.connect(self._mostrar_teste)

    def _com_procurar(self, campo: QLineEdit, acao) -> QHBoxLayout:  # noqa: ANN001
        linha = QHBoxLayout()
        botao = QPushButton("Procurar")
        botao.clicked.connect(acao)
        linha.addWidget(campo, 1)
        linha.addWidget(botao)
        return linha

    def _procurar_orca(self) -> None:
        caminho, _ = QFileDialog.getOpenFileName(self, "Localizar o OrcaSlicer", self.campo_orca.text(), "orca-slicer.exe (*.exe)")
        if caminho:
            self.campo_orca.setText(caminho)

    def _procurar_pasta(self) -> None:
        caminho = QFileDialog.getExistingDirectory(self, "Pasta das peças", self.campo_projetos.text())
        if caminho:
            self.campo_projetos.setText(caminho)

    def _testar(self) -> None:
        self.botao_testar.setEnabled(False)
        self.rotulo_teste.setText("Testando…")
        ia = replace(self.config.ia, endpoint=self.campo_endpoint.text().strip().rstrip("/"), api_key=self.campo_chave.text().strip(), modelo=self.campo_modelo.currentText().strip())

        def executar() -> None:
            try:
                self._resultado_teste.emit(ClienteIA(ia).listar_modelos(), "")
            except ErroIA as erro:
                self._resultado_teste.emit(None, str(erro))

        threading.Thread(target=executar, daemon=True).start()

    def _mostrar_teste(self, modelos: list[str] | None, erro: str) -> None:
        self.botao_testar.setEnabled(True)
        if modelos is None:
            self.rotulo_teste.setText(erro)
            return
        atual = self.campo_modelo.currentText()
        self.campo_modelo.clear()
        self.campo_modelo.addItems(modelos)
        self.campo_modelo.setCurrentText(atual)
        situacao = "disponível" if atual in modelos else "não está na lista"
        self.rotulo_teste.setText(f"Conectado: {len(modelos)} modelos. O modelo escolhido {situacao}.")

    def _salvar(self) -> None:
        configuracao.salvar_preferencias(
            self.campo_endpoint.text().strip().rstrip("/"),
            self.campo_chave.text().strip(),
            self.campo_modelo.currentText().strip(),
            self.campo_orca.text().strip(),
            self.campo_projetos.text().strip(),
        )
        self.accept()
