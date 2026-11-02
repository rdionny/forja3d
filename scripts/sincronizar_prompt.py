"""Copia PROMPT_AGENTE_3D.md para dentro do pacote. Rode depois de editar o prompt."""
import shutil
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
shutil.copyfile(RAIZ / "PROMPT_AGENTE_3D.md", RAIZ / "src" / "forja3d" / "ia" / "prompts" / "agente_3d.md")
print("Prompt sincronizado.")
