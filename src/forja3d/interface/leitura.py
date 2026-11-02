"""Lê mensagens em voz alta com as vozes do próprio Windows: o texto não sai do computador."""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

# O texto vem pela entrada padrão, nunca dentro do comando: uma mensagem não tem como virar código.
SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$voz = New-Object System.Speech.Synthesis.SpeechSynthesizer
$portugues = $voz.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'pt*' } | Select-Object -First 1
if ($portugues) { $voz.SelectVoice($portugues.VoiceInfo.Name) }
$entrada = [Console]::OpenStandardInput()
$memoria = New-Object System.IO.MemoryStream
$entrada.CopyTo($memoria)
$voz.Speak([System.Text.Encoding]::UTF8.GetString($memoria.ToArray()))
"""


def comando_de_voz(script: str = SCRIPT) -> tuple[str, list[str]]:
    # Caminho completo: o Windows também procura executáveis na pasta atual.
    pasta = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
    codificado = base64.b64encode(script.encode("utf-16-le")).decode()
    return str(pasta / "powershell.exe"), ["-NoProfile", "-NonInteractive", "-EncodedCommand", codificado]


def texto_para_fala(texto: str) -> str:
    """Tira as marcas de Markdown, que a voz leria como "asterisco" e "sublinhado"."""
    limpo = re.sub(r"```.*?```", " Trecho de código. ", texto, flags=re.DOTALL)
    limpo = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", limpo)
    limpo = re.sub(r"^\s*#+\s*", "", limpo, flags=re.MULTILINE)
    limpo = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", limpo, flags=re.MULTILINE)
    limpo = re.sub(r"[*`]", "", limpo)
    limpo = re.sub(r"(?<=\w)_(?=\w)", " ", limpo)
    return limpo.strip()


class LeitorEmVoz(QObject):
    terminou = Signal()
    falhou = Signal(str)

    def __init__(self, parent: QObject | None = None, comando: tuple[str, list[str]] | None = None) -> None:
        super().__init__(parent)
        self._comando = comando or comando_de_voz()
        self._processo: QProcess | None = None

    def lendo(self) -> bool:
        return self._processo is not None

    def ler(self, texto: str) -> None:
        self.parar()
        processo = QProcess(self)
        processo.finished.connect(self._ao_terminar)
        processo.errorOccurred.connect(self._ao_errar)
        self._processo = processo
        processo.start(*self._comando)
        if self._processo is processo:
            processo.write(texto.encode("utf-8"))
            processo.closeWriteChannel()

    def parar(self) -> None:
        processo, self._processo = self._processo, None
        if processo is None:
            return
        processo.blockSignals(True)
        processo.kill()
        processo.waitForFinished(1000)
        processo.deleteLater()

    def _ao_terminar(self, codigo: int) -> None:
        processo, self._processo = self._processo, None
        if processo is None:
            return
        processo.deleteLater()
        if codigo == 0:
            self.terminou.emit()
        else:
            self.falhou.emit("Não consegui ler em voz alta: a voz do Windows devolveu um erro.")

    def _ao_errar(self, erro: QProcess.ProcessError) -> None:
        # Quando o processo já rodou, quem avisa é o _ao_terminar.
        if erro == QProcess.ProcessError.FailedToStart:
            self.parar()
            self.falhou.emit("Não consegui ler em voz alta: o PowerShell do Windows não abriu.")
