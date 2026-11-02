"""Fase 0: abre uma janela PySide6 com o STL sobre a mesa e também faz um render fora da tela."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

import pyvista as pv
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from pyvistaqt import QtInteractor

sys.stdout.reconfigure(encoding="utf-8")

MESA_X, MESA_Y, ALTURA = 220.0, 220.0, 250.0


def montar_cena(plotter, malha):
    mesa = pv.Plane(center=(0, 0, -0.5), direction=(0, 0, 1), i_size=MESA_X, j_size=MESA_Y)
    plotter.add_mesh(mesa, color="#3a3f47", show_edges=False)
    volume = pv.Box(bounds=(-MESA_X / 2, MESA_X / 2, -MESA_Y / 2, MESA_Y / 2, 0, ALTURA))
    plotter.add_mesh(volume, color="#7aa2f7", style="wireframe", opacity=0.4)
    plotter.add_mesh(malha, color="#e0af68", smooth_shading=False, show_edges=False)
    plotter.add_axes()


def render_fora_da_tela(malha, destino):
    plotter = pv.Plotter(off_screen=True, window_size=(1024, 1024))
    plotter.set_background("#1a1b26")
    plotter.add_mesh(malha, color="#e0af68")
    plotter.add_axes()
    plotter.camera_position = "iso"
    plotter.reset_camera()
    plotter.screenshot(str(destino))
    plotter.close()
    print("render fora da tela:", destino, destino.stat().st_size, "bytes")


def janela(malha, destino):
    app = QApplication.instance() or QApplication(sys.argv)
    principal = QMainWindow()
    principal.setWindowTitle("Forja3D: teste da Fase 0")
    principal.resize(900, 700)
    interator = QtInteractor(principal)
    principal.setCentralWidget(interator.interactor)
    interator.set_background("#1a1b26")
    montar_cena(interator, malha)
    interator.camera_position = "iso"
    interator.reset_camera()
    principal.show()

    def encerrar():
        interator.screenshot(str(destino))
        print("janela Qt:", destino, destino.stat().st_size, "bytes")
        interator.close()
        principal.close()
        app.quit()

    QTimer.singleShot(2500, encerrar)
    app.exec()


if __name__ == "__main__":
    stl = Path(sys.argv[1])
    pasta = Path(sys.argv[2])
    pasta.mkdir(parents=True, exist_ok=True)
    malha = pv.read(str(stl))
    print("malha:", malha.n_cells, "triângulos, limites", [round(v, 2) for v in malha.bounds])
    render_fora_da_tela(malha, pasta / "render_fora_da_tela.png")
    janela(malha.translate((-malha.center[0], -malha.center[1], -malha.bounds[4])), pasta / "janela_qt.png")
    print("OK")
