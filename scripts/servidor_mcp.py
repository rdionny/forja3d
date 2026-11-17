"""Inicia o servidor MCP do Forja3D direto do código, sem instalar o pacote. Veja "Usar com o Claude ou o Codex" no README.

Uso: .venv\\Scripts\\python.exe scripts\\servidor_mcp.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forja3d.__main__ import main

sys.exit(main(["--mcp"]))
