"""Confere um pacote do Forja3D antes de ele ir para outra pessoa.

Uso: conferir_pacote.py <pasta do pacote> [--orca <pasta do OrcaSlicer>]
     conferir_pacote.py --instalador <instalador.exe>

Procura as chaves e os endpoints deste computador em todos os arquivos, inclusive dentro do arquivo
compactado do PyInstaller e de ZIPs, onde uma busca comum não acha nada. Nunca imprime uma chave.
Também confere se está tudo lá e se nenhuma DLL depende de algo que só existe neste computador.

Sai com 0 só se não houver nenhum problema.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from collections.abc import Iterator
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "instalador"))
from lista_do_pacote import NOMES_PROIBIDOS, SUFIXOS_PROIBIDOS, dados_do_app  # noqa: E402

TAMANHO_MINIMO_DO_SEGREDO = 8
SUFIXOS_COMPACTADOS = (".zip", ".pyz", ".whl", ".jar", ".egg")
SUFIXOS_PE = (".exe", ".dll", ".pyd")
# Ficam em System32 quando o Visual C++ está instalado, mas não vêm com o Windows.
PREFIXOS_DO_VISUAL_CPP = ("msvcp1", "msvcp2", "vcruntime", "vcomp", "concrt", "vccorlib", "mfc", "ucrtbased", "msvcr1")
# Vêm com o Windows 10 e o 11, mas o produto que declaram é o do projeto ICU.
DO_WINDOWS_SEM_A_MARCA = {"icuuc.dll", "icuin.dll"}
ARQUIVOS_NECESSARIOS = (
    "Forja3D.exe",
    "_internal/PySide6/plugins/imageformats/qsvg.dll",
    "_internal/PySide6/plugins/iconengines/qsvgicon.dll",
    "_internal/PySide6/translations/qtbase_pt_BR.qm",
    "_internal/certifi/cacert.pem",
)
# Chaves curtas ou a palavra de quem não tem chave dariam falso alarme.
NAO_SAO_SEGREDO = {"sem-chave"}


# ---------- Segredos ----------


def _segredos_do_toml(caminho: Path, origem: str) -> dict[str, str]:
    if not caminho.is_file():
        return {}
    dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    provedores = [{"nome": "Padrão", **dados.get("ia", {})}, *dados.get("provedores", [])]
    segredos = {}
    for provedor in provedores:
        nome = provedor.get("nome", "?")
        if provedor.get("api_key"):
            segredos[provedor["api_key"]] = f"chave do provedor {nome} ({origem})"
        if provedor.get("endpoint"):
            segredos[provedor["endpoint"].rstrip("/")] = f"endpoint do provedor {nome} ({origem})"
    return segredos


def segredos_deste_computador() -> dict[str, str]:
    """Valor procurado e o nome que aparece no relatório, para o valor nunca ser impresso."""
    segredos = _segredos_do_toml(RAIZ / "config" / "ia_padrao.example.toml", "ia_padrao.example.toml")
    segredos |= _segredos_do_toml(RAIZ / "config" / "ia_padrao.toml", "ia_padrao.toml")
    appdata = os.environ.get("APPDATA")
    if appdata:
        segredos |= _segredos_do_toml(Path(appdata) / "Forja3D" / "config.toml", "config.toml do usuário")
    return {valor: nome for valor, nome in segredos.items() if len(valor) >= TAMANHO_MINIMO_DO_SEGREDO and valor not in NAO_SAO_SEGREDO}


def _formas(segredos: dict[str, str]) -> list[tuple[bytes, str]]:
    # UTF-16 é como um texto fica dentro de boa parte das DLLs do Windows.
    return [(forma, nome) for valor, nome in segredos.items() for forma in (valor.encode("utf-8"), valor.encode("utf-16-le"))]


def _conteudos_do_pyinstaller(caminho: Path) -> Iterator[tuple[str, bytes]]:
    """O que vai compactado dentro do executável: o código do app e das bibliotecas."""
    from PyInstaller.archive.readers import PKG_ITEM_PYZ, ArchiveReadError, CArchiveReader, NotAnArchiveError

    try:
        pacote = CArchiveReader(str(caminho))
    except (ArchiveReadError, NotAnArchiveError):
        return
    for nome, entrada in pacote.toc.items():
        if entrada[-1] == PKG_ITEM_PYZ:
            modulos = pacote.open_embedded_archive(nome)
            for modulo in modulos.toc:
                yield f"{nome}/{modulo}", modulos.extract(modulo, raw=True) or b""
        else:
            yield nome, pacote.extract(nome)


def _conteudos(caminho: Path) -> Iterator[tuple[str, bytes]]:
    """O arquivo como está e o que há compactado dentro dele."""
    yield "", caminho.read_bytes()
    if caminho.suffix.lower() in SUFIXOS_COMPACTADOS and zipfile.is_zipfile(caminho):
        with zipfile.ZipFile(caminho) as compactado:
            for nome in compactado.namelist():
                yield nome, compactado.read(nome)
    elif caminho.suffix.lower() == ".exe":
        yield from _conteudos_do_pyinstaller(caminho)


def procurar_segredos(pasta: Path, segredos: dict[str, str]) -> list[str]:
    formas = _formas(segredos)
    problemas = []
    for caminho in sorted(p for p in pasta.rglob("*") if p.is_file()):
        for interno, dados in _conteudos(caminho):
            for forma, nome in formas:
                if forma in dados:
                    local = caminho.relative_to(pasta).as_posix() + (f" > {interno}" if interno else "")
                    problemas.append(f"{nome} encontrada em {local}")
    return problemas


# ---------- Arquivos ----------


def procurar_arquivos_proibidos(pasta: Path) -> list[str]:
    return [
        f"arquivo que não pode ir no pacote: {p.relative_to(pasta).as_posix()}"
        for p in pasta.rglob("*")
        if p.is_file() and (p.name.lower() in NOMES_PROIBIDOS or p.name.lower().endswith(SUFIXOS_PROIBIDOS))
    ]


def conferir_lista_branca(pasta: Path, raiz: Path = RAIZ) -> list[str]:
    """A pasta config do pacote tem exatamente o que a lista manda, e o resto da lista está no lugar."""
    esperados = {f"{destino}/{Path(origem).name}" for origem, destino in dados_do_app(raiz)}
    interna = pasta / "_internal"
    faltando = [f"faltando no pacote: {e}" for e in sorted(esperados) if not (interna / e).is_file()]
    faltando += [f"faltando no pacote: {e}" for e in ARQUIVOS_NECESSARIOS if not (pasta / e).is_file()]
    config = interna / "config"
    a_mais = {p.relative_to(interna).as_posix() for p in config.rglob("*") if p.is_file()} - esperados if config.is_dir() else set()
    return faltando + [f"fora da lista branca: {e}" for e in sorted(a_mais)]


# ---------- Dependências ----------


def _dlls_importadas(caminho: Path) -> set[str]:
    import pefile

    try:
        pe = pefile.PE(str(caminho), fast_load=True)
    except pefile.PEFormatError:
        return set()
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"], pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]]
        )
        nomes = set()
        for atributo in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            nomes |= {e.dll.decode("ascii", "replace").lower() for e in getattr(pe, atributo, [])}
        return nomes
    finally:
        pe.close()


def _produto_da_dll(caminho: Path) -> str:
    import pefile

    pe = pefile.PE(str(caminho), fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        for informacao in getattr(pe, "FileInfo", []) or []:
            for item in informacao:
                for tabela in getattr(item, "StringTable", []):
                    produto = tabela.entries.get(b"ProductName")
                    if produto:
                        return produto.decode("utf-8", "replace")
        return ""
    finally:
        pe.close()


def vem_com_o_windows(dll: str, sistema: Path | None = None) -> bool:
    if dll.startswith(("api-ms-win-", "ext-ms-")) or dll in DO_WINDOWS_SEM_A_MARCA:
        return True
    if dll.startswith(PREFIXOS_DO_VISUAL_CPP):
        return False
    caminho = (sistema or Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32") / dll
    if not caminho.is_file():
        return False
    # Um programa pode ter deixado a própria DLL em System32: só vale o que o produto diz ser do Windows.
    return "Windows" in _produto_da_dll(caminho)


def procurar_dependencias_faltando(*pastas: Path) -> list[str]:
    """Só um Windows limpo prova que o pacote abre. Isto acha antes o caso mais comum de falha nele."""
    problemas = []
    for pasta in pastas:
        arquivos = [p for p in pasta.rglob("*") if p.is_file()]
        presentes = {p.name.lower() for p in arquivos}
        quem_precisa: dict[str, list[str]] = {}
        for caminho in arquivos:
            if caminho.suffix.lower() in SUFIXOS_PE:
                for dll in _dlls_importadas(caminho):
                    if dll not in presentes:
                        quem_precisa.setdefault(dll, []).append(caminho.relative_to(pasta).as_posix())
        for dll, usuarios in sorted(quem_precisa.items()):
            if not vem_com_o_windows(dll):
                problemas.append(f"{dll} não está em {pasta.name} nem vem com o Windows. Usada por {usuarios[0]} e mais {len(usuarios) - 1}")
    return problemas


# ---------- Execução ----------


def conferir_pasta(pasta: Path, orca: Path | None = None) -> list[str]:
    segredos = segredos_deste_computador()
    print(f"Procurando {len(segredos)} valores deste computador em {pasta}", flush=True)
    problemas = conferir_lista_branca(pasta) + procurar_arquivos_proibidos(pasta) + procurar_segredos(pasta, segredos)
    print("Conferindo as dependências das DLLs", flush=True)
    return problemas + procurar_dependencias_faltando(pasta, *([orca] if orca else []))


def conferir_instalador(instalador: Path) -> list[str]:
    """Instala em uma pasta temporária, sem atalho nem associação, confere o resultado e desinstala."""
    with tempfile.TemporaryDirectory(prefix="forja3d_instalado_", ignore_cleanup_errors=True) as temporaria:
        destino = Path(temporaria) / "Forja3D"
        subprocess.run(
            [str(instalador), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/MERGETASKS=!associar,!atalho", f"/DIR={destino}"],
            check=True,
        )
        try:
            return conferir_pasta(destino, destino / "ferramentas" / "OrcaSlicer_2.4.2")
        finally:
            subprocess.run([str(destino / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=False)
            # O desinstalador se copia para outro lugar e devolve o controle antes de terminar.
            limite = time.monotonic() + 120
            while (destino / "Forja3D.exe").exists() and time.monotonic() < limite:
                time.sleep(1)


def principal() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    analisador = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    analisador.add_argument("pasta", nargs="?", type=Path)
    analisador.add_argument("--orca", type=Path)
    analisador.add_argument("--instalador", type=Path)
    analisador.add_argument("--json", action="store_true", help="relatório em JSON")
    dados = analisador.parse_args()
    if bool(dados.pasta) == bool(dados.instalador):
        analisador.error("informe a pasta do pacote ou --instalador")

    problemas = conferir_instalador(dados.instalador) if dados.instalador else conferir_pasta(dados.pasta, dados.orca)
    if dados.json:
        print(json.dumps(problemas, ensure_ascii=False, indent=1))
    for problema in problemas:
        print(f"PROBLEMA: {problema}")
    print("Pacote conferido: nenhum problema." if not problemas else f"{len(problemas)} problemas.")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(principal())
