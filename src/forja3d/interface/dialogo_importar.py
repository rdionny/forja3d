"""Escolhe o tamanho e a orientação de um modelo baixado, antes de ele ir para a mesa."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from forja3d.importacao.preparo import (
    ModeloLido,
    OpcoesDoModelo,
    altura_maxima,
    altura_sugerida,
    desenhar_previa,
    nome_da_parte,
    tamanho_girado,
)
from forja3d.interface import tema
from forja3d.interface.dialogo_cores import FUNDO_LIGADO_ATE_MM, EditorDeCores

LADO_DA_PREVIA = 340
ALTURA_MINIMA_MM = 1.0
GIROS = {
    "frente": ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    "lado": ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
    "mesa": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
}


def _rgb(cor: str) -> tuple[int, int, int]:
    return tuple(int(cor[i : i + 2], 16) for i in (1, 3, 5))


class DialogoImportar(QDialog):
    def __init__(self, modelo: ModeloLido, perfil: dict[str, Any], ocupados: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.modelo = modelo
        self.perfil = perfil
        self.ocupados = ocupados
        self.rotacao = np.eye(3)
        self.amostra = np.load(modelo.previa)
        self.setWindowTitle("Importar modelo 3D")

        area = QHBoxLayout(self)
        area.setSpacing(16)
        area.addLayout(self._montar_previa())
        area.addLayout(self._montar_opcoes(), 1)
        self.setMinimumWidth(720)

        self._aplicar_altura(altura_sugerida(modelo.tamanho_mm, self.rotacao, perfil))
        self.campo_altura.valueChanged.connect(self._atualizar_medidas)
        self.editor.mudou.connect(self._desenhar)
        self._desenhar()

    # ---------- Montagem ----------

    def _montar_previa(self) -> QVBoxLayout:
        coluna = QVBoxLayout()
        coluna.setSpacing(8)
        self.rotulo_previa = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.rotulo_previa.setFixedSize(LADO_DA_PREVIA, LADO_DA_PREVIA)
        coluna.addWidget(self.rotulo_previa)
        botoes = QHBoxLayout()
        botoes.setSpacing(6)
        self.botoes_giro: dict[str, QPushButton] = {}
        for chave, texto, dica in (
            ("frente", "Tombar p/ frente", "Gira 90° em torno de X. Use se o modelo veio deitado."),
            ("lado", "Tombar de lado", "Gira 90° em torno de Y."),
            ("mesa", "Girar na mesa", "Gira 90° em torno de Z."),
        ):
            botao = QPushButton(texto, toolTip=dica)
            botao.clicked.connect(lambda _marcado=False, c=chave: self.girar(c))
            botoes.addWidget(botao)
            self.botoes_giro[chave] = botao
        coluna.addLayout(botoes)
        return coluna

    def _montar_opcoes(self) -> QVBoxLayout:
        coluna = QVBoxLayout()
        coluna.setSpacing(10)
        partes = self.modelo.partes_de_cor
        titulo = f"{len(partes)} arquivos, um para cada cor" if partes else self.modelo.arquivo.name
        coluna.addWidget(QLabel(titulo, objectName="marca", wordWrap=True, textFormat=Qt.TextFormat.PlainText))
        formulario = QFormLayout()
        formulario.setSpacing(8)
        self.campo_nome = QLineEdit(nome_da_parte(self.modelo.nome_comum or self.modelo.arquivo.stem, self.ocupados))
        formulario.addRow("Nome", self.campo_nome)
        self.campo_altura = QDoubleSpinBox(decimals=1, singleStep=1.0, suffix=" mm", toolTip="As outras medidas acompanham, na mesma proporção")
        formulario.addRow("Altura", self.campo_altura)
        self.rotulo_medidas = QLabel("", objectName="medida")
        formulario.addRow("Na mesa", self.rotulo_medidas)
        coluna.addLayout(formulario)
        self.editor = self._criar_editor_de_cores()
        coluna.addWidget(self.editor)
        for texto, nome in self._avisos():
            coluna.addWidget(QLabel(texto, objectName=nome, wordWrap=True))
        coluna.addStretch(1)

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botoes.button(QDialogButtonBox.StandardButton.Ok).setText("Colocar na mesa")
        botoes.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primario")
        botoes.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        botoes.accepted.connect(self.accept)
        botoes.rejected.connect(self.reject)
        coluna.addWidget(botoes)
        self.botoes = botoes
        return coluna

    def _criar_editor_de_cores(self) -> EditorDeCores:
        partes = self.modelo.partes_de_cor
        altura = float(self.modelo.tamanho_mm[2])
        if not partes:
            return EditorDeCores({"": None}, None, altura)
        # Só serve de fundo quem encosta na mesa. As outras partes ficariam soltas no ar.
        na_mesa = [p for p in partes if p.get("z_inicial", 0.0) <= 0.01] or partes
        maior = max(na_mesa, key=lambda p: p.get("volume_mm3", 0.0))
        # Peça baixa é o caso do logo: a técnica de colorir só a primeira camada já vem ligada.
        fundo = maior["nome"] if altura <= FUNDO_LIGADO_ATE_MM else None
        return EditorDeCores({p["nome"]: p.get("cor") for p in partes}, fundo, altura, candidatos_a_fundo=[p["nome"] for p in na_mesa])

    def _avisos(self) -> list[tuple[str, str]]:
        modelo = self.modelo
        avisos = [("Só a forma vai para a impressão. Cor e textura ficam de fora, e detalhe que só existe na textura não aparece na peça.", "suave")]
        if modelo.triangulos < modelo.triangulos_originais:
            avisos.append((f"Reduzi de {modelo.triangulos_originais:,} para {modelo.triangulos:,} triângulos. O bico de 0,4 mm não aproveitaria mais do que isso.".replace(",", "."), "suave"))
        if not modelo.fechada:
            avisos.append(("A malha tem buracos que não consegui fechar. O fatiador tenta consertar, mas a peça pode sair com defeito.", "aviso"))
        if modelo.corpos > 1 and not modelo.partes_de_cor:
            avisos.append((f"O modelo tem {modelo.corpos} corpos separados. O que não encostar em nada sai no ar.", "aviso"))
        return avisos

    # ---------- Tamanho e giro ----------

    def _altura_base(self) -> float:
        return float(tamanho_girado(self.modelo.tamanho_mm, self.rotacao)[2])

    def _aplicar_altura(self, altura: float) -> None:
        """O máximo muda com o giro: o modelo inteiro precisa continuar cabendo na mesa."""
        # Para baixo: arredondar para cima passaria do limite por um décimo.
        maxima = max(ALTURA_MINIMA_MM, math.floor(altura_maxima(self.modelo.tamanho_mm, self.rotacao, self.perfil) * 10) / 10)
        self.campo_altura.setRange(ALTURA_MINIMA_MM, maxima)
        self.campo_altura.setValue(altura)
        self._atualizar_medidas()

    def _escala(self) -> float:
        return self.campo_altura.value() / self._altura_base()

    def _atualizar_medidas(self) -> None:
        x, y, z = tamanho_girado(self.modelo.tamanho_mm, self.rotacao) * self._escala()
        self.rotulo_medidas.setText(f"{tema.numero(x)} × {tema.numero(y)} × {tema.numero(z)} mm")

    def girar(self, chave: str) -> None:
        escala = self._escala()
        self.rotacao = np.array(GIROS[chave], dtype=np.float64) @ self.rotacao
        # Girar não muda o tamanho da peça, a não ser que ela deixe de caber.
        self._aplicar_altura(self._altura_base() * escala)
        self._desenhar()

    def _cor_da_previa(self) -> tuple[int, int, int] | list[tuple[int, int, int]]:
        if self.modelo.partes_de_cor:
            return [_rgb(self.editor.botoes[p["nome"]].cor or tema.COR_PECA) for p in self.modelo.partes_de_cor]
        return _rgb(self.editor.botoes[""].cor or tema.COR_PECA)

    def _desenhar(self) -> None:
        imagem, chao = desenhar_previa(self.amostra, self.rotacao, self._cor_da_previa(), LADO_DA_PREVIA)
        fundo = QPixmap(LADO_DA_PREVIA, LADO_DA_PREVIA)
        fundo.fill(QColor(tema.FUNDO_3D))
        pintor = QPainter(fundo)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        pintor.setPen(QColor(tema.SUAVE))
        pintor.setBrush(QColor("#23272f"))
        pintor.drawPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in chao]))
        pintor.drawImage(0, 0, QImage(imagem.data, LADO_DA_PREVIA, LADO_DA_PREVIA, LADO_DA_PREVIA * 4, QImage.Format.Format_RGBA8888))
        pintor.end()
        self.rotulo_previa.setPixmap(fundo)

    def opcoes(self) -> OpcoesDoModelo:
        return OpcoesDoModelo(nome_da_parte(self.campo_nome.text(), self.ocupados), self._escala(), self.rotacao.tolist(), self.editor.cores(), self.editor.fundo(), self.modelo.partes_de_cor)
