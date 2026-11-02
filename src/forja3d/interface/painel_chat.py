"""Conversa com o agente."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayoutItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from forja3d.conceitos.referencia import EXTENSOES
from forja3d.interface import tema
from forja3d.interface.leitura import LeitorEmVoz, texto_para_fala

LARGURA_MINIATURA = 320
ALTURA_DA_EDICAO = 96
TEMPO_DO_AVISO_DE_COPIA_MS = 1500
SUGESTOES = (
    "Suporte em L para parede, em PLA: base de 40 × 30 × 4 mm, parede de 30 mm de altura e 2 furos para parafuso M4 na base",
    "Caixa de 80 × 60 × 30 mm com tampa de encaixe, paredes de 2 mm, em PLA",
    "Gancho de parede para toalha, com 2 furos para parafuso M4, em PETG",
)


def imagem_do_mime(mime: QMimeData) -> bytes | Path | None:
    """Arquivo local ou imagem copiada. Nunca busca nada na internet."""
    for url in mime.urls():
        if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in EXTENSOES:
            return Path(url.toLocalFile())
    if mime.hasImage():
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        mime.imageData().save(buffer, "PNG")
        return bytes(buffer.data())
    return None


class Bolha(QFrame):
    """Uma mensagem, com a barra de ações embaixo. `numero_pedido` diz a qual pedido do usuário ela pertence."""

    leitura_pedida = Signal(object)
    edicao_pedida = Signal(object)
    reenvio_pedido = Signal(object)
    edicao_concluida = Signal(object, str)

    def __init__(self, papel: str, texto: str, imagem: Path | None = None, numero_pedido: int = -1, editavel: bool = True) -> None:
        nomes = {"usuario": "bolhaUsuario", "agente": "bolhaAgente", "erro": "bolhaErro"}
        super().__init__(objectName=nomes[papel])
        self.papel = papel
        self.texto = texto.strip()
        self.numero_pedido = numero_pedido
        self.editavel = editavel
        self.linha: QHBoxLayout | None = None
        self.campo_edicao: CampoTexto | None = None
        self.caixa_edicao: QWidget | None = None
        self.botao_concluir: QPushButton | None = None
        self._ocupado = False
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 9, 8, 5)
        self._layout.setSpacing(4)
        if imagem is not None and imagem.exists():
            miniatura = QPixmap(str(imagem)).scaledToWidth(LARGURA_MINIATURA, Qt.TransformationMode.SmoothTransformation)
            self._layout.addWidget(QLabel(pixmap=miniatura))
        self.rotulo = QLabel(texto, wordWrap=True)
        self.rotulo.setTextFormat(Qt.TextFormat.PlainText if papel == "usuario" else Qt.TextFormat.MarkdownText)
        self.rotulo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rotulo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout.addWidget(self.rotulo)
        self._layout.addWidget(self._criar_barra())

    def _criar_barra(self) -> QWidget:
        self.barra = QWidget()
        linha = QHBoxLayout(self.barra)
        linha.setContentsMargins(0, 0, 0, 0)
        linha.setSpacing(2)
        do_pedido = "" if self.papel == "usuario" else " a pergunta que gerou esta resposta"
        acoes = [("copiar", "copiar", "Copiar o texto", self._copiar)]
        # Um erro não tem o que refazer por si: quem reenvia é o pedido, logo acima.
        if self.papel != "erro" and self.numero_pedido >= 0:
            if self.editavel:
                acoes.append(("editar", "editar", f"Editar{do_pedido or ' esta mensagem'}", lambda: self.edicao_pedida.emit(self)))
            acoes.append(("reenviar", "reenviar", f"Enviar de novo{do_pedido}", lambda: self.reenvio_pedido.emit(self)))
        acoes.append(("ler", "ouvir", "Ler em voz alta", lambda: self.leitura_pedida.emit(self)))
        if self.papel == "usuario":
            linha.addStretch(1)
        self.botoes: dict[str, QPushButton] = {}
        for chave, icone, dica, acao in acoes:
            botao = QPushButton(objectName="acaoMensagem", toolTip=dica, accessibleName=dica)
            botao.setIcon(tema.icone(icone))
            botao.setIconSize(QSize(15, 15))
            botao.setFixedSize(24, 24)
            botao.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            botao.clicked.connect(acao)
            self.botoes[chave] = botao
            linha.addWidget(botao)
        if self.papel != "usuario":
            linha.addStretch(1)
        return self.barra

    def _copiar(self) -> None:
        QGuiApplication.clipboard().setText(self.texto)
        self.botoes["copiar"].setIcon(tema.icone("copiado"))
        self.botoes["copiar"].setToolTip("Copiado")
        QTimer.singleShot(TEMPO_DO_AVISO_DE_COPIA_MS, self, self._voltar_icone_de_copia)

    def _voltar_icone_de_copia(self) -> None:
        self.botoes["copiar"].setIcon(tema.icone("copiar"))
        self.botoes["copiar"].setToolTip("Copiar o texto")

    def marcar_leitura(self, lendo: bool) -> None:
        self.botoes["ler"].setIcon(tema.icone("parar" if lendo else "ouvir"))
        self.botoes["ler"].setToolTip("Parar de ler" if lendo else "Ler em voz alta")

    def definir_ocupado(self, ocupado: bool) -> None:
        # Copiar e ouvir não mexem na conversa: continuam valendo com o agente trabalhando.
        self._ocupado = ocupado
        for chave in ("editar", "reenviar"):
            if chave in self.botoes:
                self.botoes[chave].setEnabled(not ocupado)
        if self.botao_concluir is not None:
            self.botao_concluir.setEnabled(not ocupado)

    def iniciar_edicao(self) -> None:
        if self.caixa_edicao is None:
            self._criar_edicao()
        self.campo_edicao.setPlainText(self.texto)
        self.campo_edicao.moveCursor(QTextCursor.MoveOperation.End)
        self.rotulo.hide()
        self.barra.hide()
        self.caixa_edicao.show()
        self.botao_concluir.setEnabled(not self._ocupado)
        self.campo_edicao.setFocus()

    def _criar_edicao(self) -> None:
        self.caixa_edicao = QWidget()
        coluna = QVBoxLayout(self.caixa_edicao)
        coluna.setContentsMargins(0, 0, 0, 4)
        coluna.setSpacing(6)
        self.campo_edicao = CampoTexto()
        self.campo_edicao.setFixedHeight(ALTURA_DA_EDICAO)
        self.campo_edicao.enviar.connect(self._concluir_edicao)
        atalho = QShortcut(QKeySequence("Esc"), self.campo_edicao, context=Qt.ShortcutContext.WidgetShortcut)
        atalho.activated.connect(self.cancelar_edicao)
        coluna.addWidget(self.campo_edicao)
        linha = QHBoxLayout()
        linha.addStretch(1)
        cancelar = QPushButton("Cancelar", objectName="plano")
        cancelar.clicked.connect(self.cancelar_edicao)
        self.botao_concluir = QPushButton("Enviar", objectName="primario")
        self.botao_concluir.clicked.connect(self._concluir_edicao)
        linha.addWidget(cancelar)
        linha.addWidget(self.botao_concluir)
        coluna.addLayout(linha)
        self._layout.addWidget(self.caixa_edicao)

    def cancelar_edicao(self) -> None:
        self.caixa_edicao.hide()
        self.rotulo.show()
        self.barra.show()

    def _concluir_edicao(self) -> None:
        novo = self.campo_edicao.toPlainText().strip()
        if novo and not self._ocupado:
            self.edicao_concluida.emit(self, novo)


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
    imagem_recebida = Signal(object)

    def keyPressEvent(self, evento) -> None:  # noqa: ANN001, N802 - nome definido pelo Qt.
        enter = evento.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not evento.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.enviar.emit()
            return
        super().keyPressEvent(evento)

    def canInsertFromMimeData(self, origem: QMimeData) -> bool:  # noqa: N802 - nome definido pelo Qt.
        return imagem_do_mime(origem) is not None or super().canInsertFromMimeData(origem)

    def insertFromMimeData(self, origem: QMimeData) -> None:  # noqa: N802 - nome definido pelo Qt.
        # Colar e soltar passam por aqui.
        imagem = imagem_do_mime(origem)
        if imagem is not None:
            self.imagem_recebida.emit(imagem)
        else:
            super().insertFromMimeData(origem)


class PainelChat(QWidget):
    enviado = Signal(str)
    cancelar = Signal()
    imagem_pedida = Signal()
    imagem_recebida = Signal(object)
    # Número do pedido e o texto novo, ou nada se é para enviar igual.
    refazer_pedido = Signal(int, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="painel")
        self.setAcceptDrops(True)
        self.referencia: str | None = None
        self.bolhas: list[Bolha] = []
        self.leitor = LeitorEmVoz(self)
        self.leitor.terminou.connect(lambda: self._marcar_leitura(None))
        self.leitor.falhou.connect(self._ao_falhar_leitura)
        self._lendo: Bolha | None = None
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

        self.botao_imagem = QPushButton(
            "Imagem de referência",
            toolTip="Envie uma imagem para ver opções de conceito, com a peça montada e a vista explodida. Também dá para arrastar ou colar.",
        )
        self.botao_imagem.clicked.connect(self.imagem_pedida)
        linha_imagem = QHBoxLayout()
        linha_imagem.addWidget(self.botao_imagem)
        self.chip_referencia = QFrame(objectName="chipReferencia")
        linha_chip = QHBoxLayout(self.chip_referencia)
        linha_chip.setContentsMargins(8, 2, 2, 2)
        linha_chip.setSpacing(4)
        self.rotulo_referencia = QLabel("")
        self.rotulo_referencia.setTextFormat(Qt.TextFormat.PlainText)
        botao_tirar = QPushButton("✕", objectName="plano", toolTip="Não usar esta parte como referência")
        botao_tirar.clicked.connect(self.limpar_referencia)
        linha_chip.addWidget(self.rotulo_referencia)
        linha_chip.addWidget(botao_tirar)
        self.chip_referencia.hide()
        linha_imagem.addWidget(self.chip_referencia)
        linha_imagem.addStretch(1)
        layout.addLayout(linha_imagem)

        entrada = QHBoxLayout()
        entrada.setSpacing(8)
        self.campo = CampoTexto(placeholderText="Descreva a peça ou peça um ajuste…")
        self.campo.setFixedHeight(66)
        self.campo.enviar.connect(self._enviar)
        self.campo.imagem_recebida.connect(self._receber_imagem)
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

    def definir_referencia(self, parte: str) -> None:
        """A próxima mensagem vai falar desta parte."""
        self.referencia = parte
        self.rotulo_referencia.setText(f"Sobre a parte: {parte}")
        self.chip_referencia.show()
        self.campo.setFocus()

    def limpar_referencia(self) -> None:
        self.referencia = None
        self.chip_referencia.hide()

    def _receber_imagem(self, imagem: bytes | Path) -> None:
        if not self._ocupado:
            self.imagem_recebida.emit(imagem)

    def dragEnterEvent(self, evento) -> None:  # noqa: N802 - nome definido pelo Qt.
        if not self._ocupado and imagem_do_mime(evento.mimeData()) is not None:
            evento.acceptProposedAction()

    def dropEvent(self, evento) -> None:  # noqa: N802 - nome definido pelo Qt.
        imagem = imagem_do_mime(evento.mimeData())
        if imagem is not None:
            evento.acceptProposedAction()
            self._receber_imagem(imagem)

    def adicionar(self, papel: str, texto: str, imagem: Path | None = None) -> None:
        self.vazio.hide()
        pedidos = self.pedidos()
        if papel == "usuario":
            numero, editavel = len(pedidos), imagem is None
        else:
            numero, editavel = len(pedidos) - 1, bool(pedidos) and pedidos[-1].editavel
        bolha = Bolha(papel, texto, imagem, numero, editavel)
        bolha.leitura_pedida.connect(self._alternar_leitura)
        bolha.edicao_pedida.connect(self._editar_pedido)
        bolha.reenvio_pedido.connect(lambda b: self.refazer_pedido.emit(b.numero_pedido, None))
        bolha.edicao_concluida.connect(lambda b, novo: self.refazer_pedido.emit(b.numero_pedido, novo))
        bolha.definir_ocupado(self._ocupado)
        self.bolhas.append(bolha)
        linha = QHBoxLayout()
        linha.setContentsMargins(0, 0, 0, 0)
        if papel == "usuario":
            linha.addSpacing(36)
            linha.addWidget(bolha, 1)
        else:
            linha.addWidget(bolha, 1)
            linha.addSpacing(12)
        bolha.linha = linha
        self.lista.insertLayout(self.lista.count() - 1, linha)
        self._rolar_para_o_fim()

    def pedidos(self) -> list[Bolha]:
        return [b for b in self.bolhas if b.papel == "usuario"]

    def _editar_pedido(self, bolha: Bolha) -> None:
        # Editar uma resposta é editar a pergunta que a gerou.
        pedido = self.pedidos()[bolha.numero_pedido]
        pedido.iniciar_edicao()
        QTimer.singleShot(0, self, lambda: self.rolagem.ensureWidgetVisible(pedido))

    def remover_desde(self, numero: int) -> None:
        """Tira da tela esse pedido e tudo o que veio depois dele."""
        self.parar_leitura()
        inicio = self.lista.indexOf(self.pedidos()[numero].linha)
        while self.lista.count() - 1 > inicio:
            self._descartar(self.lista.takeAt(inicio))
        self.bolhas = [b for b in self.bolhas if b.numero_pedido < numero]

    def _alternar_leitura(self, bolha: Bolha) -> None:
        if self._lendo is bolha:
            self.parar_leitura()
            return
        self._marcar_leitura(bolha)
        self.leitor.ler(texto_para_fala(bolha.texto))

    def parar_leitura(self) -> None:
        self.leitor.parar()
        self._marcar_leitura(None)

    def _marcar_leitura(self, bolha: Bolha | None) -> None:
        if self._lendo is not None:
            self._lendo.marcar_leitura(False)
        self._lendo = bolha
        if bolha is not None:
            bolha.marcar_leitura(True)

    def _ao_falhar_leitura(self, motivo: str) -> None:
        self._marcar_leitura(None)
        self.adicionar_evento(motivo)

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
        self.parar_leitura()
        self.bolhas = []
        while self.lista.count() > 1:
            self._descartar(self.lista.takeAt(0))
        self.lista.insertWidget(0, self.vazio)
        self.vazio.show()

    def _descartar(self, item: QLayoutItem) -> None:
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

    def definir_ocupado(self, ocupado: bool) -> None:
        self._ocupado = ocupado
        self.botao_enviar.setEnabled(not ocupado)
        self.botao_imagem.setEnabled(not ocupado)
        for bolha in self.bolhas:
            bolha.definir_ocupado(ocupado)
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
