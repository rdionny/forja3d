"""Diálogo que transforma uma imagem de referência em opções de conceito para escolher."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from forja3d.conceitos.fluxo import OPCOES_POR_RODADA, FluxoConceitos, OpcaoConceito, ResultadoOpcao, Rodada
from forja3d.ia.cliente import Cancelado, ErroIA, ModeloIndisponivel
from forja3d.projetos.armazenamento import Projeto

log = logging.getLogger(__name__)

LARGURA_IMAGEM = 500
ALTURA_IMAGEM = 280
TAMANHO_REFERENCIA = 130
ESPERA_AO_FECHAR_S = 2.0


def _rotulo_de_dados(texto: str, nome: str = "") -> QLabel:
    """Textos vindos da IA são dados: nunca são interpretados como HTML."""
    rotulo = QLabel(texto, wordWrap=True, objectName=nome)
    rotulo.setTextFormat(Qt.TextFormat.PlainText)
    return rotulo


class ImagemClicavel(QLabel):
    clicada = Signal()

    def mousePressEvent(self, evento) -> None:
        self.clicada.emit()
        super().mousePressEvent(evento)


class DialogoAmpliado(QDialog):
    def __init__(self, imagem: QPixmap, titulo: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(titulo)
        tela = parent.screen().availableGeometry()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        limite = Qt.AspectRatioMode.KeepAspectRatio
        layout.addWidget(QLabel(pixmap=imagem.scaled(tela.width() - 80, tela.height() - 120, limite, Qt.TransformationMode.SmoothTransformation)))


class CartaoConceito(QFrame):
    """Uma opção: a imagem com a peça montada e a vista explodida, e o que a compõe."""

    escolher = Signal(object)
    gerar_de_novo = Signal(object)

    def __init__(self, rodada: Rodada, numero: int, opcao: OpcaoConceito) -> None:
        super().__init__(objectName="cartaoConceito")
        self.rodada = rodada
        self.numero = numero
        self.opcao = opcao
        self.resultado: ResultadoOpcao | None = None
        self._pixmap: QPixmap | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        self.imagem = ImagemClicavel("Aguardando…", objectName="imagemConceito", alignment=Qt.AlignmentFlag.AlignCenter)
        self.imagem.setFixedSize(LARGURA_IMAGEM, ALTURA_IMAGEM)
        self.imagem.clicada.connect(self._ampliar)
        layout.addWidget(self.imagem)

        legendas = QHBoxLayout()
        for legenda in ("Montada", "Vista explodida"):
            legendas.addWidget(QLabel(legenda, objectName="suave", alignment=Qt.AlignmentFlag.AlignCenter), 1)
        layout.addLayout(legendas)

        layout.addWidget(_rotulo_de_dados(opcao.titulo, "tituloConceito"))
        layout.addWidget(_rotulo_de_dados(opcao.descricao))
        linhas = [f"{c.quantidade} × {c.nome}" + ("" if c.impresso else " (não impresso)") for c in opcao.componentes]
        layout.addWidget(_rotulo_de_dados("Componentes: " + "; ".join(linhas) + ".", "suave"))

        self.rotulo_aviso = _rotulo_de_dados("", "aviso")
        self.rotulo_aviso.hide()
        layout.addWidget(self.rotulo_aviso)
        layout.addStretch(1)

        botoes = QHBoxLayout()
        self.botao_escolher = QPushButton("Escolher esta", objectName="primario", enabled=False)
        self.botao_escolher.clicked.connect(lambda: self.escolher.emit(self))
        self.botao_de_novo = QPushButton("Gerar de novo", enabled=False)
        self.botao_de_novo.clicked.connect(lambda: self.gerar_de_novo.emit(self))
        botoes.addWidget(self.botao_escolher)
        botoes.addWidget(self.botao_de_novo)
        botoes.addStretch(1)
        layout.addLayout(botoes)

    @property
    def pronta(self) -> bool:
        return self.resultado is not None and self.resultado.imagem is not None

    def mostrar_progresso(self, texto: str) -> None:
        self.rotulo_aviso.hide()
        if self._pixmap is None:
            self.imagem.setText(f"{texto}…")
        else:
            self.imagem.setToolTip(f"{texto}…")

    def encerrar_progresso(self) -> None:
        """Depois de uma falha ou de um cancelamento, a opção não pode continuar dizendo que está gerando."""
        if self._pixmap is None:
            self.imagem.setText("Sem imagem")
        self.imagem.setToolTip("Clique para ampliar" if self._pixmap is not None else "")

    def mostrar_resultado(self, resultado: ResultadoOpcao) -> None:
        self.resultado = resultado
        self.imagem.setToolTip("Clique para ampliar")
        if resultado.imagem is not None:
            self._pixmap = QPixmap(str(resultado.imagem))
            self.imagem.setPixmap(
                self._pixmap.scaled(LARGURA_IMAGEM - 4, ALTURA_IMAGEM - 4, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
            self.imagem.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self._pixmap = None
            self.imagem.clear()
            self.imagem.setText("Sem imagem")
        if resultado.erro:
            self._avisar(f"Não consegui gerar esta opção: {resultado.erro}", "erro")
        elif not resultado.aprovada:
            self._avisar(f"Os dois lados da imagem podem não bater. {resultado.observacao}".strip(), "aviso")
        else:
            self.rotulo_aviso.hide()

    def _avisar(self, texto: str, estilo: str) -> None:
        self.rotulo_aviso.setText(texto)
        self.rotulo_aviso.setObjectName(estilo)
        self.rotulo_aviso.style().unpolish(self.rotulo_aviso)
        self.rotulo_aviso.style().polish(self.rotulo_aviso)
        self.rotulo_aviso.show()

    def _ampliar(self) -> None:
        if self._pixmap is not None:
            DialogoAmpliado(self._pixmap, self.opcao.titulo, self).exec()


class DialogoConceitos(QDialog):
    _estado = Signal(str)
    _rodada_criada = Signal(object)
    _progresso = Signal(object, int, str)
    _opcao_pronta = Signal(object)
    _falhou = Signal(str, bool)
    _terminou = Signal()

    def __init__(self, referencia: bytes, cliente: Any, obter_projeto: Callable[[], Projeto], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.referencia = referencia
        self.cliente = cliente
        self.obter_projeto = obter_projeto
        self.conceito: dict[str, Any] | None = None
        self.quer_configuracoes = False
        self._cancelar = threading.Event()
        self._fluxo: FluxoConceitos | None = None
        self._cartoes: list[CartaoConceito] = []
        self._ativas = 0
        self._linhas: list[threading.Thread] = []
        self._inicio = 0.0
        self._texto_estado = ""

        self.setWindowTitle("Imagem de referência")
        self.setMinimumSize(1120, 640)
        tela = self.screen().availableGeometry()
        self.resize(min(1200, tela.width() - 40), min(860, tela.height() - 60))
        self._montar()

        self._estado.connect(self._definir_estado)
        self._rodada_criada.connect(self._mostrar_rodada)
        self._progresso.connect(self._mostrar_progresso)
        self._opcao_pronta.connect(self._mostrar_opcao)
        self._falhou.connect(self._mostrar_falha)
        self._terminou.connect(self._ao_terminar)
        self._relogio = QTimer(self, interval=1000)
        self._relogio.timeout.connect(self._atualizar_estado)
        self._atualizar_botoes()

    def _montar(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        topo = QHBoxLayout()
        topo.setSpacing(12)
        miniatura = QLabel(objectName="imagemConceito", alignment=Qt.AlignmentFlag.AlignCenter)
        miniatura.setFixedSize(TAMANHO_REFERENCIA, TAMANHO_REFERENCIA)
        pixmap = QPixmap()
        pixmap.loadFromData(self.referencia)
        miniatura.setPixmap(
            pixmap.scaled(TAMANHO_REFERENCIA - 4, TAMANHO_REFERENCIA - 4, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
        topo.addWidget(miniatura)

        coluna = QVBoxLayout()
        coluna.setSpacing(6)
        coluna.addWidget(QLabel("O que você quer a partir desta imagem?"))
        self.campo_pedido = QPlainTextEdit(placeholderText="Opcional. Por exemplo: uma caixa parecida para guardar um carregador.")
        self.campo_pedido.setFixedHeight(58)
        coluna.addWidget(self.campo_pedido)
        acoes = QHBoxLayout()
        self.botao_gerar = QPushButton("Gerar opções", objectName="primario")
        self.botao_gerar.clicked.connect(self._gerar_opcoes)
        self.rotulo_estado = QLabel("", objectName="etapa")
        acoes.addWidget(self.botao_gerar)
        acoes.addWidget(self.rotulo_estado, 1)
        coluna.addLayout(acoes)
        topo.addLayout(coluna, 1)
        layout.addLayout(topo)

        layout.addWidget(
            QLabel(
                "As imagens são só uma ilustração do conceito: não têm medidas reais. Depois da escolha, o agente pergunta o que faltar e modela a peça.",
                objectName="suave",
                wordWrap=True,
            )
        )

        self.rolagem = QScrollArea(widgetResizable=True)
        conteudo = QWidget()
        self.grade = QGridLayout(conteudo)
        self.grade.setContentsMargins(0, 0, 4, 0)
        self.grade.setSpacing(12)
        self.grade.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.rolagem.setWidget(conteudo)
        layout.addWidget(self.rolagem, 1)

        self.rotulo_erro = _rotulo_de_dados("", "erro")
        self.rotulo_erro.hide()
        layout.addWidget(self.rotulo_erro)

        rodape = QHBoxLayout()
        self.botao_config = QPushButton("Abrir Configurações")
        self.botao_config.clicked.connect(self._abrir_configuracoes)
        self.botao_config.hide()
        self.botao_mais = QPushButton("Mais 2 opções")
        self.botao_mais.clicked.connect(self._mais_opcoes)
        botao_cancelar = QPushButton("Cancelar")
        botao_cancelar.clicked.connect(self.reject)
        rodape.addWidget(self.botao_config)
        rodape.addStretch(1)
        rodape.addWidget(self.botao_mais)
        rodape.addWidget(botao_cancelar)
        layout.addLayout(rodape)

    # ---------- Ações ----------

    def _gerar_opcoes(self) -> None:
        self._iniciar_rodada(self.campo_pedido.toPlainText(), [])

    def _mais_opcoes(self) -> None:
        self._iniciar_rodada(self.campo_pedido.toPlainText(), [c.opcao.titulo for c in self._cartoes])

    def _iniciar_rodada(self, pedido: str, ja_mostrados: list[str]) -> None:
        if self._ativas:
            return
        if self._fluxo is None:
            self._fluxo = FluxoConceitos(self.cliente, self.obter_projeto(), self.referencia, self._cancelar)
        fluxo = self._fluxo

        def trabalho() -> None:
            self._emitir(self._estado, "Analisando a imagem")
            rodada = fluxo.iniciar_rodada(pedido, ja_mostrados)
            if rodada.analise.recusa:
                self._emitir(self._falhou, f"Não posso seguir com este pedido: {rodada.analise.recusa}", False)
                return
            self._emitir(self._rodada_criada, rodada)
            self._emitir(self._estado, "Gerando as opções")
            fluxo.produzir_rodada(
                rodada,
                lambda resultado: self._emitir(self._opcao_pronta, resultado),
                lambda numero, texto: self._emitir(self._progresso, rodada, numero, texto),
            )

        self._rodar(trabalho)

    def _gerar_de_novo(self, cartao: CartaoConceito) -> None:
        if self._ativas or self._fluxo is None:
            return
        fluxo = self._fluxo

        def trabalho() -> None:
            self._emitir(self._estado, f"Gerando de novo: {cartao.opcao.titulo}")
            resultado = fluxo.produzir_opcao(
                cartao.rodada, cartao.numero, lambda numero, texto: self._emitir(self._progresso, cartao.rodada, numero, texto)
            )
            self._emitir(self._opcao_pronta, resultado)

        self._rodar(trabalho)

    def _escolher(self, cartao: CartaoConceito) -> None:
        if self._ativas or self._fluxo is None or not cartao.pronta or self._quantidade_prontas() < OPCOES_POR_RODADA:
            return
        self.conceito = self._fluxo.escolher(cartao.resultado)
        self.accept()

    def _abrir_configuracoes(self) -> None:
        self.quer_configuracoes = True
        self.reject()

    # ---------- Trabalho em segundo plano ----------

    def _rodar(self, trabalho: Callable[[], None]) -> None:
        self.rotulo_erro.hide()
        self.botao_config.hide()
        self._ativas += 1
        self._inicio = time.monotonic()
        self._relogio.start()
        self._atualizar_botoes()

        def executar() -> None:
            try:
                trabalho()
            except Cancelado:
                pass
            except ModeloIndisponivel as erro:
                self._emitir(self._falhou, str(erro), True)
            except ErroIA as erro:
                self._emitir(self._falhou, str(erro), False)
            except Exception:
                log.exception("Falha inesperada ao gerar os conceitos")
                self._emitir(self._falhou, "Algo deu errado ao gerar as opções. Os detalhes foram para o log.", False)
            finally:
                self._emitir(self._terminou)

        linha = threading.Thread(target=executar, daemon=True)
        self._linhas.append(linha)
        linha.start()

    @staticmethod
    def _emitir(sinal: Any, *argumentos: Any) -> None:
        # O diálogo pode já ter sido destruído quando a thread termina.
        try:
            sinal.emit(*argumentos)
        except RuntimeError:
            log.debug("Sinal ignorado: o diálogo já foi fechado")

    def done(self, resultado: int) -> None:
        self._cancelar.set()
        self._relogio.stop()
        for linha in self._linhas:
            linha.join(ESPERA_AO_FECHAR_S)
        super().done(resultado)

    # ---------- Tela ----------

    def _quantidade_prontas(self) -> int:
        return sum(1 for c in self._cartoes if c.pronta)

    def _atualizar_botoes(self) -> None:
        livre = self._ativas == 0
        liberar_escolha = livre and self._quantidade_prontas() >= OPCOES_POR_RODADA
        self.botao_gerar.setEnabled(livre)
        self.botao_gerar.setText("Refazer com este pedido" if self._cartoes else "Gerar opções")
        self.botao_mais.setEnabled(livre and bool(self._cartoes))
        self.campo_pedido.setReadOnly(not livre)
        for cartao in self._cartoes:
            cartao.botao_escolher.setEnabled(liberar_escolha and cartao.pronta)
            cartao.botao_de_novo.setEnabled(livre)

    def _definir_estado(self, texto: str) -> None:
        self._texto_estado = texto
        self._atualizar_estado()

    def _atualizar_estado(self) -> None:
        if self._ativas and self._texto_estado:
            self.rotulo_estado.setText(f"{self._texto_estado}… {int(time.monotonic() - self._inicio)} s")
        else:
            self.rotulo_estado.setText("")

    def _mostrar_rodada(self, rodada: Rodada) -> None:
        for numero, opcao in enumerate(rodada.analise.opcoes, start=1):
            cartao = CartaoConceito(rodada, numero, opcao)
            cartao.escolher.connect(self._escolher)
            cartao.gerar_de_novo.connect(self._gerar_de_novo)
            posicao = len(self._cartoes)
            self.grade.addWidget(cartao, posicao // 2, posicao % 2)
            self._cartoes.append(cartao)
        self._atualizar_botoes()
        QTimer.singleShot(50, self, self._ir_para_o_fim)

    def _ir_para_o_fim(self) -> None:
        barra = self.rolagem.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _cartao(self, rodada: Rodada, numero: int) -> CartaoConceito | None:
        return next((c for c in self._cartoes if c.rodada is rodada and c.numero == numero), None)

    def _mostrar_progresso(self, rodada: Rodada, numero: int, texto: str) -> None:
        cartao = self._cartao(rodada, numero)
        if cartao is not None:
            cartao.mostrar_progresso(texto)

    def _mostrar_opcao(self, resultado: ResultadoOpcao) -> None:
        cartao = self._cartao(resultado.rodada, resultado.numero)
        if cartao is not None:
            cartao.mostrar_resultado(resultado)
        self._atualizar_botoes()

    def _mostrar_falha(self, texto: str, modelo_indisponivel: bool) -> None:
        self.rotulo_erro.setText(texto)
        self.rotulo_erro.show()
        self.botao_config.setVisible(modelo_indisponivel)

    def _ao_terminar(self) -> None:
        self._ativas -= 1
        if not self._ativas:
            self._relogio.stop()
            self._texto_estado = ""
            self._atualizar_estado()
            for cartao in self._cartoes:
                cartao.encerrar_progresso()
        self._atualizar_botoes()
        prontas = self._quantidade_prontas()
        if not self._ativas and self._cartoes and prontas < OPCOES_POR_RODADA and self.rotulo_erro.isHidden():
            self._mostrar_falha("Preciso de pelo menos 2 imagens prontas para você escolher. Clique em Gerar de novo na opção que falhou.", False)
