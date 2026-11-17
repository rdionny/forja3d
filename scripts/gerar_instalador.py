"""Gera o instalador do Forja3D. Veja a seção "Gerar o instalador" do README.

Uso: gerar_instalador.py [--sem-instalador]

Confere o OrcaSlicer pelo SHA-256, empacota o app, confere o pacote e, com o Inno Setup instalado,
gera o instalador e confere de novo o que ele instala.
"""
import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ZIP_ORCA = RAIZ / "ferramentas" / "download" / "OrcaSlicer_Windows_V2.4.2_x64_portable.zip"
URL_ORCA = "https://github.com/SoftFever/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Windows_V2.4.2_x64_portable.zip"
SHA256_ORCA = "feba3009dfb9d268779cca5758a1a5bc3b7d0722bf8fa48d5c57340de975d6be"
BUILD = RAIZ / "build"
PASTA_ORCA = BUILD / "orca" / "OrcaSlicer_2.4.2"
MARCA_ORCA = BUILD / "orca" / "extraido.sha256"
ICONE = BUILD / "forja3d.ico"
PACOTE = RAIZ / "dist" / "Forja3D"
LOCAIS_DO_ISCC = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Inno Setup 6" / "ISCC.exe",
)


def sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        while bloco := arquivo.read(1024 * 1024):
            resumo.update(bloco)
    return resumo.hexdigest()


def conferir_hash_do_orca(caminho: Path, esperado: str = SHA256_ORCA) -> None:
    encontrado = sha256(caminho)
    if encontrado != esperado:
        raise SystemExit(f"O SHA-256 de {caminho.name} não confere.\n  esperado:   {esperado}\n  encontrado: {encontrado}\nApague o arquivo e baixe de novo.")


def zip_do_orca() -> Path:
    """O ZIP oficial, baixado se faltar e sempre conferido pelo SHA-256."""
    if not ZIP_ORCA.is_file():
        print(f"Baixando {URL_ORCA}", flush=True)
        ZIP_ORCA.parent.mkdir(parents=True, exist_ok=True)
        parcial = ZIP_ORCA.with_suffix(".parcial")
        urllib.request.urlretrieve(URL_ORCA, parcial)  # noqa: S310 - endereço fixo acima, conferido pelo SHA-256 abaixo.
        conferir_hash_do_orca(parcial)
        parcial.replace(ZIP_ORCA)
    conferir_hash_do_orca(ZIP_ORCA)
    print("OrcaSlicer: SHA-256 confere.", flush=True)
    return ZIP_ORCA


def preparar_orca() -> None:
    """Extrai do ZIP conferido, nunca da pasta de trabalho: ela acumula logs e arquivos gerados pelo uso."""
    zip_do_orca()
    if MARCA_ORCA.is_file() and MARCA_ORCA.read_text(encoding="ascii") == SHA256_ORCA and (PASTA_ORCA / "orca-slicer.exe").is_file():
        return
    shutil.rmtree(PASTA_ORCA, ignore_errors=True)
    with zipfile.ZipFile(ZIP_ORCA) as compactado:
        compactado.extractall(PASTA_ORCA)
    MARCA_ORCA.write_text(SHA256_ORCA, encoding="ascii")


def gerar_icone() -> None:
    """Os mesmos desenhos que o app usa na janela, sem reduzir o grande: em 16 px, as camadas virariam borrão."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    sys.path.insert(0, str(RAIZ / "src"))
    from forja3d.interface.aplicativo import TAMANHOS_DO_ICONE, criar_icone

    _aplicativo = QApplication.instance() or QApplication([])
    imagens = []
    for lado in sorted(TAMANHOS_DO_ICONE, reverse=True):
        memoria = QBuffer()
        memoria.open(QIODevice.OpenModeFlag.WriteOnly)
        criar_icone(lado).save(memoria, "PNG")
        imagens.append(Image.open(io.BytesIO(bytes(memoria.data()))))
    ICONE.parent.mkdir(parents=True, exist_ok=True)
    imagens[0].save(ICONE, sizes=[imagem.size for imagem in imagens], append_images=imagens[1:])


def rodar(*comando: object) -> None:
    subprocess.run([str(parte) for parte in comando], check=True, cwd=RAIZ)


def localizar_iscc() -> Path | None:
    encontrado = shutil.which("ISCC")
    if encontrado:
        return Path(encontrado)
    return next((local for local in LOCAIS_DO_ISCC if local.is_file()), None)


def principal() -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--sem-instalador", action="store_true", help="para no pacote em dist/Forja3D")
    dados = analisador.parse_args()

    preparar_orca()
    gerar_icone()
    rodar(sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", RAIZ / "dist", "--workpath", BUILD / "pyinstaller", RAIZ / "instalador" / "forja3d.spec")
    conferir = (sys.executable, RAIZ / "scripts" / "conferir_pacote.py")
    rodar(*conferir, PACOTE, "--orca", PASTA_ORCA)
    if dados.sem_instalador:
        return 0

    iscc = localizar_iscc()
    if iscc is None:
        print("Não encontrei o Inno Setup 6. Instale com: winget install JRSoftware.InnoSetup", file=sys.stderr)
        return 1
    versao = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    rodar(iscc, f"/DVersao={versao}", RAIZ / "instalador" / "forja3d.iss")
    instalador = RAIZ / "dist" / "instalador" / f"Forja3D-{versao}-instalador.exe"
    rodar(*conferir, "--instalador", instalador)
    print(f"\nInstalador pronto: {instalador} ({instalador.stat().st_size / 1024**2:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
