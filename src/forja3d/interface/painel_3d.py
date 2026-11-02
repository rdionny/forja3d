"""Visualizador 3D: os itens em escala real, sobre a mesa da impressora.

Dá para arrastar cada item, copiar, colar, duplicar, girar, deletar e separar em arquivos.
A lógica fica em `LayoutMesa`, que não depende da tela: aqui só o mouse, o teclado e o desenho.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from forja3d.interface import tema
from forja3d.layout_mesa import ItemMesa, LayoutMesa, ponto_no_plano

os.environ.setdefault("QT_API", "pyside6")

COR_SELECIONADO = "#ffc078"
COR_CONFLITO = tema.ERRO
COR_ARESTA = "#5e3413"
DISTANCIA_DE_CLIQUE_PX = 4
DICA_DA_MESA = "Clique em um item para selecionar. Arraste para mover."


class Painel3D(QWidget):
    mesa_mudou = Signal()
    referencia_pedida = Signal(str)

    def __init__(self, perfil: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.perfil = perfil
        self.cena = None
        self.mesa: LayoutMesa | None = None
        self.stls: dict[str, Path] = {}
        self.arquivo_ativo = 1
        self.selecionado: str | None = None
        self.editavel = False
        self.brim_mm = 0.0
        self._copiado: tuple[str, int] | None = None
        self._historico: list[dict[str, Any]] = []
        self._malhas: dict[str, tuple[Any, Any]] = {}
        self._atores: dict[str, tuple[Any, Any]] = {}
        self._arrasto: dict[str, Any] | None = None
        self._clique: QPointF | None = None
        self._clique_direito: QPointF | None = None
        self._limites_peca: tuple[float, ...] | None = None

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
            ("Esc", lambda: self.selecionar(None)),
        ):
            atalho = QShortcut(QKeySequence(tecla), self)
            atalho.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            atalho.activated.connect(acao)

        self._atualizar_barra()

        if os.environ.get("FORJA3D_SEM_3D"):
            self.area = QLabel("Visualizador desligado", alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.area, 1)
            return

        import pyvista as pv
        from pyvistaqt import QtInteractor

        self._pv = pv
        self.cena = QtInteractor(self)
        layout.addWidget(self.cena.interactor, 1)
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
            ("deletar", "Deletar", self.deletar, "Tira o item da mesa, sem mudar o modelo (Delete)"),
        ):
            botao = QPushButton(texto, objectName="plano", toolTip=dica)
            botao.clicked.connect(acao)
            self.botoes[chave] = botao
            linha.addWidget(botao)
        self.botao_mais = QPushButton("Mesa", objectName="plano", toolTip="Adicionar partes, mover entre arquivos, organizar e desfazer")
        self.menu_mesa = QMenu(self)
        self.menu_mesa.aboutToShow.connect(lambda: self._preencher_menu(self.menu_mesa))
        self.botao_mais.setMenu(self.menu_mesa)
        linha.addWidget(self.botao_mais)
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

    def mostrar_mesa(self, mesa: LayoutMesa, stls: dict[str, Path], editavel: bool = True) -> None:
        self.limpar()
        self.mesa = mesa
        self.stls = stls
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
        self.mesa = None
        self.stls = {}
        self.selecionado = None
        self.editavel = False
        self._historico = []
        self._limites_peca = None
        self._arrasto = None
        self.rotulo_medida.setText("")
        self._remover_atores()
        self._malhas = {}
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
            malha = malha.translate((-(x0 + x1) / 2, -(y0 + y1) / 2, -z0))
            arestas = malha.extract_feature_edges(feature_angle=30, boundary_edges=False, non_manifold_edges=False, manifold_edges=False)
            self._malhas[parte] = (malha, arestas)

    def _remover_atores(self) -> None:
        if self.cena is None:
            self._atores = {}
            return
        for ator, arestas in self._atores.values():
            self.cena.remove_actor(ator, reset_camera=False, render=False)
            if arestas is not None:
                self.cena.remove_actor(arestas, reset_camera=False, render=False)
        self._atores = {}

    def _redesenhar(self) -> None:
        """Refaz a cena depois de uma mudança na lista de itens. Mover só atualiza a posição."""
        self._atualizar_combo_arquivos()
        self._atualizar_barra()
        self._atualizar_textos()
        if self.cena is None or self.mesa is None:
            return
        self._remover_atores()
        for item in self.mesa.itens_do_arquivo(self.arquivo_ativo):
            malha, contorno = self._malhas[item.parte]
            ator = self.cena.add_mesh(
                malha, color=tema.COR_PECA, name=f"item_{item.id}", smooth_shading=False, specular=0.25, specular_power=20, reset_camera=False, render=False
            )
            arestas = None
            if contorno.n_cells:
                arestas = self.cena.add_mesh(
                    contorno, color=COR_ARESTA, line_width=1.5, name=f"arestas_{item.id}", lighting=False, pickable=False, reset_camera=False, render=False
                )
            self._atores[item.id] = (ator, arestas)
            self._posicionar_ator(item)
        self._colorir()
        self._atualizar_limites()
        self.cena.render()

    def _posicionar_ator(self, item: ItemMesa) -> None:
        for ator in self._atores[item.id]:
            if ator is not None:
                ator.SetPosition(item.x, item.y, 0.0)
                ator.SetOrientation(0.0, 0.0, float(item.giro))

    def _colorir(self) -> None:
        if self.mesa is None:
            return
        conflitos = self.mesa.conflitos(self.brim_mm)
        for identificador, (ator, _) in self._atores.items():
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
        z1 = max((self._malhas[i.parte][0].bounds[5] for i in self.mesa.itens_do_arquivo(self.arquivo_ativo)), default=1.0)
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
        for chave in ("copiar", "duplicar", "girar", "deletar"):
            self.botoes[chave].setEnabled(tem_selecao)
        self.botoes["colar"].setEnabled(self.editavel and self._copiado is not None)
        self.botao_mais.setEnabled(self.editavel)
        self.botao_ia.setEnabled(tem_selecao)
        self.combo_arquivo.setEnabled(self.mesa is not None and len(self.mesa.arquivos()) > 1)

    def _atualizar_textos(self) -> None:
        if self.mesa is None:
            self.rotulo_medida.setText("")
            self.rotulo_mesa.setText(DICA_DA_MESA)
            self.rotulo_aviso.setText("")
            return
        itens = self.mesa.itens_do_arquivo(self.arquivo_ativo)
        if len(itens) == 1:
            dx, dy = self.mesa.tamanho_do_item(itens[0])
            self.rotulo_medida.setText(f"{tema.numero(dx)} × {tema.numero(dy)} mm na mesa")
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
            self.rotulo_aviso.setText("")

    def definir_editavel(self, editavel: bool) -> None:
        self.editavel = editavel
        self._atualizar_barra()
        self._atualizar_textos()

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

    def _guardar_para_desfazer(self) -> None:
        self._historico.append(self.mesa.para_dados())

    def _depois_de_mudar(self) -> None:
        self._redesenhar()
        self.mesa_mudou.emit()

    def copiar(self) -> None:
        item = self._item_editavel()
        if item is not None:
            self._copiado = (item.parte, item.giro)
            self._atualizar_barra()

    def colar(self) -> None:
        if not self.editavel or self.mesa is None or self._copiado is None:
            return
        parte, giro = self._copiado
        if parte not in self.mesa.tamanhos:
            return
        self._guardar_para_desfazer()
        referencia = self.mesa.item(self.selecionado)
        perto = (referencia.x + self.mesa.tamanho_do_item(referencia)[0] + 6.0, referencia.y) if referencia else None
        self._colocar(self.mesa.adicionar(parte, self.arquivo_ativo, perto, giro))

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
            self.mesa.restaurar(self._historico.pop())
            if self.mesa.item(self.selecionado) is None:
                self.selecionado = None
            self._depois_de_mudar()

    def enviar_para_ia(self) -> None:
        item = self._item_editavel()
        if item is not None:
            self.referencia_pedida.emit(item.parte)

    def _item_editavel(self) -> ItemMesa | None:
        if not self.editavel or self.mesa is None:
            return None
        return self.mesa.item(self.selecionado)

    def _ao_escolher_arquivo(self, indice: int) -> None:
        numero = self.combo_arquivo.itemData(indice)
        if numero is not None and numero != self.arquivo_ativo:
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
        menu.addSeparator()
        menu.addAction("Organizar a mesa", self.organizar)
        desfazer = menu.addAction("Desfazer", self.desfazer)
        desfazer.setEnabled(bool(self._historico))

    def _abrir_menu_do_item(self, posicao: QPoint) -> None:
        menu = QMenu(self)
        for texto, acao in (("Enviar à IA", self.enviar_para_ia), ("Copiar", self.copiar), ("Duplicar", self.duplicar), ("Girar 90°", self.girar), ("Deletar", self.deletar)):
            menu.addAction(texto, acao)
        menu.addSeparator()
        self._preencher_itens_extra(menu)
        menu.exec(posicao)

    def _preencher_itens_extra(self, menu: QMenu) -> None:
        extra = QMenu("Mesa", menu)
        self._preencher_menu(extra)
        menu.addMenu(extra)

    # ---------- Mouse ----------

    def eventFilter(self, alvo: Any, evento: QEvent) -> bool:
        tipo = evento.type()
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
                self._historico.append(arrasto["antes"])
                self._colorir()
                self._atualizar_limites()
                self._atualizar_textos()
                self.cena.render()
                self.mesa_mudou.emit()
            return True
        if evento.button() == Qt.MouseButton.LeftButton and self._clique is not None:
            # Clique no vazio tira a seleção. Se o mouse andou, era a câmera girando.
            if (evento.position() - self._clique).manhattanLength() <= DISTANCIA_DE_CLIQUE_PX:
                self.selecionar(None)
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
            if candidato.GetAddressAsString("") == endereco:
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
        if self.cena is not None:
            self.cena.close()
