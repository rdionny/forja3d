"""Roda o agente fora da thread da interface. A interface nunca pode travar."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from forja3d.agente.orquestrador import Orquestrador
from forja3d.configuracao import Configuracao
from forja3d.fatiamento.orca import ErroFatiamento, ParametrosFatiamento
from forja3d.ia.cliente import Cancelado, ClienteIA, ErroIA
from forja3d.projetos.armazenamento import Projeto

log = logging.getLogger(__name__)


class OuvinteQt(QObject):
    """Recebe os eventos do agente na thread de trabalho e os entrega como sinais."""

    sinal_etapa = Signal(str)
    sinal_especificacao = Signal(dict)
    sinal_versao = Signal(str)
    sinal_verificacao = Signal(str, dict)
    sinal_fatiamento = Signal(str, dict)

    def etapa(self, texto: str) -> None:
        self.sinal_etapa.emit(texto)

    def especificacao(self, especificacao: dict[str, Any]) -> None:
        self.sinal_especificacao.emit(especificacao)

    def versao(self, versao: str) -> None:
        self.sinal_versao.emit(versao)

    def verificacao(self, versao: str, resultado: dict[str, Any]) -> None:
        self.sinal_verificacao.emit(versao, resultado)

    def fatiamento(self, versao: str, resumo: dict[str, Any]) -> None:
        self.sinal_fatiamento.emit(versao, resumo)


class Trabalhador(QObject):
    resposta = Signal(str)
    erro = Signal(str)
    concluido = Signal()
    fatiado = Signal(dict)

    def __init__(self, config: Configuracao, perfil: dict[str, Any], fabrica_cliente: Callable[..., Any] = ClienteIA) -> None:
        super().__init__()
        self.config = config
        self.perfil = perfil
        self.fabrica_cliente = fabrica_cliente
        self.ouvinte = OuvinteQt()
        self.orquestrador: Orquestrador | None = None

    def abrir(self, projeto: Projeto) -> Orquestrador:
        self.orquestrador = Orquestrador(
            self.config, self.perfil, self.fabrica_cliente(self.config.ia), projeto, self.ouvinte
        )
        return self.orquestrador

    @Slot(str)
    def processar(self, texto: str) -> None:
        self._atender(lambda: self.orquestrador.processar(texto))

    @Slot(object)
    def processar_conceito(self, conceito: dict[str, Any]) -> None:
        self._atender(lambda: self.orquestrador.processar_conceito(conceito))

    @Slot(str, str)
    def processar_parte(self, texto: str, parte: str) -> None:
        self._atender(lambda: self.orquestrador.processar_parte(texto, parte))

    def _atender(self, pedido: Callable[[], str]) -> None:
        try:
            self.resposta.emit(pedido())
        except ErroIA as falha:
            self.erro.emit(str(falha))
        except Exception:
            log.exception("Falha inesperada no agente")
            self.erro.emit("Algo deu errado no agente. Os detalhes foram para o log. Suas versões salvas continuam intactas.")
        finally:
            self.concluido.emit()

    @Slot(object)
    def aprovar_e_fatiar(self, parametros: ParametrosFatiamento) -> None:
        try:
            self.orquestrador.aprovar()
            self.fatiado.emit(self.orquestrador.fatiar(parametros))
        except Cancelado:
            self.erro.emit("Fatiamento cancelado.")
        except (ErroFatiamento, ValueError) as falha:
            self.erro.emit(str(falha))
        except Exception:
            log.exception("Falha inesperada ao fatiar")
            self.erro.emit("Algo deu errado ao fatiar. Os detalhes foram para o log.")
        finally:
            self.concluido.emit()
