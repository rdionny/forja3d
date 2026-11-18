"""Áudio no chat: o texto transcrito vai para o campo, ao vivo e no fim, e nunca é enviado sozinho."""
import io
import json
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from forja3d import configuracao
from forja3d.interface import painel_chat, tema
from forja3d.interface.microfone import TAXA, ErroDeAudio, _para_wav, faixas_do_espectro, transcrever
from forja3d.interface.painel_chat import PainelChat

WAV = _para_wav(b"\x01\x00" * 1600)


class GravadorFalso:
    def __init__(self):
        self.gravando = False
        self.segundos = 0.0

    def iniciar(self):
        self.gravando = True

    def faixas(self):
        return [0.5] * 24

    def wav(self):
        return WAV

    def parar(self):
        self.gravando = False
        return WAV

    def cancelar(self):
        self.gravando = False


@pytest.fixture
def chat(qtbot):
    painel = PainelChat()
    painel.endereco_transcricao = "http://servidor/v1"
    painel.gravador = GravadorFalso()
    qtbot.addWidget(painel)
    return painel


def eventos(painel):
    widgets = (painel.lista.itemAt(i).widget() for i in range(painel.lista.count()))
    return [w.text() for w in widgets if w is not None and w.objectName() == "suave"]


def test_audio_transcrito_entra_no_campo_sem_enviar(chat, qtbot, monkeypatch):
    recebidos = []
    monkeypatch.setattr(painel_chat, "transcrever", lambda audio, endpoint: (recebidos.append((audio, endpoint)), "caixa de 80 mm")[1])
    enviados = []
    chat.enviado.connect(enviados.append)
    chat.campo.setPlainText("Quero uma")
    chat.botao_microfone.click()
    assert chat.gravador.gravando and chat.linha_audio.isVisibleTo(chat) and chat.campo.isReadOnly()
    chat.botao_microfone.click()
    qtbot.waitUntil(lambda: chat.campo.toPlainText() == "Quero uma caixa de 80 mm", timeout=5000)
    assert recebidos == [(WAV, "http://servidor/v1")]
    assert enviados == []
    assert chat.botao_microfone.isEnabled() and not chat.linha_audio.isVisibleTo(chat) and not chat.campo.isReadOnly()


def test_transcricao_ao_vivo_aparece_enquanto_fala_e_a_final_fica(chat, qtbot, monkeypatch):
    respostas = iter(["caixa de", "caixa de 80 mm com tampa"])
    monkeypatch.setattr(painel_chat, "transcrever", lambda _audio, _endpoint: next(respostas))
    chat.botao_microfone.click()
    chat._relogio_audio.stop()  # O teste avança o tempo da gravação à mão.
    chat.gravador.segundos = 1.0
    chat._atualizar_gravacao()
    assert not chat._parcial_em_andamento, "com pouca fala ainda não há o que transcrever"
    chat.gravador.segundos = painel_chat.INTERVALO_AO_VIVO_S + 0.5
    chat._atualizar_gravacao()
    qtbot.waitUntil(lambda: chat.campo.toPlainText() == "caixa de", timeout=5000)
    assert chat.gravador.gravando
    chat.botao_microfone.click()
    qtbot.waitUntil(lambda: chat.campo.toPlainText() == "caixa de 80 mm com tampa", timeout=5000)


def test_enter_durante_a_gravacao_nao_envia_o_texto_pela_metade(chat, monkeypatch):
    monkeypatch.setattr(painel_chat, "transcrever", lambda _audio, _endpoint: "")
    enviados = []
    chat.enviado.connect(enviados.append)
    chat.campo.setPlainText("rascunho")
    chat.botao_microfone.click()
    chat.botao_enviar.click()
    assert enviados == [] and chat.campo.toPlainText() == "rascunho"


def test_espectro_mostra_as_barras_e_desce_devagar(chat):
    chat.botao_microfone.click()
    chat._relogio_audio.stop()
    assert chat.espectro.alturas == [0.5] * 24
    chat.espectro.mostrar([0.0] * 24)
    assert all(0 < a < 0.5 for a in chat.espectro.alturas)
    chat.espectro.grab()  # Desenha de verdade.
    assert tema.ACENTO


def test_sem_servidor_configurado_avisa_e_nao_grava(chat):
    chat.endereco_transcricao = ""
    chat.botao_microfone.click()
    assert not chat.gravador.gravando
    assert any("Configurações" in e for e in eventos(chat))


def test_falha_na_transcricao_avisa_e_nao_mexe_no_campo(chat, qtbot, monkeypatch):
    def falhar(_audio, _endpoint):
        raise ErroDeAudio("Não consegui conectar ao servidor de transcrição.")

    monkeypatch.setattr(painel_chat, "transcrever", falhar)
    chat.campo.setPlainText("rascunho")
    chat.botao_microfone.click()
    chat.botao_microfone.click()
    qtbot.waitUntil(lambda: any("conectar" in e for e in eventos(chat)), timeout=5000)
    assert chat.campo.toPlainText() == "rascunho"
    assert chat.botao_microfone.isEnabled() and not chat.campo.isReadOnly()


def test_tom_de_1_khz_acende_a_faixa_de_1_khz():
    tempo = np.arange(2048) / TAXA
    tom = (np.sin(2 * np.pi * 1000 * tempo) * 8000).astype("<i2").tobytes()
    faixas = faixas_do_espectro(tom)
    bordas = np.geomspace(90, 6000, len(faixas) + 1)
    da_frequencia = int(np.searchsorted(bordas, 1000)) - 1
    assert faixas[da_frequencia] == max(faixas) > 0.5
    assert faixas_do_espectro(b"\0" * 4096) == [0.0] * len(faixas), "silêncio não mexe as barras"


def test_wav_gravado_e_o_que_o_whisper_espera():
    with wave.open(io.BytesIO(WAV)) as arquivo:
        assert (arquivo.getframerate(), arquivo.getnchannels(), arquivo.getsampwidth()) == (16000, 1, 2)


class Servidor(BaseHTTPRequestHandler):
    pedidos: ClassVar[list] = []

    def do_POST(self):  # noqa: N802 - nome definido pelo http.server.
        corpo = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).pedidos.append((self.path, corpo))
        resposta = json.dumps({"text": " Faça uma caixa. "}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resposta)))
        self.end_headers()
        self.wfile.write(resposta)

    def log_message(self, *_argumentos):
        pass


def test_transcrever_manda_o_wav_no_formato_da_openai():
    Servidor.pedidos = []
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        assert transcrever(WAV, f"http://127.0.0.1:{servidor.server_port}/v1") == "Faça uma caixa."
    finally:
        servidor.shutdown()
    caminho, corpo = Servidor.pedidos[0]
    assert caminho == "/v1/audio/transcriptions"
    assert WAV in corpo and b"whisper-1" in corpo and b'name="language"' in corpo


def test_servidor_fora_do_ar_vira_mensagem():
    with pytest.raises(ErroDeAudio, match="conectar"):
        transcrever(WAV, "http://127.0.0.1:9/v1")


def test_endereco_da_transcricao_e_salvo(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    configuracao.salvar_preferencias("http://u/v1", "k", "m", "", "", transcricao="https://voz/v1")
    assert configuracao.carregar().transcricao == "https://voz/v1"
