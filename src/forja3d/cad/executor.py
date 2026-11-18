"""Inicia processos auxiliares com ambiente limpo, timeout e cancelamento."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forja3d.cad.seguranca_ast import CodigoRecusado, validar_codigo
from forja3d.configuracao import EMPACOTADO, RAIZ
from forja3d.ia.cliente import Cancelado

SEM_JANELA = 0x08000000 if os.name == "nt" else 0
VARIAVEIS_PERMITIDAS = (
    "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC", "USERPROFILE", "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "SYSTEMDRIVE", "PROGRAMFILES", "PROGRAMDATA",
    # No Linux: a pasta do usuário, a tela para o VTK desenhar as vistas e o idioma.
    "HOME", "TMPDIR", "LANG", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
)


def ambiente_limpo() -> dict[str, str]:
    """Só o necessário. A chave da API e as variáveis do Forja3D nunca chegam aos processos auxiliares."""
    ambiente = {nome: os.environ[nome] for nome in VARIAVEIS_PERMITIDAS if nome in os.environ}
    if not EMPACOTADO:
        ambiente["PYTHONPATH"] = str(RAIZ / "src")
    ambiente["PYTHONIOENCODING"] = "utf-8"
    ambiente["PYTHONUTF8"] = "1"
    return ambiente


def comando_auxiliar(argumento: str, *resto: str) -> list[str]:
    # Em app empacotado, sys.executable é o próprio aplicativo.
    if EMPACOTADO:
        return [sys.executable, argumento, *resto]
    executavel = Path(sys.executable)
    # pythonw não tem console: os auxiliares usam python.exe, escondido por SEM_JANELA.
    if executavel.name.lower() == "pythonw.exe":
        executavel = executavel.with_name("python.exe")
    return [str(executavel), "-m", "forja3d", argumento, *resto]


def encerrar_arvore(processo: subprocess.Popen) -> None:
    # No Windows, o python.exe do venv é um lançador: o processo real é filho dele.
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(processo.pid), "/T", "/F"],
            capture_output=True,
            creationflags=SEM_JANELA,
            check=False,
        )
    processo.kill()
    processo.wait()


def aguardar(processo: subprocess.Popen, timeout: float, cancelar: threading.Event | None) -> str:
    """Devolve 'ok', 'timeout' ou lança Cancelado."""
    limite = time.monotonic() + timeout
    while processo.poll() is None:
        if cancelar is not None and cancelar.is_set():
            encerrar_arvore(processo)
            raise Cancelado
        if time.monotonic() > limite:
            encerrar_arvore(processo)
            return "timeout"
        time.sleep(0.05)
    return "ok"


@dataclass(frozen=True)
class ResultadoExecucao:
    ok: bool
    partes: list[dict[str, Any]] = field(default_factory=list)
    tipo_erro: str = ""
    erro: str = ""
    linha: int | None = None
    trecho: str = ""


def executar_script(
    codigo: str, pasta: Path, timeout: float = 120.0, cancelar: threading.Event | None = None
) -> ResultadoExecucao:
    """Executa o script em `pasta`, onde ficam o STL, o STEP e `resultado.json`."""
    try:
        validar_codigo(codigo)
    except CodigoRecusado as erro:
        return ResultadoExecucao(False, tipo_erro="CodigoRecusado", erro=erro.motivo, linha=erro.linha, trecho=_trecho(codigo, erro.linha))

    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "script.py").write_text(codigo, encoding="utf-8")
    with open(pasta / "worker_erros.txt", "wb") as erros:
        processo = subprocess.Popen(
            comando_auxiliar("--worker-cad", str(pasta)),
            cwd=pasta,
            env=ambiente_limpo(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=erros,
            creationflags=SEM_JANELA,
        )
        estado = aguardar(processo, timeout, cancelar)
    if estado == "timeout":
        return ResultadoExecucao(False, tipo_erro="Timeout", erro=f"O script passou de {timeout:.0f} s e foi encerrado.")

    arquivo = pasta / "resultado.json"
    if not arquivo.exists():
        return ResultadoExecucao(
            False,
            tipo_erro="FalhaKernel",
            erro=f"O kernel CAD encerrou de forma inesperada (código {processo.returncode}). Simplifique a operação mais recente.",
        )
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    if dados.get("ok"):
        return ResultadoExecucao(True, partes=dados["partes"])
    linha = dados.get("linha")
    return ResultadoExecucao(False, tipo_erro=dados.get("tipo", ""), erro=dados.get("erro", ""), linha=linha, trecho=_trecho(codigo, linha))


def _trecho(codigo: str, linha: int | None) -> str:
    if not linha:
        return ""
    linhas = codigo.splitlines()
    return linhas[linha - 1].strip() if 0 < linha <= len(linhas) else ""
