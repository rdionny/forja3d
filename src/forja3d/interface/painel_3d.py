"""Visualizador 3D: a peça em escala real, sobre a mesa da impressora."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from forja3d.interface import tema

os.environ.setdefault("QT_API", "pyside6")

ESPACO_ENTRE_PARTES = 10.0


class Painel3D(QWidget):
    def __init__(self, perfil: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.perfil = perfil
        self.cena = None
        self._limites_peca: tuple[float, ...] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        barra = QWidget(objectName="painel")
        linha = QHBoxLayout(barra)
        linha.setContentsMargins(10, 6, 10, 6)
        linha.setSpacing(4)
        for texto, acao, dica in (
            ("Peça", self.ver_peca, "Aproxima a câmera da peça"),
            ("Mesa", self.ver_mesa, "Mostra toda a mesa e o volume de impressão"),
            ("Frente", lambda: self.ver_lado((0, -1, 0)), "Vista frontal"),
            ("Lado", lambda: self.ver_lado((1, 0, 0)), "Vista lateral"),
            ("Topo", lambda: self.ver_lado((0, 0, 1)), "Vista de cima"),
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
        layout.addWidget(barra)

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
        self._montar_mesa()
        self.ver_mesa()

    def _montar_mesa(self) -> None:
        pv = self._pv
        volume = self.perfil["volume_mm"]
        x, y, z = volume["fisico"]["x"], volume["fisico"]["y"], volume["fisico"]["z"]
        seguro = volume["seguro"]
        centro_x, centro_y = x / 2, y / 2

        mesa = pv.Box(bounds=(0, x, 0, y, -2.0, -0.05))
        self.cena.add_mesh(mesa, color="#23272f", name="mesa", smooth_shading=False, lighting=True)
        grade = pv.Plane(center=(centro_x, centro_y, 0), direction=(0, 0, 1), i_size=x, j_size=y, i_resolution=11, j_resolution=11)
        self.cena.add_mesh(grade, style="wireframe", color="#3a404c", line_width=1, name="grade", lighting=False)

        margem_x, margem_y = (x - seguro["x"]) / 2, (y - seguro["y"]) / 2
        limite = pv.Box(bounds=(margem_x, x - margem_x, margem_y, y - margem_y, 0, seguro["z"]))
        fisico = pv.Box(bounds=(0, x, 0, y, 0, z))
        self._volume = [
            self.cena.add_mesh(fisico, style="wireframe", color="#5a6372", opacity=0.45, line_width=1, name="volume", lighting=False),
            self.cena.add_mesh(limite, style="wireframe", color=tema.ACENTO, opacity=0.3, line_width=1, name="limite", lighting=False),
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

    def mostrar(self, stls: list[Path]) -> None:
        """Coloca as partes centralizadas na mesa, como o fatiador faz."""
        self.limpar()
        if not stls:
            return
        if self.cena is None:
            self.rotulo_medida.setText(", ".join(s.stem for s in stls))
            return
        pv = self._pv
        malhas = []
        deslocamento = 0.0
        for caminho in stls:
            malha = pv.read(str(caminho))
            x0, x1, y0, y1, z0, _ = malha.bounds
            malhas.append(malha.translate((deslocamento - x0, -(y0 + y1) / 2, -z0)))
            deslocamento += (x1 - x0) + ESPACO_ENTRE_PARTES
        conjunto = malhas[0] if len(malhas) == 1 else malhas[0].merge(malhas[1:])
        x0, x1, y0, y1, z0, z1 = conjunto.bounds
        volume = self.perfil["volume_mm"]["fisico"]
        conjunto = conjunto.translate((volume["x"] / 2 - (x0 + x1) / 2, volume["y"] / 2 - (y0 + y1) / 2, 0))
        self._limites_peca = tuple(conjunto.bounds)

        self.cena.add_mesh(conjunto, color=tema.COR_PECA, name="peca", smooth_shading=False, specular=0.25, specular_power=20)
        arestas = conjunto.extract_feature_edges(feature_angle=30, boundary_edges=False, non_manifold_edges=False, manifold_edges=False)
        if arestas.n_cells:
            self.cena.add_mesh(arestas, color="#5e3413", line_width=1.5, name="peca_arestas", lighting=False)
        if len(stls) == 1:
            self.rotulo_medida.setText(f"{tema.numero(x1 - x0)} × {tema.numero(y1 - y0)} × {tema.numero(z1 - z0)} mm")
        else:
            self.rotulo_medida.setText(f"{len(stls)} partes")
        self.ver_peca()

    def limpar(self) -> None:
        self._limites_peca = None
        self.rotulo_medida.setText("")
        if self.cena is not None:
            self.cena.remove_actor("peca", render=False)
            self.cena.remove_actor("peca_arestas", render=False)
            self.cena.render()

    def _enquadrar(self, limites: tuple[float, ...], direcao: tuple[float, float, float], para_cima: tuple[float, float, float], zoom: float = 1.0) -> None:
        """Olha na direção pedida e deixa o VTK calcular a distância que enquadra os limites."""
        if self.cena is None:
            return
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
        para_cima = (0, 1, 0) if abs(direcao[2]) == 1 else (0, 0, 1)
        self._enquadrar(self._limites_peca or self._limites_mesa(), direcao, para_cima, 0.9)

    def fechar(self) -> None:
        if self.cena is not None:
            self.cena.close()
