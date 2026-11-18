"""Impressão pela USB, contra uma impressora falsa que responde como o Marlin. Nenhum teste abre porta de verdade."""
import json
import os
import re
import threading
import time
from collections import deque
from contextlib import nullcontext
from functools import reduce
from operator import xor

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from forja3d.fatiamento import usb
from forja3d.fatiamento.cores import bloco_de_troca
from forja3d.fatiamento.exportacao import ExportacaoBloqueada, gcode_aprovado
from test_mesa_interface import aberta, janela, preparar_falso  # noqa: F401  As fixtures da janela com uma peça aberta.

Z_MAXIMO = 239.0
INICIO = ["M190 S55", "M109 S220", "G28", "G90", "M83", "G1 Z0.2 F3000", "G1 X10 Y10 F9000"]
CAMADA = [f"G1 X{10 + i} Y{10 + i} E0.05 F3000" for i in range(1, 30)]


class MarlinFalsa:
    """Confere número e checksum de cada linha, pede de novo o que chegou estragado e responde ok, como o Marlin."""

    def __init__(self, estragar=(), antes_do_ok=None, prender=None, cair_depois_de=None, m27="Not SD printing"):
        self.recebidas: list[str] = []
        self.saida = deque([b"start\n", b"echo:Marlin bugfix-2.0.x\n"])
        self.ultima = 0
        self.estragar = set(estragar)
        self.antes_do_ok = {"M27": [m27], **(antes_do_ok or {})}
        self.prender = prender  # Este comando só responde depois de um M108 ou de soltar(), como um M109 aquecendo.
        self.preso = False
        self.cair_depois_de = cair_depois_de
        self.fechada = False

    def write(self, dados: bytes) -> int:
        if self.cair_depois_de is not None and len(self.recebidas) >= self.cair_depois_de:
            raise OSError("ClearCommError failed")
        for linha in dados.decode("ascii").splitlines():
            self._receber(linha)
        return len(dados)

    def readline(self) -> bytes:
        if self.cair_depois_de is not None and len(self.recebidas) >= self.cair_depois_de:
            raise OSError("ClearCommError failed")
        if self.saida:
            return self.saida.popleft()
        time.sleep(0.001)
        return b""

    def close(self) -> None:
        self.fechada = True

    def esperar_presa(self) -> None:
        fim = time.monotonic() + 5
        while not self.preso:
            assert time.monotonic() < fim, "a linha presa não chegou"
            time.sleep(0.01)

    def soltar(self) -> None:
        self.preso = False
        self._responder("ok")

    def _responder(self, *linhas: str) -> None:
        self.saida.extend(f"{linha}\n".encode() for linha in linhas)

    def _receber(self, linha: str) -> None:
        if linha.startswith("N"):
            corpo, _, checksum = linha.partition("*")
            numero, comando = corpo.split(" ", 1)
            numero = int(numero[1:])
            if numero in self.estragar or int(checksum) != reduce(xor, corpo.encode(), 0):
                self.estragar.discard(numero)
                self._responder(f"Error:checksum mismatch, Last Line: {self.ultima}", f"Resend: {self.ultima + 1}", "ok")
                return
            if numero != self.ultima + 1:
                self._responder(f"Error:Line Number is not Last Line Number+1, Last Line: {self.ultima}", f"Resend: {self.ultima + 1}", "ok")
                return
            self.ultima = numero
        else:
            comando = linha
        if comando.startswith("M110"):
            self.ultima = int(re.search(r"N(\d+)", comando).group(1))
        self.recebidas.append(comando)
        if self.preso and comando == "M108":
            self.preso = False
            self._responder("ok")
        if comando == self.prender:
            self.preso = True
            self._responder("T:150.0 /220.0 B:55.0 /55.0 @:127 B@:0")
            return
        self._responder(*self.antes_do_ok.get(comando.split()[0], []), "ok")

    def do_gcode(self) -> list[str]:
        """O que chegou depois da conversa inicial: M110, M115 e M27."""
        return self.recebidas[self.recebidas.index("M27") + 1 :]


class Avisos(list):
    def __call__(self, estado, texto, fracao, restante):
        self.append((estado, texto, fracao, restante))

    @property
    def estados(self):
        return [a[0] for a in self]

    def esperar(self, estado, segundos=5.0):
        fim = time.monotonic() + segundos
        while estado not in self.estados:
            assert time.monotonic() < fim, f"não chegou a {estado}: {self.estados}"
            time.sleep(0.01)


@pytest.fixture(autouse=True)
def rapido(monkeypatch):
    monkeypatch.setattr(usb, "M105_NA_PAUSA_A_CADA_S", 0.05)
    monkeypatch.setattr(usb, "AVISO_A_CADA_S", 0.0)
    monkeypatch.setattr(usb, "ESPERA_DA_TENTATIVA_S", 0.2)
    monkeypatch.setattr(usb, "ESPERA_DO_DESCARTE_S", 0.05)


def gravar(tmp_path, linhas):
    gcode = tmp_path / "peca.gcode"
    gcode.write_text("; gerado pelo teste\n" + "\n".join(linhas) + "\n", encoding="utf-8")
    return gcode


def rodar(tmp_path, linhas, impressora, tempo=600.0):
    avisos = Avisos()
    impressao = usb.ImpressaoUsb(gravar(tmp_path, linhas), tempo, Z_MAXIMO, avisos)
    linha = threading.Thread(target=impressao.executar, args=(impressora,), daemon=True)
    linha.start()
    return impressao, avisos, linha


def comandos_do_arquivo(linhas):
    return [c for linha in linhas if (c := linha.split(";", 1)[0].strip()) and c != "M25"]


def test_manda_todas_as_linhas_numeradas_e_termina(tmp_path):
    impressora = MarlinFalsa()
    linhas = [*INICIO, "; comentário que não vai", "", *CAMADA]
    _, avisos, linha = rodar(tmp_path, linhas, impressora)
    linha.join(10)
    assert avisos[-1][0] == "concluido"
    assert impressora.do_gcode() == [*comandos_do_arquivo(linhas), "M400"]
    assert impressora.recebidas[0] == "M110 N0"
    fracoes = [a[2] for a in avisos if a[0] == "imprimindo"]
    assert fracoes == sorted(fracoes) and avisos[-1][2] == 1.0
    assert avisos[-1][3] == 0


def test_linha_estragada_vai_de_novo(tmp_path):
    impressora = MarlinFalsa(estragar={5, 9})
    _, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    linha.join(10)
    assert avisos[-1][0] == "concluido"
    assert impressora.do_gcode() == [*comandos_do_arquivo([*INICIO, *CAMADA]), "M400"]


def test_busy_e_temperatura_durante_o_aquecimento(tmp_path):
    respostas = {"M109": ["T:200.0 /220.0 B:55.0 /55.0 @:127 B@:0", "echo:busy: processing", "T:220.0 /220.0 B:55.0 /55.0"], "G28": ["echo:busy: processing"] * 3}
    impressora = MarlinFalsa(antes_do_ok=respostas)
    _, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    linha.join(10)
    assert avisos[-1][0] == "concluido"
    assert any("Bico 200 de 220 °C, mesa 55 de 55 °C" in a[1] for a in avisos)


def test_progresso_segue_o_tempo_dos_movimentos(tmp_path):
    curto, longo = "G1 X1 Y0 F600", "G1 X101 Y0 F600"
    tempos = usb.tempos_acumulados(["G90", curto, longo])
    assert tempos[1] == pytest.approx(0.1)
    assert tempos[2] == pytest.approx(10.1)


def test_troca_de_cor_espera_o_retomar_sem_mandar_o_m25(tmp_path):
    impressora = MarlinFalsa()
    troca = bloco_de_troca(1, 1, 2, 0.4, 0.4, 3000, "#1976D2", Z_MAXIMO, com_torre=True)
    linhas = [*INICIO, *CAMADA[:5], *troca, *CAMADA[5:]]
    impressao, avisos, linha = rodar(tmp_path, linhas, impressora)
    avisos.esperar("troca")
    time.sleep(0.2)
    assert impressora.recebidas[-1] in ("M105", "M117 Troca 1/1: AZUL")
    assert "M25" not in impressora.recebidas
    texto = next(a[1] for a in avisos if a[0] == "troca")
    assert texto.startswith("Troca 1 de 1: coloque azul.") and "Retomar" in texto
    assert "M105" in impressora.recebidas, "na pausa, o app confere a ligação"

    impressao.retomar()
    linha.join(10)
    assert avisos[-1][0] == "concluido"
    sem_conferencias = [c for c in impressora.do_gcode() if c != "M105"]
    assert sem_conferencias == [*comandos_do_arquivo(linhas), "M400"]


def test_pausa_sobe_vai_para_o_canto_e_volta(tmp_path):
    impressora = MarlinFalsa(prender=CAMADA[4])
    impressao, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    impressora.esperar_presa()
    impressao.pausar()
    impressora.soltar()
    avisos.esperar("pausado")
    recebidas = impressora.recebidas
    subida = recebidas.index("G1 Z5.200 F300")
    assert recebidas[subida - 1] == CAMADA[4]
    assert recebidas[subida + 1 : subida + 3] == [usb.CANTO, "M400"]
    x, y = re.match(r"G1 X(\S+) Y(\S+)", CAMADA[4]).groups()

    impressao.retomar()
    linha.join(10)
    assert avisos[-1][0] == "concluido"
    volta = [c for c in recebidas[subida + 3 :] if c != "M105"][:3]
    assert volta == [f"G1 X{float(x):.3f} Y{float(y):.3f} F5000", "G1 Z0.200 F300", "G1 F3000"]


def test_cancelar_desliga_bico_e_mesa(tmp_path):
    impressora = MarlinFalsa(prender=CAMADA[4])
    impressao, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    impressora.esperar_presa()
    impressao.cancelar()
    linha.join(10)
    assert avisos[-1][0] == "cancelado" and "desligados" in avisos[-1][1]
    final = impressora.recebidas[-8:]
    assert final[:4] == ["M108", "M104 S0", "M140 S0", "M107"]
    assert final[-1] == "M84 X Y E"
    assert CAMADA[5] not in impressora.recebidas


def test_cancelar_no_aquecimento_interrompe_a_espera(tmp_path):
    impressora = MarlinFalsa(prender="M109 S220")
    impressao, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    impressora.esperar_presa()
    impressao.cancelar()
    linha.join(10)
    assert avisos[-1][0] == "cancelado" and "desligados" in avisos[-1][1]
    assert "G28" not in impressora.recebidas


def test_queda_da_ligacao_e_avisada(tmp_path):
    impressora = MarlinFalsa(cair_depois_de=12)
    _, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    linha.join(10)
    estado, texto, _, _ = avisos[-1]
    assert estado == "erro"
    assert "A ligação com a impressora caiu" in texto and "desligue a impressora" in texto.lower()


def test_erro_da_impressora_para_e_desliga(tmp_path):
    impressora = MarlinFalsa(antes_do_ok={"G28": ["Error:Thermal Runaway, system stopped! Heater_ID: 0"]})
    _, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    linha.join(10)
    assert avisos[-1][0] == "erro" and "Thermal Runaway" in avisos[-1][1]
    assert "M104 S0" in impressora.recebidas
    assert "G1 X10 Y10 F9000" not in impressora.recebidas


def test_reinicio_no_meio_e_avisado(tmp_path):
    impressora = MarlinFalsa(antes_do_ok={"G28": ["start"]})
    _, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    linha.join(10)
    assert avisos[-1][0] == "erro" and "reiniciou" in avisos[-1][1]


def test_nao_atrapalha_uma_impressao_pelo_cartao(tmp_path):
    impressora = MarlinFalsa(m27="SD printing byte 1200/80000")
    _, avisos, linha = rodar(tmp_path, [*INICIO, *CAMADA], impressora)
    linha.join(10)
    assert avisos[-1][0] == "erro" and "imprimindo pelo cartão" in avisos[-1][1]
    assert impressora.do_gcode() == [], "nem o G-code nem o desligamento podem chegar"


def test_impressora_que_nao_responde(tmp_path, monkeypatch):
    monkeypatch.setattr(usb, "TENTATIVAS_DE_LIGAR", 2)
    impressora = MarlinFalsa()
    impressora.write = lambda dados: len(dados)
    impressora.saida.clear()
    _, avisos, linha = rodar(tmp_path, INICIO, impressora)
    linha.join(10)
    assert avisos[-1][0] == "erro" and "não respondeu" in avisos[-1][1]


def test_acha_a_impressora_pelo_chip_usb(monkeypatch):
    list_ports = pytest.importorskip("serial.tools.list_ports")

    class Porta:
        def __init__(self, device, vid, pid):
            self.device, self.vid, self.pid = device, vid, pid

    monkeypatch.setattr(list_ports, "comports", lambda: [Porta("COM1", None, None), Porta("COM3", 0x1A86, 0x7523)])
    assert usb.achar_impressora() == "COM3"
    monkeypatch.setattr(list_ports, "comports", lambda: [Porta("/dev/ttyS0", None, None)])
    assert usb.achar_impressora() is None


def test_so_um_gcode_aprovado_sai(tmp_path):
    (tmp_path / "peca.gcode").write_text("G28\n", encoding="utf-8")
    (tmp_path / "resumo.json").write_text(json.dumps({"validacao": {"aprovado": False}}), encoding="utf-8")
    with pytest.raises(ExportacaoBloqueada):
        gcode_aprovado(tmp_path)
    (tmp_path / "resumo.json").write_text(json.dumps({"validacao": {"aprovado": True}}), encoding="utf-8")
    assert gcode_aprovado(tmp_path) == tmp_path / "peca.gcode"


# ---------- Na janela ----------


@pytest.fixture
def com_impressora_falsa(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from forja3d.interface import janela_principal

    impressora = MarlinFalsa(prender="G28")
    monkeypatch.setattr(janela_principal, "achar_impressora", lambda: "COM3")
    monkeypatch.setattr(usb, "abrir_porta", lambda _dispositivo: impressora)
    monkeypatch.setattr(usb, "sem_dormir", nullcontext)
    perguntas = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **_k: (perguntas.append(a[2]), QMessageBox.StandardButton.Yes)[1])
    return impressora, perguntas


def test_botao_usb_imprime_o_arquivo_aprovado(aberta, qtbot, com_impressora_falsa):  # noqa: F811
    impressora, perguntas = com_impressora_falsa
    resumo, pasta = preparar_falso(aberta)
    (pasta / "peca.gcode").write_text("\n".join([*INICIO, *CAMADA]) + "\n", encoding="utf-8")
    assert not aberta.botao_usb.isEnabled(), "sem fatiar, não imprime"
    aberta.pasta_impressao = pasta
    aberta._ao_fatiar(resumo)
    assert aberta.botao_usb.isEnabled()

    aberta.botao_usb.click()
    assert "COM3" in perguntas[0] and "dormir" in perguntas[0] and "reiniciar" in perguntas[0]
    dialogo = aberta._dialogo_usb
    qtbot.waitUntil(lambda: impressora.preso, timeout=5000)
    assert not aberta.botao_usb.isEnabled(), "uma impressão de cada vez"
    assert "dormir" in dialogo.rotulo_aviso.text()

    # Fechar o app no meio pede confirmação e desliga bico e mesa.
    aberta.close()
    assert "cancela a impressão" in perguntas[-1]
    assert "M104 S0" in impressora.recebidas


def test_janela_da_usb_mostra_andamento_e_troca(qtbot, tmp_path, monkeypatch):
    from forja3d.interface.dialogo_usb import DialogoUsb

    impressora = MarlinFalsa()
    monkeypatch.setattr(usb, "abrir_porta", lambda _dispositivo: impressora)
    monkeypatch.setattr(usb, "sem_dormir", nullcontext)
    troca = bloco_de_troca(1, 1, 2, 0.4, 0.4, 3000, "#D32F2F", Z_MAXIMO, com_torre=True)
    dialogo = DialogoUsb(gravar(tmp_path, [*INICIO, *CAMADA[:5], *troca, *CAMADA[5:]]), 3600, Z_MAXIMO, "COM3")
    qtbot.addWidget(dialogo)
    dialogo.iniciar()
    qtbot.waitUntil(lambda: dialogo.estado == "troca", timeout=5000)
    assert dialogo.botao_pausar.text() == "Retomar" and dialogo.botao_pausar.isEnabled()
    assert "vermelho" in dialogo.rotulo_estado.text()
    assert dialogo.rotulo_tempo.text().startswith("Faltam ")
    dialogo.botao_pausar.click()
    qtbot.waitUntil(lambda: dialogo.estado == "concluido" and impressora.fechada, timeout=5000)
    assert dialogo.barra.value() == 1000
    assert dialogo.botao_fechar.isVisible() and not dialogo.botao_cancelar.isVisible()
