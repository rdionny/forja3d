"""Impressão direta pela USB, sem o cartão SD: o G-code aprovado vai linha por linha, como o Marlin pede.

Cada linha leva número e checksum (N12 G1 X10*34), e a seguinte só sai depois do ok. Um erro de
transmissão volta como Resend, e a linha vai de novo. O que no cartão a impressora faz sozinha, aqui
depende do computador: se ele dormir, a USB cair ou o app fechar, a impressão para no meio.

A troca de cor do app pausa com M25, que no Marlin só pausa a impressão do cartão. Pela USB, a
impressora seguiria direto para a purga, sem esperar ninguém. Por isso o M25 não vai: o app para de
mandar linhas ali e espera o clique em Retomar.

Conferido na V3 SE (Marlin V1.1.0 de abril de 2025): abrir a porta sem DTR não reiniciou a placa, o
firmware tem EMERGENCY_PARSER, então o M108 do cancelamento interrompe um M109 que ainda aquece, e
não tem HOST_ACTION_COMMANDS, então o visor da impressora não pausa nem retoma uma impressão pela USB.
"""
from __future__ import annotations

import ctypes
import errno
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from array import array
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from functools import reduce
from operator import xor
from pathlib import Path
from typing import Any

from forja3d.fatiamento.cores import estado_depois_da_linha, nome_da_cor
from forja3d.fatiamento.simulacao import VELOCIDADE_INICIAL_MM_MIN
from forja3d.fatiamento.validador_gcode import MARCA_INICIO

log = logging.getLogger(__name__)

VID_CH340, PID_CH340 = 0x1A86, 0x7523  # O chip USB da placa da V3 SE, que o Windows mostra como USB-SERIAL CH340.
VELOCIDADE_BAUD = 115200
LEITURA_S = 0.5
CUTUCAR_DEPOIS_DE_S = 30  # Sem nada da impressora: um M105 descobre se a linha ou o ok se perdeu.
DESISTIR_DEPOIS_DE_S = 180  # O aquecimento e o G28 mandam mensagens enquanto trabalham: tanto silêncio é ligação perdida.
TENTATIVAS_DE_LIGAR = 15
ESPERA_DA_TENTATIVA_S = 2.0
ESPERA_DO_DESCARTE_S = 1.0
AVISO_A_CADA_S = 0.5
M105_NA_PAUSA_A_CADA_S = 10
TEMPERATURA_VALE_S = 10
CONFIRMAR_O_DESLIGAMENTO_S = 120
LINHAS_GUARDADAS = 200
SUBIDA_NA_PAUSA_MM = 5.0
SUBIDA_NO_CANCELAMENTO_MM = 10.0
CANTO = "G1 X-2.1 Y20 F5000"  # O mesmo canto da troca de cor, fora da mesa.
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

_TROCA = re.compile(r"(\d+)/(\d+) .*para=(\S+)")
_RESEND = re.compile(r"(?:Resend|rs)\s*:?\s*N?(\d+)", re.IGNORECASE)
_TEMPERATURA = re.compile(r"T:\s*([\d.]+)\s*/\s*([\d.]+)(?:.*?B:\s*([\d.]+)\s*/\s*([\d.]+))?")
_E = re.compile(r"E(-?\d*\.?\d+)")
# Estes erros são da transmissão: o Marlin pede a linha de novo logo em seguida. Os outros param a impressora.
_ERROS_DE_TRANSMISSAO = ("line number", "checksum", "last line")

COMO_TROCAR_PELA_USB = (
    "A cabeça está no canto. Aperte a alavanca do extrusor, tire o filamento e coloque o novo até sair plástico. "
    "Depois clique em Retomar, aqui no app: a impressora purga sozinha e volta para a peça. "
    "O bico continua quente: não encoste nele."
)


class ErroUsb(Exception):
    """A impressão pela USB não começou ou parou. A mensagem é para o usuário."""


class _Cancelado(Exception):
    pass


def achar_impressora() -> str | None:
    """A porta pelo chip USB da impressora: COM3 no Windows, /dev/ttyUSB0 no Linux."""
    try:
        # Dentro da função: um pacote sem o pyserial ainda abre, e só esta parte avisa.
        from serial.tools import list_ports
    except ImportError as erro:
        raise ErroUsb("Falta o pyserial, a biblioteca que fala com a porta USB. Instale com: pip install pyserial==3.5") from erro
    return next((p.device for p in list_ports.comports() if (p.vid, p.pid) == (VID_CH340, PID_CH340)), None)


def abrir_porta(dispositivo: str) -> Any:
    import serial

    porta = serial.Serial(baudrate=VELOCIDADE_BAUD, timeout=LEITURA_S, write_timeout=10)
    porta.port = dispositivo
    # Nas placas que reiniciam pelo DTR, assim não reinicia. No Linux o sistema liga o DTR ao abrir, e ela pode reiniciar mesmo assim.
    porta.dtr = False
    try:
        porta.open()
    except serial.SerialException as erro:
        raise ErroUsb(_motivo_da_porta(dispositivo, erro)) from erro
    return porta


def _motivo_da_porta(dispositivo: str, erro: Exception) -> str:
    texto = str(erro)
    if getattr(erro, "errno", None) == errno.EACCES or "PermissionError" in texto:
        if os.name == "nt":
            return f"A porta {dispositivo} está em uso por outro programa, como o Creality Print, o Cura ou o OrcaSlicer. Feche-o e tente de novo."
        return f"Sem permissão para abrir {dispositivo}. Entre no grupo dialout com: sudo usermod -aG dialout $USER. Depois saia da sessão e entre de novo."
    return f"Não consegui abrir {dispositivo}: {texto}"


def impede_o_descanso() -> bool:
    return os.name == "nt" or shutil.which("systemd-inhibit") is not None


@contextmanager
def sem_dormir() -> Iterator[None]:
    """O sistema não dorme sozinho durante a impressão. Fechar a tampa ou mandar dormir ainda para tudo."""
    inibidor = None
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    elif shutil.which("systemd-inhibit"):
        inibidor = subprocess.Popen(
            ["systemd-inhibit", "--what=idle:sleep", "--who=Forja3D", "--why=Imprimindo pela USB", "sleep", "infinity"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    try:
        yield
    finally:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        elif inibidor is not None:
            with suppress(ProcessLookupError):
                os.killpg(inibidor.pid, signal.SIGTERM)


def ler_comandos(gcode: Path) -> tuple[list[str], dict[int, tuple[str, str, str | None]]]:
    """Os comandos sem os comentários e, para cada M25, a troca que ele marca: número, total e a cor seguinte."""
    comandos: list[str] = []
    trocas: dict[int, tuple[str, str, str | None]] = {}
    marca = None
    for linha in gcode.read_text(encoding="utf-8", errors="replace").splitlines():
        if linha.startswith(MARCA_INICIO):
            marca = _TROCA.search(linha)
        comando = linha.split(";", 1)[0].strip()
        if not comando:
            continue
        if comando.upper() == "M25":
            numero, total, cor = marca.groups() if marca else ("?", "?", "livre")
            trocas[len(comandos)] = (numero, total, None if cor == "livre" else cor)
        comandos.append(comando)
    return comandos, trocas


def tempos_acumulados(comandos: list[str]) -> array:
    """O tempo de movimento até o fim de cada comando, pela distância e pela velocidade. Sem aceleração: só vale como proporção."""
    estado: dict[str, Any] = {"x": 0.0, "y": 0.0, "z": 0.0, "f": VELOCIDADE_INICIAL_MM_MIN, "relativo": False}
    total = 0.0
    acumulados = array("d")
    for comando in comandos:
        if comando[:3] in ("G0 ", "G1 "):
            antes = (estado["x"], estado["y"], estado["z"])
            estado_depois_da_linha(comando, estado)
            extrusao = _E.search(comando)
            distancia = max(math.dist(antes, (estado["x"], estado["y"], estado["z"])), abs(float(extrusao.group(1))) if extrusao else 0.0)
            total += distancia / max(estado["f"], 1.0) * 60
        else:
            estado_depois_da_linha(comando, estado)
        acumulados.append(total)
    return acumulados


class ImpressaoUsb:
    """Manda um G-code aprovado pela USB. executar() roda numa thread; pausar, retomar e cancelar vêm de outra.

    avisar(estado, texto, fração, segundos restantes) conta o que acontece. Os estados: ligando, imprimindo,
    pausando, pausado, troca, cancelando, cancelado, concluido e erro. Os três últimos encerram.
    """

    def __init__(self, gcode: Path, tempo_estimado_s: float | None, z_maximo: float, avisar: Callable[[str, str, float, int | None], None]) -> None:
        self.gcode = gcode
        self.tempo_estimado_s = tempo_estimado_s
        self.z_maximo = z_maximo
        self.avisar = avisar
        self.comandos: list[str] = []
        self.trocas: dict[int, tuple[str, str, str | None]] = {}
        self.estado: dict[str, Any] = {"x": None, "y": None, "z": 0.0, "f": None, "relativo": False}
        self.porta: Any = None
        self.numero = 0
        self.fracao = 0.0
        self._acumulados = array("d")
        self._total_s = 1.0
        self._guardadas: dict[int, bytes] = {}
        self._resto = b""
        self._em_voo = False
        self._ligada = False
        self._temperaturas = ""
        self._hora_das_temperaturas = 0.0
        self._ultimo_aviso = 0.0
        self._estado_atual = ""
        self._pausar = threading.Event()
        self._retomar = threading.Event()
        self._cancelar = threading.Event()

    # ---------- Pedidos que vêm da interface ----------

    def pausar(self) -> None:
        self._retomar.clear()
        self._pausar.set()

    def retomar(self) -> None:
        self._pausar.clear()
        self._retomar.set()

    def cancelar(self) -> None:
        self._cancelar.set()
        self._retomar.set()  # Acorda quem espera numa pausa.

    # ---------- A impressão ----------

    def executar(self, porta: Any) -> None:
        self.porta = porta
        try:
            try:
                self._avisar("ligando", "Lendo o G-code…")
                self.comandos, self.trocas = ler_comandos(self.gcode)
                self._acumulados = tempos_acumulados(self.comandos)
                self._total_s = self.tempo_estimado_s or (self._acumulados[-1] if self._acumulados else 0.0) or 1.0
                self._ligar()
                self._imprimir()
            except _Cancelado:
                self._desligar()
            except ErroUsb as erro:
                if self._ligada:
                    porta.write(self._linhas_de_desligar())  # Sem esperar: a impressora pode nem responder mais.
                self._avisar("erro", str(erro))
        except OSError as erro:  # É assim que o pyserial avisa que a USB caiu.
            log.warning("A ligação USB com a impressora caiu: %s", erro)
            self._avisar(
                "erro",
                f"A ligação com a impressora caiu com {self.fracao:.0%} impresso: o computador dormiu, o cabo saiu ou a impressora desligou. "
                "A impressão parou e não tem como continuar de onde estava. Vá até a impressora: o bico e a mesa podem continuar quentes. "
                "Desligue a impressora no botão para esfriar.",
            )

    def _ligar(self) -> None:
        self._avisar("ligando", "Ligando à impressora…")
        for _ in range(TENTATIVAS_DE_LIGAR):
            self.porta.write(b"M110 N0\n")
            if self._ok_solto(ESPERA_DA_TENTATIVA_S):
                break
        else:
            raise ErroUsb("A impressora não respondeu pela USB. Confira se ela está ligada e se o cabo está bem encaixado nos dois lados.")
        while self._ok_solto(ESPERA_DO_DESCARTE_S):  # O ok de uma tentativa anterior que chegou atrasado.
            pass
        log.info("Impressora pela USB: %s", " | ".join(self._mandar("M115")))
        if any("SD printing byte" in resposta for resposta in self._mandar("M27")):
            raise ErroUsb("A impressora está imprimindo pelo cartão. Espere terminar, ou pare pelo visor dela, antes de imprimir pela USB.")
        self._ligada = True

    def _imprimir(self) -> None:
        for indice, comando in enumerate(self.comandos):
            self._atender_pedidos()
            if indice in self.trocas:
                self._trocar(*self.trocas[indice])
            else:
                self._mandar(comando)
                estado_depois_da_linha(comando, self.estado)
            self.fracao = self._acumulados[indice] / (self._acumulados[-1] or 1.0)
            if time.monotonic() - self._ultimo_aviso >= AVISO_A_CADA_S:
                self._avisar("imprimindo", self._temperaturas_recentes())
        self._mandar("M400")  # O último ok só diz que a linha entrou na fila: este espera a impressora terminar.
        self.fracao = 1.0
        self._avisar("concluido", "Impressão concluída. Espere a mesa esfriar para tirar a peça.")

    def _atender_pedidos(self) -> None:
        if self._cancelar.is_set():
            raise _Cancelado
        # Só pausa com as coordenadas absolutas: num trecho relativo, como a rotina de troca, a volta não teria referência.
        if self._pausar.is_set() and not self.estado["relativo"]:
            self._pausar.clear()
            self._pausa_do_usuario()

    def _pausa_do_usuario(self) -> None:
        """Sobe a cabeça e leva para o canto, e na volta deixa tudo onde estava. Antes do primeiro movimento, só para de mandar."""
        x, y, z, f = (self.estado[chave] for chave in ("x", "y", "z", "f"))
        estacionar = x is not None and y is not None
        self._avisar("pausando", "Pausando: a cabeça sobe e vai para o canto…" if estacionar else "Pausando…")
        if estacionar:
            self._mandar(f"G1 Z{z + max(0.0, min(SUBIDA_NA_PAUSA_MM, self.z_maximo - z)):.3f} F300")
            self._mandar(CANTO)
        self._mandar("M400")
        # shortcut: sem retração na pausa, porque o começo do arquivo usa extrusão absoluta; na volta, a primeira linha sai um pouco fraca.
        self._esperar("pausado", "Pausado. O bico e a mesa continuam quentes. Clique em Retomar para continuar.")
        if estacionar:
            self._mandar(f"G1 X{x:.3f} Y{y:.3f} F5000")
            self._mandar(f"G1 Z{z:.3f} F300")
            if f:
                self._mandar(f"G1 F{f:g}")

    def _trocar(self, numero: str, total: str, cor: str | None) -> None:
        """No lugar do M25: a rotina já levou a cabeça para o canto, e o que vem depois purga e volta."""
        self._retomar.clear()
        self._esperar("troca", f"Troca {numero} de {total}: coloque {nome_da_cor(cor)}. {COMO_TROCAR_PELA_USB}")

    def _esperar(self, estado: str, texto: str) -> None:
        """Para de mandar o G-code até o Retomar. Um M105 de vez em quando confere a ligação e mostra as temperaturas."""
        self._avisar(estado, texto)
        while not self._retomar.wait(M105_NA_PAUSA_A_CADA_S):
            self._mandar("M105")
            self._avisar(estado, f"{texto}\n{self._temperaturas_recentes()}".strip())
        if self._cancelar.is_set():
            raise _Cancelado
        self._retomar.clear()
        self._avisar("imprimindo", "")

    def _linhas_de_desligar(self) -> bytes:
        """Sem número de linha, para entrar mesmo com um comando esperando, como um M109 que ainda aquece. O M108 interrompe essa espera."""
        z = self.estado["z"] or 0.0
        subida = max(0.0, min(SUBIDA_NO_CANCELAMENTO_MM, self.z_maximo - z))
        comandos = ("M108", "M104 S0", "M140 S0", "M107", "G91", f"G1 Z{subida:g} F600", "G90", "M84 X Y E")
        return "".join(f"{c}\n" for c in comandos).encode()

    def _desligar(self) -> None:
        if not self._ligada:
            self._avisar("cancelado", "Cancelado antes de começar a imprimir.")
            return
        self._avisar("cancelando", "Cancelando: desligando o bico e a mesa…")
        linhas = self._linhas_de_desligar()
        esperados = linhas.count(b"\n") + self._em_voo
        self.porta.write(linhas)
        oks = 0
        fim = time.monotonic() + CONFIRMAR_O_DESLIGAMENTO_S
        while oks < esperados and time.monotonic() < fim:
            oks += self._ler().startswith("ok")
        if oks >= esperados:
            self._avisar("cancelado", "Impressão cancelada. O bico e a mesa foram desligados, e a cabeça subiu. Espere esfriar antes de tirar a peça.")
        else:
            self._avisar(
                "cancelado",
                "Impressão cancelada, mas a impressora não confirmou que desligou o aquecimento. "
                "Confira no visor dela se o bico e a mesa estão esfriando. Se não estiverem, desligue a impressora no botão.",
            )

    # ---------- O protocolo ----------

    def _mandar(self, comando: str) -> list[str]:
        """Manda uma linha com número e checksum e espera o ok. Devolve o que a impressora respondeu antes dele."""
        self.numero += 1
        corpo = f"N{self.numero} {comando}".encode("ascii", "replace")
        self._guardadas[self.numero] = corpo + b"*%d\n" % reduce(xor, corpo, 0)
        self._guardadas.pop(self.numero - LINHAS_GUARDADAS, None)
        return self._esperar_ok(self.numero)

    def _esperar_ok(self, ultimo: int) -> list[str]:
        """Um Resend volta para a linha pedida, e as seguintes vão de novo, uma a uma, até a última."""
        atual = ultimo
        reenviar = False
        respostas: list[str] = []
        self.porta.write(self._guardadas[atual])
        self._em_voo = True
        silencio = time.monotonic()
        cutucou = False
        while True:
            if self._cancelar.is_set():
                raise _Cancelado
            linha = self._ler()
            if not linha:
                parado = time.monotonic() - silencio
                if parado > DESISTIR_DEPOIS_DE_S:
                    raise ErroUsb(f"A impressora parou de responder há {DESISTIR_DEPOIS_DE_S // 60} minutos. A impressão parou. Confira o visor e o cabo.")
                if parado > CUTUCAR_DEPOIS_DE_S and not cutucou:
                    self.porta.write(b"M105\n")
                    cutucou = True
                continue
            silencio = time.monotonic()
            cutucou = False
            if linha.startswith("ok"):
                if reenviar:
                    reenviar = False
                elif atual == ultimo:
                    self._em_voo = False
                    return respostas
                else:
                    atual += 1
                self.porta.write(self._guardadas[atual])
            elif pedido := _RESEND.match(linha):
                atual = int(pedido.group(1))
                if atual not in self._guardadas:
                    raise ErroUsb(f"A impressora pediu de novo a linha {atual}, que o app não tem mais. A impressão parou.")
                reenviar = True
            else:
                self._tratar(linha)
                respostas.append(linha)

    def _ok_solto(self, segundos: float) -> bool:
        """Espera um ok sem número de linha, como o do M110 que começa a conversa."""
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            if self._cancelar.is_set():
                raise _Cancelado
            linha = self._ler()
            if linha.startswith("ok"):
                return True
            if linha:
                self._tratar(linha)
        return False

    def _ler(self) -> str:
        """Uma linha inteira. O que chega pela metade, quando o tempo de leitura acaba, espera o resto."""
        dados = self.porta.readline()
        if not dados.endswith(b"\n"):
            self._resto += dados
            return ""
        linha, self._resto = self._resto + dados, b""
        return linha.decode("ascii", "replace").strip()

    def _tratar(self, linha: str) -> None:
        if self._ligada and linha.startswith("start"):
            raise ErroUsb("A impressora reiniciou no meio da impressão, por falta de energia ou pela USB. A impressão parou e não tem como continuar de onde estava.")
        if linha.startswith(("Error:", "!!")) and not any(t in linha.lower() for t in _ERROS_DE_TRANSMISSAO):
            raise ErroUsb(f"A impressora parou com um erro: {linha}. Confira o visor dela antes de tentar de novo.")
        if temperatura := _TEMPERATURA.search(linha):
            bico, alvo, mesa, alvo_mesa = temperatura.groups()
            self._temperaturas = f"Bico {float(bico):.0f} de {float(alvo):.0f} °C" + (f", mesa {float(mesa):.0f} de {float(alvo_mesa):.0f} °C" if mesa else "")
            self._hora_das_temperaturas = time.monotonic()
            # Enquanto aquece, nenhuma linha nova sai: sem isto, a tela ficaria parada por minutos.
            if self._estado_atual == "imprimindo" and time.monotonic() - self._ultimo_aviso >= AVISO_A_CADA_S:
                self._avisar("imprimindo", self._temperaturas)

    def _temperaturas_recentes(self) -> str:
        return self._temperaturas if time.monotonic() - self._hora_das_temperaturas < TEMPERATURA_VALE_S else ""

    def _avisar(self, estado: str, texto: str) -> None:
        self._estado_atual = estado
        self._ultimo_aviso = time.monotonic()
        self.avisar(estado, texto, self.fracao, round(self._total_s * (1 - self.fracao)))


def imprimir(impressao: ImpressaoUsb, dispositivo: str) -> None:
    """Abre a porta, imprime e fecha. É o que a thread da janela roda."""
    try:
        porta = abrir_porta(dispositivo)
    except ErroUsb as erro:
        impressao.avisar("erro", str(erro), 0.0, None)
        return
    try:
        with sem_dormir():
            impressao.executar(porta)
    finally:
        porta.close()
