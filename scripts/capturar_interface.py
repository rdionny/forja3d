"""Abre a janela real, opcionalmente faz um pedido, e salva uma captura.

Uso: capturar_interface.py <saida.png> [pedido] [--segundos N] [--tamanho LxA] [--fatiar]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from forja3d import configuracao  # noqa: E402
from forja3d.impressora import carregar_perfil  # noqa: E402
from forja3d.interface import tema  # noqa: E402
from forja3d.interface.janela_principal import JanelaPrincipal  # noqa: E402
from forja3d.registro import configurar_logs  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

analisador = argparse.ArgumentParser()
analisador.add_argument("saida")
analisador.add_argument("pedido", nargs="?")
analisador.add_argument("--segundos", type=float, default=4)
analisador.add_argument("--tamanho", default="1440x880")
analisador.add_argument("--fatiar", action="store_true")
analisador.add_argument("--limite", type=float, default=420)
analisador.add_argument("--projeto", help="abre uma peça salva, sem chamar a IA")
dados = analisador.parse_args()

config = configuracao.carregar()
configurar_logs(config.ia.api_key)
aplicativo = QApplication(sys.argv)
aplicativo.setStyleSheet(tema.ESTILO)
janela = JanelaPrincipal(config, carregar_perfil())
largura, altura = (int(v) for v in dados.tamanho.split("x"))
janela.resize(largura, altura)
janela.show()
if dados.projeto:
    from forja3d.projetos.armazenamento import Projeto

    janela.abrir_projeto(Projeto(Path(dados.projeto)))
inicio = time.monotonic()
estado = {"fatiou": False, "pediu": False}


def capturar(sufixo: str = "") -> None:
    aplicativo.processEvents()
    destino = Path(dados.saida)
    if sufixo:
        destino = destino.with_name(destino.stem + sufixo + destino.suffix)
    destino.parent.mkdir(parents=True, exist_ok=True)
    janela.screen().grabWindow(janela.winId()).save(str(destino))
    print("captura:", destino, flush=True)


def relatorio() -> None:
    agente = janela.orquestrador
    if agente is None:
        return
    print(
        json.dumps(
            {
                "projeto": str(agente.projeto.pasta),
                "versao": agente.versao_atual,
                "versoes": agente.projeto.versoes(),
                "pode_aprovar": agente.pode_aprovar(),
                "aprovar_ativo": janela.botao_aprovar.isEnabled(),
                "exportar_ativo": janela.botao_exportar.isEnabled(),
                "resumo": janela.rotulo_resumo.text(),
                "verificacao": agente.resultado_verificacao and {
                    "aprovado": agente.resultado_verificacao["aprovado"],
                    "bloqueios": [i["codigo"] for i in agente.resultado_verificacao["bloqueios"]],
                    "alertas": [i["codigo"] for i in agente.resultado_verificacao["alertas"]],
                },
            },
            ensure_ascii=False,
            indent=1,
        ),
        flush=True,
    )


def passo() -> None:
    if dados.pedido and not estado["pediu"] and time.monotonic() - inicio > 1.5:
        estado["pediu"] = True
        janela.painel_chat.campo.setPlainText(dados.pedido)
        janela.painel_chat._enviar()
        QTimer.singleShot(4000, lambda: capturar("_trabalhando"))
        return
    if dados.pedido and estado["pediu"] and janela.ocupado:
        if time.monotonic() - inicio > dados.limite:
            print("Tempo esgotado", flush=True)
            capturar()
            aplicativo.quit()
        return
    if dados.pedido and estado["pediu"] and dados.fatiar and not estado["fatiou"]:
        if janela.botao_aprovar.isEnabled():
            capturar("_antes_de_fatiar")
            estado["fatiou"] = True
            janela._aprovar_e_fatiar()
            return
        print("Botão de aprovar desativado", flush=True)
    if time.monotonic() - inicio > dados.segundos and (not dados.pedido or estado["pediu"]):
        relatorio()
        capturar()
        aplicativo.quit()


relogio = QTimer()
relogio.timeout.connect(passo)
relogio.start(300)
codigo = aplicativo.exec()
janela.close()
sys.exit(codigo)
