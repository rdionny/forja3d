"""Uma janela só: abrir o app ou um arquivo com ele já aberto entrega o pedido para a janela que existe.

Duas janelas gravariam nos mesmos projetos ao mesmo tempo, e o arquivo aberto com dois cliques
não pode depender de o app estar fechado.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import socket
import time
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from forja3d.configuracao import pasta_usuario

log = logging.getLogger(__name__)

TENTATIVAS_DE_ENTREGA = 20
ESPERA_DA_TENTATIVA_S = 0.25
ESPERA_DO_SOCKET_MS = 2000
ESPERA_DA_LEITURA_S = 90.0
LIMITE_DO_CAMINHO_BYTES = 128 * 1024
# Outro processo, como o servidor MCP, mudou a peça desta pasta.
PREFIXO_DE_MUDANCA = "mudou:"


def nome_do_canal() -> str:
    return "forja3d-" + hashlib.sha256(str(pasta_usuario()).encode("utf-8")).hexdigest()[:24]


def avisar_que_mudou(pasta: Path) -> bool:
    """Avisa a janela aberta, se houver uma, que a peça mudou por fora. Bloqueia até a janela ler: chame em outra thread."""
    dados = (PREFIXO_DE_MUDANCA + str(pasta)).encode("utf-8")
    try:
        if os.name == "nt":
            # O QLocalServer do Windows é um pipe com nome: dá para falar com ele sem o Qt.
            with open(rf"\\.\pipe\{nome_do_canal()}", "wb", buffering=0) as canal:
                canal.write(dados)
        else:
            # No Linux, é um socket Unix na pasta temporária do Qt.
            with socket.socket(socket.AF_UNIX) as canal:
                canal.connect(str(Path(QDir.tempPath()) / nome_do_canal()))
                canal.sendall(dados)
        return True
    except OSError:
        return False


class InstanciaUnica(QObject):
    # O caminho do arquivo, ou None quando só pediram para abrir o app.
    recebido = Signal(object)
    # A pasta de uma peça que outro processo mudou.
    peca_mudou = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        pasta = pasta_usuario()
        pasta.mkdir(parents=True, exist_ok=True)
        self._nome = nome_do_canal()
        self._trava = QLockFile(str(pasta / "instancia.lock"))
        # Só a morte do processo libera a trava: uma janela aberta há dias continua valendo.
        self._trava.setStaleLockTime(0)
        self._servidor: QLocalServer | None = None

    def assumir(self) -> bool:
        """Devolve False quando já existe uma janela aberta."""
        if not self._trava.tryLock(0):
            return False
        self._servidor = QLocalServer(self)
        self._servidor.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._servidor.newConnection.connect(self._ao_conectar)
        if not self._servidor.listen(self._nome):
            log.warning("Não consegui escutar outros pedidos de abertura: %s", self._servidor.errorString())
        return True

    def entregar(self, arquivo: Path | None) -> bool:
        """Manda o pedido para a janela aberta, que pode ainda estar carregando."""
        if os.name == "nt":
            # Sem isso, o Windows só pisca a janela na barra de tarefas em vez de trazê-la para a frente.
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        cliente = QLocalSocket()
        for _ in range(TENTATIVAS_DE_ENTREGA):
            cliente.connectToServer(self._nome)
            if cliente.waitForConnected(ESPERA_DO_SOCKET_MS):
                break
            time.sleep(ESPERA_DA_TENTATIVA_S)
        else:
            log.warning("A janela aberta não respondeu: %s", cliente.errorString())
            return False
        cliente.write(str(arquivo or "").encode("utf-8"))
        # O pipe não guarda nada: a escrita só termina quando a janela lê, e ela pode estar carregando o VTK.
        limite = time.monotonic() + ESPERA_DA_LEITURA_S
        while cliente.bytesToWrite() > 0:
            if time.monotonic() > limite:
                log.warning("A janela aberta não leu o pedido a tempo")
                return False
            cliente.waitForBytesWritten(ESPERA_DO_SOCKET_MS)
        cliente.disconnectFromServer()
        if cliente.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            cliente.waitForDisconnected(ESPERA_DO_SOCKET_MS)
        return True

    def _ao_conectar(self) -> None:
        while self._servidor.hasPendingConnections():
            cliente = self._servidor.nextPendingConnection()
            cliente.disconnected.connect(lambda c=cliente: self._ler_pedido(c))
            if cliente.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self._ler_pedido(cliente)

    def _ler_pedido(self, cliente: QLocalSocket) -> None:
        dados = bytes(cliente.readAll())
        cliente.deleteLater()
        if len(dados) > LIMITE_DO_CAMINHO_BYTES:
            log.warning("Pedido de abertura ignorado: grande demais")
            return
        texto = dados.decode("utf-8", errors="replace")
        if texto.startswith(PREFIXO_DE_MUDANCA):
            self.peca_mudou.emit(Path(texto.removeprefix(PREFIXO_DE_MUDANCA)))
        elif not texto:
            self.recebido.emit(None)
        elif texto.lower().endswith(".forja3d"):
            self.recebido.emit(Path(texto))
        else:
            log.warning("Pedido de abertura ignorado: não é um arquivo .forja3d")
