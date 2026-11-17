"""Põe o OrcaSlicer 2.4.2 em ferramentas/, para usar o Forja3D pelo código em uma máquina nova. Só Windows.

Uso: .venv\\Scripts\\python.exe scripts\\baixar_orca.py

A pasta ferramentas/ não vai para o git: tem arquivos acima do limite de 100 MB do GitHub.
"""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gerar_instalador import RAIZ, zip_do_orca

DESTINO = RAIZ / "ferramentas" / "OrcaSlicer_2.4.2"

if (DESTINO / "orca-slicer.exe").is_file():
    print(f"O OrcaSlicer já está em {DESTINO}")
else:
    with zipfile.ZipFile(zip_do_orca()) as compactado:
        compactado.extractall(DESTINO)
    print(f"OrcaSlicer pronto em {DESTINO}")
