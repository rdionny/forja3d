# -*- mode: python ; coding: utf-8 -*-
"""Empacota o Forja3D em uma pasta. Gere por scripts/gerar_instalador.py, que também confere o resultado.

Em uma pasta, e não em um arquivo só: o CAD e as vistas reabrem o próprio executável a cada peça,
e um arquivo único descompactaria mais de 1 GB em cada abertura.
"""
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

sys.path.insert(0, SPECPATH)
from lista_do_pacote import dados_do_app  # noqa: E402

RAIZ = Path(SPECPATH).parent
# O ícone .ico e os dados de versão são do executável do Windows. No Linux, o mesmo spec gera a pasta sem eles.
NO_WINDOWS = sys.platform == "win32"
VERSAO =tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
NUMEROS = (*(int(n) for n in VERSAO.split(".")), 0)
# Só funcionam com DLLs que vêm com esses produtos comerciais. O CadQuery usa o ipopt.
PLUGINS_SEM_USO = (
    "conic_xpress", "linsol_ma27", "nlpsol_ccopt", "nlpsol_conopt", "nlpsol_knitro", "nlpsol_madnlp", "nlpsol_snopt",
    "nlpsol_worhp", "libmatlab_ipc",
)

analise = Analysis(
    [str(RAIZ / "src" / "forja3d" / "__main__.py")],
    pathex=[str(RAIZ / "src")],
    # O CadQuery resolve montagens com plugins que o casadi carrega pelo nome, sem nenhum import.
    binaries=[par for par in collect_dynamic_libs("casadi") if not any(p in Path(par[0]).name for p in PLUGINS_SEM_USO)],
    datas=dados_do_app(RAIZ) + collect_data_files("ezdxf"),
    hiddenimports=[
        "PySide6.QtSvg",
        "vtkmodules.vtkInteractionStyle",
        "vtkmodules.vtkRenderingContextOpenGL2",
        "vtkmodules.vtkRenderingFreeType",
        "vtkmodules.vtkRenderingOpenGL2",
        "vtkmodules.vtkRenderingVolumeOpenGL2",
    ],
    excludes=[
        # Só para desenvolver.
        "PyInstaller", "_pyinstaller_hooks_contrib", "pytest", "_pytest", "pytestqt", "pluggy", "iniconfig", "ruff",
        # Visualizadores que o app não usa. O numba só serve para cadquery.vis, que a análise do script já proíbe.
        "cadquery.vis", "cadquery.fig", "numba", "llvmlite",
        "pyvista.ext", "trame", "trame_client", "trame_common", "trame_components",
        "trame_server", "trame_vtk", "trame_vuetify", "wslink", "aiohttp",
        "IPython", "ipywidgets", "jupyter_client", "notebook", "tkinter",
        "PyQt5", "PyQt6", "PySide2",
    ],
    noarchive=False,
)
pacote = PYZ(analise.pure)
executavel = EXE(
    pacote,
    analise.scripts,
    [],
    exclude_binaries=True,
    name="Forja3D",
    console=False,
    # Sem isso, um erro no CAD ou nas vistas abre uma janela de aviso, e o processo auxiliar fica esperando até o tempo limite.
    disable_windowed_traceback=True,
    upx=False,
    icon=str(RAIZ / "build" / "forja3d.ico") if NO_WINDOWS else None,
    version=None if not NO_WINDOWS else VSVersionInfo(
        ffi=FixedFileInfo(filevers=NUMEROS, prodvers=NUMEROS),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "041604B0",
                        [
                            StringStruct("FileDescription", "Forja3D"),
                            StringStruct("FileVersion", VERSAO),
                            StringStruct("InternalName", "Forja3D"),
                            StringStruct("OriginalFilename", "Forja3D.exe"),
                            StringStruct("ProductName", "Forja3D"),
                            StringStruct("ProductVersion", VERSAO),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0416, 1200])]),
        ],
    ),
)
COLLECT(executavel, analise.binaries, analise.datas, name="Forja3D", upx=False)
