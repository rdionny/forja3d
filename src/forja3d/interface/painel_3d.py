"""Visualizador 3D: os itens em escala real, sobre a mesa da impressora.

Dá para arrastar cada item, copiar, colar, duplicar, girar, deletar e separar em arquivos.
A lógica fica em `LayoutMesa`, que não depende da tela: aqui só o mouse, o teclado e o desenho.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from forja3d.fatiamento.cores import CAMADA_INICIAL_MM, ErroDeCor, cores_do_item, fundo_do_item, plano_do_arquivo
from forja3d.fatiamento.simulacao import Percurso
from forja3d.importacao.leitura import FORMATOS_ACEITOS, FORMATOS_RECUSADOS
from forja3d.importacao.separacao import BASE, Regioes
from forja3d.interface import tema
from forja3d.interface.barra_separacao import BarraDeSeparacao
from forja3d.interface.barra_simulacao import BarraDeSimulacao
from forja3d.interface.dialogo_cores import DialogoAlturas, DialogoCores
from forja3d.interface.dialogo_tombar import DIRECOES, DialogoTombar
from forja3d.layout_mesa import ItemMesa, LayoutMesa, ponto_no_plano
from forja3d.verificacao.verificador import avisos_da_posicao

os.environ.setdefault("QT_API", "pyside6")

COR_SELECIONADO = "#ffc078"
COR_CONFLITO = tema.ERRO
COR_ARESTA = "#5e3413"
# Na tela de separar cores: neutro, para a cor do filamento ser a que chama a atenção.
COR_SEM_PINTURA = "#c8ccd4"
COR_DA_DIVISA = "#3a3f4b"
DISTANCIA_DE_CLIQUE_PX = 4
DICA_DA_MESA = "Clique em um item para selecionar. Arraste para mover."


def _modelos_arrastados(dados: QMimeData) -> list[Path]:
    """Os arquivos com jeito de modelo 3D, inclusive os formatos em que o app explica o que fazer."""
    caminhos = [Path(url.toLocalFile()) for url in dados.urls()]
    return [c for c in caminhos if c.suffix.lower() in (*FORMATOS_ACEITOS, *FORMATOS_RECUSADOS)]


def _faixa(malha: Any, de_z: float, ate_z: float | None) -> Any:
    """O pedaço da malha entre duas alturas. A malha do visualizador sempre começa em Z = 0."""
    if de_z > 0:
        malha = malha.clip(normal=(0, 0, 1), origin=(0, 0, de_z), invert=False)
    if ate_z is not None:
        malha = malha.clip(normal=(0, 0, 1), origin=(0, 0, ate_z), invert=True)
    return malha


class Painel3D(QWidget):
    mesa_mudou = Signal()
    referencia_pedida = Signal(str)
    desfazer_mudou = Signal(bool, bool)
    importar_pedido = Signal()
    modelo_solto = Signal(object)
    separacao_pedida = Signal(str)
    # A parte, as regiões, a cor de cada região pintada e se leva pino.
    separar_pedido = Signal(str, object, object, bool)

    def __init__(self, perfil: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.perfil = perfil
        self.cena = None
        self.mesa: LayoutMesa | None = None
        self.stls: dict[str, Path] = {}
        self.medidas: dict[str, dict[str, Any]] = {}
        self.arquivo_ativo = 1
        self.selecionado: str | None = None
        self.editavel = False
        self.brim_mm = 0.0
        self._copiado: tuple[str, int, dict[str, str], str | None] | None = None
        self._historico: list[dict[str, Any]] = []
        self._refeitos: list[dict[str, Any]] = []
        self._malhas: dict[str, tuple[Any, Any]] = {}
        self._malhas_dos_corpos: dict[str, dict[str, Any]] = {}
        # Por parte e posição: tombar de novo para a mesma posição não refaz nada.
        self._malhas_deitadas: dict[tuple[Any, ...], tuple[Any, Any, dict[str, Any]]] = {}
        self._avisos_guardados: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        self._atores: dict[str, tuple[Any, Any]] = {}
        # Uma peça colorida tem um ator por cor: o de `_atores` é o primeiro, e estes são os outros.
        self._outros_atores: dict[str, list[Any]] = {}
        self._cor_escolhida: dict[str, str | None] = {}
        self._arrasto: dict[str, Any] | None = None
        self._clique: QPointF | None = None
        self._clique_direito: QPointF | None = None
        self._limites_peca: tuple[float, ...] | None = None
        self._percurso: Percurso | None = None
        self._editavel_depois_da_simulacao = False
        self._linhas_da_simulacao: dict[str, Any] | None = None
        self.barra_simulacao = BarraDeSimulacao()
        self.barra_simulacao.tempo_mudou.connect(self._desenhar_simulacao)
        self.barra_simulacao.fechar_pedido.connect(self.encerrar_simulacao)
        self._separacao: dict[str, Any] | None = None
        self.barra_separacao = BarraDeSeparacao()
        self.barra_separacao.separar_pedido.connect(self._pedir_o_corte)
        self.barra_separacao.fechar_pedido.connect(self.encerrar_separacao)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._criar_barra_vistas())
        layout.addWidget(self._criar_barra_mesa())
        # Altura fixa: se a faixa surgisse no meio de um arrasto, a cena mudaria de tamanho e o item sairia de baixo do mouse.
        self.rotulo_aviso = QLabel("", objectName="aviso")
        self.rotulo_aviso.setContentsMargins(12, 0, 12, 0)
        self.rotulo_aviso.setFixedHeight(22)
        self.rotulo_aviso.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.rotulo_aviso)

        for tecla, acao in (
            (QKeySequence.StandardKey.Copy, self.copiar),
            (QKeySequence.StandardKey.Paste, self.colar),
            ("Ctrl+D", self.duplicar),
            (QKeySequence.StandardKey.Delete, self.deletar),
            ("R", self.girar),
            (QKeySequence.StandardKey.Undo, self.desfazer),
            ("Ctrl+Y", self.refazer),
            ("Ctrl+Shift+Z", self.refazer),
            ("Esc", lambda: self.selecionar(None)),
        ):
            atalho = QShortcut(QKeySequence(tecla), self)
            atalho.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            atalho.activated.connect(acao)

        self._atualizar_barra()

        if os.environ.get("FORJA3D_SEM_3D"):
            self.area = QLabel("Visualizador desligado", alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.area, 1)
            layout.addWidget(self.barra_simulacao)
            layout.addWidget(self.barra_separacao)
            return

        import pyvista as pv
        from pyvistaqt import QtInteractor

        self._pv = pv
        self.cena = QtInteractor(self)
        layout.addWidget(self.cena.interactor, 1)
        layout.addWidget(self.barra_simulacao)
        layout.addWidget(self.barra_separacao)
        self.cena.set_background(tema.FUNDO_3D)
        self.cena.enable_anti_aliasing("fxaa")
        self.cena.add_axes(color=tema.SUAVE, line_width=2)
        # O modo livre do VTK deixa inclinar a câmera até a mesa perder o "para cima".
        self.cena.enable_terrain_style(mouse_wheel_zooms=True, shift_pans=True)
        self.cena.interactor.installEventFilter(self)
        self._montar_mesa()
        self.ver_mesa()

    # ---------- Montagem ----------

    def _criar_barra_vistas(self) -> QWidget:
        barra = QWidget(objectName="painel")
        linha = QHBoxLayout(barra)
        linha.setContentsMargins(10, 6, 10, 2)
        linha.setSpacing(4)
        for texto, acao, dica in (
            ("Peça", self.ver_peca, "Aproxima a câmera dos itens"),
            ("Mesa", self.ver_mesa, "Mostra toda a mesa e o volume de impressão"),
            ("Frente", lambda: self.ver_lado((0, -1, 0)), "Vista frontal"),
            ("Lado", lambda: self.ver_lado((1, 0, 0)), "Vista lateral"),
            ("Topo", lambda: self.ver_lado((0, 0, 1)), "Vista de cima: a melhor para posicionar"),
            ("Baixo", lambda: self.ver_lado((0, 0, -1)), "Vista de baixo: mostra a face que encosta na mesa"),
        ):
            botao = QPushButton(texto, objectName="plano", toolTip=dica)
            botao.clicked.connect(acao)
            linha.addWidget(botao)
        linha.addSpacing(10)
        self.caixa_volume = QCheckBox("Volume", checked=True, toolTip="Mostra o volume de impressão e o limite seguro")
        self.caixa_volume.toggled.connect(self._alternar_volume)
        linha.addWidget(self.caixa_volume)
        linha.addStretch(1)
        self.rotulo_medida = QLabel("", objectName="medida")
        linha.addWidget(self.rotulo_medida)
        return barra

    def _criar_barra_mesa(self) -> QWidget:
        barra = QWidget(objectName="painel")
        linha = QHBoxLayout(barra)
        linha.setContentsMargins(10, 2, 10, 6)
        linha.setSpacing(4)
        self.combo_arquivo = QComboBox(toolTip="Cada arquivo vira um G-code separado, para imprimir em outro momento")
        self.combo_arquivo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_arquivo.setMinimumContentsLength(15)
        self.combo_arquivo.currentIndexChanged.connect(self._ao_escolher_arquivo)
        linha.addWidget(self.combo_arquivo)
        self.botoes: dict[str, QPushButton] = {}
        for chave, texto, acao, dica in (
            ("copiar", "Copiar", self.copiar, "Copia o item selecionado (Ctrl+C)"),
            ("colar", "Colar", self.colar, "Coloca uma cópia na mesa (Ctrl+V)"),
            ("duplicar", "Duplicar", self.duplicar, "Copia e cola de uma vez (Ctrl+D)"),
            ("girar", "Girar", self.girar, "Gira o item 90° sobre a mesa (R)"),
            ("tombar", "Tombar", None, "Deita a peça: muda qual face fica na mesa, sem mudar o modelo"),
            ("deletar", "Deletar", self.deletar, "Tira o item da mesa, sem mudar o modelo (Delete)"),
            ("cor", "Cor", self.escolher_cor, "Escolhe a cor do filamento deste item"),
        ):
            botao = QPushButton(texto, objectName="plano", toolTip=dica)
            if acao is not None:
                botao.clicked.connect(acao)
            self.botoes[chave] = botao
            linha.addWidget(botao)
        self.menu_tombar = QMenu(self)
        self.menu_tombar.aboutToShow.connect(lambda: self._preencher_menu_tombar(self.menu_tombar))
        self.botoes["tombar"].setMenu(self.menu_tombar)
        self.botao_mais = QPushButton("Mesa", objectName="plano", toolTip="Adicionar partes, mover entre arquivos, organizar e desfazer")
        self.menu_mesa = QMenu(self)
        self.menu_mesa.aboutToShow.connect(lambda: self._preencher_menu(self.menu_mesa))
        self.botao_mais.setMenu(self.menu_mesa)
        linha.addWidget(self.botao_mais)
        self.botao_importar = QPushButton("Importar", objectName="plano", toolTip="Coloca na mesa um modelo 3D baixado: STL, GLB, OBJ, FBX, 3MF ou PLY. Também dá para arrastar o arquivo até aqui (Ctrl+I)")
        self.botao_importar.clicked.connect(self.importar_pedido)
        linha.addWidget(self.botao_importar)
        linha.addSpacing(6)
        self.botao_ia = QPushButton("Enviar à IA", toolTip="Usa o item selecionado como referência na próxima mensagem do chat")
        self.botao_ia.clicked.connect(self.enviar_para_ia)
        linha.addWidget(self.botao_ia)
        linha.addSpacing(6)
        self.rotulo_mesa = QLabel(DICA_DA_MESA, objectName="suave")
        self.rotulo_mesa.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        linha.addWidget(self.rotulo_mesa, 1)
        return barra

    def _montar_mesa(self) -> None:
        pv = self._pv
        volume = self.perfil["volume_mm"]
        x, y, z = volume["fisico"]["x"], volume["fisico"]["y"], volume["fisico"]["z"]
        seguro = volume["seguro"]
        centro_x, centro_y = x / 2, y / 2

        mesa = pv.Box(bounds=(0, x, 0, y, -2.0, -0.05))
        self.cena.add_mesh(mesa, color="#23272f", name="mesa", smooth_shading=False, lighting=True, pickable=False)
        grade = pv.Plane(center=(centro_x, centro_y, 0), direction=(0, 0, 1), i_size=x, j_size=y, i_resolution=11, j_resolution=11)
        self.cena.add_mesh(grade, style="wireframe", color="#3a404c", line_width=1, name="grade", lighting=False, pickable=False)

        margem_x, margem_y = (x - seguro["x"]) / 2, (y - seguro["y"]) / 2
        limite = pv.Box(bounds=(margem_x, x - margem_x, margem_y, y - margem_y, 0, seguro["z"]))
        fisico = pv.Box(bounds=(0, x, 0, y, 0, z))
        self._volume = [
            self.cena.add_mesh(fisico, style="wireframe", color="#5a6372", opacity=0.45, line_width=1, name="volume", lighting=False, pickable=False),
            self.cena.add_mesh(limite, style="wireframe", color=tema.ACENTO, opacity=0.3, line_width=1, name="limite", lighting=False, pickable=False),
        ]
        self.cena.add_point_labels(
            [(centro_x, -14, 0), (x + 12, centro_y, 0), (centro_x, y + 12, 0)],
            ["Frente", "+X", "+Y"],
            text_color=tema.SUAVE, font_size=11, shape=None, show_points=False, always_visible=True, name="rotulos",
        )

    def _alternar_volume(self, visivel: bool) -> None:
        if self.cena is None:
            return
        for ator in self._volume:
            ator.SetVisibility(visivel)
        self.cena.render()

    # ---------- Conteúdo ----------

    def mostrar_mesa(self, mesa: LayoutMesa, stls: dict[str, Path], editavel: bool = True, medidas: dict[str, dict[str, Any]] | None = None) -> None:
        self.limpar()
        self.mesa = mesa
        self.stls = stls
        self.medidas = medidas or {}
        self.editavel = editavel
        arquivos = mesa.arquivos()
        self.arquivo_ativo = arquivos[0] if arquivos else 1
        self._carregar_malhas()
        self._redesenhar()
        if len(mesa.itens) > 1:
            self.ver_mesa()
        else:
            self.ver_peca()

    def limpar(self) -> None:
        self._soltar_simulacao()
        self._soltar_separacao()
        self.mesa = None
        self.stls = {}
        self.medidas = {}
        self.selecionado = None
        self.editavel = False
        self._historico = []
        self._refeitos = []
        self._limites_peca = None
        self._arrasto = None
        self.rotulo_medida.setText("")
        self._remover_atores()
        self._malhas = {}
        self._malhas_dos_corpos = {}
        self._malhas_deitadas = {}
        self._avisos_guardados = {}
        self._atualizar_barra()
        if self.cena is not None:
            self.cena.render()

    def _carregar_malhas(self) -> None:
        if self.cena is None:
            return
        for parte, caminho in self.stls.items():
            malha = self._pv.read(str(caminho))
            x0, x1, y0, y1, z0, _ = malha.bounds
            # Com o centro na origem, a posição e o giro do ator são os do item.
            deslocamento = (-(x0 + x1) / 2, -(y0 + y1) / 2, -z0)
            malha = malha.translate(deslocamento)
            arestas = malha.extract_feature_edges(feature_angle=30, boundary_edges=False, non_manifold_edges=False, manifold_edges=False)
            self._malhas[parte] = (malha, arestas)
            corpos = (self.medidas.get(parte) or {}).get("corpos") or []
            self._malhas_dos_corpos[parte] = {c["nome"]: self._pv.read(str(caminho.parent / c["stl"])).translate(deslocamento) for c in corpos}

    def _geometria(self, item: ItemMesa) -> tuple[Any, Any, dict[str, Any]]:
        """A malha, o contorno e os corpos do item, já deitados e apoiados em Z = 0. Falta só o giro e a posição, que são do ator."""
        malha, arestas = self._malhas[item.parte]
        corpos = self._malhas_dos_corpos.get(item.parte) or {}
        if item.tombo is None:
            return malha, arestas, corpos
        chave = (item.parte, tuple(item.tombo))
        if chave not in self._malhas_deitadas:
            # Gira em torno do centro da caixa. Assim o centro continua sobre a origem, como o fatiador espera.
            rotacao = np.reshape(item.tombo, (3, 3))
            centro = np.array([0.0, 0.0, (malha.bounds[4] + malha.bounds[5]) / 2])
            base = ((np.asarray(malha.points) - centro) @ rotacao.T)[:, 2].min()
            matriz = np.eye(4)
            matriz[:3, :3] = rotacao
            matriz[:3, 3] = (0.0, 0.0, -base) - rotacao @ centro
            self._malhas_deitadas[chave] = (malha.transform(matriz, inplace=False), arestas.transform(matriz, inplace=False), {nome: m.transform(matriz, inplace=False) for nome, m in corpos.items()})
        return self._malhas_deitadas[chave]

    def _pedacos(self, item: ItemMesa) -> list[tuple[Any, str | None]]:
        """A malha de cada cor do item, como vai sair na impressão."""
        malha, _, corpos = self._geometria(item)
        medida = self.medidas.get(item.parte)
        cores = cores_do_item(item, medida)
        if corpos:
            fundo = fundo_do_item(item, medida)
            if fundo in cores:
                pedacos = [(_faixa(m, 0.0, CAMADA_INICIAL_MM), cores[nome]) for nome, m in corpos.items()]
                pedacos += [(_faixa(m, CAMADA_INICIAL_MM, None), cores[fundo]) for m in corpos.values()]
            else:
                pedacos = [(m, cores[nome]) for nome, m in corpos.items()]
        else:
            cor = cores[""]
            trocas = self.mesa.alturas.get(item.arquivo)
            if trocas:
                limites = [0.0, *(t["z"] for t in trocas), None]
                sequencia = [cor, *(t["cor"] for t in trocas)]
                pedacos = [(_faixa(malha, limites[i], limites[i + 1]), sequencia[i]) for i in range(len(sequencia))]
            else:
                pedacos = [(malha, cor)]
        return [p for p in pedacos if p[0].n_cells] or [(malha, None)]

    def _remover_atores(self) -> None:
        if self.cena is None:
            self._atores = {}
            self._outros_atores = {}
            self._cor_escolhida = {}
            return
        for ator, arestas in self._atores.values():
            self.cena.remove_actor(ator, reset_camera=False, render=False)
            if arestas is not None:
                self.cena.remove_actor(arestas, reset_camera=False, render=False)
        for atores in self._outros_atores.values():
            for ator in atores:
                self.cena.remove_actor(ator, reset_camera=False, render=False)
        self._atores = {}
        self._outros_atores = {}
        self._cor_escolhida = {}

    def _redesenhar(self) -> None:
        """Refaz a cena depois de uma mudança na lista de itens. Mover só atualiza a posição."""
        self._atualizar_combo_arquivos()
        self._atualizar_barra()
        self._atualizar_textos()
        if self.cena is None or self.mesa is None:
            return
        self._remover_atores()
        for item in self.mesa.itens_do_arquivo(self.arquivo_ativo):
            _, contorno, _ = self._geometria(item)
            pedacos = self._pedacos(item)
            atores = [
                self.cena.add_mesh(
                    malha, color=cor or tema.COR_PECA, name=f"item_{item.id}" if indice == 0 else f"item_{item.id}_{indice}", smooth_shading=False, specular=0.25, specular_power=20, reset_camera=False, render=False
                )
                for indice, (malha, cor) in enumerate(pedacos)
            ]
            arestas = None
            if contorno.n_cells:
                arestas = self.cena.add_mesh(
                    contorno, color=COR_ARESTA, line_width=1.5, name=f"arestas_{item.id}", lighting=False, pickable=False, reset_camera=False, render=False
                )
            self._atores[item.id] = (atores[0], arestas)
            self._outros_atores[item.id] = atores[1:]
            self._cor_escolhida[item.id] = None if len(pedacos) == 1 and pedacos[0][1] is None else pedacos[0][1] or tema.COR_PECA
            self._posicionar_ator(item)
        self._colorir()
        self._atualizar_limites()
        self.cena.render()

    def _posicionar_ator(self, item: ItemMesa) -> None:
        for ator in (*self._atores[item.id], *self._outros_atores.get(item.id, [])):
            if ator is not None:
                ator.SetPosition(item.x, item.y, 0.0)
                ator.SetOrientation(0.0, 0.0, float(item.giro))

    def _colorir(self) -> None:
        if self.mesa is None:
            return
        conflitos = self.mesa.conflitos(self.brim_mm)
        for identificador, (ator, contorno) in self._atores.items():
            if self._cor_escolhida.get(identificador):
                # A cor é a do filamento, então não muda ao selecionar: a seleção clareia a peça, e o conflito pinta o contorno.
                for parte_do_item in (ator, *self._outros_atores.get(identificador, [])):
                    parte_do_item.prop.ambient = 0.4 if identificador == self.selecionado else 0.0
                if contorno is not None:
                    contorno.prop.color = COR_CONFLITO if identificador in conflitos else COR_ARESTA
                continue
            if identificador == self.selecionado:
                cor = COR_SELECIONADO
            elif identificador in conflitos:
                cor = COR_CONFLITO
            else:
                cor = tema.COR_PECA
            ator.prop.color = cor

    def _atualizar_limites(self) -> None:
        self._limites_peca = None
        if self.mesa is None or not self.mesa.itens_do_arquivo(self.arquivo_ativo):
            return
        x0, x1, y0, y1 = self.mesa.area_do_arquivo(self.arquivo_ativo)
        z1 = max((self._geometria(i)[0].bounds[5] for i in self.mesa.itens_do_arquivo(self.arquivo_ativo)), default=1.0)
        self._limites_peca = (x0, x1, y0, y1, 0.0, z1)

    def _atualizar_combo_arquivos(self) -> None:
        numeros = self.mesa.arquivos() if self.mesa else []
        if numeros and self.arquivo_ativo not in numeros:
            self.arquivo_ativo = numeros[-1]
        self.combo_arquivo.blockSignals(True)
        self.combo_arquivo.clear()
        for numero in numeros:
            self.combo_arquivo.addItem(f"Arquivo {numero} de {len(numeros)}", numero)
        if numeros:
            self.combo_arquivo.setCurrentIndex(numeros.index(self.arquivo_ativo))
        self.combo_arquivo.blockSignals(False)

    def _atualizar_barra(self) -> None:
        tem_selecao = self.editavel and self.selecionado is not None
        for chave in ("copiar", "duplicar", "girar", "tombar", "deletar", "cor"):
            self.botoes[chave].setEnabled(tem_selecao)
        self.botoes["colar"].setEnabled(self.editavel and self._copiado is not None)
        self.botao_mais.setEnabled(self.editavel)
        self.botao_ia.setEnabled(tem_selecao)
        self.combo_arquivo.setEnabled(self.mesa is not None and len(self.mesa.arquivos()) > 1 and self._percurso is None and self._separacao is None)
        self.desfazer_mudou.emit(self.editavel and bool(self._historico), self.editavel and bool(self._refeitos))

    def _atualizar_textos(self) -> None:
        if self.mesa is None:
            self.rotulo_medida.setText("")
            self.rotulo_mesa.setText(DICA_DA_MESA)
            self.rotulo_aviso.setText("")
            return
        itens = self.mesa.itens_do_arquivo(self.arquivo_ativo)
        if len(itens) == 1:
            dx, dy = self.mesa.tamanho_do_item(itens[0])
            altura = f", {tema.numero(self.mesa.altura_do_item(itens[0]))} mm de altura" if itens[0].tombo else ""
            self.rotulo_medida.setText(f"{tema.numero(dx)} × {tema.numero(dy)} mm na mesa{altura}")
        else:
            self.rotulo_medida.setText(f"{len(itens)} itens")

        item = self.mesa.item(self.selecionado)
        if item is None:
            self.rotulo_mesa.setText(DICA_DA_MESA if self.editavel else "Versão antiga: só para ver.")
        else:
            self.rotulo_mesa.setText(f"{item.parte}: X {tema.numero(item.x)}, Y {tema.numero(item.y)} mm")

        if not self.mesa.itens:
            self.rotulo_aviso.setText("A mesa está vazia. Use o botão Mesa para adicionar uma parte.")
        elif self.mesa.conflitos(self.brim_mm):
            folga = "com o brim, os itens precisam de mais espaço" if self.brim_mm else "afaste-os ou use Mesa > Organizar"
            self.rotulo_aviso.setText(f"Itens perto demais ou sobrepostos, em vermelho: {folga}.")
        else:
            avisos = self.avisos_de_posicao(self.mesa.itens_do_arquivo(self.arquivo_ativo))
            self.rotulo_aviso.setText(avisos[0]["mensagem"] if avisos else "")

    def avisos_de_posicao(self, itens: list[ItemMesa] | None = None) -> list[dict[str, Any]]:
        """O que a posição dos itens deitados pede, sem repetir para as cópias."""
        avisos: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for item in self.mesa.itens if itens is None else itens:
            if item.tombo is None or item.parte not in self.stls:
                continue
            chave = (item.parte, tuple(item.tombo))
            if chave not in self._avisos_guardados:
                self._avisos_guardados[chave] = avisos_da_posicao(self.stls[item.parte], item.tombo, item.parte, self.perfil)
            avisos[chave] = self._avisos_guardados[chave]
        return [aviso for lista in avisos.values() for aviso in lista]

    def definir_editavel(self, editavel: bool) -> None:
        # Durante a simulação e a separação de cores a mesa fica fechada. O pedido vale para quando elas acabarem.
        if self._percurso is not None or self._separacao is not None:
            self._editavel_depois_da_simulacao = editavel
            # Com o app ocupado, cortando a peça, a pintura espera.
            self.barra_separacao.setEnabled(editavel)
            return
        self.editavel = editavel
        self._atualizar_barra()
        self._atualizar_textos()

    # ---------- Simulação ----------

    @property
    def simulando(self) -> bool:
        return self._percurso is not None

    def iniciar_simulacao(self, percurso: Percurso) -> None:
        """Troca as peças pelo percurso do G-code, e a barra passa a mandar no que aparece."""
        self.encerrar_simulacao()
        self.encerrar_separacao()
        self._percurso = percurso
        self._editavel_depois_da_simulacao = self.editavel
        self.editavel = False
        self.selecionado = None
        self._remover_atores()
        self._atualizar_barra()
        self.rotulo_mesa.setText("Simulação do G-code preparado.")
        self.rotulo_aviso.setText("")
        self._montar_simulacao(percurso)
        self.barra_simulacao.iniciar(percurso)
        if self.cena is not None:
            self.ver_mesa()
        self.barra_simulacao.reproduzir()

    def encerrar_simulacao(self) -> None:
        if self._percurso is None:
            return
        self._soltar_simulacao()
        self.editavel = self._editavel_depois_da_simulacao
        self._redesenhar()

    def _soltar_simulacao(self) -> None:
        if self._percurso is None:
            return
        self.barra_simulacao.encerrar()
        self._percurso = None
        if self.cena is not None and self._linhas_da_simulacao is not None:
            self.cena.remove_actor(self._linhas_da_simulacao["ator"], reset_camera=False, render=False)
            self.cena.remove_actor(self._linhas_da_simulacao["bico"], reset_camera=False, render=False)
        self._linhas_da_simulacao = None

    def _montar_simulacao(self, percurso: Percurso) -> None:
        if self.cena is None:
            return
        from vtkmodules.util.numpy_support import numpy_to_vtk
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkPolyData
        from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

        pontos = vtkPoints()
        pontos.SetData(numpy_to_vtk(percurso.pontos, deep=False))
        linhas = vtkPolyData()
        linhas.SetPoints(pontos)
        mapeador = vtkPolyDataMapper()
        mapeador.SetInputData(linhas)
        mapeador.SetScalarModeToUseCellData()
        mapeador.SetColorModeToDirectScalars()
        ator = vtkActor()
        ator.SetMapper(mapeador)
        # Como tubos e com luz: é o que dá volume às camadas e deixa ver os furos.
        ator.GetProperty().SetLineWidth(2.5)
        ator.GetProperty().SetRenderLinesAsTubes(True)
        ator.GetProperty().LightingOn()
        self.cena.add_actor(ator, reset_camera=False, pickable=False, render=False, name="simulacao_linhas")
        ponta = self._pv.Cone(center=(0.0, 0.0, 7.0), direction=(0.0, 0.0, -1.0), height=14.0, radius=5.0, resolution=24)
        bico = self.cena.add_mesh(ponta, color=tema.TEXTO, name="simulacao_bico", pickable=False, reset_camera=False, render=False)
        total = len(percurso.tempo_dos_trechos)
        # Sem copiar: a cada passo da barra, o VTK só passa a olhar um pedaço maior ou menor destes vetores.
        self._linhas_da_simulacao = {
            "dados": linhas,
            "ator": ator,
            "bico": bico,
            "inicios": np.arange(0, 2 * total + 1, 2, dtype=np.int64),
            "conexoes": np.arange(2 * total, dtype=np.int64),
            "mostrados": -1,
        }

    def _desenhar_simulacao(self, tempo: float) -> None:
        if self.cena is None or self._percurso is None or self._linhas_da_simulacao is None:
            return
        desenho = self._linhas_da_simulacao
        quantidade = self._percurso.trechos_ate(tempo)
        if quantidade != desenho["mostrados"]:
            self._mostrar_trechos(quantidade)
        desenho["bico"].SetPosition(*self._percurso.bico_em(tempo))
        self.cena.render()

    def _mostrar_trechos(self, quantidade: int) -> None:
        from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
        from vtkmodules.vtkCommonCore import VTK_UNSIGNED_CHAR
        from vtkmodules.vtkCommonDataModel import vtkCellArray

        desenho = self._linhas_da_simulacao
        desenho["mostrados"] = quantidade
        desenho["ator"].SetVisibility(quantidade > 0)
        if not quantidade:
            return
        celulas = vtkCellArray()
        celulas.SetData(numpy_to_vtkIdTypeArray(desenho["inicios"][: quantidade + 1], deep=False), numpy_to_vtkIdTypeArray(desenho["conexoes"][: 2 * quantidade], deep=False))
        linhas = desenho["dados"]
        linhas.SetLines(celulas)
        cores = numpy_to_vtk(self._percurso.cores[:quantidade], deep=False, array_type=VTK_UNSIGNED_CHAR)
        cores.SetName("cor")
        linhas.GetCellData().SetScalars(cores)
        linhas.Modified()

    # ---------- Separar cores ----------

    def _pode_separar(self, parte: str) -> bool:
        """Só modelo baixado de um corpo só: a peça do agente muda no script, e a de vários corpos já vem separada."""
        medida = self.medidas.get(parte) or {}
        return bool(medida.get("importada")) and not medida.get("corpos")

    def pedir_separacao(self) -> None:
        item = self._item_editavel()
        if item is not None and self._pode_separar(item.parte):
            self.separacao_pedida.emit(item.parte)

    def iniciar_separacao(self, parte: str, regioes: Regioes) -> None:
        """Troca a mesa pela peça sozinha. Cada clique pinta uma região com a cor da barra, e Separar corta as pintadas."""
        self.encerrar_simulacao()
        self.encerrar_separacao()
        self._separacao = {"parte": parte, "regioes": regioes, "pintura": {}}
        self._editavel_depois_da_simulacao = self.editavel
        self.editavel = False
        self.selecionado = None
        self._remover_atores()
        self._atualizar_barra()
        self.rotulo_mesa.setText(f"Separando as cores de {parte}.")
        self.rotulo_aviso.setText("")
        self._montar_separacao()
        self.barra_separacao.iniciar(regioes.separaveis)
        if self.cena is not None:
            self.ver_peca()

    def encerrar_separacao(self) -> None:
        if self._separacao is None:
            return
        self._soltar_separacao()
        self.editavel = self._editavel_depois_da_simulacao
        self._redesenhar()

    def _soltar_separacao(self) -> None:
        if self._separacao is None:
            return
        self.barra_separacao.hide()
        if self.cena is not None:
            for nome in ("ator", "linhas"):
                if self._separacao.get(nome) is not None:
                    self.cena.remove_actor(self._separacao[nome], reset_camera=False, render=False)
        self._separacao = None

    def _montar_separacao(self) -> None:
        if self.cena is None:
            return
        dados = self._separacao
        regioes = dados["regioes"]
        malha = self._pv.PolyData(regioes.vertices, np.column_stack([np.full(len(regioes.faces), 3), regioes.faces]).ravel())
        x0, x1, y0, y1, z0, _ = malha.bounds
        # No meio da mesa. As faces ficam na mesma ordem das regiões: o clique devolve o número da face.
        malha = malha.translate((self.mesa.largura / 2 - (x0 + x1) / 2, self.mesa.profundidade / 2 - (y0 + y1) / 2, -z0), inplace=False)
        malha.cell_data["cor"] = self._cores_da_separacao()
        dados["malha"] = malha
        dados["ator"] = self.cena.add_mesh(malha, scalars="cor", rgb=True, smooth_shading=False, specular=0.25, specular_power=20, name="separacao_peca", reset_camera=False, render=False)
        if len(regioes.contornos):
            divisas = self._pv.PolyData(malha.points, lines=np.column_stack([np.full(len(regioes.contornos), 2), regioes.contornos]).ravel())
            dados["linhas"] = self.cena.add_mesh(divisas, color=COR_DA_DIVISA, line_width=2, lighting=False, pickable=False, name="separacao_divisas", reset_camera=False, render=False)
        self._limites_peca = tuple(malha.bounds)

    def _cores_da_separacao(self) -> np.ndarray:
        regiao_da_face = self._separacao["regioes"].regiao_da_face
        cores = np.empty((len(regiao_da_face), 3), dtype=np.uint8)
        cores[:] = QColor(COR_SEM_PINTURA).getRgb()[:3]
        for numero, cor in self._separacao["pintura"].items():
            cores[regiao_da_face == numero] = QColor(cor).getRgb()[:3]
        return cores

    def pintar_regiao(self, numero: int) -> None:
        """Pinta a região com a cor da barra. Clicar de novo, com a mesma cor, tira a pintura."""
        dados = self._separacao
        if dados is None or not self.barra_separacao.isEnabled():
            return
        if numero <= BASE:
            self.rotulo_aviso.setText("Esta é a maior região: ela fica como o corpo da peça." if numero == BASE else "Esta região é pequena demais para virar uma peça.")
            return
        cor = self.barra_separacao.botao_cor.cor
        if dados["pintura"].get(numero) == cor:
            del dados["pintura"][numero]
        else:
            dados["pintura"][numero] = cor
        self.rotulo_aviso.setText("")
        self.barra_separacao.mostrar_pintadas(len(dados["pintura"]))
        if dados.get("malha") is not None:
            dados["malha"].cell_data["cor"] = self._cores_da_separacao()
            self.cena.render()

    def _pedir_o_corte(self) -> None:
        dados = self._separacao
        if dados is not None and dados["pintura"]:
            self.separar_pedido.emit(dados["parte"], dados["regioes"], dict(dados["pintura"]), self.barra_separacao.caixa_pino.isChecked())

    def _regiao_sob(self, posicao: QPointF) -> int | None:
        from vtkmodules.vtkRenderingCore import vtkCellPicker

        seletor = vtkCellPicker()
        seletor.SetTolerance(0.0005)
        x, y = self._na_tela(posicao)
        if not seletor.Pick(x, y, 0, self.cena.renderer) or seletor.GetCellId() < 0 or seletor.GetActor() is None:
            return None
        if seletor.GetActor().GetAddressAsString("") != self._separacao["ator"].GetAddressAsString(""):
            return None
        return int(self._separacao["regioes"].regiao_da_face[seletor.GetCellId()])

    def definir_brim(self, brim_mm: float) -> None:
        self.brim_mm = brim_mm
        self._colorir()
        self._atualizar_textos()
        if self.cena is not None:
            self.cena.render()

    # ---------- Ações ----------

    def selecionar(self, identificador: str | None) -> None:
        self.selecionado = identificador if self.mesa and self.mesa.item(identificador) else None
        self._colorir()
        self._atualizar_barra()
        self._atualizar_textos()
        if self.cena is not None:
            self.cena.render()

    def _guardar_para_desfazer(self, estado: dict[str, Any] | None = None) -> None:
        self._historico.append(estado or self.mesa.para_dados())
        # Uma alteração nova abre outro caminho: o que foi desfeito antes não tem mais como voltar.
        self._refeitos = []

    def _depois_de_mudar(self) -> None:
        self._redesenhar()
        self.mesa_mudou.emit()

    def copiar(self) -> None:
        item = self._item_editavel()
        if item is not None:
            self._copiado = (item.parte, item.giro, dict(item.cores), item.fundo, item.tombo)
            self._atualizar_barra()

    def colar(self) -> None:
        if not self.editavel or self.mesa is None or self._copiado is None:
            return
        parte, giro, cores, fundo, tombo = self._copiado
        if parte not in self.mesa.tamanhos:
            return
        self._guardar_para_desfazer()
        referencia = self.mesa.item(self.selecionado)
        perto = (referencia.x + self.mesa.tamanho_do_item(referencia)[0] + 6.0, referencia.y) if referencia else None
        self._colocar(self.mesa.adicionar(parte, self.arquivo_ativo, perto, giro, cores, fundo, tombo))

    def duplicar(self) -> None:
        item = self._item_editavel()
        if item is not None:
            self._guardar_para_desfazer()
            self._colocar(self.mesa.duplicar(item.id))

    def adicionar_parte(self, parte: str) -> None:
        if self.editavel and self.mesa is not None:
            self._guardar_para_desfazer()
            self._colocar(self.mesa.adicionar(parte, self.arquivo_ativo))

    def _colocar(self, novo: ItemMesa) -> None:
        """Mostra o item recém-colocado. Se não coube aqui, a tela acompanha o arquivo em que ele ficou."""
        self.arquivo_ativo = novo.arquivo
        self.selecionado = novo.id
        self._depois_de_mudar()

    def girar(self) -> None:
        item = self._item_editavel()
        if item is not None:
            self._guardar_para_desfazer()
            self.mesa.girar(item.id)
            self._depois_de_mudar()

    def tombar(self, eixo: str, graus: float) -> None:
        item = self._item_editavel()
        if item is None:
            return
        antes = self.mesa.para_dados()
        if not self.mesa.tombar(item.id, eixo, graus):
            QMessageBox.information(self, "Tombar", "Nessa posição, a peça não cabe na impressora. Escolha outra direção ou outro ângulo.")
            return
        self._guardar_para_desfazer(antes)
        self._depois_de_mudar()

    def endireitar(self) -> None:
        item = self._item_editavel()
        if item is not None and item.tombo is not None:
            self._guardar_para_desfazer()
            self.mesa.endireitar(item.id)
            self._depois_de_mudar()

    def escolher_tombo(self) -> None:
        dialogo = DialogoTombar(self)
        aceito = dialogo.exec()
        eixo, graus = dialogo.escolha()
        dialogo.deleteLater()
        if aceito:
            self.tombar(eixo, graus)

    def _preencher_menu_tombar(self, menu: QMenu) -> None:
        menu.clear()
        for texto, eixo, sinal in DIRECOES:
            menu.addAction(f"Deitar {texto} (90°)", lambda e=eixo, s=sinal: self.tombar(e, s * 90.0))
        menu.addSeparator()
        menu.addAction("Outro ângulo…", self.escolher_tombo)
        item = self._item_editavel()
        voltar = menu.addAction("Voltar à posição original", self.endireitar)
        voltar.setEnabled(item is not None and item.tombo is not None)

    def deletar(self) -> None:
        item = self._item_editavel()
        if item is not None:
            self._guardar_para_desfazer()
            self.mesa.remover(item.id)
            self.selecionado = None
            self._depois_de_mudar()

    def mover_para_arquivo(self, numero: int | None) -> None:
        item = self._item_editavel()
        if item is not None:
            self._guardar_para_desfazer()
            self.arquivo_ativo = self.mesa.mover_para_arquivo(item.id, numero)
            self._depois_de_mudar()

    def mover_selecionado(self, x: float, y: float) -> None:
        item = self._item_editavel()
        if item is not None:
            self._guardar_para_desfazer()
            self.mesa.mover(item.id, x, y)
            self._depois_de_mudar()

    def organizar(self) -> None:
        if self.editavel and self.mesa is not None:
            self._guardar_para_desfazer()
            self.mesa.organizar()
            self._depois_de_mudar()

    def desfazer(self) -> None:
        if self.editavel and self.mesa is not None and self._historico:
            self._refeitos.append(self.mesa.para_dados())
            self._restaurar(self._historico.pop())

    def refazer(self) -> None:
        if self.editavel and self.mesa is not None and self._refeitos:
            self._historico.append(self.mesa.para_dados())
            self._restaurar(self._refeitos.pop())

    def _restaurar(self, estado: dict[str, Any]) -> None:
        self.mesa.restaurar(estado)
        if self.mesa.item(self.selecionado) is None:
            self.selecionado = None
        self._depois_de_mudar()

    def enviar_para_ia(self) -> None:
        item = self._item_editavel()
        if item is not None:
            self.referencia_pedida.emit(item.parte)

    # ---------- Cores ----------

    def _altura_do_item(self, item: ItemMesa) -> float:
        if item.tombo:
            return self.mesa.altura_do_item(item)
        tamanho = (self.medidas.get(item.parte) or {}).get("tamanho") or [0.0, 0.0, 1.0]
        return float(tamanho[2])

    def escolher_cor(self) -> None:
        item = self._item_editavel()
        if item is None:
            return
        medida = self.medidas.get(item.parte)
        dialogo = DialogoCores(item.parte, cores_do_item(item, medida), fundo_do_item(item, medida), self._altura_do_item(item), CAMADA_INICIAL_MM, self)
        aceito = dialogo.exec()
        cores, fundo = dialogo.editor.cores(), dialogo.editor.fundo()
        dialogo.deleteLater()
        if aceito:
            self._guardar_para_desfazer()
            self.mesa.definir_cores(item.id, cores, fundo)
            self._depois_de_mudar()

    def escolher_alturas(self) -> None:
        if not self.editavel or self.mesa is None or not self.mesa.itens_do_arquivo(self.arquivo_ativo):
            return
        try:
            varias_cores = plano_do_arquivo(self.mesa, self.arquivo_ativo, self.medidas).multicor
        except ErroDeCor:
            varias_cores = True
        if varias_cores:
            QMessageBox.information(self, "Trocar a cor em uma altura", "Este arquivo tem peças de cores diferentes. A troca por altura vale para a mesa inteira: deixe tudo em uma cor só, ou separe em outro arquivo com Mesa > Um arquivo por cor.")
            return
        altura = max(self._altura_do_item(i) for i in self.mesa.itens_do_arquivo(self.arquivo_ativo))
        dialogo = DialogoAlturas(self.mesa.alturas.get(self.arquivo_ativo, []), altura, self)
        aceito = dialogo.exec()
        trocas = dialogo.trocas()
        dialogo.deleteLater()
        if aceito:
            self._guardar_para_desfazer()
            self.mesa.definir_alturas(self.arquivo_ativo, trocas)
            self._depois_de_mudar()

    def separar_por_cor(self) -> None:
        """Peças separadas de cores diferentes saem melhor em arquivos diferentes: nenhuma pausa no meio."""
        if not self.editavel or self.mesa is None:
            return

        def cor_do_item(item: ItemMesa) -> str | None:
            cores = set(cores_do_item(item, self.medidas.get(item.parte)).values())
            if len(cores) > 1:
                return None
            return next(iter(cores)) or "sem cor"

        self._guardar_para_desfazer()
        if self.mesa.separar_por_cor(self.arquivo_ativo, cor_do_item):
            self._depois_de_mudar()
        else:
            self._historico.pop()

    def _item_editavel(self) -> ItemMesa | None:
        if not self.editavel or self.mesa is None:
            return None
        return self.mesa.item(self.selecionado)

    def _ao_escolher_arquivo(self, indice: int) -> None:
        numero = self.combo_arquivo.itemData(indice)
        if numero is not None:
            self.mostrar_arquivo(numero)

    def mostrar_arquivo(self, numero: int) -> None:
        if numero != self.arquivo_ativo and self.mesa is not None and numero in self.mesa.arquivos():
            self.arquivo_ativo = numero
            self.selecionado = None
            self._redesenhar()

    def _preencher_menu(self, menu: QMenu) -> None:
        menu.clear()
        if self.mesa is None:
            return
        partes = menu.addMenu("Adicionar parte")
        for parte in self.mesa.tamanhos:
            partes.addAction(parte, lambda p=parte: self.adicionar_parte(p))
        destino = menu.addMenu("Mover para o arquivo")
        destino.setEnabled(self._item_editavel() is not None)
        if destino.isEnabled():
            atual = self.mesa.item(self.selecionado).arquivo
            for numero in self.mesa.arquivos():
                if numero != atual:
                    destino.addAction(f"Arquivo {numero}", lambda n=numero: self.mover_para_arquivo(n))
            destino.addAction("Arquivo novo", lambda: self.mover_para_arquivo(None))
        menu.addAction("Um arquivo por cor", self.separar_por_cor)
        menu.addAction("Trocar a cor em uma altura…", self.escolher_alturas)
        separar = menu.addAction("Separar em peças por cor…", self.pedir_separacao)
        selecionado = self._item_editavel()
        separar.setEnabled(selecionado is not None and self._pode_separar(selecionado.parte))
        menu.addSeparator()
        menu.addAction("Organizar a mesa", self.organizar)
        desfazer = menu.addAction("Desfazer", self.desfazer)
        desfazer.setEnabled(bool(self._historico))
        refazer = menu.addAction("Refazer", self.refazer)
        refazer.setEnabled(bool(self._refeitos))

    def _abrir_menu_do_item(self, posicao: QPoint) -> None:
        menu = QMenu(self)
        for texto, acao in (("Enviar à IA", self.enviar_para_ia), ("Cor…", self.escolher_cor), ("Copiar", self.copiar), ("Duplicar", self.duplicar), ("Girar 90°", self.girar), ("Deletar", self.deletar)):
            menu.addAction(texto, acao)
        self._preencher_menu_tombar(menu.addMenu("Tombar"))
        menu.addSeparator()
        self._preencher_itens_extra(menu)
        menu.exec(posicao)

    def _preencher_itens_extra(self, menu: QMenu) -> None:
        extra = QMenu("Mesa", menu)
        self._preencher_menu(extra)
        menu.addMenu(extra)

    # ---------- Mouse ----------

    def dragEnterEvent(self, evento: Any) -> None:
        if _modelos_arrastados(evento.mimeData()):
            evento.acceptProposedAction()

    def dropEvent(self, evento: Any) -> None:
        caminhos = _modelos_arrastados(evento.mimeData())
        if caminhos:
            evento.acceptProposedAction()
            self.modelo_solto.emit(caminhos)

    def eventFilter(self, alvo: Any, evento: QEvent) -> bool:
        tipo = evento.type()
        # O visualizador tem o próprio soltar arquivos, que só desenharia a malha por cima da mesa.
        if tipo in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if _modelos_arrastados(evento.mimeData()):
                evento.acceptProposedAction()
            else:
                evento.ignore()
            return True
        if tipo == QEvent.Type.Drop:
            self.dropEvent(evento)
            return True
        if tipo == QEvent.Type.MouseButtonPress:
            return self._ao_pressionar(evento)
        if tipo == QEvent.Type.MouseMove and self._arrasto is not None:
            self._ao_arrastar(evento)
            return True
        if tipo == QEvent.Type.MouseButtonRelease:
            return self._ao_soltar(evento)
        if tipo == QEvent.Type.ContextMenu and self.editavel:
            return self._ao_pedir_menu(evento)
        return False

    def _ao_pressionar(self, evento: Any) -> bool:
        posicao = evento.position()
        if evento.button() == Qt.MouseButton.RightButton:
            self._clique_direito = posicao
            return False
        if evento.button() != Qt.MouseButton.LeftButton or evento.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return False
        self.cena.interactor.setFocus()
        identificador, z = self._item_sob(posicao)
        if identificador is None:
            self._clique = posicao
            return False
        self.selecionar(identificador)
        if self.editavel:
            item = self.mesa.item(identificador)
            self._arrasto = {
                "id": identificador,
                "origem": (item.x, item.y),
                "z": z,
                "inicio": self._ponto_na_mesa(posicao, z),
                "antes": self.mesa.para_dados(),
                "moveu": False,
            }
        return True

    def _ao_arrastar(self, evento: Any) -> None:
        arrasto = self._arrasto
        ponto = self._ponto_na_mesa(evento.position(), arrasto["z"])
        if ponto is None or arrasto["inicio"] is None:
            return
        x = arrasto["origem"][0] + ponto[0] - arrasto["inicio"][0]
        y = arrasto["origem"][1] + ponto[1] - arrasto["inicio"][1]
        self.mesa.mover(arrasto["id"], x, y)
        item = self.mesa.item(arrasto["id"])
        arrasto["moveu"] = (item.x, item.y) != arrasto["origem"]
        self._posicionar_ator(item)
        self._atualizar_textos()
        self.cena.render()

    def _ao_soltar(self, evento: Any) -> bool:
        if evento.button() == Qt.MouseButton.LeftButton and self._arrasto is not None:
            arrasto, self._arrasto = self._arrasto, None
            if arrasto["moveu"]:
                self._guardar_para_desfazer(arrasto["antes"])
                self._colorir()
                self._atualizar_limites()
                self._atualizar_barra()
                self._atualizar_textos()
                self.cena.render()
                self.mesa_mudou.emit()
            return True
        if evento.button() == Qt.MouseButton.LeftButton and self._clique is not None:
            # Clique no vazio tira a seleção. Se o mouse andou, era a câmera girando.
            if (evento.position() - self._clique).manhattanLength() <= DISTANCIA_DE_CLIQUE_PX:
                if self._separacao is None:
                    self.selecionar(None)
                elif (regiao := self._regiao_sob(evento.position())) is not None:
                    self.pintar_regiao(regiao)
            self._clique = None
        return False

    def _ao_pedir_menu(self, evento: Any) -> bool:
        # Arrastar com o botão direito aproxima a câmera: só um clique parado abre o menu.
        local = QPointF(evento.pos())
        if self._clique_direito is not None and (local - self._clique_direito).manhattanLength() > DISTANCIA_DE_CLIQUE_PX:
            return True
        identificador, _ = self._item_sob(local)
        if identificador is None:
            menu = QMenu(self)
            self._preencher_menu(menu)
            menu.exec(evento.globalPos())
            return True
        self.selecionar(identificador)
        self._abrir_menu_do_item(evento.globalPos())
        return True

    def _na_tela(self, posicao: QPointF) -> tuple[float, float]:
        """De coordenadas do Qt para as do VTK, que contam pixels físicos a partir de baixo."""
        interativo = self.cena.interactor
        razao = interativo.devicePixelRatioF()
        return posicao.x() * razao, (interativo.height() - posicao.y() - 1) * razao

    def _item_sob(self, posicao: QPointF) -> tuple[str | None, float]:
        from vtkmodules.vtkRenderingCore import vtkCellPicker

        seletor = vtkCellPicker()
        seletor.SetTolerance(0.0005)
        x, y = self._na_tela(posicao)
        if not seletor.Pick(x, y, 0, self.cena.renderer):
            return None, 0.0
        ator = seletor.GetActor()
        if ator is None:
            return None, 0.0
        endereco = ator.GetAddressAsString("")
        for identificador, (candidato, _) in self._atores.items():
            for parte_do_item in (candidato, *self._outros_atores.get(identificador, [])):
                if parte_do_item.GetAddressAsString("") == endereco:
                    return identificador, seletor.GetPickPosition()[2]
        return None, 0.0

    def _ponto_na_mesa(self, posicao: QPointF, z: float) -> tuple[float, float] | None:
        renderizador = self.cena.renderer
        x, y = self._na_tela(posicao)
        extremos = []
        for profundidade in (0.0, 1.0):
            renderizador.SetDisplayPoint(x, y, profundidade)
            renderizador.DisplayToWorld()
            mundo = renderizador.GetWorldPoint()
            extremos.append((mundo[0] / mundo[3], mundo[1] / mundo[3], mundo[2] / mundo[3]))
        return ponto_no_plano(extremos[0], extremos[1], z)

    # ---------- Câmera ----------

    def _enquadrar(
        self, limites: tuple[float, ...], direcao: tuple[float, float, float], para_cima: tuple[float, float, float], zoom: float = 1.0, paralela: bool = False
    ) -> None:
        """Olha na direção pedida e deixa o VTK calcular a distância que enquadra os limites."""
        if self.cena is None:
            return
        if paralela:
            self.cena.enable_parallel_projection()
        else:
            self.cena.disable_parallel_projection()
        centro = ((limites[0] + limites[1]) / 2, (limites[2] + limites[3]) / 2, (limites[4] + limites[5]) / 2)
        posicao = tuple(centro[i] + direcao[i] * 100.0 for i in range(3))
        self.cena.camera_position = [posicao, centro, para_cima]
        self.cena.reset_camera(render=False, bounds=limites)
        self.cena.camera.zoom(zoom)
        self.cena.reset_camera_clipping_range()
        self.cena.render()

    def _limites_mesa(self) -> tuple[float, ...]:
        volume = self.perfil["volume_mm"]["fisico"]
        return (0.0, float(volume["x"]), 0.0, float(volume["y"]), 0.0, float(volume["z"]))

    def ver_mesa(self) -> None:
        self._enquadrar(self._limites_mesa(), (0.72, -1.0, 0.62), (0, 0, 1), 1.25)

    def ver_peca(self) -> None:
        self._enquadrar(self._limites_peca or self._limites_mesa(), (0.72, -1.0, 0.62), (0, 0, 1), 0.85)

    def ver_lado(self, direcao: tuple[float, float, float]) -> None:
        # Sem perspectiva: de cima, uma peça alta "tombaria" sobre as vizinhas e atrapalharia posicionar.
        para_cima = (0, 1, 0) if abs(direcao[2]) == 1 else (0, 0, 1)
        limites = self._limites_mesa() if abs(direcao[2]) == 1 else self._limites_peca or self._limites_mesa()
        self._enquadrar(limites, direcao, para_cima, 0.9, paralela=True)

    def fechar(self) -> None:
        self._soltar_simulacao()
        self._soltar_separacao()
        if self.cena is not None:
            self.cena.close()
