"""Grava o microfone e transcreve no servidor configurado. O texto volta para o campo da conversa, para revisar antes de enviar.

O som chega em pedaços de 50 ms e fica só na memória: dá para desenhar o espectro e mandar
o que já foi falado para a transcrição ao vivo, sem arquivo no disco.
"""
from __future__ import annotations

import ctypes
import io
import os
import shutil
import subprocess
import threading
import time
import wave

import numpy as np
import openai
from openai import OpenAI

# O Whisper trabalha em 16 kHz mono: gravar mais só aumentaria o envio.
TAXA = 16000
BYTES_POR_AMOSTRA = 2
BYTES_POR_BLOCO = 1600  # 50 ms
BLOCOS_NA_FILA = 8
FAIXAS = 24
JANELA_DO_ESPECTRO = 1024  # 64 ms
# Medidos em um notebook: o ruído da sala fica abaixo de -62 dB, e a fala passa de -15 dB.
PISO_DB = -58.0
TETO_DB = -5.0
# O nome padrão da OpenAI. Servidores próprios, como o faster-whisper, aceitam e usam o modelo que têm.
MODELO = "whisper-1"
TEMPO_LIMITE_S = 180.0
WHDR_DONE = 1


class ErroDeAudio(Exception):
    """A mensagem é para o usuário."""


def faixas_do_espectro(pcm: bytes, quantidade: int = FAIXAS) -> list[float]:
    """De 0 a 1 por faixa de frequência, das graves às agudas, nos últimos 64 ms do som."""
    amostras = np.frombuffer(pcm[-BYTES_POR_AMOSTRA * JANELA_DO_ESPECTRO :], dtype="<i2").astype(np.float32) / 32768
    if len(amostras) < JANELA_DO_ESPECTRO:
        return [0.0] * quantidade
    intensidade = np.abs(np.fft.rfft(amostras * np.hanning(JANELA_DO_ESPECTRO)))
    frequencias = np.fft.rfftfreq(JANELA_DO_ESPECTRO, 1 / TAXA)
    bordas = np.geomspace(90, 6000, quantidade + 1)
    faixas = []
    for inicio, fim in zip(bordas, bordas[1:], strict=False):
        trecho = intensidade[(frequencias >= inicio) & (frequencias < fim)]
        db = 20 * np.log10(trecho.mean() + 1e-9) if trecho.size else PISO_DB
        faixas.append(float(np.clip((db - PISO_DB) / (TETO_DB - PISO_DB), 0, 1)))
    return faixas


def _para_wav(pcm: bytes) -> bytes:
    saida = io.BytesIO()
    with wave.open(saida, "wb") as arquivo:
        arquivo.setnchannels(1)
        arquivo.setsampwidth(BYTES_POR_AMOSTRA)
        arquivo.setframerate(TAXA)
        arquivo.writeframes(pcm)
    return saida.getvalue()


class _Formato(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16), ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32), ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
        ("cbSize", ctypes.c_uint16),
    ]


class _Cabecalho(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32), ("dwBytesRecorded", ctypes.c_uint32),
        ("dwUser", ctypes.c_size_t), ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t),
    ]


def _winmm():
    winmm = ctypes.windll.winmm
    tamanho = ctypes.c_uint
    winmm.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.POINTER(_Formato), ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint32]
    for nome in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
        getattr(winmm, nome).argtypes = [ctypes.c_void_p, ctypes.POINTER(_Cabecalho), tamanho]
    for nome in ("waveInStart", "waveInReset", "waveInClose"):
        getattr(winmm, nome).argtypes = [ctypes.c_void_p]
    winmm.waveInGetErrorTextW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    return winmm


class Gravador:
    """No Windows, a entrada de som do próprio sistema (waveIn). No Linux, o arecord."""

    def __init__(self) -> None:
        self._pcm = bytearray()
        self._trava = threading.Lock()
        self._parar = threading.Event()
        self._linha: threading.Thread | None = None
        self._processo: subprocess.Popen | None = None
        self._dispositivo = ctypes.c_void_p()
        self._cabecalhos: list[_Cabecalho] = []
        self._memorias: list[ctypes.Array] = []
        self._proximo = 0

    @property
    def gravando(self) -> bool:
        return self._linha is not None

    @property
    def segundos(self) -> float:
        return len(self._pcm) / (BYTES_POR_AMOSTRA * TAXA)

    def iniciar(self) -> None:
        self._pcm = bytearray()
        self._parar.clear()
        if os.name == "nt":
            self._abrir_no_windows()
            alvo = self._coletar_no_windows
        elif shutil.which("arecord"):
            self._processo = subprocess.Popen(
                ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(TAXA), "-c", "1"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            alvo = self._coletar_no_linux
        else:
            raise ErroDeAudio("Para gravar áudio no Linux, instale o arecord, que vem no pacote alsa-utils.")
        self._linha = threading.Thread(target=alvo, daemon=True)
        self._linha.start()

    def faixas(self) -> list[float]:
        with self._trava:
            recente = bytes(self._pcm[-BYTES_POR_AMOSTRA * JANELA_DO_ESPECTRO :])
        return faixas_do_espectro(recente)

    def wav(self) -> bytes:
        """O que já foi gravado, para a transcrição ao vivo."""
        with self._trava:
            pcm = bytes(self._pcm)
        return _para_wav(pcm)

    def parar(self) -> bytes:
        """Encerra e devolve o WAV inteiro."""
        self._encerrar()
        if self.segundos < 0.1:
            raise ErroDeAudio("O microfone não gravou nada. Confira se ele está ligado e liberado para aplicativos nas configurações do sistema.")
        audio = self.wav()
        self._pcm = bytearray()
        return audio

    def cancelar(self) -> None:
        if self.gravando:
            self._encerrar()
        self._pcm = bytearray()

    def _encerrar(self) -> None:
        self._parar.set()
        if os.name == "nt":
            self._linha.join(2)
            self._fechar_no_windows()
        else:
            self._processo.terminate()
            self._linha.join(5)
            self._processo.wait(5)
            self._processo = None
        self._linha = None

    def _guardar(self, dados: bytes) -> None:
        with self._trava:
            self._pcm += dados

    # ---------- Windows ----------

    def _conferir(self, codigo: int) -> None:
        if codigo:
            texto = ctypes.create_unicode_buffer(256)
            _winmm().waveInGetErrorTextW(codigo, texto, 256)
            raise ErroDeAudio(f"Não consegui usar o microfone: {texto.value}")

    def _abrir_no_windows(self) -> None:
        winmm = _winmm()
        formato = _Formato(1, 1, TAXA, TAXA * BYTES_POR_AMOSTRA, BYTES_POR_AMOSTRA, 8 * BYTES_POR_AMOSTRA, 0)
        qualquer_microfone, sem_aviso = 0xFFFFFFFF, 0
        self._conferir(winmm.waveInOpen(ctypes.byref(self._dispositivo), qualquer_microfone, ctypes.byref(formato), 0, 0, sem_aviso))
        self._memorias = [ctypes.create_string_buffer(BYTES_POR_BLOCO) for _ in range(BLOCOS_NA_FILA)]
        self._cabecalhos = [_Cabecalho(ctypes.addressof(m), BYTES_POR_BLOCO) for m in self._memorias]
        self._proximo = 0
        try:
            for cabecalho in self._cabecalhos:
                self._conferir(winmm.waveInPrepareHeader(self._dispositivo, ctypes.byref(cabecalho), ctypes.sizeof(cabecalho)))
                self._conferir(winmm.waveInAddBuffer(self._dispositivo, ctypes.byref(cabecalho), ctypes.sizeof(cabecalho)))
            self._conferir(winmm.waveInStart(self._dispositivo))
        except ErroDeAudio:
            self._fechar_no_windows()
            raise

    def _pegar_bloco_pronto(self) -> bool:
        cabecalho = self._cabecalhos[self._proximo]
        if not cabecalho.dwFlags & WHDR_DONE:
            return False
        self._guardar(ctypes.string_at(cabecalho.lpData, cabecalho.dwBytesRecorded))
        cabecalho.dwBytesRecorded = 0
        cabecalho.dwFlags &= ~WHDR_DONE
        self._proximo = (self._proximo + 1) % len(self._cabecalhos)
        return True

    def _coletar_no_windows(self) -> None:
        winmm = _winmm()
        while not self._parar.is_set():
            anterior = self._proximo
            if self._pegar_bloco_pronto():
                cabecalho = self._cabecalhos[anterior]
                winmm.waveInAddBuffer(self._dispositivo, ctypes.byref(cabecalho), ctypes.sizeof(cabecalho))
            else:
                time.sleep(0.01)

    def _fechar_no_windows(self) -> None:
        winmm = _winmm()
        # O reset devolve os blocos da fila marcados como prontos, o último só com o que deu tempo de gravar.
        winmm.waveInReset(self._dispositivo)
        for _ in range(len(self._cabecalhos)):
            if not self._pegar_bloco_pronto():
                break
        for cabecalho in self._cabecalhos:
            winmm.waveInUnprepareHeader(self._dispositivo, ctypes.byref(cabecalho), ctypes.sizeof(cabecalho))
        winmm.waveInClose(self._dispositivo)
        self._cabecalhos, self._memorias = [], []

    # ---------- Linux ----------

    def _coletar_no_linux(self) -> None:
        while dados := self._processo.stdout.read1(BYTES_POR_BLOCO):
            self._guardar(dados)


def transcrever(audio: bytes, endpoint: str) -> str:
    # O servidor de transcrição não pede chave, mas o pacote da OpenAI não aceita chave vazia.
    cliente = OpenAI(base_url=endpoint, api_key="sem-chave", timeout=TEMPO_LIMITE_S, max_retries=1)
    try:
        return cliente.audio.transcriptions.create(model=MODELO, file=("voz.wav", audio, "audio/wav"), language="pt").text.strip()
    except openai.APITimeoutError as erro:
        raise ErroDeAudio("O servidor de transcrição demorou demais. Tente um áudio mais curto.") from erro
    except openai.APIConnectionError as erro:
        raise ErroDeAudio("Não consegui conectar ao servidor de transcrição. Confira a rede e o endereço em Configurações.") from erro
    except openai.APIStatusError as erro:
        raise ErroDeAudio(f"O servidor de transcrição recusou o áudio (código {erro.status_code}). Confira o endereço em Configurações.") from erro
