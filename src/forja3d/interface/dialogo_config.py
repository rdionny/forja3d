"""Configurações: modelo do chat, modelo de imagem, provedores de IA e caminhos do app."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

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
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from forja3d import configuracao
from forja3d.configuracao import PROVEDOR_PADRAO, ConfigIA, Configuracao, Provedor
from forja3d.ia.cliente import ClienteIA, ErroIA, eh_modelo_de_imagem

log = logging.getLogger(__name__)

TEMPO_LIMITE_TESTE_S = 30.0


class SeletorDeModelo(QWidget):
    """Provedor e modelo de uma função do app. O modelo é editável: a lista do servidor pode estar incompleta."""

    def __init__(self, filtro: Callable[[str], bool]) -> None:
        super().__init__()
        self.filtro = filtro
        self.trocar_modelo = False
        self.combo_provedor = QComboBox()
        self.combo_provedor.setMinimumWidth(150)
        self.combo_modelo = QComboBox(editable=True)
        linha = QHBoxLayout(self)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.addWidget(self.combo_provedor)
        linha.addWidget(self.combo_modelo, 1)

    def preencher(self, modelos: list[str]) -> None:
        atual = self.combo_modelo.currentText()
        # Um provedor sem nenhum nome reconhecido ainda mostra a lista inteira.
        lista = [m for m in modelos if self.filtro(m)] or modelos
        self.combo_modelo.clear()
        self.combo_modelo.addItems(lista)
        if self.trocar_modelo and atual not in lista and lista:
            self.combo_modelo.setCurrentIndex(0)
        else:
            self.combo_modelo.setCurrentText(atual)
        self.trocar_modelo = False


class DialogoConfig(QDialog):
    _modelos_carregados = Signal(object, object, str)

    def __init__(self, config: Configuracao, parent: QWidget | None = None, fabrica_cliente: Callable[..., Any] = ClienteIA) -> None:
        super().__init__(parent)
        self.config = config
        self._fabrica_cliente = fabrica_cliente
        self._provedores: list[dict[str, Any]] = [
            {"nome": p.nome, "endpoint": p.endpoint, "api_key": p.api_key, "modelos": None} for p in config.todos_os_provedores
        ]
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(720)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel("MODELOS", objectName="titulo"))
        layout.addLayout(self._montar_modelos())
        layout.addSpacing(6)
        layout.addWidget(QLabel("PROVEDORES", objectName="titulo"))
        layout.addLayout(self._montar_provedores())
        layout.addSpacing(6)
        layout.addWidget(QLabel("APLICATIVO", objectName="titulo"))
        layout.addLayout(self._montar_aplicativo())

        self.rotulo_erro = QLabel("", objectName="erro", wordWrap=True)
        self.rotulo_erro.hide()
        layout.addWidget(self.rotulo_erro)

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        botoes.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        botoes.button(QDialogButtonBox.StandardButton.Save).setObjectName("primario")
        botoes.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        botoes.accepted.connect(self._salvar)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)
        self.botoes = botoes

        self._modelos_carregados.connect(self._mostrar_modelos)
        self._aplicar_selecao_inicial()

    # ---------- Montagem ----------

    def _montar_modelos(self) -> QFormLayout:
        formulario = QFormLayout()
        formulario.setSpacing(8)
        self.seletor_chat = SeletorDeModelo(lambda m: not eh_modelo_de_imagem(m))
        self.seletor_imagem = SeletorDeModelo(eh_modelo_de_imagem)
        formulario.addRow("Modelo do chat", self.seletor_chat)
        formulario.addRow("Geração de imagem", self.seletor_imagem)
        return formulario

    def _montar_provedores(self) -> QHBoxLayout:
        area = QHBoxLayout()
        area.setSpacing(12)

        coluna = QVBoxLayout()
        coluna.setSpacing(6)
        self.lista = QListWidget(objectName="listaProvedores")
        self.lista.setFixedWidth(190)
        self.lista.setFixedHeight(128)
        self.lista.addItems([p["nome"] for p in self._provedores])
        self.lista.currentRowChanged.connect(self._selecionar_provedor)
        coluna.addWidget(self.lista)
        botoes = QHBoxLayout()
        self.botao_adicionar = QPushButton("Adicionar")
        self.botao_adicionar.clicked.connect(self._adicionar_provedor)
        self.botao_remover = QPushButton("Remover")
        self.botao_remover.clicked.connect(self._remover_provedor)
        botoes.addWidget(self.botao_adicionar)
        botoes.addWidget(self.botao_remover)
        coluna.addLayout(botoes)
        area.addLayout(coluna)

        detalhes = QVBoxLayout()
        detalhes.setSpacing(6)
        formulario = QFormLayout()
        formulario.setSpacing(8)
        self.campo_nome = QLineEdit()
        self.campo_nome.textEdited.connect(self._renomear_provedor)
        formulario.addRow("Nome", self.campo_nome)
        self.campo_endpoint = QLineEdit(placeholderText="https://servidor/v1")
        self.campo_endpoint.textEdited.connect(self._editar_endpoint)
        formulario.addRow("Endpoint", self.campo_endpoint)
        self.campo_chave = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.campo_chave.textEdited.connect(self._editar_chave)
        formulario.addRow("Chave da API", self.campo_chave)
        detalhes.addLayout(formulario)

        linha_teste = QHBoxLayout()
        self.botao_testar = QPushButton("Testar conexão")
        self.botao_testar.clicked.connect(self._testar)
        self.rotulo_teste = QLabel("", objectName="suave", wordWrap=True)
        linha_teste.addWidget(self.botao_testar)
        linha_teste.addWidget(self.rotulo_teste, 1)
        detalhes.addLayout(linha_teste)

        self.rotulo_aviso = QLabel("Este endpoint usa HTTP, sem criptografia: a chave e os pedidos trafegam em texto aberto.", objectName="aviso", wordWrap=True)
        detalhes.addWidget(self.rotulo_aviso)
        detalhes.addWidget(QLabel("As chaves ficam só neste computador, fora do git. O provedor precisa ser compatível com a API da OpenAI.", objectName="suave", wordWrap=True))
        area.addLayout(detalhes, 1)
        return area

    def _montar_aplicativo(self) -> QFormLayout:
        formulario = QFormLayout()
        formulario.setSpacing(8)
        self.campo_orca = QLineEdit(str(self.config.caminho_orca))
        formulario.addRow("OrcaSlicer", self._com_procurar(self.campo_orca, self._procurar_orca))
        self.campo_projetos = QLineEdit(str(self.config.pasta_projetos))
        formulario.addRow("Pasta das peças", self._com_procurar(self.campo_projetos, self._procurar_pasta))
        return formulario

    def _com_procurar(self, campo: QLineEdit, acao: Callable[[], None]) -> QHBoxLayout:
        linha = QHBoxLayout()
        botao = QPushButton("Procurar")
        botao.clicked.connect(acao)
        linha.addWidget(campo, 1)
        linha.addWidget(botao)
        return linha

    def _aplicar_selecao_inicial(self) -> None:
        ia = self.config.ia
        nomes = [p["nome"] for p in self._provedores]
        for seletor, provedor, modelo in (
            (self.seletor_chat, ia.provedor, ia.modelo),
            (self.seletor_imagem, ia.provedor_imagem, ia.modelo_imagem),
        ):
            seletor.combo_provedor.addItems(nomes)
            if provedor in nomes:
                seletor.combo_provedor.setCurrentIndex(nomes.index(provedor))
            seletor.combo_modelo.setCurrentText(modelo)
            seletor.combo_provedor.currentIndexChanged.connect(lambda _i, s=seletor: self._trocar_provedor_do_seletor(s))
        self.lista.setCurrentRow(0)
        vistos = []
        for seletor in (self.seletor_chat, self.seletor_imagem):
            provedor = self._provedor_do(seletor)
            if not any(provedor is v for v in vistos):
                vistos.append(provedor)
                self._carregar_modelos(provedor)

    # ---------- Provedores ----------

    @property
    def _seletores(self) -> tuple[SeletorDeModelo, SeletorDeModelo]:
        return (self.seletor_chat, self.seletor_imagem)

    def _provedor_do(self, seletor: SeletorDeModelo) -> dict[str, Any]:
        return self._provedores[seletor.combo_provedor.currentIndex()]

    def _provedor_selecionado(self) -> dict[str, Any] | None:
        linha = self.lista.currentRow()
        return self._provedores[linha] if 0 <= linha < len(self._provedores) else None

    def _selecionar_provedor(self, linha: int) -> None:
        provedor = self._provedor_selecionado()
        if provedor is None:
            return
        self.campo_nome.setText(provedor["nome"])
        self.campo_endpoint.setText(provedor["endpoint"])
        self.campo_chave.setText(provedor["api_key"])
        # O provedor Padrão guarda a configuração original do projeto: nunca some nem muda de nome.
        self.campo_nome.setReadOnly(linha == 0)
        self.botao_remover.setEnabled(linha != 0)
        self.rotulo_teste.clear()
        self._atualizar_aviso()

    def _atualizar_aviso(self) -> None:
        self.rotulo_aviso.setVisible(self.campo_endpoint.text().strip().startswith("http://"))

    def _renomear_provedor(self, nome: str) -> None:
        linha = self.lista.currentRow()
        self._provedores[linha]["nome"] = nome
        self.lista.item(linha).setText(nome)
        for seletor in self._seletores:
            seletor.combo_provedor.setItemText(linha, nome)

    def _editar_endpoint(self, endpoint: str) -> None:
        provedor = self._provedor_selecionado()
        provedor["endpoint"] = endpoint
        provedor["modelos"] = None
        self._atualizar_aviso()

    def _editar_chave(self, chave: str) -> None:
        provedor = self._provedor_selecionado()
        provedor["api_key"] = chave
        provedor["modelos"] = None

    def _adicionar_provedor(self) -> None:
        existentes = {p["nome"] for p in self._provedores}
        numero = len(self._provedores) + 1
        while f"Provedor {numero}" in existentes:
            numero += 1
        nome = f"Provedor {numero}"
        self._provedores.append({"nome": nome, "endpoint": "", "api_key": "", "modelos": None})
        self.lista.addItem(nome)
        for seletor in self._seletores:
            seletor.combo_provedor.addItem(nome)
        self.lista.setCurrentRow(len(self._provedores) - 1)
        self.campo_nome.setFocus()
        self.campo_nome.selectAll()

    def _remover_provedor(self) -> None:
        linha = self.lista.currentRow()
        if linha <= 0:
            return
        for seletor in self._seletores:
            if seletor.combo_provedor.currentIndex() == linha:
                seletor.combo_provedor.setCurrentIndex(0)
            seletor.combo_provedor.removeItem(linha)
        del self._provedores[linha]
        self.lista.takeItem(linha)
        self.lista.setCurrentRow(min(linha, len(self._provedores) - 1))

    # ---------- Modelos ----------

    def _trocar_provedor_do_seletor(self, seletor: SeletorDeModelo) -> None:
        provedor = self._provedor_do(seletor)
        seletor.trocar_modelo = True
        if provedor["modelos"] is not None:
            seletor.preencher(provedor["modelos"])
        else:
            self._carregar_modelos(provedor)

    def _testar(self) -> None:
        provedor = self._provedor_selecionado()
        if provedor is None or not provedor["endpoint"].strip():
            self.rotulo_teste.setText("Preencha o endpoint para testar.")
            return
        self.botao_testar.setEnabled(False)
        self.rotulo_teste.setText("Testando…")
        self._carregar_modelos(provedor)

    def _carregar_modelos(self, provedor: dict[str, Any]) -> None:
        endpoint = provedor["endpoint"].strip().rstrip("/")
        if not endpoint:
            return
        ia = ConfigIA(endpoint=endpoint, api_key=provedor["api_key"].strip(), modelo="", timeout_s=TEMPO_LIMITE_TESTE_S, max_tentativas_rede=0)
        fabrica = self._fabrica_cliente

        def executar() -> None:
            try:
                self._emitir(provedor, fabrica(ia).listar_modelos(), "")
            except ErroIA as erro:
                self._emitir(provedor, None, str(erro))
            except Exception:
                log.exception("Falha ao consultar os modelos do provedor")
                self._emitir(provedor, None, "Não consegui consultar esse provedor.")

        threading.Thread(target=executar, daemon=True).start()

    def _emitir(self, provedor: dict[str, Any], modelos: list[str] | None, erro: str) -> None:
        # O diálogo pode já ter sido fechado quando a resposta chega.
        try:
            self._modelos_carregados.emit(provedor, modelos, erro)
        except RuntimeError:
            log.debug("Resposta ignorada: o diálogo já foi fechado")

    def _mostrar_modelos(self, provedor: dict[str, Any], modelos: list[str] | None, erro: str) -> None:
        self.botao_testar.setEnabled(True)
        if not any(provedor is p for p in self._provedores):
            return
        selecionado = self._provedor_selecionado() is provedor
        if modelos is None:
            if selecionado:
                self.rotulo_teste.setText(erro)
            return
        provedor["modelos"] = modelos
        for seletor in self._seletores:
            if self._provedor_do(seletor) is provedor:
                seletor.preencher(modelos)
        if selecionado:
            de_imagem = sum(1 for m in modelos if eh_modelo_de_imagem(m))
            self.rotulo_teste.setText(f"Conectado: {len(modelos)} modelos, {de_imagem} de imagem.")

    # ---------- Caminhos ----------

    def _procurar_orca(self) -> None:
        caminho, _ = QFileDialog.getOpenFileName(self, "Localizar o OrcaSlicer", self.campo_orca.text(), "orca-slicer.exe (*.exe)")
        if caminho:
            self.campo_orca.setText(caminho)

    def _procurar_pasta(self) -> None:
        caminho = QFileDialog.getExistingDirectory(self, "Pasta das peças", self.campo_projetos.text())
        if caminho:
            self.campo_projetos.setText(caminho)

    # ---------- Salvar ----------

    def _problema(self) -> tuple[int | None, str] | None:
        nomes: set[str] = set()
        for indice, provedor in enumerate(self._provedores):
            nome = provedor["nome"].strip()
            if not nome:
                return indice, "Dê um nome ao provedor."
            if nome.casefold() in nomes:
                return indice, f"Já existe um provedor chamado {nome}."
            nomes.add(nome.casefold())
            if not provedor["endpoint"].strip().startswith(("http://", "https://")):
                return indice, f"O endpoint de {nome} precisa começar com http:// ou https://."
        if not self.seletor_chat.combo_modelo.currentText().strip():
            return None, "Escolha o modelo do chat."
        if not self.seletor_imagem.combo_modelo.currentText().strip():
            return None, "Escolha o modelo de geração de imagem."
        return None

    def _salvar(self) -> None:
        problema = self._problema()
        if problema is not None:
            indice, mensagem = problema
            if indice is not None:
                self.lista.setCurrentRow(indice)
            self.rotulo_erro.setText(mensagem)
            self.rotulo_erro.show()
            return
        limpos = [
            Provedor(p["nome"].strip(), p["endpoint"].strip().rstrip("/"), p["api_key"].strip()) for p in self._provedores
        ]
        configuracao.salvar_preferencias(
            limpos[0].endpoint,
            limpos[0].api_key,
            self.seletor_chat.combo_modelo.currentText().strip(),
            self.campo_orca.text().strip(),
            self.campo_projetos.text().strip(),
            self.seletor_imagem.combo_modelo.currentText().strip(),
            provedores=[p for p in limpos if p.nome != PROVEDOR_PADRAO],
            provedor=limpos[self.seletor_chat.combo_provedor.currentIndex()].nome,
            provedor_imagem=limpos[self.seletor_imagem.combo_provedor.currentIndex()].nome,
        )
        self.accept()
