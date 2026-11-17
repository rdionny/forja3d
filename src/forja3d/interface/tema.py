"""Cores e estilo da interface."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

PASTA_ICONES = Path(__file__).parent / "icones"
_ICONES = PASTA_ICONES.as_posix()
OPACIDADE_DESLIGADO = 0.3

FUNDO = "#14161a"
PAINEL = "#1b1e24"
ELEVADO = "#242831"
BORDA = "#323743"
TEXTO = "#e8eaee"
SUAVE = "#9aa2b1"
ACENTO = "#e8873a"
ACENTO_FORTE = "#f29a52"
OK = "#5fbf7f"
ALERTA = "#e5b84b"
ERRO = "#ee6470"
BOLHA_USUARIO = "#37404f"
BOLHA_AGENTE = "#242831"
FUNDO_3D = "#101216"
COR_PECA = "#e8873a"

ESTILO = f"""
* {{ font-family: "Segoe UI", sans-serif; font-size: 13px; color: {TEXTO}; }}
QMainWindow, QDialog {{ background: {FUNDO}; }}
QWidget#painel {{ background: {PAINEL}; }}
QLabel {{ background: transparent; }}
QLabel#titulo {{ font-size: 12px; font-weight: 600; color: {SUAVE}; letter-spacing: 0.5px; }}
QLabel#marca {{ font-size: 16px; font-weight: 700; color: {TEXTO}; }}
QLabel#suave {{ color: {SUAVE}; }}
QLabel#medida {{ color: {TEXTO}; font-weight: 600; }}
QLabel#etapa {{ color: {ACENTO_FORTE}; font-weight: 600; }}
QLabel#aviso {{ color: {ALERTA}; }}
QLabel#erro {{ color: {ERRO}; }}
QLabel#tempo {{ color: {ACENTO_FORTE}; font-weight: 600; }}
QFrame#chipReferencia {{ background: {BOLHA_USUARIO}; border: 1px solid {ACENTO}; border-radius: 6px; }}
QMenu {{ background: {ELEVADO}; border: 1px solid {BORDA}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACENTO}; color: #14161a; }}
QMenu::item:disabled {{ color: #636a78; }}
QMenu::separator {{ height: 1px; background: {BORDA}; margin: 4px 6px; }}
QLabel#tituloConceito {{ font-size: 14px; font-weight: 600; }}
QLabel#imagemConceito {{ background: {FUNDO_3D}; border: 1px solid {BORDA}; border-radius: 6px; color: {SUAVE}; }}
QFrame#cartaoConceito {{ background: {ELEVADO}; border: 1px solid {BORDA}; border-radius: 8px; }}
QFrame#bolhaUsuario {{ background: {BOLHA_USUARIO}; border-radius: 10px; }}
QFrame#bolhaAgente {{ background: {BOLHA_AGENTE}; border-radius: 10px; }}
QFrame#bolhaErro {{ background: #3a2228; border: 1px solid {ERRO}; border-radius: 10px; }}
QFrame#barraImpressao {{ background: {PAINEL}; border-top: 1px solid {BORDA}; }}
QFrame#linhaEtapa {{ background: {ELEVADO}; border-radius: 8px; }}
QFrame#itemCheck {{ background: {ELEVADO}; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDA}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACENTO}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXTO}; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDA}; border-radius: 3px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QSplitter::handle {{ background: {FUNDO}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox {{
    background: {ELEVADO}; border: 1px solid {BORDA}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {ACENTO}; selection-color: #14161a;
}}
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACENTO}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({_ICONES}/seta_baixo.svg); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {ELEVADO}; border: 1px solid {BORDA}; selection-background-color: {ACENTO}; selection-color: #14161a; }}
QSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button {{ subcontrol-position: top right; width: 16px; border: none; background: transparent; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; width: 16px; border: none; background: transparent; }}
QSpinBox::up-arrow {{ image: url({_ICONES}/seta_cima.svg); width: 8px; height: 5px; }}
QSpinBox::down-arrow {{ image: url({_ICONES}/seta_baixo.svg); width: 8px; height: 5px; }}
QPushButton {{
    background: {ELEVADO}; border: 1px solid {BORDA}; border-radius: 6px; padding: 6px 12px; font-weight: 500;
}}
QPushButton:hover {{ border-color: {SUAVE}; }}
QPushButton:pressed {{ background: {BORDA}; }}
QPushButton:disabled {{ color: #636a78; background: {PAINEL}; border-color: {ELEVADO}; }}
QPushButton#primario {{ background: {ACENTO}; color: #14161a; border: none; font-weight: 600; }}
QPushButton#primario:hover {{ background: {ACENTO_FORTE}; }}
QPushButton#primario:disabled {{ background: {ELEVADO}; color: #636a78; }}
QPushButton#plano {{ background: transparent; border: none; color: {SUAVE}; padding: 4px 8px; }}
QPushButton#plano:hover {{ color: {TEXTO}; background: {ELEVADO}; }}
QPushButton#icone {{ background: transparent; border: none; border-radius: 6px; padding: 0; }}
QPushButton#icone:hover {{ background: {ELEVADO}; }}
QPushButton#icone:pressed {{ background: {BORDA}; }}
QPushButton#acaoMensagem {{ background: transparent; border: none; border-radius: 5px; padding: 0; }}
QPushButton#acaoMensagem:hover {{ background: {FUNDO}; }}
QPushButton#acaoMensagem:pressed {{ background: {BORDA}; }}
QListWidget#listaProvedores {{ background: {ELEVADO}; border: 1px solid {BORDA}; border-radius: 6px; }}
QFrame#sugestao {{ background: {ELEVADO}; border: 1px solid {BORDA}; border-radius: 6px; }}
QFrame#sugestao:hover {{ border-color: {ACENTO}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {ELEVADO}; }}
QListWidget::item:selected {{ background: {BOLHA_USUARIO}; color: {TEXTO}; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {BORDA}; border-radius: 3px; background: {ELEVADO}; }}
QCheckBox::indicator:checked {{ background: {ACENTO}; border-color: {ACENTO}; image: url({_ICONES}/marcado.svg); }}
QStatusBar {{ background: {PAINEL}; border-top: 1px solid {BORDA}; }}
QStatusBar QLabel {{ color: {SUAVE}; padding: 0 8px; }}
QStatusBar::item {{ border: none; }}
QToolTip {{ background: {ELEVADO}; color: {TEXTO}; border: 1px solid {BORDA}; padding: 4px; }}
QMessageBox {{ background: {PAINEL}; }}
"""


def icone(nome: str) -> QIcon:
    """O Qt quase não muda um ícone cinza quando ele desliga: o estado desligado precisa ser visível."""
    normal = QIcon(f"{_ICONES}/{nome}.svg").pixmap(48, 48)
    apagado = QPixmap(normal.size())
    apagado.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(apagado)
    pintor.setOpacity(OPACIDADE_DESLIGADO)
    pintor.drawPixmap(0, 0, normal)
    pintor.end()
    # Só um ícone feito de imagens usa a imagem pedida em qualquer tamanho: o de SVG só no tamanho exato.
    resultado = QIcon()
    resultado.addPixmap(normal, QIcon.Mode.Normal)
    resultado.addPixmap(apagado, QIcon.Mode.Disabled)
    return resultado


def numero(valor: float, casas: int = 1) -> str:
    """Formata em português: 8319.6 vira 8.319,6."""
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")
